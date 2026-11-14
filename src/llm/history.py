"""Earlier turns a follow-up question may be shown.

A turn is sent only when it was actually answered and in the same scope as the
new question. When that question goes to a cloud service and the scope is
cases, the earlier turn's data kind has to match: a fictional question sends
turns asked on fictional data, and a real question sends turns a cloud service
answered under trimming. A turn whose data kind was never recorded counts as
real. A local model still receives every same-scope turn. The text that is
sent is the answer body: verification notes and the research footer are gone,
and the body is cut at 2500 characters. ``history=()`` adds nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.llm.prompts import ARTICLE_VERIFICATION_FOOTER, VERIFICATION_FOOTER

# The same strings as ``cloud.CLOUD_BACKENDS``. Kept here so this module does not
# import the cloud client (that module reads history objects, and the two must
# not import each other).
CLOUD_BACKENDS = frozenset({"claude_api", "claude_cli", "codex_cli"})

HISTORY_LIMIT = 3
BODY_CAP = 2500
EARLIER_PREFIX = "Earlier question: "

# ``agent.WITHHELD_ANSWER`` starts with this. A withheld answer is not a source
# and is not shown to the next question. True for a local model and a cloud service.
_WITHHELD_PREFIX = "The answer is withheld"
_NOTE = "**Verification note:**"


@dataclass(frozen=True)
class PriorTurn:
    """One earlier turn, copied at the time it was answered.

    Later edits to the turn on screen (folding a check, rewriting the label)
    do not change this copy, so a follow-up is shown what the model wrote.
    """

    question: str
    answer: str
    sources: tuple[str, ...] = ()
    pids: tuple[str, ...] = ()
    backend: str = ""
    trimmed: bool = False
    scope: str = "cases"
    state: str = "answered"
    # False is real data, and also a turn whose data kind was never recorded.
    demo: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "pids", tuple(self.pids))


def scope_name(value: str) -> str:
    """``Cases`` / ``Articles`` and the lower-case names the answer path uses."""
    text = (value or "cases").strip().lower()
    if text.startswith("article"):
        return "articles"
    return "cases"


def answer_body(answer: str, *, cap: int = BODY_CAP) -> str:
    """The earlier answer as the next question may see it.

    Lines of a ``**Verification note:**`` paragraph are dropped, and so is the
    research-only footer. What remains is cut to ``cap`` characters. A body
    that merely uses the word "note" is left whole. Stripping happens before
    the cut, so a note after a short body does not use up the budget.
    """
    text = answer or ""
    for footer in (VERIFICATION_FOOTER, ARTICLE_VERIFICATION_FOOTER):
        if footer and footer in text:
            text = text.replace(footer, "")
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        if line.lstrip().startswith(_NOTE):
            skipping = True
            continue
        if skipping:
            if line.strip() == "":
                skipping = False
            continue
        kept.append(line)
    body = "\n".join(kept).strip()
    if len(body) > cap:
        body = body[:cap]
    return body


def _sendable(turn: PriorTurn, *, scope: str, real_data: bool, to_cloud: bool) -> bool:
    if turn.state != "answered":
        return False
    if scope_name(turn.scope) != scope:
        return False
    if (turn.answer or "").lstrip().startswith(_WITHHELD_PREFIX):
        return False
    # One rule for every cloud service. Articles are published text, so they are
    # exempt. A local model (to_cloud false) still receives every same-scope turn.
    # demo False counts as real: a turn that never recorded its data kind stays here.
    if to_cloud and scope == "cases":
        if real_data:
            if not (turn.trimmed and turn.backend in CLOUD_BACKENDS):
                return False
        elif not turn.demo:
            return False
    return bool(answer_body(turn.answer).strip())


def turns_to_send(
    history: Sequence[PriorTurn],
    *,
    scope: str = "cases",
    real_data: bool = False,
    to_cloud: bool = False,
    limit: int = HISTORY_LIMIT,
) -> tuple[PriorTurn, ...]:
    """The prior turns the new question is shown, oldest first.

    Non-answers (failed, cancelled, still running, no model, withheld) and
    turns from the other scope are dropped first. The last ``limit`` of what
    remains are kept, so three sendable turns go out even when older turns
    were not answers. On a cloud service, a cases question is shown an earlier
    turn only when the data kind matches. Fictional data sends turns asked on
    fictional data (``demo``). Real data sends turns a cloud service answered
    under trimming. A turn with no recorded data kind counts as real. Articles,
    and a local model, still receive every answered turn in the same scope.
    """
    want = scope_name(scope)
    kept = [
        turn for turn in history
        if _sendable(turn, scope=want, real_data=real_data, to_cloud=to_cloud)
    ]
    if limit < 0:
        return ()
    return tuple(kept[-limit:])


def history_messages(turns: Sequence[PriorTurn]) -> list[dict[str, str]]:
    """User/assistant pairs placed after the system message and before the new question."""
    messages: list[dict[str, str]] = []
    for turn in turns:
        messages.append({"role": "user", "content": f"{EARLIER_PREFIX}{turn.question}"})
        messages.append({"role": "assistant", "content": answer_body(turn.answer)})
    return messages


def earlier_bodies(turns: Sequence[PriorTurn]) -> list[str]:
    """The bodies that were sent, for the number check. Notes are not sources."""
    return [answer_body(turn.answer) for turn in turns]


def follow_up_query(question: str, history: Sequence[PriorTurn]) -> str:
    """The search query: the earlier questions, then this one.

    With no earlier turn the query is the new question, unchanged.
    """
    earlier = [turn.question for turn in history]
    if not earlier:
        return question
    return "\n".join([*earlier, question])


def cited_then_ranked(history: Sequence[PriorTurn], ranked: Sequence[str]) -> list[str]:
    """Newest prior turn's cited cases, then older turns', then the new ranking.

    Duplicates are dropped. The caller still fetches at most three extracts.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for turn in reversed(tuple(history)):
        for pid in turn.pids:
            if pid and pid not in seen:
                seen.add(pid)
                ordered.append(pid)
    for pid in ranked:
        if pid and pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    return ordered


def alias_pids(current: Sequence[str], history: Sequence[PriorTurn]) -> list[str]:
    """Cases the trimmer must know: every prior turn sent, then the current candidates.

    One case keeps one alias for the whole request. A case that appears only in
    an earlier turn is still aliased when it is not fetched again.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for turn in history:
        for pid in turn.pids:
            if pid and pid not in seen:
                seen.add(pid)
                ordered.append(str(pid))
    for pid in current:
        if pid and pid not in seen:
            seen.add(pid)
            ordered.append(str(pid))
    return ordered
