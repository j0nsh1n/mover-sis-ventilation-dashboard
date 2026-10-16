"""Tool-using agent loop (Daily Scheduler aipanel pattern).

Native Ollama tool_calls → execute → re-prompt (≤ MAX_TOOL_ROUNDS).
Falls back to extract_tool_calls when models print JSON/TOOL blocks.
Ends with verify_answer + research footer.
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple

import pandas as pd

from src.llm.advice_check import AdviceCheck, NotRun, check_advice
from src.llm.grounding import (
    PID_TOKEN,
    REMOVED_NOTE,
    find_ungrounded_numbers,
    remove_ungrounded_numbers,
)
from src.llm.ollama_client import ChatResult, OllamaClient, OllamaError
from src.llm.prompts import (
    MODE_CHAT,
    REWRITE_ASK,
    REWRITE_INSTRUCTION,
    REWRITE_RESEARCH,
    VERIFICATION_FOOTER,
    build_system_prompt,
    session_header_text,
    user_message,
)
from src.llm.schemas import (
    AI_TOOLS,
    MAX_TOOL_ROUNDS,
    extract_tool_calls,
    looks_like_tool_text,
    strip_think,
)
from src.llm.tools import SessionState, execute_tool, tool_select_case

_PID_TOKEN = PID_TOKEN
# Each shape targets advice *aimed at a reader*. Past-tense description of what the
# record shows ("PEEP was increased at t=40", "TV was reduced") must stay legal, so the
# verbs are anchored to imperative/gerund/modal framing rather than matched bare.
# tests/fixtures/gate_drafts.yaml holds the sentences each shape must catch or pass.
_ADVICE_VERB = (
    r"consider|recommend|advise|ensure|optimi[sz]e|adjust|reduce|lower|increase|raise|"
    r"titrate|administer|initiate|address|correct|avoid|monitor|check|maintain|"
    r"troubleshoot|manage|assess|evaluate|verify|confirm|escalate|intervene|"
    r"extubate|switch|repeat|set|keep|use|try|give|start|stop|apply|wean|recruit|"
    r"reverse|review|reassess|perform|obtain|prevent|continue|document|kept|"
    r"do not|don't|make sure|be sure"
)
_DIRECTIVE_SHAPES = (
    # explicit orders
    r"\b(?:you should|must administer|prescribe|increase the dose|extubate now|"
    r"start dopamine|give bolus|treat the patient|order a)\b",
    # obligation: "should be reduced", "ought to have extubated", "has to be kept"
    rf"\b(?:should|must|needs? to|ought to|has to|have to)\b"
    rf"(?:\s+\w+){{0,3}}\s+\b(?:be\s+)?(?:{_ADVICE_VERB})\w*",
    # sentence-initial, bulleted or quoted imperative: "Consider reducing…",
    # "- Adjust the…". A noun reading ("Increase in PIP", "Monitor alarms were")
    # is description.
    rf"(?:^|[.!?]\s+|[\n\r]|:|[\"“'‘(])\s*[-*•]?\s*(?:please\s+)?\b(?:{_ADVICE_VERB})\b"
    r"(?!\s+(?:in|of)\b)(?!(?:\s+[\w-]+){1,2}\s+(?:was|were|occurred|resumed)\b)"
    r"\s+\w+",
    # urgency framing: "requires immediate intervention", "warrants prompt…"
    r"\b(?:requires?|warrants?|necessitates?)\s+(?:\w+\s+){0,2}"
    r"(?:intervention|attention|action|treatment|management|troubleshooting|monitoring)",
    # goal framing: "focus on avoiding…"
    rf"\b(?:focus(?:ing)? on|aim(?:ing)? to)\s+(?:\w+\s+){{0,2}}\b(?:{_ADVICE_VERB})\w*",
    # the footer's own "not clinical recommendations" is a disclaimer
    r"\brecommend(?:s|ed|ing)?\b|\b(?<!not clinical )recommendations?\b|"
    r"\badvis(?:e|es|ed|able)\b|\b(?:is|are)\s+(?:the\s+)?preferred\b",
    r"\b(?:i|we)(?:'d|’d|\s+would)?\s+(?:\w+\s+)?(?:suggest|propose)\b|"
    r"\bmy\s+(?:suggestion|advice)\b",
    # hedged advice: "would likely be better", "might help to"
    r"\b(?:would|could|might|may)\s+(?:\w+\s+){0,2}(?:better|preferable|reasonable|"
    r"prudent|sensible|wise|safer|beneficial|appropriate|help\s+to)\b",
    # "It is best to keep…", "It is essential to confirm…"; "important to note" is not
    r"\b(?:better|best|preferable|safer|wiser?|prudent|reasonable|sensible|important|"
    r"essential|crucial|necessary)\s+to\s+(?!note\b|mention\b)\w+",
    r"\bworth\s+(?:a\s+(?:trial|try)|(?!noting\b|mentioning\b)\w+ing)\b",
    # "may be warranted"; "is indicated by" infers from the data
    r"\b(?:is|are|be)\s+(?:\w+\s+)?(?:warranted|indicated)\b(?!\s+by\b)",
    r"\b(?:clinicians?|providers?|team|anesthesiologists?)\s+"
    r"(?:could|should|ought|must|need)\b",
    r"\b(?:may|might|could)\s+(?:\w+\s+){0,2}(?:consider|benefit from|want to|wish to)\b",
    # hindsight critique; "might have been caused" stays a reading of the data
    rf"\b(?:could|should)\s+(?:\w+\s+)?have\s+(?:been\s+)?(?:{_ADVICE_VERB})\w*|"
    r"\b(?:might|would)\s+(?:\w+\s+)?have\s+been\s+(?:avoided|prevented)\b",
    r"\b(?:option|approach|alternative)\s+(?:would|could|might)\s+be\s+to\b",
    r"\b(?:going forward|in future cases|next time|next step|ideally)\b",
)
# One alternation over ASCII-lowercased text runs about a third faster than the shapes
# one by one with IGNORECASE, and the offsets still index the original casing.
_DIRECTIVE = re.compile("|".join(f"(?:{shape})" for shape in _DIRECTIVE_SHAPES))
_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def find_directive_phrases(text: str, *, limit: int = 6) -> list[str]:
    """
    Snippets where the answer advises a reader instead of describing the record.

    Returns the offending text so the rewrite prompt can quote it back — models correct
    far more reliably when shown their own words than when given an abstract rule.
    """
    found: list[str] = []
    seen: set[str] = set()
    text = text or ""
    for m in _DIRECTIVE.finditer(text.translate(_ASCII_LOWER)):
        snippet = " ".join(text[m.start() : m.end()].split()).strip(" -*•:")
        key = snippet.lower()
        if snippet and key not in seen:
            seen.add(key)
            found.append(snippet)
            if len(found) >= limit:
                return found
    return found


def _advice_phrases(text: str, advice: Sequence[str]) -> list[str]:
    """Pattern hits, then each model-flagged sentence that no pattern hit already quotes."""
    found = find_directive_phrases(text)
    quoted = [f.lower() for f in found]
    found += [s for s in advice if s in text and not any(q in s.lower() for q in quoted)]
    return found


@dataclass(frozen=True)
class CaseFacts:
    """One case the answer gate checks a draft against."""

    pid: str
    age: int | None
    # Ask compares a described patient with the selected case, so its age must appear.
    # A Research answer may leave ages out; it must only not misstate them.
    age_required: bool
    # None: not preloaded, so the dose check reads this case's rows from the EMR.
    medications: pd.DataFrame | None


def _record_age(session: SessionState, pid: str) -> int | None:
    cases = session.cases
    if cases is None or cases.empty:
        return None
    rows = cases[cases["PID"].astype(str) == pid]
    if rows.empty:
        return None
    try:
        return int(float(rows.iloc[0].get("Age")))
    except (TypeError, ValueError):
        return None


def _preloaded_medications(session: SessionState, pid: str) -> pd.DataFrame | None:
    med = session.medications
    if med is None:
        return None
    if med.empty or "PID" not in med.columns:
        return pd.DataFrame()
    return med[med["PID"].astype(str) == pid]


def gate_cases(session: SessionState) -> list[CaseFacts]:
    """The cases a draft is checked against: the cited cases, else the Ask selection."""
    if session.cited_pids:
        pids, required = [str(p) for p in session.cited_pids], False
    elif session.active_pid:
        pids, required = [str(session.active_pid)], True
    else:
        return []
    return [
        CaseFacts(
            pid=pid,
            age=_record_age(session, pid),
            age_required=required,
            medications=_preloaded_medications(session, pid),
        )
        for pid in pids
    ]


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_CLAUSE_BREAK = re.compile(r"[;,]|\b(?:and|but|while|whereas)\b", re.IGNORECASE)


# Words that may stand beside PIDs in a shared subject: "Both A and B were…".
_SUBJECT_WORDS = frozenset({"both", "all", "the", "case", "cases", "pid", "pids", "patient", "patients"})


class _Attribution(NamedTuple):
    """The answer split by the cited cases each part describes."""

    # Every clause about the case, including a clause naming it alongside others.
    by_case: dict[str, str]
    # Text naming no cited case; its facts must match at least one of them.
    unattributed: str


def _clause_owners(clauses: list[str], pids: list[str]) -> list[frozenset[str]]:
    """
    The cited PIDs each clause names. A clause that is only a subject ("Both A", "B,")
    lends its PIDs to the next clause, so "A and B were 55" names both cases.
    """
    owners: list[frozenset[str]] = []
    subject: frozenset[str] = frozenset()
    for clause in clauses:
        rest = clause.lower()
        named = frozenset(p for p in pids if p.lower() in rest)
        for pid in named:
            rest = rest.replace(pid.lower(), " ")
        words = re.findall(r"[a-z]+|\d", rest)
        if named and _SUBJECT_WORDS.issuperset(words):
            subject |= named
            owners.append(subject)
        elif words:
            owners.append(subject | named if named else frozenset())
            subject = frozenset()
        else:
            owners.append(frozenset())
    return owners


def _attribute(text: str, pids: list[str]) -> _Attribution:
    """
    Each case's share of the answer, so a fact is checked against its own case.

    A single case owns the whole answer. With several, a clause belongs to the cited
    PIDs it names, else to the nearest clause naming one in its sentence, else to the
    last ones named before it. Text before any PID is named is unattributed.
    """
    if len(pids) == 1:
        return _Attribution({pids[0]: text}, "")
    owned: dict[str, list[str]] = {p: [] for p in pids}
    loose: list[str] = []
    last: frozenset[str] = frozenset()
    for sentence in _SENTENCE_BREAK.split(text or ""):
        clauses = _CLAUSE_BREAK.split(sentence)
        named = _clause_owners(clauses, pids)
        for i, clause in enumerate(clauses):
            owners = (
                named[i]
                or next((n for n in reversed(named[:i]) if n), frozenset())
                or next((n for n in named[i + 1 :] if n), frozenset())
                or last
            )
            for pid in owners:
                owned[pid].append(clause)
            if not owners:
                loose.append(clause)
        last = next((n for n in reversed(named) if n), last)
    return _Attribution({p: "\n".join(parts) for p, parts in owned.items()}, "\n".join(loose))


def _claimed_ages(text: str) -> set[int]:
    # Asserted ages, not the digits anywhere in the text. Mere presence is useless:
    # "60" turns up in flag times, counts, even the note this check appends — while
    # the answer still calls a 60-year-old case "55-year-old".
    return {
        int(g)
        for m in re.finditer(
            r"(?<!\d)(\d{1,3})\s*(?:-|\s)?\s*(?:years?[- ]?old|years?\s+of\s+age|y/?o\b|yr[s]?\b|[FM]\b)"
            r"|(?:\bage[d]?\s*[:=]?\s*)(\d{1,3})(?!\d)",
            text or "",
            re.IGNORECASE,
        )
        for g in [m.group(1) or m.group(2)]
        if g
    }


# Ages that describe a group, not a person: a statistic, a bound, or a range.
# "mean age 56" or "aged 80 or older" must not read as a claimed age of 56 or 80.
_POPULATION_AGE = re.compile(
    r"\b(?:mean|median|average|modal)\s+ages?\s*(?:of\s+)?[:=]?\s*\d{1,3}"
    r"|\bage[sd]?\s*[:=]?\s*\d{1,3}\s*(?:or|and)\s+(?:older|younger|over|under|above|below)"
    r"|\bages?\s*[:=]?\s*\d{1,3}\s*(?:to|through|and|-|\u2013)\s*\d{1,3}"
    r"|\bages?\s*(?:>=|<=|>|<|\u2265|\u2264)\s*\d{1,3}",
    re.IGNORECASE,
)


def find_unstated_case_facts(text: str, session: SessionState) -> list[str]:
    """
    Checks the answer reports each checked case's own age.

    The observed failure was an answer claiming "Age: 55 (aligned)" for a case that is
    60 — restating the user's description as though it were the record. Requiring the
    real value to appear catches that without guessing at sentence meaning. A case
    without a recorded age is never checked, so no age is ever supplied for it. An age
    in text naming no cited case must be the age of one of them.
    """
    problems: list[str] = []
    cases = gate_cases(session)
    if not cases:
        return []
    parts = _attribute(text, [c.pid for c in cases])
    for case in cases:
        if case.age is None:
            continue
        claimed = _claimed_ages(parts.by_case[case.pid])
        if not claimed:
            if case.age_required:
                problems.append(
                    f"the matched case {case.pid} is age {case.age}, "
                    f"but the answer never states it"
                )
        elif case.age not in claimed:
            wrong = ", ".join(str(c) for c in sorted(claimed))
            problems.append(
                f"the answer describes {case.pid} as age {wrong}, but {case.pid} is "
                f"age {case.age} — state the real age and note the difference from the "
                f"described patient"
            )
    ages = {c.age for c in cases}
    # A case without a recorded age could be any age, so loose ages are then unchecked.
    if None not in ages:
        known = sorted(a for a in ages if a is not None)
        loose = _POPULATION_AGE.sub(" ", parts.unattributed)
        stray = sorted(_claimed_ages(loose) - set(known))
        if stray:
            problems.append(
                f"the answer states age {', '.join(map(str, stray))} without naming a "
                f"case, but the cited cases are age {', '.join(map(str, known))} — "
                f"attach each age to the case it belongs to"
            )
    return problems


def _unknown_pids(text: str, known: set[str]) -> list[str]:
    """PID-like tokens in the answer that match none of ``known``."""
    if not known:
        return []
    known_l = {p.lower() for p in known}
    claimed = {m.group(1) for m in _PID_TOKEN.finditer(text)}
    return sorted((c for c in claimed if c.lower() not in known_l), key=str.lower)

ProgressCb = Callable[[str], None]


@dataclass
class AgentResult:
    answer: str
    tool_trace: list[str] = field(default_factory=list)
    active_pid: str | None = None
    focus_pids: list[str] = field(default_factory=list)
    rounds: int = 0
    mode: str = MODE_CHAT


def _normalize_native_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI/Ollama tool_calls → [{name, args, id}]."""
    out: list[dict[str, Any]] = []
    for i, call in enumerate(calls or []):
        fn = call.get("function") or call
        name = fn.get("name") or call.get("name") or "?"
        args = fn.get("arguments") if isinstance(fn, dict) else {}
        if args is None:
            args = call.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, dict):
            args = {}
        out.append(
            {
                "name": name,
                "args": args,
                "id": call.get("id") or f"call_{i}",
                "raw": call,
            }
        )
    return out


def _looks_like_patient_description(q: str) -> bool:
    """True for free-text vignettes (not pure meta questions)."""
    t = (q or "").strip()
    if len(t) < 12:
        return False
    if re.search(
        r"\b(what is|what does|how do I|help me use|list tools|who are you)\b",
        t,
        re.I,
    ) and not re.search(
        r"\b(patient|surgery|sevo|desflurane|isoflurane|chole|hyster|"
        r"knee|pip|etco2|anesthesia|ventilat)\b",
        t,
        re.I,
    ):
        return False
    return True


def _heuristic_prefetch(
    question: str,
    session: SessionState,
    *,
    mode: str = "analyze",
) -> list[str]:
    """
    App-side retrieval so the user only describes the patient.

    In analyze/compare modes (default), always search + open top case(s) +
    management snapshot before the model writes.
    """
    traces: list[str] = []
    q = question or ""
    if session.cases is None or session.cases.empty:
        return traces

    from src.llm.tools import (
        tool_compare_cases,
        tool_find_similar_cases,
        tool_search_cases,
        tool_summarize_management,
        tool_top_anomaly_cases,
        tool_verify_selection,
    )

    pids = {p.lower(): p for p in session.cases["PID"].astype(str)}
    for m in _PID_TOKEN.finditer(q):
        pid = pids.get(m.group(1).lower())
        if pid:
            out = tool_summarize_management(session, pid=pid)
            traces.append(f"[auto] summarize_management pid={pid}\n{out[:1200]}")
            traces.append(f"[auto] verify_selection\n{tool_verify_selection(session)[:600]}")
            return traces

    mode = (mode or "analyze").lower()
    force = mode in {"analyze", "compare"} or _looks_like_patient_description(q)

    if not force and not re.search(
        r"\b(find|search|show|list|similar|patient|case)\b", q, re.I
    ):
        return traces

    if re.search(r"\b(top|highest)\b.*\b(anomal|score)\b", q, re.I):
        out = tool_top_anomaly_cases(session, limit=10)
        traces.append(f"[auto] top_anomaly_cases\n{out[:900]}")
    else:
        out = tool_find_similar_cases(session, procedure=q, limit=12)
        traces.append(f"[auto] find_similar_cases\n{out[:900]}")
        if "SEARCH_EMPTY" in out or "NO_DATA" in out:
            out2 = tool_search_cases(session, query=q, limit=12)
            traces.append(f"[auto] search_cases fallback\n{out2[:900]}")

    # Open top match + management (and a second case for compare)
    if session.focus_pids:
        top = session.focus_pids[0]
        mg = tool_summarize_management(session, pid=top)
        traces.append(f"[auto] summarize_management pid={top}\n{mg[:2000]}")
        if mode == "compare" and len(session.focus_pids) >= 2:
            pair = ",".join(session.focus_pids[:3])
            cmp_ = tool_compare_cases(session, pids=pair)
            traces.append(f"[auto] compare_cases\n{cmp_[:2000]}")
        traces.append(f"[auto] verify_selection\n{tool_verify_selection(session)[:800]}")
    return traces


def verify_answer(
    answer: str,
    session: SessionState,
    *,
    advice: AdviceCheck | None = None,
    sources: Sequence[str] | None = None,
    question: str = "",
) -> tuple[str, list[str]]:
    """
    Append a visible note for each problem the answer still has.

    ``advice`` is the model check of this answer; None when the caller ran none.
    ``sources`` are the extracts or abstracts the answer was written from; a number
    found in none of them (or in ``question``) is replaced. None skips that check.
    """
    notes: list[str] = []
    text = strip_think((answer or "").strip())
    if not text:
        return (
            "I could not produce a grounded answer. Try a vignette or a PID from the focus list.",
            ["empty_answer"],
        )

    if looks_like_tool_text(text) and extract_tool_calls(text):
        notes.append("leftover_tool_text")
        text = (
            "I retrieved tool data but did not finish a prose answer. "
            "Please ask again: e.g. 'summarize management for the active case'."
        )

    corpus = set()
    if session.cases is not None and not session.cases.empty:
        corpus = set(session.cases["PID"].astype(str))
    if session.corpus is not None and session.has_corpus_scan():
        corpus |= set(session.corpus["PID"].astype(str))

    body_end = len(text)
    bad = _unknown_pids(text, corpus)
    if bad:
        notes.append("unknown_pids:" + ",".join(bad[:5]))
        text += (
            "\n\n**Verification note:** PID-like tokens not in the loaded sample or full-EMR scan: "
            f"{', '.join(bad[:5])}. Prefer PIDs from tool results only."
        )

    directives = _advice_phrases(text, advice.advice if advice else ())
    if directives:
        notes.append("clinical_directive_language:" + " | ".join(directives[:3]))
        text += (
            "\n\n**Verification note:** Directive clinical language was detected "
            f"({'; '.join(repr(d) for d in directives[:3])}). "
            "This co-pilot is research-only and must not be used for live care decisions."
        )
    if isinstance(advice, NotRun):
        notes.append(f"advice_check_not_run:{advice.reason}")
        text += (
            "\n\n**Verification note:** the model advice check did not run, so only "
            "the word-pattern check was applied."
        )

    for problem in find_unstated_case_facts(text, session):
        notes.append("unstated_case_facts")
        text += f"\n\n**Verification note:** {problem}."

    dose_problems = find_medication_mismatches(text, session)
    if dose_problems:
        notes.append("dose_mismatch")
        text += (
            "\n\n**Verification note:** reported doses disagree with the medication "
            "record — " + "; ".join(dose_problems[:3]) + "."
        )
    if session.medications_unreadable:
        notes.append(f"dose_check_skipped:{session.medications_unreadable}")
        text += (
            "\n\n**Verification note:** doses were not checked — the medication record "
            "could not be read."
        )

    # Last, so the checks above still see the numbers they judge; the replacement
    # touches only the answer body, never the notes or footer.
    if sources is not None:
        body, removed = remove_ungrounded_numbers(text[:body_end], sources, question)
        text = body + text[body_end:]
        if removed:
            notes.append(f"ungrounded_numbers:{removed}")
            text += f"\n\n**Verification note:** {REMOVED_NOTE}"

    if VERIFICATION_FOOTER.strip() not in text:
        text = text.rstrip() + VERIFICATION_FOOTER
    return text, notes


_DOSE_UNITS = r"mcg|µg|ug|mg|g|mL|ml|L|units?|IU"


def _read_medications(pid: str) -> pd.DataFrame:
    try:
        from src.llm.case_context import load_case_medications

        return load_case_medications(pid)
    except Exception:
        return pd.DataFrame()


def find_medication_mismatches(
    text: str, session: SessionState, *, limit: int = 5
) -> list[str]:
    """
    Doses in the answer that disagree with each checked case's medication record.

    Observed: an answer reported "Phenylephrine 50 mcg" for a case documenting 100 mcg,
    and "Vecuronium 0.15 mg/kg" where the record holds absolute mg. Altered doses are
    worse than absent ones — they read as extracted fact.

    Best-effort: silent when the EMR is unreachable or the case has no medications.
    """
    if not (text or "").strip():
        return []
    cases = gate_cases(session)
    if not cases:
        return []
    parts = _attribute(text, [c.pid for c in cases])
    records = {
        c.pid: _read_medications(c.pid) if c.medications is None else c.medications
        for c in cases
    }
    problems: list[str] = []
    for pid, med in records.items():
        problems += _dose_problems(parts.by_case[pid], med)
    if parts.unattributed.strip():
        problems += _dose_problems(parts.unattributed, pd.concat(records.values()))
    return problems[:limit]


def _dose_problems(text: str, med: pd.DataFrame) -> list[str]:
    if med.empty or "Drug_name" not in med.columns:
        return []

    documented: dict[str, set[tuple[str, str]]] = {}
    for _, row in med.iterrows():
        name = str(row.get("Drug_name") or "").strip().lower()
        if not name:
            continue
        dose = str(row.get("Dose") or "").strip()
        unit = str(row.get("Drug_units") or "").strip().lower()
        documented.setdefault(name, set()).add((dose, unit))

    def _same(a: str, b: str) -> bool:
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return a.strip() == b.strip()

    problems: list[str] = []
    for drug, pairs in documented.items():
        head = drug.split()[0]
        if len(head) < 4:
            continue
        for m in re.finditer(
            rf"{re.escape(head)}\b[^.\n]{{0,60}}?([\d.]+)\s*({_DOSE_UNITS})\s*(/\s*kg)?",
            text,
            re.IGNORECASE,
        ):
            claimed, unit, per_kg = m.group(1), m.group(2).lower(), m.group(3)
            if per_kg:
                problems.append(
                    f'"{head} {claimed} {unit}/kg" is weight-normalised, but the record '
                    f"holds absolute doses ({', '.join(sorted(d + ' ' + u for d, u in pairs))})"
                )
            elif not any(_same(claimed, d) and unit == u.lower() for d, u in pairs):
                documented_str = ", ".join(sorted(f"{d} {u}" for d, u in pairs))
                problems.append(
                    f'"{head} {claimed} {unit}" does not match the record '
                    f"({documented_str})"
                )
    return problems


def answer_problems(
    text: str,
    session: SessionState,
    *,
    advice: Sequence[str] = (),
    sources: Sequence[str] | None = None,
    question: str = "",
) -> list[str]:
    """
    Reasons a draft must be rewritten, phrased for the model to act on.

    ``advice`` holds the sentences the model check labeled as advice. ``sources`` are
    the extracts or abstracts the draft was written from; None skips the number check.
    """
    problems = [
        f'- prescriptive phrasing: "{d}" — describe what the record shows instead'
        for d in _advice_phrases(text, advice)
    ]
    problems += [f"- {p}" for p in find_unstated_case_facts(text, session)]
    problems += [
        f"- dose does not match the extract: {p}"
        for p in find_medication_mismatches(text, session)
    ]
    # Ask keeps an appended note for unknown PIDs; a Research answer may name only
    # the cases it was given.
    cited = ", ".join(session.cited_pids)
    problems += [
        f"- {pid} is not one of the cited cases ({cited}) — name only the cited cases"
        for pid in _unknown_pids(text, set(session.cited_pids))
    ]
    if sources is not None:
        problems += [
            f"- the number {number} is not in the source extracts or the question — "
            f"use only numbers that appear there"
            for number in find_ungrounded_numbers(text, sources, question)[:8]
        ]
    return problems


def run_agent(
    *,
    model: str,
    question: str,
    session: SessionState,
    client: OllamaClient | None = None,
    mode: str = MODE_CHAT,
    max_rounds: int = MAX_TOOL_ROUNDS,
    temperature: float | None = None,
    on_status: ProgressCb | None = None,
    skip_prefetch: bool = False,
    extra_prefetched: str = "",
    allow_tools: bool = True,
    sources: Sequence[str] | None = None,
) -> AgentResult:
    client = client or OllamaClient()
    mode = (mode or MODE_CHAT).lower()
    # Analyze slightly warmer (scheduler suggest mode); chat/compare stay low for tools
    if temperature is None:
        temperature = 0.25 if mode == "analyze" else 0.12

    system = build_system_prompt(mode=mode, model=model)
    if not allow_tools:
        system += "\nTools are unavailable for this answer. Use only the supplied source extracts."
    n_cases = 0 if session.cases is None else len(session.cases)

    auto_traces: list[str] = []
    if extra_prefetched.strip():
        auto_traces.append(extra_prefetched.strip())
    if not skip_prefetch:
        auto_traces.extend(_heuristic_prefetch(question, session, mode=mode))
    for t in auto_traces:
        session.last_tool_trace.append(t)

    active_brief = ""
    if session.active_pid:
        try:
            active_brief = tool_select_case(session, pid=session.active_pid)
        except Exception:
            active_brief = ""

    header = session_header_text(
        n_cases=n_cases,
        active_pid=session.active_pid,
        focus_pids=session.focus_pids,
        mode=mode,
    )
    prefetched = "\n\n".join(auto_traces) if auto_traces else ""

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": user_message(
                question,
                session_header=header,
                active_briefing=active_brief if session.active_pid else "",
                mode=mode,
                prefetched=prefetched,
            ),
        },
    ]

    trace: list[str] = list(auto_traces)
    final_text = ""
    rounds_done = 0

    for round_i in range(max_rounds):
        rounds_done = round_i + 1
        if on_status:
            on_status(f"Model round {round_i + 1}/{max_rounds}…")
        try:
            result: ChatResult = client.chat_turn(
                model,
                messages,
                temperature=temperature,
                stream=False,
                tools=AI_TOOLS if allow_tools else None,
            )
        except OllamaError:
            raise
        except Exception as e:
            raise OllamaError(str(e)) from e

        content = strip_think(result.content or "")
        native = _normalize_native_calls(result.tool_calls) if allow_tools else []
        recovered = (
            extract_tool_calls(content)
            if allow_tools and not native and looks_like_tool_text(content)
            else []
        )
        calls = native or [
            {"name": e["name"], "args": e["args"], "id": f"rec_{i}", "raw": e}
            for i, e in enumerate(recovered)
        ]

        if calls:
            # Assistant message with tool_calls for Ollama multi-turn
            asst: dict[str, Any] = {"role": "assistant", "content": content or ""}
            if native:
                asst["tool_calls"] = result.tool_calls
            messages.append(asst)

            for i, call in enumerate(calls):
                name = call["name"]
                args = call["args"]
                if on_status:
                    on_status(f"Tool: {name}…")
                out = execute_tool(name, session, args)
                trace.append(f"{name}({args})")
                tcid = call.get("id") or f"call_{i}"
                # Ollama accepts role=tool with content; include name for compatibility
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tcid,
                        "name": name,
                        "content": out,
                    }
                )
            # Nudge verification after several tool rounds
            if round_i >= 2 and "verify_selection" not in "".join(trace[-6:]):
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "If you have enough cases, call verify_selection once, "
                            "then give the FINAL user-facing answer (no tool JSON)."
                        ),
                    }
                )
            continue

        # Final prose
        final_text = content
        break
    else:
        if on_status:
            on_status("Finalizing…")
        messages.append(
            {
                "role": "user",
                "content": (
                    "Stop calling tools. Using only tool results already returned, "
                    "write the FINAL research answer for the user."
                ),
            }
        )
        try:
            result = client.chat_turn(
                model, messages, temperature=temperature, stream=False, tools=None
            )
            final_text = strip_think(result.content or "")
        except Exception as e:
            final_text = f"Stopped after tool rounds; finalization failed: {e}"

    if not (final_text or "").strip():
        final_text = (
            "Tools ran but no prose answer was produced. "
            "Ask: 'Using the focus list, summarize management for the top case.'"
        )

    # Safety rewrite: a draft that advises the reader or misstates a checked case is
    # rejected and sent back once.
    # Annotating prescriptive text with a disclaimer still ships the prescription.
    advice = check_advice(final_text, client, model)
    problems = answer_problems(
        final_text, session, advice=advice.advice, sources=sources, question=question
    )
    if problems:
        trace.append("reject:" + ";".join(p.strip("- ") for p in problems)[:300])
        if on_status:
            on_status("Rewriting the draft to pass the answer check…")
        messages.append({"role": "assistant", "content": final_text})
        messages.append(
            {
                "role": "user",
                "content": REWRITE_INSTRUCTION.format(
                    problems="\n".join(problems),
                    **(REWRITE_RESEARCH if session.cited_pids else REWRITE_ASK),
                ),
            }
        )
        try:
            retry = client.chat_turn(
                model, messages, temperature=0.0, stream=False, tools=None
            )
            rewritten = strip_think(retry.content or "").strip()
            if rewritten:
                rewritten_advice = check_advice(rewritten, client, model)
                left = answer_problems(
                    rewritten,
                    session,
                    advice=rewritten_advice.advice,
                    sources=sources,
                    question=question,
                )
                introduced_advice = (
                    _advice_phrases(rewritten, rewritten_advice.advice)
                    and not _advice_phrases(final_text, advice.advice)
                )
                if not introduced_advice and len(left) < len(problems):
                    final_text, advice = rewritten, rewritten_advice
                trace.append("rewrite:still_flagged" if left else "rewrite:accepted")
        except Exception as e:  # keep the original answer if the retry fails
            trace.append(f"rewrite:failed:{e}")

    verified, notes = verify_answer(
        final_text, session, advice=advice, sources=sources, question=question
    )
    if notes:
        trace.append("verify:" + ";".join(notes))

    return AgentResult(
        answer=verified,
        tool_trace=trace,
        active_pid=session.active_pid,
        focus_pids=list(session.focus_pids),
        rounds=rounds_done,
        mode=mode,
    )


# Back-compat exports used by tests
def parse_tool_calls(text: str):
    from types import SimpleNamespace

    return [
        SimpleNamespace(name=e["name"], arguments=e["args"])
        for e in extract_tool_calls(text)
    ]


def looks_like_tool_turn(text: str) -> bool:
    return bool(extract_tool_calls(text)) or looks_like_tool_text(text)
