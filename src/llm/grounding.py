"""Check that every number in a research answer appears in the text it was given.

The answer gate already checks ages, PIDs and doses. A fabricated measurement
("PIP reached 45 cmH2O" when the extract's maximum is 22.09) passes all of those,
so each number is compared with the source extracts (or article abstracts) and the
question. Units are not compared, only values, so cmH2O, cm H₂O, mmHg, % and /min
spellings all match.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# MOVER PIDs are 16 hex characters; synthetic ones are SYN + 10 (src/pipeline/synthetic.py).
# Anything looser reads counts and timestamps as PIDs.
PID_TOKEN = re.compile(r"\b(SYN[0-9a-f]{10}|[0-9a-f]{16})\b", re.IGNORECASE)

REMOVED_MARK = "[number not in sources]"
REMOVED_NOTE = "Removed a number not found in the sources."
NOTE_PREFIX = "**Verification note:**"

# A digit run glued to a letter ("ETCO2", "SpO2", "N2O", "FiO2") is a name, not a value.
_UNITS = r"cm\s*H[₂2]O|mmHg|mL(?:/kg)?|bpm|minutes?|mins?|seconds?|secs?|hours?|hrs?|vol%|%|/min"
_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])(?:\.\d+|(?:\d{1,3}(?:,\d{3})+(?!\d)|"
    r"\d+(?:[\u2009\u202f]\d+)+|\d+(?: +\d+)+(?:\.\d+)?(?=\s*(?:" + _UNITS + r"))|\d+)(?:\.\d+)?)"
)
_SMALL_WORDS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS_WORDS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_WORD_VALUES = {word: i for i, word in enumerate(_SMALL_WORDS)}
_WORD_VALUES.update({word: i * 10 for i, word in enumerate(_TENS_WORDS, 2)})
_WORD_VALUES["hundred"] = 100
_WORD_NUMBER = re.compile(
    r"\b(?:" + "|".join(_TENS_WORDS) + r")(?:[- ](?:" + "|".join(_SMALL_WORDS[1:10])
    + r"))?\b|\b(?:" + "|".join(_WORD_VALUES) + r")\b", re.IGNORECASE,
)
_UNIT_AFTER = re.compile(r"\s*[\])]*\s*(" + _UNITS + r")(?![A-Za-z])", re.IGNORECASE)
_MINUTE_BEFORE = re.compile(r"\bminutes?\s*$", re.IGNORECASE)
_RANGE_TAIL = re.compile(r"\s*(?:to|[–—-])\s*[-−]?(?:\d+(?:\.\d+)?)\s*[\])]*\s*(" + _UNITS + r")", re.IGNORECASE)
_TIME = re.compile(r"(?:(\d{4}-\d{2}-\d{2})[ T])?(\d{2}):(\d{2}):(\d{2})")
_DIFFERENCE = re.compile(r"\b(?:duration|difference|differed|span|lasted|(?:rose|fell|increased|decreased) by)\b", re.IGNORECASE)
_SIGNED_RANGE_LEAD = re.compile(r"(?:\d\s*[–—-]|\bto)\s*$")
_ARTICLE_HEADER = re.compile(r"(?m)^\[[A-Za-z]\d+\][^\n.]*\b(?:19|20)\d{2}\.")
_MINUS = "-−"
_SIGN_LEAD = set(" \t\n([{=:~,;<>≈±/")
_MAX_INTEGER_DIGITS = 15
_HEDGE_TOLERANCE = Decimal("0.05")

_HEDGE = re.compile(
    r"(?:\babout|\baround|\bapproximately|\bapprox\.?|\broughly|\bnearly|\balmost"
    r"|\bcirca|~|≈)\s*(?:[-−]?\d[\d.,]*\s*(?:to|-|–|and|or)\s*)?$",
    re.IGNORECASE,
)
_CITATION_BRACKET = re.compile(
    r"\[\s*([A-Za-z]{0,2})(\d{1,3})((?:\s*[,;\-–]\s*[A-Za-z]{0,2}\d{1,3})*)\s*\]"
)
_SOURCE_LABEL = re.compile(
    r"\b(?:sources?|extracts?|abstracts?|articles?|cases?|refs?\.?|references?)"
    r"\s*#?\s*(\d{1,2}(?:\s*(?:,|and|&|-|–|to)\s*\d{1,2})*)",
    re.IGNORECASE,
)
_COUNT_NOUN = re.compile(
    r"(?<![A-Za-z0-9_.])(\d{1,3})\s+(?:cases?|sources?|extracts?|records?|abstracts?"
    r"|articles?|patients?|papers?|studies)\b",
    re.IGNORECASE,
)
_LIST_PREFIX = re.compile(r"(?m)^[ \t>]*(?:[-*•]\s+)?\**(\d{1,3})[.)]\**(?=\s)")
_ORDINAL = re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.IGNORECASE)
# A year is a citation detail only in author-year form: "et al. 2019", "(Smith, 2019)".
_CITED_YEAR = re.compile(
    r"\bet al\.?,?\s*\(?(?:19|20)\d{2}[a-z]?\)?"
    r"|\(\s*[A-Z][^()\d]*?,?\s*(?:19|20)\d{2}[a-z]?(?:\s*[;,]\s*[^()\d]*?(?:19|20)\d{2}[a-z]?)*\s*\)"
    r"|\b[A-Z][a-z]+\s*\((?:19|20)\d{2}[a-z]?\)"
)
_NUMBER_IN_LABEL = re.compile(r"\d+")


@dataclass(frozen=True)
class _Found:
    start: int
    end: int
    text: str
    value: Decimal
    decimals: int
    negative: bool
    hedged: bool
    parts: tuple["_Found", ...] = ()


def _sign_at(text: str, start: int) -> bool:
    if start == 0 or text[start - 1] not in _MINUS:
        return False
    before = text[start - 2] if start >= 2 else ""
    if _SIGNED_RANGE_LEAD.search(text[max(0, start - 30):start - 1]):
        return True
    if text[start - 1] == "−":
        return not before.isdigit()
    if before == "" or before in _SIGN_LEAD:
        if before.isspace():
            return not text[: start - 1].rstrip()[-1:].isdigit()
        return True
    return False


def _decimal(token: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[, \u2009\u202f]", "", token))
    except InvalidOperation:
        return None


def _scan(text: str) -> list[_Found]:
    found: list[_Found] = []
    for m in _NUMBER.finditer(text):
        token = m.group(0)
        whole = token.split(".")[0].replace(",", "")
        if len(whole) > _MAX_INTEGER_DIGITS:
            continue
        value = _decimal(token)
        if value is None:
            continue
        decimals = len(token.split(".")[1]) if "." in token else 0
        negative = _sign_at(text, m.start())
        start = m.start() - 1 if negative else m.start()
        hedged = bool(_HEDGE.search(text[max(0, start - 28) : start]))
        parts: tuple[_Found, ...] = ()
        if "," in token:
            # "120,130" is a list when it is not a thousands separator
            offset = m.start()
            pieces = []
            for piece in token.split(","):
                pv = _decimal(piece.split(".")[0])
                if pv is not None:
                    pieces.append(
                        _Found(offset, offset + len(piece), piece, pv, 0, False, hedged)
                    )
                offset += len(piece) + 1
            parts = tuple(pieces)
        found.append(
            _Found(
                start,
                m.end(),
                text[start : m.end()],
                -value if negative else value,
                decimals,
                negative,
                hedged,
                parts,
            )
        )
    for m in _WORD_NUMBER.finditer(text):
        word_value = sum(_WORD_VALUES[word] for word in re.split("[- ]", m.group().lower()))
        found.append(_Found(m.start(), m.end(), m.group(), Decimal(word_value), 0, False,
                            bool(_HEDGE.search(text[max(0, m.start() - 28):m.start()]))))
    return sorted(found, key=lambda number: number.start)


def _blank(chars: list[str], start: int, end: int) -> None:
    for i in range(start, end):
        chars[i] = "\x00"


def _mask_ignored(text: str, n_sources: int) -> str:
    """Blank out the numbers that are labels, not values, keeping offsets intact."""
    chars = list(text)

    def blank(match: re.Match[str], group: int = 0) -> None:
        _blank(chars, match.start(group), match.end(group))

    for m in PID_TOKEN.finditer(text):
        blank(m)
    for m in _CITATION_BRACKET.finditer(text):
        numbers = [int(x) for x in _NUMBER_IN_LABEL.findall(m.group(2) + m.group(3))]
        letters = bool(m.group(1)) or bool(re.search(r"[A-Za-z]", m.group(3)))
        if all(1 <= n <= (9 if letters else n_sources) for n in numbers):
            blank(m)
    for m in _SOURCE_LABEL.finditer(text):
        for n in _NUMBER_IN_LABEL.finditer(m.group(1)):
            if 1 <= int(n.group(0)) <= n_sources:
                _blank(chars, m.start(1) + n.start(), m.start(1) + n.end())
    for m in _COUNT_NOUN.finditer(text):
        if int(m.group(1)) == n_sources:
            blank(m, 1)
    for m in _LIST_PREFIX.finditer(text):
        blank(m, 1)
    for m in _ORDINAL.finditer(text):
        blank(m)
    for m in _CITED_YEAR.finditer(text):
        blank(m)
    for number in _scan(text):
        if number.value == n_sources and re.match(
            r"\s+(?:cases?|sources?|extracts?|records?|abstracts?|articles?|patients?|papers?|studies)\b",
            text[number.end:], re.IGNORECASE,
        ):
            _blank(chars, number.start, number.end)
    return "".join(chars)


def _kind(text: str, number: _Found) -> str:
    match = _UNIT_AFTER.match(text, number.end) or _RANGE_TAIL.match(text, number.end)
    if match:
        unit = re.sub(r"\s", "", match.group(1).lower()).replace("₂", "2")
        if unit.startswith(("min", "sec", "hr", "hour")):
            return "min" if unit.startswith("min") else "sec" if unit.startswith("sec") else "hour"
        return unit
    if _MINUTE_BEFORE.search(text[max(0, number.start - 16):number.start]):
        return "min"
    return ""


class _SourceValues:
    """Every value in the sources and question, ready for rounded comparison."""

    def __init__(self, texts: Sequence[str], question: str) -> None:
        values: set[Decimal] = set()
        derived_values: set[Decimal] = set()
        kinds: dict[str, set[Decimal]] = {}
        for text in texts:
            headers = [(m.start(), m.end()) for m in _ARTICLE_HEADER.finditer(text)]
            for number in _scan(text):
                values.add(number.value)
                if not any(start <= number.start < end for start, end in headers):
                    derived_values.add(number.value)
                kind = _kind(text, number)
                if kind:
                    kinds.setdefault(kind, set()).add(number.value)
                for part in number.parts:
                    values.add(part.value)
            for match in _TIME.finditer(text):
                day, hour, minute, second = match.groups()
                try:
                    timestamp = datetime.fromisoformat(f"{day or '2000-01-01'} {hour}:{minute}:{second}")
                except ValueError:
                    continue
                minutes = Decimal(timestamp.toordinal() * 1440 + timestamp.hour * 60 + timestamp.minute) + Decimal(timestamp.second) / 60
                kinds.setdefault("min", set()).add(minutes)
        self.source_values = sorted(derived_values)
        self.kinds = {kind: sorted(items) for kind, items in kinds.items()}
        values.update(number.value for number in _scan(question))
        self.values = values
        self._rounded: dict[int, tuple[set[Decimal], set[Decimal]]] = {}
        self._derived: dict[tuple[Decimal, int, str], bool] = {}

    @staticmethod
    def _round(value: Decimal, quantum: Decimal) -> Decimal:
        try:
            return value.quantize(quantum, ROUND_HALF_UP)
        except InvalidOperation:
            return value

    def _at(self, decimals: int) -> tuple[set[Decimal], set[Decimal]]:
        cached = self._rounded.get(decimals)
        if cached is None:
            quantum = Decimal(1).scaleb(-decimals)
            signed = {self._round(v, quantum) for v in self.values}
            cached = (signed, {abs(v) for v in signed})
            self._rounded[decimals] = cached
        return cached

    def has(self, number: _Found) -> bool:
        signed, magnitudes = self._at(number.decimals)
        # An unsigned "3" may describe a recorded "-3" ("fell by 3").
        if number.value in signed or (not number.negative and number.value in magnitudes):
            return True
        if not number.hedged:
            return False
        tolerance = max(Decimal(1).scaleb(-number.decimals), abs(number.value) * _HEDGE_TOLERANCE)
        return any(abs(number.value - v) <= tolerance for v in self.values)

    def derived(self, number: _Found, answer: str) -> bool:
        kind = _kind(answer, number)
        percentage = kind in ("%", "vol%")
        if percentage:
            values = self.source_values
        elif kind and (kind in ("min", "sec", "hour") or _DIFFERENCE.search(answer)):
            values = self.kinds.get(kind, [])
        else:
            return False
        key = (number.value, number.decimals, kind)
        if key in self._derived:
            return self._derived[key]
        self._derived[key] = False
        if number.value < 0 or (percentage and number.value > 100):
            return False
        quantum = Decimal(1).scaleb(-number.decimals)
        for b in values:
            if percentage and b <= 0:
                continue
            target = number.value * b / 100 if percentage else b - number.value
            half = quantum * b / 200 if percentage else quantum / 2
            at = bisect_left(values, target - half)
            for a in values[at:at + 2]:
                if a > b or (percentage and a < 0):
                    continue
                result = 100 * a / b if percentage else b - a
                if self._round(result, quantum) == number.value:
                    self._derived[key] = True
                    return True
        return False


def _ungrounded(
    answer: str, sources: Sequence[str], question: str
) -> list[_Found]:
    masked = _mask_ignored(answer, len(sources))
    index = _SourceValues(sources, question)
    bad = []
    for number in _scan(masked):
        if index.has(number) or index.derived(number, answer):
            continue
        if number.parts and all(index.has(part) for part in number.parts):
            continue
        bad.append(number)
    return bad


def find_ungrounded_numbers(
    answer: str, sources: Sequence[str], question: str = ""
) -> list[str]:
    """
    The numbers in ``answer`` that appear in neither ``sources`` nor ``question``.

    A number is grounded by the same value in a source, rounded to the precision the
    answer states. An integer also matches a source value that rounds to it, and a
    hedged one ("about 20", "~20") a source value within 5%. Citation markers, list
    numbers, PIDs, signal names, author-year citations and a count equal to the number
    of sources are not values. Returned as written, once each, in answer order.
    """
    if not (answer or "").strip():
        return []
    seen: dict[str, None] = {}
    for number in _ungrounded(answer, sources, question):
        seen.setdefault(number.text, None)
    return list(seen)


def remove_ungrounded_numbers(
    answer: str, sources: Sequence[str], question: str = ""
) -> tuple[str, int]:
    """``answer`` with each ungrounded number replaced by a visible marker, and how many."""
    bad = _ungrounded(answer, sources, question)
    text = answer
    for number in sorted(bad, key=lambda n: n.start, reverse=True):
        text = text[: number.start] + REMOVED_MARK + text[number.end :]
    return text, len(bad)
