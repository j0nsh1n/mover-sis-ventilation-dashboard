"""The training-answer writer: what reaches Codex or Kimi, what is kept, and where it may write."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path

import pytest

from src.llm.ollama_client import ChatResult
from tools.local_model import build_prompts, eval_heldout, write_answers
from tools.local_model.scripted import ScriptedClient

SEED = 100
N_CASES = 12


def _extract(messages) -> str:
    return next(m["content"] for m in reversed(messages) if "### Source extracts" in m["content"])


def good(messages, _index=0) -> str:
    """An answer from the first case's own numbers with the five parts; says so when BIS is asked."""
    text = _extract(messages)
    pid = re.search(r"CASE PID: (\S+)", text).group(1)
    pip = re.search(r"- Peak inspiratory pressure: median ([\d.]+) cmH2O", text).group(1)
    question = text.split("### Question (from user)\n")[-1]
    gap = "BIS is not available in this extract." if "BIS" in question else "Nothing else is stated."
    return (
        f"**Cases used**\n- {pid}.\n"
        f"**What the extracts show**\n- Peak inspiratory pressure had a median of {pip} cmH2O.\n"
        "**Flags & rule meaning**\n- No rule meaning beyond the source extract is given.\n"
        f"**Gaps**\n- {gap}\n"
        "Research/education only."
    )


def _run(tmp_path, reply, *, seeds=(SEED,), limit=None):
    client = ScriptedClient(reply)
    out = tmp_path / "out"
    report = write_answers.write("codex", list(seeds), out, n_cases=N_CASES, limit=limit, client=client,
                                 allowed_roots=(tmp_path,))
    answers = [json.loads(line) for line in (out / "answers-codex.jsonl").read_text(encoding="utf-8").splitlines()]
    train_path = out / "train-codex.jsonl"
    train = [json.loads(line) for line in train_path.read_text(encoding="utf-8").splitlines()] if train_path.exists() else []
    return report, answers, train, client, out


@pytest.fixture(scope="module")
def good_run(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("good"), good)


def test_a_good_answer_becomes_a_chat_example_with_the_apps_own_messages(good_run):
    report, answers, train, client, _out = good_run
    assert report["asked"] == len(answers) > 5
    assert report["kept"] == len(train) == sum(a["kept"] for a in answers) > 0
    for example in train:
        roles = [m["role"] for m in example["messages"]]
        assert roles[0] == "system" and roles[-1] == "assistant"
        assert "Writer note" not in json.dumps(example["messages"])
        assert example["messages"][-1]["content"] == good(example["messages"][:-1])
    # The writer saw the note on the last message, and only once per question.
    assert all(write_answers.WRITER_NOTE in call[-1]["content"] for call in client.draft_calls)
    assert len(client.draft_calls) == len(answers)
    assert client.structured_calls == 0


def test_every_training_example_comes_from_a_training_seed_and_family(good_run):
    _report, answers, train, _client, _out = good_run
    assert {a["seed"] for a in answers} == {SEED}
    assert all(a["family"].startswith("train-") for a in answers)
    assert not build_prompts.splits.leaks(train, [])


def test_a_second_run_resumes_without_asking_again(good_run, tmp_path):
    _report, answers, _train, _client, out = good_run
    again = ScriptedClient(good)
    report = write_answers.write("codex", [SEED], out, n_cases=N_CASES, client=again,
                                 allowed_roots=(out.parent,))
    assert report["asked"] == 0 and not again.draft_calls
    lines = (out / "answers-codex.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(answers)


def test_limit_stops_after_that_many_new_drafts(tmp_path):
    report, answers, _train, client, _out = _run(tmp_path, good, limit=2)
    assert report["asked"] == len(answers) == len(client.draft_calls) == 2


def test_a_draft_the_app_would_rewrite_is_dropped_after_one_call(tmp_path):
    def unsourced(messages, index):
        return good(messages, index).replace("had a median of", "had a median of 99.9 cmH2O, against")

    _report, answers, train, client, _out = _run(tmp_path, unsourced, limit=3)
    assert train == []
    assert len(client.draft_calls) == len(answers) == 3
    assert all(not a["kept"] and a["calls"] == 1 for a in answers)
    assert all(any("not in the extracts" in r or r.startswith("problem:") for r in a["drop"]) for a in answers)


def test_an_answer_that_names_no_case_is_dropped(tmp_path):
    def vague(messages, index):
        return re.sub(r"SYN\w+", "the case", good(messages, index))

    _report, answers, train, _client, _out = _run(tmp_path, vague, limit=2)
    assert train == []
    assert all("names none of the cases it was given" in a["drop"] for a in answers)


def test_an_answer_missing_a_heading_is_dropped(tmp_path):
    def no_gaps(messages, index):
        return good(messages, index).replace("**Gaps**", "**Missing**")

    _report, answers, train, _client, _out = _run(tmp_path, no_gaps, limit=2)
    assert train == []
    assert all("missing a part of the answer layout" in a["drop"] for a in answers)


def test_drop_reasons_name_each_failure():
    base = {"error": None, "empty": False, "problems": [], "draft_grounded": True, "not_in_extract": None,
            "first_draft": "**Cases used** SYN1 **What the extracts show** **Flags & rule meaning** **Gaps** "
                           "Research/education only."}
    assert write_answers.drop_reasons(base, ["SYN1"]) == []
    assert write_answers.drop_reasons({**base, "error": "RuntimeError: x"}) == ["error: RuntimeError: x"]
    assert write_answers.drop_reasons({**base, "empty": True}) == ["empty reply"]
    assert write_answers.drop_reasons({**base, "problems": ["p"]}, ["SYN1"]) == ["problem: p"]
    assert write_answers.drop_reasons({**base, "draft_grounded": False}, ["SYN1"]) == ["a number is not in the extracts"]
    assert write_answers.drop_reasons({**base, "not_in_extract": False}, ["SYN1"]) == [
        "does not say the asked signal is not available"]
    assert write_answers.drop_reasons(base, ["SYN2"]) == ["names none of the cases it was given"]


def test_the_advice_check_never_reaches_the_writer():
    inner = ScriptedClient("draft")
    probe = write_answers.FirstDraftOnly(inner)
    with pytest.raises(write_answers.AdviceNotRun):
        probe.chat_turn("m", [{"role": "user", "content": "x"}], format={"type": "object"})
    assert probe.chat_turn("m", [{"role": "user", "content": "q"}]).content == "draft"
    assert probe.chat_turn("m", [{"role": "user", "content": "rewrite please"}]).content == "draft"
    assert probe.calls == 1 and inner.structured_calls == 0 and len(inner.draft_calls) == 1
    assert probe.messages == [{"role": "user", "content": "q"}]


@pytest.mark.parametrize("seeds", [[7], [8], [800], [900], [100, 805]])
def test_only_training_seeds_are_written(seeds, tmp_path):
    client = ScriptedClient(good)
    with pytest.raises(build_prompts.Refused):
        write_answers.write("codex", seeds, tmp_path / "o", client=client, allowed_roots=(tmp_path,))
    assert not client.draft_calls and not (tmp_path / "o").exists()


def test_claude_and_unknown_writers_are_refused(tmp_path):
    assert set(write_answers.WRITERS) == {"codex", "kimi"}
    with pytest.raises(build_prompts.Refused):
        write_answers.write("claude", [SEED], tmp_path / "o", client=ScriptedClient(good), allowed_roots=(tmp_path,))
    with pytest.raises(SystemExit) as stopped:
        write_answers.main(["--writer", "claude", "--seeds", "100", "--out", str(tmp_path / "o")])
    assert stopped.value.code == 2


def test_the_output_folder_must_sit_under_the_allowed_root(tmp_path):
    with pytest.raises(build_prompts.Refused):
        write_answers.write("codex", [SEED], tmp_path / "elsewhere", client=ScriptedClient(good),
                            allowed_roots=(tmp_path / "ok",))
    assert write_answers.main(["--writer", "codex", "--seeds", "100", "--out", str(tmp_path / "x")],
                              allowed_roots=(tmp_path / "ok",)) == 2


def _fake_kimi(tmp_path: Path, body: str) -> str:
    script = tmp_path / "kimi"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_kimi_reply_is_the_last_assistant_text_of_its_stream(tmp_path):
    events = [{"role": "meta", "type": "system.version"}, {"role": "assistant", "content": "thinking out loud"},
              {"role": "tool", "content": "x"}, {"role": "assistant", "content": "**Cases used**\n- SYN1"},
              {"role": "meta", "type": "session.resume_hint", "content": "kimi -r s"}]
    stream = "\n".join(json.dumps(e) for e in events) + "\nnot json\n"
    binary = _fake_kimi(tmp_path, f"cat <<'EOF'\n{stream}EOF")
    client = write_answers.KimiCliClient(tmp_path / "calls", binary=binary)
    result = client.chat_turn("kimi-code/k3.1-pro", [{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}])
    assert isinstance(result, ChatResult) and result.content == "**Cases used**\n- SYN1"
    assert client.command("m", "P")[-4:] == ["-m", "m", "--output-format", "stream-json"]
    assert not any((tmp_path / "calls").iterdir())


def test_a_failed_kimi_call_raises(tmp_path):
    client = write_answers.KimiCliClient(tmp_path / "calls", binary=_fake_kimi(tmp_path, "echo boom >&2; exit 3"))
    with pytest.raises(RuntimeError, match="kimi exited 3: boom"):
        client.chat_turn("m", [{"role": "user", "content": "Q"}])


def test_a_slash_research_disclaimer_counts_as_the_fifth_part():
    answer = "**Cases used** **What the extracts show** **Flags & rule meaning** **Gaps** Research/education only."
    assert eval_heldout.has_structure(answer)
    assert not eval_heldout.has_structure(answer.replace("Research/education only.", "Done."))
