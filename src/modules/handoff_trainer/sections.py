"""The five I-PASS sections of a handoff, and the one text the grader receives for them.

The grader reads one text. A section's closing full stop and the line break after it end a sentence for
every look-back in the grader, so what one section says never changes how another is graded.
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

# Sections whose text is saved and shown but never graded, with the line the page shows for each.
NOT_GRADED: dict[str, str] = {
    "situation_awareness": "Not graded: plans and if-then statements are not checked against the timeline.",
}


def heading_of(key: str) -> str:
    return dict(SECTIONS)[key]


def has_text(sections: Mapping[str, str]) -> bool:
    """Whether any box, graded or not, has something written in it."""
    return any(text.strip() for text in sections.values())


def join_sections(sections: Mapping[str, str]) -> str:
    """The graded sections, in order, each under its heading; blank and ungraded sections are left out.

    Each section ends in a full stop and the next starts on a new line, so a minute, a value, a drug or a
    negation written in one section is never read as part of another.
    """
    parts: list[str] = []
    for key, heading in SECTIONS:
        body = sections.get(key, "").strip()
        if not body or key in NOT_GRADED:
            continue
        if not body.endswith("."):
            body += "."
        parts.append(f"{heading}: {body}")
    return "\n".join(parts)
