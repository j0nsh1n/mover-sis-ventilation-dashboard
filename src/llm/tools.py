"""Deterministic corpus tools the local LLM may call (app-executed)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from src.llm.case_context import build_case_context, phase_counts_line
from src.runtime_paths import wave_dir, waveform_case_dir
from src.search import filter_cases_by_keywords

# Max tool result characters returned to the model (keeps light models on track)
MAX_TOOL_CHARS = 6000


@dataclass
class SessionState:
    """Mutable session state shared between UI and the agent loop."""

    cases: pd.DataFrame | None = None
    timeseries: pd.DataFrame | None = None
    flags: pd.DataFrame | None = None
    episodes: pd.DataFrame | None = None
    events: pd.DataFrame | None = None
    active_pid: str | None = None
    focus_pids: list[str] = field(default_factory=list)
    last_tool_trace: list[str] = field(default_factory=list)
    # Full-EMR scan (src.pipeline.corpus_scan): one scored row per ventilated
    # surgery, no timeseries. ``cases`` above is only the analyzed sample.
    corpus: pd.DataFrame | None = None
    corpus_episodes: pd.DataFrame | None = None
    corpus_meta: dict[str, Any] | None = None

    def has_corpus_scan(self) -> bool:
        return self.corpus is not None and not self.corpus.empty

    def focus_frame(self) -> pd.DataFrame:
        if self.cases is None or self.cases.empty:
            return pd.DataFrame()
        if self.focus_pids:
            return self.cases[
                self.cases["PID"].astype(str).isin([str(p) for p in self.focus_pids])
            ].copy()
        return self.cases.copy()


def _clip(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 40] + "\n… [tool output truncated]"


def _case_line(row: pd.Series) -> str:
    pid = str(row.get("PID", ""))
    proc = str(row.get("Procedure_short", row.get("Procedure", "")) or "")[:60]
    agent = str(row.get("primary_agent_name", "") or "")
    score = row.get("anomaly_score", "")
    age = row.get("Age", "")
    sex = row.get("Gender", "")
    return (
        f"- PID={pid} | age={age} {sex} | agent={agent} | "
        f"score={score} | {proc}"
    )


SCOPE_FULL = "full"
SCOPE_SAMPLE = "sample"


def _emr_counts() -> tuple[int | None, int | None]:
    """(indexed surgeries, surgeries with ventilator rows) in the configured EMR."""
    try:
        from src.services.case_fetch import emr_index

        index = emr_index()
    except Exception:
        return None, None
    if index is None or index.empty:
        return None, None
    n_vent = int(index["has_vent"].sum()) if "has_vent" in index.columns else None
    return int(len(index)), n_vent


def _resolve_scope(session: SessionState, scope: str | None) -> str:
    """'full' when a full-EMR scan is loaded (or requested and present), else 'sample'."""
    want = str(scope or "auto").strip().lower()
    if want == SCOPE_SAMPLE:
        return SCOPE_SAMPLE
    return SCOPE_FULL if session.has_corpus_scan() else SCOPE_SAMPLE


def _scope_frame(session: SessionState, scope: str) -> pd.DataFrame | None:
    return session.corpus if scope == SCOPE_FULL else session.cases


def _scope_line(session: SessionState, scope: str, requested: str | None = None) -> str:
    """One line every corpus tool starts with, so answers never pass a sample off as the EMR."""
    n_indexed, n_vent = _emr_counts()
    meta = session.corpus_meta or {}
    n_indexed = meta.get("n_indexed_surgeries", n_indexed)
    n_vent = meta.get("n_ventilated_surgeries", n_vent)
    emr = ""
    if n_vent is not None and n_indexed is not None:
        emr = f"{n_vent} with ventilator data of {n_indexed} indexed surgeries in the EMR"
    if scope == SCOPE_FULL:
        n = len(session.corpus) if session.corpus is not None else 0
        return (
            f"SCOPE: full-EMR scan — {n} scored surgeries"
            + (f" ({emr})" if emr else "")
            + f"; preset {meta.get('preset', '?')}, scanned {meta.get('created_at', '?')}."
        )
    n = len(session.cases) if session.cases is not None else 0
    line = f"SCOPE: loaded sample — {n} analyzed cases, NOT the whole EMR"
    if emr:
        line += f" ({emr})"
    if str(requested or "").lower() == SCOPE_FULL:
        line += ". A full-EMR scan was requested but none is loaded (File → Scan full EMR)"
    elif session.has_corpus_scan():
        line += ". A full-EMR scan is loaded; pass scope='full' to use it"
    else:
        line += ". No full-EMR scan loaded (File → Scan full EMR)"
    return line + "."


def tool_corpus_overview(session: SessionState, **_kwargs: Any) -> str:
    cases = session.cases
    no_data = "NO_DATA: No processed cases loaded. User must load/run the pipeline (File → Reload processed data or Run pipeline)."
    if (cases is None or cases.empty) and not session.has_corpus_scan():
        return no_data
    scope = _resolve_scope(session, "auto")
    cases = _scope_frame(session, scope)
    if cases is None:
        return no_data
    lines = [
        _scope_line(session, scope),
        f"Figures below describe the {'full-EMR scan' if scope == SCOPE_FULL else 'loaded sample'} only.",
        f"Active PID: {session.active_pid or '(none selected)'}",
    ]
    if scope == SCOPE_FULL and session.cases is not None and not session.cases.empty:
        lines.append(
            f"Loaded sample for charts and minute-level detail: {len(session.cases)} cases."
        )
    if session.focus_pids:
        lines.append(f"Focus set: {len(session.focus_pids)} PID(s) from last search.")
    if "primary_agent_name" in cases.columns:
        top = cases["primary_agent_name"].fillna("unknown").value_counts().head(8)
        lines.append("Top primary agents:")
        for name, n in top.items():
            lines.append(f"  - {name}: {int(n)}")
    if "anomaly_score" in cases.columns:
        s = pd.to_numeric(cases["anomaly_score"], errors="coerce")
        lines.append(
            f"Anomaly score: min={int(s.min())}, median={int(s.median())}, "
            f"max={int(s.max())}"
        )
    if "Procedure_short" in cases.columns:
        lines.append(
            f"Procedures with text: {int(cases['Procedure_short'].notna().sum())}"
        )
    w = wave_dir()
    lines.append(f"Wave root configured: {w is not None}")
    return _clip("\n".join(lines))


def tool_search_cases(
    session: SessionState,
    *,
    query: str = "",
    limit: int = 12,
    scope: str = "auto",
    **_kwargs: Any,
) -> str:
    used = _resolve_scope(session, scope)
    cases = _scope_frame(session, used)
    if cases is None or cases.empty:
        return "NO_DATA: corpus empty."
    limit = max(1, min(int(limit or 12), 30))
    q = (query or "").strip()
    if not q:
        return "ERROR: search_cases requires a non-empty query."
    # The sample has per-minute flags; scan rows carry rule ids in top_rules
    flags = session.flags if used == SCOPE_SAMPLE else None
    hit = filter_cases_by_keywords(cases, q, flags=flags, match_all=True)
    if hit.empty:
        # fallback OR match for light-model queries
        hit = filter_cases_by_keywords(cases, q, flags=flags, match_all=False)
    if hit.empty:
        session.focus_pids = []
        return f"{_scope_line(session, used, scope)}\nSEARCH_EMPTY: no cases matched query={q!r}."
    if "anomaly_score" in hit.columns:
        hit = hit.sort_values("anomaly_score", ascending=False)
    session.focus_pids = [str(p) for p in hit["PID"].head(limit).tolist()]
    lines = [
        _scope_line(session, used, scope),
        f"SEARCH_OK: query={q!r} · matched={len(hit)} · showing={len(session.focus_pids)}",
        "Set focus to these PIDs. Call select_case with one PID before case detail answers.",
    ]
    for _, row in hit.head(limit).iterrows():
        lines.append(_case_line(row))
    return _clip("\n".join(lines))


def tool_top_anomaly_cases(
    session: SessionState,
    *,
    limit: int = 10,
    scope: str = "auto",
    per_hour: bool = False,
    **_kwargs: Any,
) -> str:
    used = _resolve_scope(session, scope)
    cases = _scope_frame(session, used)
    if cases is None or cases.empty:
        return "NO_DATA: corpus empty."
    limit = max(1, min(int(limit or 10), 30))
    key = "anomaly_score_per_hour" if per_hour else "anomaly_score"
    if key not in cases.columns:
        return f"ERROR: {key} column missing."
    hit = cases.sort_values(key, ascending=False).head(limit)
    session.focus_pids = [str(p) for p in hit["PID"].tolist()]
    lines = [_scope_line(session, used, scope), f"TOP_ANOMALY: n={len(hit)} · ranked by {key}"]
    for _, row in hit.iterrows():
        lines.append(_case_line(row))
    return _clip("\n".join(lines))


def _fetch_into_session(session: SessionState, pid: str) -> bool:
    """Analyze one surgery on demand and add it to the session's sample frames."""
    try:
        from src.services.case_fetch import fetch_cases

        fetched = fetch_cases([pid])
    except Exception:
        return False
    if fetched.cases.empty or pid not in set(fetched.cases["PID"].astype(str)):
        return False
    for attr, frame in (
        ("cases", fetched.cases),
        ("timeseries", fetched.timeseries),
        ("flags", fetched.flags),
        ("episodes", fetched.episodes),
        ("events", fetched.events),
    ):
        current = getattr(session, attr)
        if current is None or current.empty:
            setattr(session, attr, frame.copy())
        elif not frame.empty:
            setattr(session, attr, pd.concat([current, frame], ignore_index=True))
    return True


def _known_elsewhere(session: SessionState, pid: str) -> bool:
    """PID is in the full-EMR scan or has ventilator rows in the EMR."""
    corpus = session.corpus
    if corpus is not None and session.has_corpus_scan() and pid in set(corpus["PID"].astype(str)):
        return True
    try:
        from src.services.case_fetch import vent_capable_pids

        return pid in vent_capable_pids()
    except Exception:
        return False


def tool_select_case(
    session: SessionState,
    *,
    pid: str = "",
    **_kwargs: Any,
) -> str:
    pid = str(pid or "").strip()
    if not pid:
        return "ERROR: select_case requires pid."
    fetched_note = ""
    sample = session.cases
    if (
        sample is None or sample.empty or pid not in set(sample["PID"].astype(str))
    ) and _known_elsewhere(session, pid):
        if _fetch_into_session(session, pid):
            fetched_note = (
                "NOTE: this surgery was not in the loaded sample; it was analyzed on "
                "demand from the EMR.\n"
            )
    cases = session.cases
    if cases is None or cases.empty:
        return "NO_DATA: corpus empty."
    # Allow partial PID match (prefix) for convenience
    pids = cases["PID"].astype(str)
    exact = pids[pids == pid]
    if exact.empty:
        partial = pids[pids.str.startswith(pid)]
        if len(partial) == 1:
            pid = str(partial.iloc[0])
        elif len(partial) > 1:
            return (
                "AMBIGUOUS_PID: multiple matches for prefix "
                f"{pid!r}: {', '.join(partial.head(8).tolist())}"
            )
        else:
            # try contains
            contains = pids[pids.str.contains(pid, regex=False)]
            if len(contains) == 1:
                pid = str(contains.iloc[0])
            elif len(contains) > 1:
                return (
                    "AMBIGUOUS_PID: "
                    f"{', '.join(contains.head(8).tolist())}"
                )
            else:
                return (
                    f"UNKNOWN_PID: {pid!r} is not in the loaded sample, the full-EMR "
                    "scan, or the EMR's ventilated surgeries."
                )
    session.active_pid = pid
    if pid not in session.focus_pids:
        session.focus_pids = [pid] + [p for p in session.focus_pids if p != pid]
    # Short briefing
    try:
        brief = build_case_context(
            pid,
            cases=session.cases,
            timeseries=session.timeseries
            if session.timeseries is not None
            else pd.DataFrame(),
            flags=session.flags,
            episodes=session.episodes,
            events=session.events,
            include_emr_extras=True,
            max_meds=25,
            max_flag_lines=20,
        )
    except Exception as e:
        return f"SELECTED: {pid} but briefing failed: {e}"
    return _clip(f"SELECTED_CASE: {pid}\n{fetched_note}\n{brief}")


def tool_get_case_briefing(
    session: SessionState,
    *,
    pid: str = "",
    **_kwargs: Any,
) -> str:
    pid = str(pid or session.active_pid or "").strip()
    if not pid:
        return "ERROR: no pid and no active case."
    return tool_select_case(session, pid=pid)


def tool_list_case_flags(
    session: SessionState,
    *,
    pid: str = "",
    **_kwargs: Any,
) -> str:
    pid = str(pid or session.active_pid or "").strip()
    if not pid:
        return "ERROR: list_case_flags needs pid or an active case."
    flags = session.flags
    if flags is None or flags.empty:
        return f"NO_FLAGS_TABLE for {pid}."
    fsub = flags[flags["PID"].astype(str) == pid]
    if fsub.empty:
        return f"NO_FLAGS for PID={pid}."
    counts = (
        fsub.groupby(["rule_id", "severity"], observed=False)
        .size()
        .reset_index(name="n")
        .sort_values("n", ascending=False)
    )
    lines = [f"FLAGS for {pid}: {len(fsub)} minute-flag rows"]
    for _, r in counts.head(30).iterrows():
        lines.append(f"- {r['rule_id']} [{r['severity']}]: {int(r['n'])}")
    by_phase = phase_counts_line(fsub)
    if by_phase:
        lines.append(by_phase)
    return _clip("\n".join(lines))


def tool_filter_by_agent(
    session: SessionState,
    *,
    agent: str = "",
    limit: int = 15,
    scope: str = "auto",
    **_kwargs: Any,
) -> str:
    used = _resolve_scope(session, scope)
    cases = _scope_frame(session, used)
    if cases is None or cases.empty:
        return "NO_DATA"
    agent = (agent or "").strip().lower()
    if not agent:
        return "ERROR: agent name required (e.g. sevoflurane)."
    if "primary_agent_name" not in cases.columns:
        return "ERROR: primary_agent_name missing."
    hit = cases[
        cases["primary_agent_name"].fillna("").astype(str).str.lower().str.contains(
            agent, regex=False
        )
    ]
    if hit.empty:
        session.focus_pids = []
        return (
            f"{_scope_line(session, used, scope)}\n"
            f"SEARCH_EMPTY: no cases with agent containing {agent!r}."
        )
    if "anomaly_score" in hit.columns:
        hit = hit.sort_values("anomaly_score", ascending=False)
    limit = max(1, min(int(limit or 15), 30))
    session.focus_pids = [str(p) for p in hit["PID"].head(limit).tolist()]
    lines = [
        _scope_line(session, used, scope),
        f"AGENT_FILTER: {agent!r} · matched={len(hit)} · showing={len(session.focus_pids)}",
    ]
    for _, row in hit.head(limit).iterrows():
        lines.append(_case_line(row))
    return _clip("\n".join(lines))


def tool_rule_reference(session: SessionState, **_kwargs: Any) -> str:
    try:
        from src.config import load_thresholds

        cfg = load_thresholds("default", validate=True)
    except Exception as e:
        return f"ERROR loading thresholds: {e}"
    lines = [
        "Rule reference (research screens, not clinical alarms):",
        f"Preset: {cfg.get('_preset', 'default')}",
    ]
    for rid, spec in (cfg.get("rules") or {}).items():
        lines.append(f"- {rid}: {spec.get('description', '')}")
        details = {k: v for k, v in spec.items() if k != "description"}
        if details:
            lines.append(f"  params: {details}")
    lines.append(
        "Phases: flags and episodes carry the anesthesia phase of the minute (pre_induction, "
        "induction, maintenance, emergence, post_emergence); skip_phases lists where a rule is "
        "not raised."
    )
    lines.append(f"Scoring: {cfg.get('scoring', {})}")
    return _clip("\n".join(lines))


def tool_wave_status(
    session: SessionState,
    *,
    pid: str = "",
    **_kwargs: Any,
) -> str:
    pid = str(pid or session.active_pid or "").strip()
    root = wave_dir()
    if root is None:
        return "Wave root not configured."
    if not pid:
        return f"Wave root: {root} (no PID requested)."
    path = waveform_case_dir(pid, root)
    if path is None:
        return f"No waveform folder for PID={pid} under {root}."
    try:
        n = sum(1 for p in path.rglob("*") if p.is_file())
    except OSError:
        n = -1
    return f"Waveform folder for {pid}: {path} ({n} files). Waveforms are not decoded here."


def tool_find_similar_cases(
    session: SessionState,
    *,
    procedure: str = "",
    agent: str = "",
    flags: str = "",
    limit: int = 10,
    scope: str = "auto",
    **_kwargs: Any,
) -> str:
    """Compose a search query from vignette fields."""
    parts = [p for p in (procedure, agent, flags) if (p or "").strip()]
    if not parts:
        return "ERROR: find_similar_cases needs procedure (and optionally agent/flags)."
    query = " ".join(str(p).strip() for p in parts)
    return tool_search_cases(session, query=query, limit=limit or 10, scope=scope)


def _management_snapshot(session: SessionState, pid: str) -> str:
    """Compact management view from case row + flags (no invented advice)."""
    cases = session.cases
    if cases is None or cases.empty:
        return f"NO_DATA for {pid}"
    sub = cases[cases["PID"].astype(str) == str(pid)]
    if sub.empty:
        return f"UNKNOWN_PID: {pid}"
    row = sub.iloc[0]
    lines = [f"MANAGEMENT_SNAPSHOT PID={pid}"]
    for label, key in [
        ("Procedure", "Procedure_short"),
        ("Age/Sex", None),
        ("Primary agent", "primary_agent_name"),
        ("Duration_min", "case_duration_min"),
        ("Anomaly_score", "anomaly_score"),
        ("Top_rules", "top_rules"),
    ]:
        if key is None:
            age = row.get("Age", "n/a")
            sex = row.get("Gender", "n/a")
            lines.append(f"- Demographics: age={age} sex={sex}")
            continue
        if key in row.index and pd.notna(row.get(key)):
            lines.append(f"- {label}: {row.get(key)}")
    # Vent medians if present on case summary
    for label, key in [
        ("median_TV", "median_TV"),
        ("median_PIP", "median_PIP"),
        ("median_PEEP", "median_PEEP"),
        ("median_ETCO2", "median_ETCO2"),
        ("median_HR", "median_HR"),
        ("median_SPO2", "median_SPO2"),
    ]:
        if key in row.index and pd.notna(row.get(key)):
            lines.append(f"- {label}: {row.get(key)}")
    if session.flags is not None and not session.flags.empty:
        fsub = session.flags[session.flags["PID"].astype(str) == str(pid)]
        if not fsub.empty and "rule_id" in fsub.columns:
            counts = fsub.groupby("rule_id").size().sort_values(ascending=False).head(8)
            lines.append("- Flag counts: " + ", ".join(f"{k}={int(v)}" for k, v in counts.items()))
    lines.append(
        "- NOTE: Snapshot is documented extract only — not a treatment prescription."
    )
    return "\n".join(lines)


def tool_summarize_management(
    session: SessionState,
    *,
    pid: str = "",
    **_kwargs: Any,
) -> str:
    pid = str(pid or session.active_pid or "").strip()
    if not pid:
        return "ERROR: summarize_management needs pid or an active case."
    # Prefer full briefing + snapshot header
    brief = tool_select_case(session, pid=pid)
    snap = _management_snapshot(session, session.active_pid or pid)
    return _clip(snap + "\n\n" + brief)


def tool_compare_cases(
    session: SessionState,
    *,
    pids: str = "",
    **_kwargs: Any,
) -> str:
    raw = str(pids or "").replace(";", ",")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    # Also accept list-like kwargs
    if not parts and isinstance(_kwargs.get("pid_list"), list):
        parts = [str(x) for x in _kwargs["pid_list"]]
    if len(parts) < 2:
        return "ERROR: compare_cases needs at least 2 PIDs (comma-separated)."
    parts = parts[:5]
    blocks = []
    for p in parts:
        # Resolve via select without losing all focus
        out = tool_select_case(session, pid=p)
        if out.startswith("UNKNOWN") or out.startswith("AMBIGUOUS"):
            blocks.append(out)
        else:
            blocks.append(_management_snapshot(session, session.active_pid or p))
    session.focus_pids = [
        str(session.active_pid or parts[-1])
    ] + [p for p in parts if p != (session.active_pid or "")]
    return _clip("COMPARE_CASES\n\n" + "\n\n---\n\n".join(blocks))


def tool_verify_selection(session: SessionState, **_kwargs: Any) -> str:
    """Read-back of active + focus (scheduler list_blocks analogue)."""
    lines = [
        "VERIFY_SELECTION (authoritative re-read)",
        f"Active PID: {session.active_pid or '(none)'}",
        f"Focus count: {len(session.focus_pids)}",
    ]
    cases = session.cases
    if cases is None or cases.empty:
        lines.append("Corpus empty.")
        return "\n".join(lines)
    show = list(session.focus_pids[:15]) if session.focus_pids else []
    if session.active_pid and session.active_pid not in show:
        show = [session.active_pid] + show
    if not show:
        # show top 5 scores as context
        if "anomaly_score" in cases.columns:
            show = [
                str(p)
                for p in cases.sort_values("anomaly_score", ascending=False)["PID"]
                .head(5)
                .tolist()
            ]
            lines.append("(no focus — showing top-5 scores for orientation)")
    corpus = session.corpus if session.has_corpus_scan() else None
    for pid in show:
        sub = cases[cases["PID"].astype(str) == str(pid)]
        if sub.empty and corpus is not None:
            sub = corpus[corpus["PID"].astype(str) == str(pid)]
        if sub.empty:
            lines.append(f"- {pid}: NOT IN LOADED SAMPLE OR FULL-EMR SCAN")
        else:
            lines.append(_case_line(sub.iloc[0]))
    lines.append(
        "If your planned answer cites any other PID, call select_case on it first."
    )
    return _clip("\n".join(lines))


def tool_rule_case_counts(
    session: SessionState,
    *,
    scope: str = "auto",
    severity: str = "",
    **_kwargs: Any,
) -> str:
    """How many surgeries each rule fired in, with episode and minute totals."""
    used = _resolve_scope(session, scope)
    episodes = session.corpus_episodes if used == SCOPE_FULL else session.episodes
    frame = _scope_frame(session, used)
    n_cases = 0 if frame is None else int(frame["PID"].nunique())
    head = _scope_line(session, used, scope)
    if episodes is None or episodes.empty or "rule_id" not in episodes.columns:
        return f"{head}\nRULE_COUNTS: no flag episodes in this scope."
    ep = episodes
    sev = (severity or "").strip().lower()
    if sev:
        ep = ep[ep["severity"].astype(str).str.lower() == sev]
    minutes = "n_minutes" if "n_minutes" in ep.columns else "duration_min"
    table = (
        ep.groupby("rule_id")
        .agg(cases=("PID", "nunique"), episodes=("PID", "size"), minutes=(minutes, "sum"))
        .sort_values(["cases", "episodes"], ascending=False)
    )
    lines = [
        head,
        f"RULE_COUNTS: {len(table)} rule(s) over {n_cases} surgeries"
        + (f" · severity={sev}" if sev else ""),
    ]
    for rule_id, row in table.iterrows():
        pct = 100.0 * row["cases"] / n_cases if n_cases else 0.0
        lines.append(
            f"- {rule_id}: {int(row['cases'])} cases ({pct:.1f}%) · "
            f"{int(row['episodes'])} episodes · {int(row['minutes'])} flagged minutes"
        )
    return _clip("\n".join(lines))


TOOL_SPECS: dict[str, dict[str, Any]] = {
    "corpus_overview": {
        "fn": tool_corpus_overview,
        "args": [],
        "help": "Counts, agents, score range, active/focus state.",
    },
    "search_cases": {
        "fn": tool_search_cases,
        "args": ["query", "limit", "scope"],
        "help": "Keyword search (procedure, PID, agent, flag rules). Sets focus list.",
    },
    "find_similar_cases": {
        "fn": tool_find_similar_cases,
        "args": ["procedure", "agent", "flags", "limit", "scope"],
        "help": "Vignette-style similar-case search.",
    },
    "top_anomaly_cases": {
        "fn": tool_top_anomaly_cases,
        "args": ["limit", "scope", "per_hour"],
        "help": "Highest anomaly_score (or per-hour) cases; sets focus.",
    },
    "select_case": {
        "fn": tool_select_case,
        "args": ["pid"],
        "help": "Select active surgery and return grounded briefing.",
    },
    "get_case_briefing": {
        "fn": tool_get_case_briefing,
        "args": ["pid"],
        "help": "Full grounded briefing for pid (or active case).",
    },
    "summarize_management": {
        "fn": tool_summarize_management,
        "args": ["pid"],
        "help": "Documented management snapshot + full briefing.",
    },
    "compare_cases": {
        "fn": tool_compare_cases,
        "args": ["pids"],
        "help": "Side-by-side management snapshots.",
    },
    "list_case_flags": {
        "fn": tool_list_case_flags,
        "args": ["pid"],
        "help": "Rule flag counts for a case.",
    },
    "filter_by_agent": {
        "fn": tool_filter_by_agent,
        "args": ["agent", "limit", "scope"],
        "help": "Cases whose primary agent name contains agent.",
    },
    "rule_case_counts": {
        "fn": tool_rule_case_counts,
        "args": ["scope", "severity"],
        "help": "Surgeries, episodes and minutes per flag rule.",
    },
    "rule_reference": {
        "fn": tool_rule_reference,
        "args": [],
        "help": "Threshold rule definitions from config.",
    },
    "wave_status": {
        "fn": tool_wave_status,
        "args": ["pid"],
        "help": "Whether waveform folder exists for a PID.",
    },
    "verify_selection": {
        "fn": tool_verify_selection,
        "args": [],
        "help": "Re-read active/focus PIDs before final answer.",
    },
}


def execute_tool(name: str, session: SessionState, arguments: dict[str, Any]) -> str:
    name = (name or "").strip()
    spec = TOOL_SPECS.get(name)
    if not spec:
        known = ", ".join(sorted(TOOL_SPECS))
        return f"ERROR: unknown tool {name!r}. Known: {known}"
    fn: Callable[..., str] = spec["fn"]
    try:
        result = fn(session, **(arguments or {}))
    except Exception as e:
        result = f"ERROR executing {name}: {e}"
    session.last_tool_trace.append(f"{name}({arguments}) → {result[:200]}")
    return result


def tool_catalog_text() -> str:
    lines = ["Available tools (app-executed; results are authoritative):"]
    for name, spec in TOOL_SPECS.items():
        args = ", ".join(spec["args"]) if spec["args"] else "(none)"
        lines.append(f"- {name} [{args}]: {spec['help']}")
    return "\n".join(lines)
