"""The five I-PASS sections of a handoff, and the one text the grader receives for them.

The grader reads one text and splits it into sentences at "." and ";" only: it
collapses every line break first. It also looks 40 characters back from a phrase
for a negation word, across sentence ends. So a section boundary is a full stop
plus a rule of dashes longer than that look-back, not a line break.
"""

from __future__ import annotations

from collections.abc import Mapping

# (key kept in the saved attempt, heading the learner and the grader see), in handoff order.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("illness_severity", "Illness severity"),
    ("patient_summary", "Patient summary"),
    ("action_list", "Action list"),
    ("situation_awareness", "Situation awareness and contingency plans"),
    ("synthesis", "Synthesis by receiver"),
)

# Longer than the grader's 40-character look-back for a negation word, so "no concerns" at the end of
# one section can never turn the first finding of the next section into a denial.
_SECTION_RULE = "-" * 44


def join_sections(sections: Mapping[str, str]) -> str:
    """The sections, in order, each under its heading; blank sections are left out.

    Each section ends in a full stop, which is a sentence end for the grader, so a minute, a value or a
    drug written in one section is never read as a detail of a finding named in another.
    """
    parts: list[str] = []
    for key, heading in SECTIONS:
        body = sections.get(key, "").strip()
        if not body:
            continue
        if not body.endswith("."):
            body += "."
        parts.append(f"{heading}: {body}")
    return f"\n{_SECTION_RULE}\n".join(parts)
