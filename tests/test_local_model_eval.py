"""The held-out evaluation runner, checked with a scripted client and synthetic corpora only."""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

import pytest

from src.llm.ollama_client import OllamaError
from tools.local_model import build_prompts, eval_heldout
from tools.local_model.scripted import ScriptedClient

SEED = 900
N_CASES = 12


def _facts(messages) -> dict[str, str]:
    """The first case's numbers, read from the extract the app put in the last message."""
    text = next(m["content"] for m in reversed(messages) if "### Source extracts" in m["content"])

    def pick(pattern: str) -> str:
        found = re.search(pattern, text)
        assert found, pattern
        return found.group(1)

    return {
        "pid": pick(r"CASE PID: (\S+)"),
        "age": pick(r"- Age: (\d+)"),
        "sex": pick(r"- Sex: (\w)"),
        "procedure": pick(r"- Procedure: ([^\n]+)"),
        "pip": pick(r"- Peak inspiratory pressure: median ([\d.]+) cmH2O"),
        "question": text.split("### Question (from user)\n")[-1].strip(),
    }


ABSENT_WORDS = re.compile(r"\b(?:BIS|blood loss|core temperature|urine output|blood glucose)\b", re.I)


def perfect(messages, _index=0, *, gap="{what} is not available in this extract.") -> str:
    """An answer built from the extract's own numbers, with the five parts; says so when a signal is absent."""
    f = _facts(messages)
    asked = ABSENT_WORDS.search(f["question"])
    line = gap.format(what=asked.group(0).capitalize()) if asked else "Nothing else is stated beyond the extract."
    return (
        f"**Cases used**\n- {f['pid']}: age {f['age']}, {f['sex']}, {f['procedure']}.\n"
        f"**What the extracts show**\n- Peak inspiratory pressure had a median of {f['pip']} cmH2O.\n"
        "**Flags & rule meaning**\n- No rule meaning beyond the source extract is given.\n"
        f"**Gaps**\n- {line}\n"
        "Research and education only."
    )


def _run(tmp_path, reply, *, repeats=1, split="test", seeds=(SEED,), advice=(), per_family=1, factory=None):
    made: list[ScriptedClient] = []

    def make(_model, seed):
        client = ScriptedClient(reply, advice_sentences=advice, seed=seed)
        made.append(client)
        return client

    out = tmp_path / "out"
    report = eval_heldout.evaluate(
        split, list(seeds), N_CASES, "scripted-model", repeats, out,
        client_factory=factory or make, allowed_roots=(tmp_path,), per_family=per_family,
    )
    answers = [json.loads(line) for line in (out / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    return report, answers, made, out


@pytest.fixture(scope="module")
def perfect_run(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("perfect"), perfect)


def test_a_perfect_answer_scores_full_marks(perfect_run):
    report, answers, _, _ = perfect_run
    overall = report["metrics"]["overall"]["per_repeat"][0]
    assert len(answers) >= 10 and overall["n"] == len(answers)
    assert {a["kind"] for a in answers} == {"question", "scenario", "compare"}
    assert overall["draft_grounding_rate"] == 1.0
    assert overall["grounding_rate"] == 1.0
    assert overall["gate_pass_rate"] == 1.0
    assert overall["structure_rate"] == 1.0
    assert overall["withheld_rate"] == 0.0 and overall["rewrite_rate"] == 0.0
    assert overall["error_rate"] == 0.0 and overall["empty_rate"] == 0.0
    assert overall["not_in_extract_n"] >= 2 and overall["not_in_extract_rate"] == 1.0
    assert all(rate == 0.0 for rate in overall["problem_rates"].values())
    assert report["metrics"]["overall"]["spread"]["grounding_rate"] == 0.0


def test_each_kind_is_reported_and_adds_up(perfect_run):
    report, answers, _, _ = perfect_run
    metrics = report["metrics"]
    assert sum(metrics[k]["per_repeat"][0]["n"] for k in ("question", "scenario", "compare")) == len(answers)
    assert metrics["scenario"]["per_repeat"][0]["structure_rate"] == 1.0
    assert all(a["repeat"] == 0 and a["final"] for a in answers)


INVENTED = "- The peak pressure also reached 613.7 cmH2O."


def _with(extra: str, *, only_draft: bool = False):
    """The perfect answer plus one line; ``only_draft`` leaves a rewrite request clean."""

    def reply(messages, index):
        text = perfect(messages)
        rewriting = "REJECTED by the research-safety check" in messages[-1]["content"]
        if only_draft and rewriting:
            return text
        return text.replace("**Flags", f"{extra}\n**Flags")

    return reply


def test_an_invented_number_is_ungrounded_and_a_gate_problem(tmp_path):
    report, answers, _, _ = _run(tmp_path, _with(INVENTED))
    overall = report["metrics"]["overall"]["per_repeat"][0]
    assert overall["draft_grounding_rate"] == 0.0
    assert overall["grounding_rate"] == 0.0
    assert overall["gate_pass_rate"] == 0.0
    assert overall["problem_rates"]["ungrounded_number"] == 1.0
    # the app strips the number before showing the answer, which the shipped rate reflects
    assert overall["shipped_grounding_rate"] == 1.0
    assert overall["problem_rates"]["directive"] == 0.0
    assert all("613.7" in " ".join(a["problems"]) for a in answers)


def test_advice_is_counted_as_a_directive_problem(tmp_path):
    advice = "Clinicians should consider raising the PEEP."
    report, answers, made, _ = _run(tmp_path, _with(advice), advice=("should consider",))
    overall = report["metrics"]["overall"]["per_repeat"][0]
    assert overall["problem_rates"]["directive"] == 1.0
    assert overall["gate_pass_rate"] == 0.0
    assert overall["problem_rates"]["ungrounded_number"] == 0.0
    assert overall["draft_grounding_rate"] == 1.0
    assert made[0].structured_calls > 0
    assert all(a["problems"] and not a["problem_kinds"]["ungrounded_number"] for a in answers)


def test_a_rewrite_that_fixes_the_draft_is_counted_as_accepted(tmp_path):
    report, answers, _, _ = _run(tmp_path, _with(INVENTED, only_draft=True))
    overall = report["metrics"]["overall"]["per_repeat"][0]
    assert overall["draft_grounding_rate"] == 0.0
    assert overall["grounding_rate"] == 1.0
    assert overall["rewrite_rate"] == 1.0
    assert overall["rewrite_success_rate"] == 1.0
    assert overall["gate_pass_rate"] == 0.0
    assert all("rewrite:accepted" in a["trace"] for a in answers)


def test_a_rewrite_that_still_fails_is_counted_as_still_flagged(tmp_path):
    report, answers, _, _ = _run(tmp_path, _with(INVENTED))
    overall = report["metrics"]["overall"]["per_repeat"][0]
    assert overall["rewrite_rate"] == 1.0
    assert overall["rewrite_success_rate"] == 0.0
    assert all("rewrite:still_flagged" in a["trace"] for a in answers)


def test_an_uncited_case_is_withheld(tmp_path):
    report, answers, _, _ = _run(tmp_path, _with("- SYN0123456789 had a PIP of 20 cmH2O."))
    overall = report["metrics"]["overall"]["per_repeat"][0]
    assert overall["withheld_rate"] == 1.0
    assert overall["problem_rates"]["uncited_pid"] == 1.0
    assert overall["structure_rate"] == 0.0
    assert all(a["final"].startswith("The answer is withheld") for a in answers)


QUESTION = "Did the lumbar laminectomy cases record core temperature?"


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Core temperature is not available in this extract.", True),
        ("The extract does not record core temperature.", True),
        ("- SYN00024ddd46: core temperature is not recorded.", True),
        ("Age 67, sex F.\nCore temperature is not available in this extract.", True),
        ("Core temperature is not available in this extract, but it read 36.5.", False),
        ("Core temperature was 36.5 in the first case.", False),
        ("The first case ran 133 minutes and used sevoflurane.", False),
        ("", False),
    ],
)
def test_not_in_extract_needs_the_gap_said_and_no_number(answer, ok):
    assert eval_heldout.not_in_extract_ok(answer, QUESTION) is ok


def test_every_absent_family_names_a_signal():
    import random

    facts = [build_prompts.questions.CaseFacts("SYN1", "Craniotomy", 44, "M", "high PIP")]
    for family in build_prompts.questions.FAMILIES:
        if family.absent and family.earlier == 0:
            for template in family.templates:
                narrow = build_prompts.questions.Family(**{**family.__dict__, "templates": (template,)})
                text = build_prompts.questions.fill(narrow, facts, random.Random(1))
                assert eval_heldout.signal_terms(text), (family.name, text)


def test_answering_an_absent_question_with_a_number_fails_only_that_metric(tmp_path):
    def reply(messages, index):
        return perfect(messages, gap="{what} is not available in this extract, but it read 36.5.")

    report, answers, _, _ = _run(tmp_path, reply)
    absent = [a for a in answers if a["absent"]]
    assert len(absent) >= 2
    assert all(a["not_in_extract"] is False for a in absent)
    assert all(a["not_in_extract"] is None for a in answers if not a["absent"])
    assert report["metrics"]["overall"]["per_repeat"][0]["not_in_extract_rate"] == 0.0


def test_an_absent_question_answered_as_not_available_passes(perfect_run):
    _, answers, _, _ = perfect_run
    absent = [a for a in answers if a["absent"]]
    assert absent and all(a["not_in_extract"] is True for a in absent)
    assert all("not available in this extract" in a["final"] for a in absent)


def test_each_repeat_gets_its_own_seed_and_the_spread_is_reported(tmp_path):
    def factory(_model, seed):
        # Only the second repeat answers badly, so the repeats differ.
        return ScriptedClient(_with(INVENTED) if seed == eval_heldout.repeat_seed(1) else perfect, seed=seed)

    made: list[ScriptedClient] = []

    def recording(model, seed):
        made.append(factory(model, seed))
        return made[-1]

    report, answers, _, out = _run(tmp_path, perfect, repeats=3, factory=recording)
    assert [c.seed for c in made] == [1000, 1001, 1002]
    assert len({c.seed for c in made}) == 3
    assert all(c.draft_calls for c in made)
    assert sorted({a["repeat"] for a in answers}) == [0, 1, 2]
    block = report["metrics"]["overall"]
    assert [m["grounding_rate"] for m in block["per_repeat"]] == [1.0, 0.0, 1.0]
    assert block["mean"]["grounding_rate"] == pytest.approx(statistics.fmean([1.0, 0.0, 1.0]))
    assert block["spread"]["grounding_rate"] == 1.0
    assert block["spread"]["structure_rate"] == 0.0
    saved = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert saved["ollama_seeds"] == [1000, 1001, 1002] and saved["repeats"] == 3 and saved["num_ctx"] == 8192


def test_the_real_client_sends_the_seed_with_its_chat_calls(monkeypatch):
    from src.llm.ollama_client import OllamaClient

    sent: list[tuple[str, dict | None]] = []

    def fake(self, method, path, body=None, *, timeout_s=None):
        sent.append((path, body))
        return {"message": {"content": "hi"}, "models": []}

    monkeypatch.setattr(OllamaClient, "_request_json", fake)
    client = eval_heldout.SeededOllamaClient(1002)
    client.chat_turn("m", [{"role": "user", "content": "x"}], temperature=0.1, max_tokens=5)
    client.list_models()
    path, body = sent[0]
    assert path == "/api/chat" and body["options"] == {"temperature": 0.1, "num_predict": 5, "seed": 1002, "num_ctx": 8192}
    assert sent[1] == ("/api/tags", None)


def test_errors_and_empty_replies_are_counted_and_scored_as_failures(tmp_path):
    def broken(messages, index):
        raise OllamaError("model crashed")

    report, answers, _, _ = _run(tmp_path, broken)
    overall = report["metrics"]["overall"]["per_repeat"][0]
    assert overall["error_rate"] == 1.0 and overall["grounding_rate"] == 0.0 and overall["gate_pass_rate"] == 0.0
    assert all("model crashed" in a["error"] for a in answers)

    report, answers, _, _ = _run(tmp_path / "second", "")
    overall = report["metrics"]["overall"]["per_repeat"][0]
    assert overall["empty_rate"] == 1.0 and overall["error_rate"] == 0.0
    assert overall["structure_rate"] == 0.0 and overall["draft_grounding_rate"] == 0.0


def test_latency_is_timed_around_the_whole_answer(tmp_path):
    import time

    def slow(messages, index):
        time.sleep(0.05)
        return perfect(messages)

    report, answers, _, _ = _run(tmp_path, slow)
    assert all(a["latency_s"] >= 0.05 for a in answers)
    overall = report["metrics"]["overall"]["per_repeat"][0]
    assert overall["latency_mean_s"] >= 0.05 and overall["latency_p95_s"] >= overall["latency_mean_s"]


def test_the_two_files_are_written_with_what_the_brief_asks(perfect_run):
    report, answers, _, out = perfect_run
    saved = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert {"model", "split", "seeds", "repeats", "metrics", "commit"} <= set(saved)
    assert saved["model"] == "scripted-model" and saved["split"] == "test" and saved["seeds"] == [SEED]
    assert len(saved["commit"]) in (40, 7) or saved["commit"] == "unknown"
    for key in ("id", "repeat", "question", "kind", "final", "first_draft", "problems", "trace"):
        assert all(key in a for a in answers), key
    assert all(a["first_draft"] == a["after_rewrite"] and a["final"].startswith(a["first_draft"]) for a in answers)
    assert sorted(p.name for p in out.iterdir()) == ["answers.jsonl", "metrics.json"]


# ---- refusals, and nothing written when Ollama is not there ----------------------------


@pytest.fixture
def nothing_built(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a corpus was built or a file was written")

    monkeypatch.setattr("tools.eval_answers.build_corpus", boom)
    monkeypatch.setattr(Path, "write_text", boom)


class _Down:
    def __init__(self, models=None):
        self.models = models

    def list_models(self):
        if self.models is None:
            raise OllamaError("Ollama is not running")
        return self.models


def _safe(model, seed):
    """A client that cannot reach anything, for runs that must be refused before they ask."""
    return _Down([model])


def _argv(tmp_path, *extra, split="test", seeds="900", model="scripted-model"):
    return ["--split", split, "--seeds", seeds, "--model", model, "--out", str(tmp_path / "out"), *extra]


def test_a_down_ollama_exits_non_zero_and_writes_nothing(tmp_path, nothing_built, capsys):
    code = eval_heldout.main(
        _argv(tmp_path), client_factory=lambda model, seed: _Down(), allowed_roots=(tmp_path,)
    )
    assert code != 0
    assert "not reachable" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_a_missing_model_exits_non_zero_and_writes_nothing(tmp_path, nothing_built, capsys):
    code = eval_heldout.main(
        _argv(tmp_path), client_factory=lambda model, seed: _Down(["other:latest"]), allowed_roots=(tmp_path,)
    )
    assert code != 0 and "not installed" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
    # a tagged install of the named model counts as installed
    eval_heldout.check_installed(_Down(["scripted-model:latest"]), "scripted-model")


def test_the_train_split_is_refused(tmp_path, nothing_built):
    with pytest.raises(SystemExit) as stop:
        eval_heldout.main(_argv(tmp_path, split="train", seeds="100"), client_factory=_safe, allowed_roots=(tmp_path,))
    assert stop.value.code == 2
    with pytest.raises(build_prompts.Refused):
        eval_heldout.evaluate(
            "train", [100], N_CASES, "m", 1, tmp_path / "o", client_factory=_safe, allowed_roots=(tmp_path,)
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("split", "seed"), [("test", 150), ("valid", 100), ("test", 805), ("valid", 905)])
def test_train_seeds_and_the_other_held_out_split_are_refused(split, seed, tmp_path, nothing_built):
    code = eval_heldout.main(_argv(tmp_path, split=split, seeds=str(seed)), client_factory=_safe, allowed_roots=(tmp_path,))
    assert code == 2 and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("seed", [7, 8])
def test_never_train_seeds_are_refused_even_inside_the_split_range(seed, monkeypatch, tmp_path, nothing_built):
    monkeypatch.setitem(eval_heldout.splits.SPLIT_SEEDS, "test", range(0, 1000))
    code = eval_heldout.main(_argv(tmp_path, seeds=str(seed)), client_factory=_safe, allowed_roots=(tmp_path,))
    assert code == 2 and list(tmp_path.iterdir()) == []


def test_the_path_guard_is_the_one_build_prompts_uses():
    assert eval_heldout.check_out is build_prompts.check_out
    assert eval_heldout.private_env is build_prompts.private_env
    assert eval_heldout.parse_seeds is build_prompts.parse_seeds
    assert eval_heldout.Refused is build_prompts.Refused


@pytest.mark.parametrize(
    "bad",
    [
        Path.home() / ".config" / "mover-suite" / "anything",
        Path("/var/mnt/games/EMR/x"),
        Path("/var/mnt/games/sis_wave_v2/x"),
    ],
)
def test_the_real_data_folders_and_outside_folders_are_refused(bad, tmp_path, nothing_built):
    argv = ["--split", "test", "--seeds", "900", "--model", "m", "--out"]
    assert eval_heldout.main([*argv, str(bad)], client_factory=_safe) == 2
    outside = tmp_path / "elsewhere"
    assert eval_heldout.main([*argv, str(outside)], client_factory=_safe) == 2
    assert not outside.exists()
