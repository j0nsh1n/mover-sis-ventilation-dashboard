"""Read a research question's shape without a model call.

``classify`` sorts a question into a plain question, a scenario (a patient or
case description to match against indexed surgeries) or a comparison of cases.
Only rules decide, so the same words always give the same intent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from src.llm.grounding import PID_TOKEN
from src.services.retrieval import CaseFilters

IntentKind = Literal["question", "scenario", "compare"]
Sex = Literal["F", "M"]

# A single stated age matches cases within this many years either side (CaseFilters' default).
AGE_TOLERANCE = 10.0
_MAX_AGE = 120

_UNIT = r"(?:years?|yrs?)"
_OLD = r"(?:[\s-]*olds?\b|\s+of\s+age\b)"
_SHORT = r"(?:y/o|yo|y)\b"
_NUM = r"(\d{1,3})"
_TO = r"\s*(?:-|–|to)\s*"

_RANGE_UNIT = re.compile(rf"(?<![\w.]){_NUM}{_TO}{_NUM}\s*-?\s*(?:{_UNIT}{_OLD}|{_SHORT})", re.I)
_RANGE_AGED = re.compile(
    rf"\bage[ds]?\s*(?:of|:)?\s*(?:between\s+)?{_NUM}(?:{_TO}|\s+and\s+){_NUM}", re.I
)
_RANGE_BETWEEN = re.compile(rf"\bbetween\s+{_NUM}\s+and\s+{_NUM}\s*(?:{_UNIT}|y\b)", re.I)
_SEXWORD = r"(?:woman|women|female|males?|man|men)"
_RANGE_SEXWORD = re.compile(
    rf"(?<![\w.]){_NUM}{_TO}{_NUM}\s+{_SEXWORD}\b|\b{_SEXWORD}[,\s]+{_NUM}{_TO}{_NUM}(?![\w.])", re.I
)
_SINGLE_SEXWORD = re.compile(
    rf"(?<![\w.]){_NUM}\s+{_SEXWORD}\b|\b{_SEXWORD}[,\s]+{_NUM}(?=\s*(?:[,;.]|$))", re.I
)
_DECADE = re.compile(r"\b(?:in\s+)?(?:his|her|their)\s+(\d)0s\b", re.I)
_AGED = re.compile(rf"\b(?:aged?|ages)\s*(?:of|:)?\s*{_NUM}\b(?!\s*(?:-|–|to|and)\s*\d)", re.I)
_SINGLE = re.compile(rf"(?<![\w.]){_NUM}\s*-?\s*(?:{_UNIT}{_OLD}|{_SHORT})", re.I)
# A bare F or M is a sex only beside a number ("F 55", "55F"); upper case only.
_SEX_BEFORE = re.compile(r"(?<![A-Za-z0-9])([FM])\s*-?\s*(\d{1,3})\b")
_SEX_AFTER = re.compile(r"(?<![A-Za-z0-9.])(\d{1,3})\s*-?\s*([FM])(?![A-Za-z0-9])")

_FEMALE = re.compile(r"\b(?:woman|women|female|females|girl|girls)\b", re.I)
_MALE = re.compile(r"\b(?:man|men|male|males|boy|boys)\b", re.I)

_PROCEDURE_SUFFIX = re.compile(
    r"\b[a-z]{3,}(?:ectom(?:y|ies)|otom(?:y|ies)|ostom(?:y|ies)|plast(?:y|ies)|scop(?:y|ies)"
    r"|pex(?:y|ies)|rrhaph(?:y|ies)|desis)\b",
    re.I,
)
_PROCEDURE_WORDS = frozenset(
    "laparoscopic robotic thoracoscopic endoscopic arthroscopic hernia bypass cesarean "
    "caesarean transplant fusion whipple".split()
)
_AGENTS = {
    "sevoflurane": "sevoflurane", "sevo": "sevoflurane",
    "desflurane": "desflurane", "isoflurane": "isoflurane",
}
_AGENT = re.compile(r"\b(" + "|".join(_AGENTS) + r")\b", re.I)
_COMPARE = re.compile(
    r"\b(?:compar(?:e|es|ed|ing|ison|isons)|versus|vs\.?|differences?\s+between|contrast)(?!\w)",
    re.I,
)
_SIGNAL = (r"(?:PIP|PEEP|E?T?CO2|ETCO₂|heart\s+rate|HR|SpO2|SPO2|tidal\s+volume|TV|RR|"
           r"FiO2|FIO2|n?MAP|MAC|ETN2O|agent)")
# "PIP versus ETCO2" contrasts signals within the data, not two surgeries.
_SIGNAL_VS = re.compile(
    rf"\b{_SIGNAL}\s+(?:versus|vs\.?|compared\s+(?:to|with))\s+{_SIGNAL}\b", re.I,
)


@dataclass(frozen=True)
class Intent:
    kind: IntentKind = "question"
    age: float | None = None
    age_range: tuple[float, float] | None = None
    sex: Sex | None = None
    procedures: tuple[str, ...] = ()
    agent: str | None = None
    pids: tuple[str, ...] = ()

    def case_filters(self) -> CaseFilters:
        low, high = self.age_range if self.age_range is not None else (None, None)
        return CaseFilters(
            age=self.age, age_tolerance=AGE_TOLERANCE, age_min=low, age_max=high,
            gender=self.sex, procedures=self.procedures, agent=self.agent,
        )

    def parts(self) -> list[tuple[str, str]]:
        """The scenario's fields as (filter name, wording), in display order."""
        out: list[tuple[str, str]] = []
        if self.sex is not None:
            out.append(("sex", "female" if self.sex == "F" else "male"))
        if self.age_range is not None:
            out.append(("age", f"{self.age_range[0]:g}-{self.age_range[1]:g}"))
        elif self.age is not None:
            out.append(("age", f"{self.age:g} ±{AGE_TOLERANCE:g}"))
        if self.procedures:
            out.append(("procedure", " ".join(self.procedures)))
        if self.agent is not None:
            out.append(("agent", self.agent))
        return out

    def scenario_text(self) -> str:
        return "Scenario: " + ", ".join(text for _, text in self.parts())


def _age(value: str) -> float | None:
    age = float(value)
    return age if 0 <= age <= _MAX_AGE else None


def _ages(text: str) -> tuple[float | None, tuple[float, float] | None]:
    """A single age or an inclusive age range, whichever the question states."""
    for pattern in (_RANGE_UNIT, _RANGE_AGED, _RANGE_BETWEEN, _RANGE_SEXWORD):
        m = pattern.search(text)
        if m:
            groups = [g for g in m.groups() if g is not None]
            low, high = _age(groups[0]), _age(groups[1])
            if low is not None and high is not None:
                return None, (min(low, high), max(low, high))
    m = _DECADE.search(text)
    if m:
        return None, (float(m.group(1)) * 10, float(m.group(1)) * 10 + 9)
    for pattern in (_AGED, _SINGLE, _SINGLE_SEXWORD):
        m = pattern.search(text)
        if m and (age := _age(next(g for g in m.groups() if g is not None))) is not None:
            return age, None
    return None, None


def _sex_and_age(text: str) -> tuple[Sex | None, float | None]:
    """Sex written as a bare letter beside an age ("F 55", "55M")."""
    for pattern, sex_at, age_at in ((_SEX_BEFORE, 1, 2), (_SEX_AFTER, 2, 1)):
        m = pattern.search(text)
        if m and (age := _age(m.group(age_at))) is not None:
            return ("F" if m.group(sex_at) == "F" else "M"), age
    return None, None


def _pids(text: str) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for m in PID_TOKEN.finditer(text):
        seen.setdefault(m.group(1).lower(), m.group(1))
    return tuple(seen.values())


def _procedures(text: str) -> tuple[str, ...]:
    words: list[str] = []
    for m in re.finditer(r"[A-Za-z]+", text):
        word = m.group(0).lower()
        if word in _PROCEDURE_WORDS or _PROCEDURE_SUFFIX.fullmatch(word):
            word = re.sub(r"ies$", "y", word)
            if word not in words:
                words.append(word)
    return tuple(words)


def classify(question: str) -> Intent:
    text = question or ""
    pids = _pids(text)
    if len(pids) >= 2:
        return Intent(kind="compare", pids=pids)
    if pids:
        # One named case: any "versus" is within that case.
        return Intent(pids=pids)
    if _COMPARE.search(text) and not _SIGNAL_VS.search(text):
        return Intent(kind="compare", pids=pids)

    age, age_range = _ages(text)
    letter_sex, letter_age = _sex_and_age(text)
    if age is None and age_range is None:
        age = letter_age
    female, male = bool(_FEMALE.search(text)), bool(_MALE.search(text))
    sex: Sex | None = letter_sex
    if sex is None and female != male:
        sex = "F" if female else "M"
    agent = _AGENT.search(text)
    intent = Intent(
        kind="scenario", age=age, age_range=age_range, sex=sex,
        procedures=_procedures(text), agent=_AGENTS[agent.group(1).lower()] if agent else None,
    )
    stated = len(intent.parts())
    if age is not None or age_range is not None or stated >= 2:
        return intent
    return Intent()
