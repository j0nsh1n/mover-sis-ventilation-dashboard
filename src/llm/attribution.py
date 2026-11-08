"""Checks that tie what an answer states to the cited case it is written about.

The number check in ``grounding`` asks whether a value appears in any source extract. These
checks ask the narrower questions a live session showed it missed: was the value written about
the case whose extract holds it, is a near-copy of a cited case ID being passed off as one, does
an "only this case" claim leave out a cited case that shows the same flag, and does the answer
explain a cause the record never states.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence

from src.llm.grounding import PID_TOKEN, find_ungrounded_numbers
from src.rules import RULE_METADATA

_ID_LIKE = re.compile(r"\b(?:SYN[0-9a-f]{6,14}|[0-9a-f]{12,20})\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Numbered lines are often section headings ("2. What the extracts show"), so only marks carry a case.
_BULLET = re.compile(r"^\s*[-*•]\s+")
_RULE = re.compile(
    r"(?<![a-z0-9_])(" + "|".join(sorted(map(re.escape, RULE_METADATA), key=len, reverse=True)) + r")(?![a-z0-9_])",
    re.IGNORECASE,
)
_EXCLUSIVE = re.compile(
    r"\b(?:the only (?:case|one)|only one case|the case with|no other (?:cited )?cases?|none of the other"
    r"|no (?:cited )?cases?|not any other)\b",
    re.IGNORECASE,
)
_SIGNAL_FLAGS = re.compile(r"\b([a-z0-9]+)\s+flags?\b", re.IGNORECASE)
_MISSING_LINE = re.compile(r"Missing minutes:[^.\n]*\(([A-Za-z0-9_]+)\)")
_CAUSAL = re.compile(
    r"\b(?:due to|caused by|because of|as a result of|secondary to|attributable to"
    r"|(?:likely|probably|possibly) (?:due|caused|reflect\w*|indicat\w*|result\w*|occurred|from))\b",
    re.IGNORECASE,
)
DIAGNOSES = (
    "ards", "acute respiratory distress", "pulmonary edema", "pulmonary oedema", "bronchospasm",
    "pneumothorax", "sepsis", "hypovolemia", "hypovolaemia", "atelectasis", "aspiration",
    "anaphylaxis", "malignant hyperthermia", "embolism", "chest trauma", "endobronchial intubation",
    "mainstem intubation",
)
_DIAGNOSIS = re.compile(r"\b(?:" + "|".join(re.escape(d) for d in DIAGNOSES) + r")\b", re.IGNORECASE)
_NEGATION_BEFORE = re.compile(r"\b(?:not|no|cannot|can't|without|never)\b[^.;\n]{0,25}$", re.IGNORECASE)
_NEGATION_AFTER = re.compile(r"^[^.;\n]{0,12}\b(?:not|no longer|never)\s+(?:recorded|documented|stated|mentioned|reported|present)\b", re.IGNORECASE)
# "due to missing samples" or "possibly from sensor noise" explain the record, not the patient.
_RECORD_CAUSE = re.compile(
    r"^\W*(?:\w+\W+){0,3}?(?:missing|samples?|sampling|artifacts?|artefacts?|sensors?|noise|threshold|rules?|gaps?|data"
    r"|record(?:ing|s)?|monitor(?:ing)?|calibration|export|extract)\b",
    re.IGNORECASE,
)

MISATTRIBUTED_MARK = "is not in that case's extract"


def sources_by_pid(sources: Sequence[str]) -> dict[str, str]:
    """Each source extract keyed by the case ID it opens with (lower case)."""
    out: dict[str, str] = {}
    for source in sources:
        match = PID_TOKEN.search(source)
        if match:
            out.setdefault(match.group(1).lower(), source)
    return out


def near_miss_pids(text: str, cited: Sequence[str]) -> list[str]:
    """Case-ID-like tokens that are a cut-off, an extension, or a one-character change of a cited ID."""
    cited_l = [pid.lower() for pid in cited]
    found: dict[str, None] = {}
    for match in _ID_LIKE.finditer(text):
        token = match.group(0)
        low = token.lower()
        if low in cited_l:
            continue
        if any(pid.startswith(low) or low.startswith(pid) or _one_edit_apart(low, pid) for pid in cited_l):
            found.setdefault(token, None)
    return list(found)


def misattributed_details(text: str, sources: Sequence[str], question: str = "") -> list[str]:
    """Values and flag names stated for one cited case that only another case's extract holds.

    A detail belongs to the case ID named before it in the same sentence (or the sentence's
    first case ID when it comes earlier). A line naming exactly one case also owns the bullet
    lines directly under it. Details with no case ID nearby are left to the number check.
    A sentence that names several cases often compares them ("19.2 and 17.5 respectively",
    "23.4 compared with 17.5 for Y"), so a detail any of those cases holds is not charged.
    A flag name written as absent ("did not have pip_rising") is not a claim of the flag.
    """
    by_pid = sources_by_pid(sources)
    if len(by_pid) < 2:
        return []
    problems: list[str] = []
    for pid, segment, named in _owned_segments(text):
        own = by_pid.get(pid.lower())
        if own is None:
            continue
        allowed = [own] + [by_pid[other] for other in named if other in by_pid and other != pid.lower()]
        others = {other: src for other, src in by_pid.items() if other not in named and other != pid.lower()}
        if not others:
            continue
        elsewhere = set(find_ungrounded_numbers(segment, list(others.values()), question))
        for number in find_ungrounded_numbers(segment, allowed, question):
            if number not in elsewhere:
                holder = _holder(number, others, question)
                problems.append(_misattributed(pid, number, holder and _display(holder, sources)))
        for match in _RULE.finditer(segment):
            rule = match.group(1).lower()
            if any(_has_rule(src, rule) for src in allowed) or _NEGATION_BEFORE.search(segment[: match.start()]):
                continue
            holder = next((other for other, src in others.items() if _has_rule(src, rule)), None)
            problems.append(_misattributed(pid, rule, holder and _display(holder, sources)))
    return list(dict.fromkeys(problems))


def incomplete_exclusive_claims(text: str, sources: Sequence[str]) -> list[str]:
    """An "only this case" or "no other case" claim that leaves out a cited case showing the same flag."""
    by_pid = sources_by_pid(sources)
    if len(by_pid) < 2:
        return []
    named = {m.group(1).lower() for m in PID_TOKEN.finditer(text)}
    stated_missing = {abbr.upper() for source in by_pid.values() for abbr in _MISSING_LINE.findall(source)}
    problems: list[str] = []
    for sentence in _sentences(text):
        if not _EXCLUSIVE.search(sentence):
            continue
        rules = {m.group(1).lower() for m in _RULE.finditer(sentence)}
        for signal in _SIGNAL_FLAGS.findall(sentence):
            rules |= {rule for rule in RULE_METADATA if rule.startswith(signal.lower() + "_")}
        missing = {abbr for abbr in stated_missing if "missing" in sentence.lower() and abbr.lower() in sentence.lower()}
        for pid, source in by_pid.items():
            if pid in named:
                continue
            shown = [rule for rule in sorted(rules) if _has_rule(source, rule)]
            shown += [f"missing {abbr}" for abbr in sorted(missing) if abbr in {a.upper() for a in _MISSING_LINE.findall(source)}]
            if shown:
                problems.append(
                    f"{_display(pid, sources)} also shows {', '.join(shown)} in its extract — the answer presents "
                    "the list as complete; name every cited case that shows it"
                )
    return list(dict.fromkeys(problems))


def causal_claims(text: str, sources: Sequence[str]) -> list[str]:
    """Phrases that explain a cause or name a diagnosis the cited extracts do not state."""
    record = " ".join(sources).casefold()
    found: dict[str, None] = {}
    for pattern in (_CAUSAL, _DIAGNOSIS):
        for match in pattern.finditer(text):
            phrase = " ".join(match.group(0).split())
            before, after = text[: match.start()], text[match.end() :]
            if phrase.casefold() in record or _NEGATION_BEFORE.search(before) or _NEGATION_AFTER.search(after):
                continue
            if pattern is _CAUSAL and _RECORD_CAUSE.search(after):
                continue
            found.setdefault(phrase, None)
    return list(found)


def _owned_segments(text: str) -> Iterator[tuple[str, str, frozenset[str]]]:
    """(case ID, the text it owns, every case ID named in that sentence, lower case)."""
    carry: str | None = None
    for line in text.split("\n"):
        if not line.strip():
            continue
        ids = [m.group(1) for m in PID_TOKEN.finditer(line)]
        if not ids:
            if carry is not None and _BULLET.match(line):
                yield carry, line, frozenset({carry.lower()})
            else:
                carry = None
            continue
        for sentence in _SENTENCE_END.split(line):
            marks = [(m.start(), m.group(1)) for m in PID_TOKEN.finditer(sentence)]
            named = frozenset(pid.lower() for _, pid in marks)
            for index, (start, pid) in enumerate(marks):
                begin = 0 if index == 0 else start
                end = marks[index + 1][0] if index + 1 < len(marks) else len(sentence)
                yield pid, sentence[begin:end], named
        carry = ids[0] if len({pid.lower() for pid in ids}) == 1 else None


def _sentences(text: str) -> Iterator[str]:
    for line in text.split("\n"):
        yield from (part for part in _SENTENCE_END.split(line) if part.strip())


def _has_rule(source: str, rule: str) -> bool:
    return re.search(rf"(?<![a-z0-9_]){re.escape(rule)}(?![a-z0-9_])", source, re.IGNORECASE) is not None


def _holder(number: str, others: dict[str, str], question: str) -> str | None:
    return next((pid for pid, src in others.items() if not find_ungrounded_numbers(number, [src], question)), None)


def _misattributed(pid: str, value: str, holder: str | None) -> str:
    where = f" (it is in {holder}'s)" if holder else ""
    return f"{pid}: {value} {MISATTRIBUTED_MARK}{where} — state each detail only for the case it comes from"


def _display(pid: str, sources: Sequence[str]) -> str:
    for source in sources:
        match = PID_TOKEN.search(source)
        if match and match.group(1).lower() == pid:
            return match.group(1)
    return pid


def _one_edit_apart(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))
