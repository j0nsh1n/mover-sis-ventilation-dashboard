"""Tool-using agent loop (Daily Scheduler aipanel pattern).

Native Ollama tool_calls → execute → re-prompt (≤ MAX_TOOL_ROUNDS).
Falls back to extract_tool_calls when models print JSON/TOOL blocks.
Ends with verify_answer + research footer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from src.llm.ollama_client import ChatResult, OllamaClient, OllamaError
from src.llm.prompts import (
    MODE_CHAT,
    REWRITE_INSTRUCTION,
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

_PID_TOKEN = re.compile(r"\b([0-9a-f]{8,32})\b", re.IGNORECASE)
_CLINICAL_DIRECTIVE = re.compile(
    r"\b(you should|must administer|prescribe|increase the dose|"
    r"extubate now|start dopamine|give bolus|clinical recommendation|"
    r"treat the patient|order a)\b",
    re.IGNORECASE,
)

# Recommendation framing the narrow list above misses entirely. Real rejected output
# included "Consider reducing TV", "adjust ventilator settings", "require immediate
# intervention" and "ensuring adequate neuromuscular blockade" — none matched.
#
# Each pattern targets advice *aimed at a reader*. Past-tense description of what the
# record shows ("PEEP was increased at t=40", "TV was reduced") must stay legal, so the
# verbs are anchored to imperative/gerund/modal framing rather than matched bare.
_ADVICE_VERB = (
    r"consider|recommend|advise|ensure|optimi[sz]e|adjust|reduce|lower|increase|raise|"
    r"titrate|administer|initiate|address|correct|avoid|monitor|check|maintain|"
    r"troubleshoot|manage|assess|evaluate|verify|confirm|escalate|intervene"
)
_DIRECTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # modal advice: "should be reduced", "must be optimized", "needs to be adjusted"
    re.compile(
        rf"\b(should|must|need(?:s)? to|ought to|is indicated|are indicated)\b"
        rf"(?:\s+\w+){{0,3}}\s+\b(?:be\s+)?(?:{_ADVICE_VERB})\w*",
        re.IGNORECASE,
    ),
    # sentence-initial or bulleted imperative: "Consider reducing…", "- Adjust the…"
    re.compile(
        rf"(?:^\s*[-*•]?\s*|[.!?]\s+[-*•]?\s*|[\n\r]\s*[-*•]?\s*|:\s*)"
        rf"\b(?:{_ADVICE_VERB})\b\s+\w+",
        re.IGNORECASE,
    ),
    # urgency framing: "requires immediate intervention", "warrants prompt…"
    re.compile(
        r"\b(require(?:s|d)?|warrant(?:s|ed)?|necessitat(?:e|es|ed))\s+"
        r"(?:\w+\s+){0,2}(immediate|prompt|urgent|close|further)?\s*"
        r"(intervention|attention|action|treatment|management|troubleshooting|monitoring)",
        re.IGNORECASE,
    ),
    # goal framing: "focus on avoiding…", "with a view to optimising…"
    re.compile(
        rf"\b(focus(?:ing)? on|aim(?:ing)? to|in order to|so as to)\s+"
        rf"(?:\w+\s+){{0,2}}\b(?:{_ADVICE_VERB})\w*",
        re.IGNORECASE,
    ),
)


def find_directive_phrases(text: str, *, limit: int = 6) -> list[str]:
    """
    Snippets where the answer advises a reader instead of describing the record.

    Returns the offending text so the rewrite prompt can quote it back — models correct
    far more reliably when shown their own words than when given an abstract rule.
    """
    found: list[str] = []
    seen: set[str] = set()
    for pattern in (_CLINICAL_DIRECTIVE, *_DIRECTIVE_PATTERNS):
        for m in pattern.finditer(text or ""):
            snippet = " ".join(m.group(0).split()).strip(" -*•:")
            key = snippet.lower()
            if snippet and key not in seen:
                seen.add(key)
                found.append(snippet)
                if len(found) >= limit:
                    return found
    return found


def find_unstated_case_facts(text: str, session: SessionState) -> list[str]:
    """
    Checks the answer actually reports the selected case's own demographics.

    The observed failure was an answer claiming "Age: 55 (aligned)" for a case that is
    60 — restating the user's description as though it were the record. Requiring the
    real value to appear catches that without guessing at sentence meaning.
    """
    problems: list[str] = []
    pid = session.active_pid
    if not pid or session.cases is None or session.cases.empty:
        return problems
    rows = session.cases[session.cases["PID"].astype(str) == str(pid)]
    if rows.empty:
        return problems
    row = rows.iloc[0]
    age = row.get("Age")
    try:
        age_int = int(float(age))
    except (TypeError, ValueError):
        return problems

    # Look for asserted ages, not for the digits anywhere in the text. Mere presence is
    # useless: "60" turns up in flag times, counts, even the note this check appends —
    # while the answer still calls a 60-year-old case "55-year-old".
    claimed = {
        int(g)
        for m in re.finditer(
            r"(?<!\d)(\d{1,3})\s*(?:-|\s)?\s*(?:year[- ]?old|y/?o\b|yr[s]?\b|[FM]\b)"
            r"|(?:\bage[d]?\s*[:=]?\s*)(\d{1,3})(?!\d)",
            text or "",
            re.IGNORECASE,
        )
        for g in [m.group(1) or m.group(2)]
        if g
    }
    if not claimed:
        problems.append(
            f"the matched case {pid} is age {age_int}, but the answer never states it"
        )
    elif age_int not in claimed:
        wrong = ", ".join(str(c) for c in sorted(claimed))
        problems.append(
            f"the answer describes the matched case as age {wrong}, but {pid} is "
            f"age {age_int} — state the real age and note the difference from the "
            f"described patient"
        )
    return problems

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

    pids = set(session.cases["PID"].astype(str))
    for m in _PID_TOKEN.finditer(q):
        cand = m.group(1)
        matches = [p for p in pids if p == cand or p.startswith(cand) or cand in p]
        if len(matches) == 1:
            out = tool_summarize_management(session, pid=matches[0])
            traces.append(f"[auto] summarize_management pid={matches[0]}\n{out[:1200]}")
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


def verify_answer(answer: str, session: SessionState) -> tuple[str, list[str]]:
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

    claimed = {m.group(1).lower() for m in _PID_TOKEN.finditer(text)}
    bad = []
    for c in claimed:
        if not corpus:
            break
        if not any(
            p.lower() == c or p.lower().startswith(c) or c in p.lower() for p in corpus
        ):
            bad.append(c)
    if bad:
        notes.append("unknown_pids:" + ",".join(bad[:5]))
        text += (
            "\n\n**Verification note:** PID-like tokens not in the loaded corpus: "
            f"{', '.join(bad[:5])}. Prefer PIDs from tool results only."
        )

    directives = find_directive_phrases(text)
    if directives:
        notes.append("clinical_directive_language:" + " | ".join(directives[:3]))
        text += (
            "\n\n**Verification note:** Directive clinical language was detected "
            f"({'; '.join(repr(d) for d in directives[:3])}). "
            "This co-pilot is research-only and must not be used for live care decisions."
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

    if VERIFICATION_FOOTER.strip() not in text:
        text = text.rstrip() + VERIFICATION_FOOTER
    return text, notes


_DOSE_UNITS = r"mcg|µg|ug|mg|g|mL|ml|L|units?|IU"


def find_medication_mismatches(
    text: str, session: SessionState, *, limit: int = 5
) -> list[str]:
    """
    Doses in the answer that disagree with the medication record.

    Observed: an answer reported "Phenylephrine 50 mcg" for a case documenting 100 mcg,
    and "Vecuronium 0.15 mg/kg" where the record holds absolute mg. Altered doses are
    worse than absent ones — they read as extracted fact.

    Best-effort: silent when the EMR is unreachable or the case has no medications.
    """
    pid = session.active_pid
    if not pid or not (text or "").strip():
        return []
    try:
        from src.llm.case_context import load_case_medications

        med = load_case_medications(str(pid))
    except Exception:
        return []
    if med is None or med.empty or "Drug_name" not in med.columns:
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
            if len(problems) >= limit:
                return problems
    return problems


def answer_problems(text: str, session: SessionState) -> list[str]:
    """Reasons a draft must be rewritten, phrased for the model to act on."""
    problems = [
        f'- prescriptive phrasing: "{d}" — describe what the record shows instead'
        for d in find_directive_phrases(text)
    ]
    problems += [f"- {p}" for p in find_unstated_case_facts(text, session)]
    problems += [
        f"- dose does not match the extract: {p}"
        for p in find_medication_mismatches(text, session)
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

    # Safety rewrite: a draft that advises the reader is rejected and sent back once.
    # Annotating prescriptive text with a disclaimer still ships the prescription.
    problems = answer_problems(final_text, session)
    if problems:
        trace.append("reject:" + ";".join(p.strip("- ") for p in problems)[:300])
        if on_status:
            on_status("Rewriting to remove prescriptive language…")
        messages.append({"role": "assistant", "content": final_text})
        messages.append(
            {
                "role": "user",
                "content": REWRITE_INSTRUCTION.format(problems="\n".join(problems)),
            }
        )
        try:
            retry = client.chat_turn(
                model, messages, temperature=0.0, stream=False, tools=None
            )
            rewritten = strip_think(retry.content or "").strip()
            if rewritten and not answer_problems(rewritten, session):
                final_text = rewritten
                trace.append("rewrite:accepted")
            elif rewritten:
                # Keep whichever draft has fewer violations
                if len(answer_problems(rewritten, session)) < len(problems):
                    final_text = rewritten
                trace.append("rewrite:still_flagged")
        except Exception as e:  # keep the original answer if the retry fails
            trace.append(f"rewrite:failed:{e}")

    verified, notes = verify_answer(final_text, session)
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
