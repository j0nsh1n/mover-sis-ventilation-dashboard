"""Training prompts for the local model come from the app's own answer path, on synthetic data only."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import src.user_settings as user_settings
from src.llm.history import PriorTurn, answer_body, history_messages
from src.llm.intent import classify
from src.llm.prompts import build_extract_system_prompt, extract_user_message
from src.llm.retrieve import format_grounded_extracts
from tools.local_model import build_prompts, questions, splits

# qwen3 in the name makes the system prompt carry /no_think, so a wrong tag changes the prompt
TAG = "qwen3-test-tag"
SEED = 100


def _read(path: Path) -> list[dict]:
    import json

    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One small real build; the spies note what the app was asked and where settings lived."""
    root = tmp_path_factory.mktemp("lm")
    seen: dict = {"retrieved": {}, "settings": []}
    real_retrieve, real_capture = build_prompts.retrieve, build_prompts.capture

    def spy_retrieve(corpus, question, history):
        got = real_retrieve(corpus, question, history)
        seen["retrieved"][(question, tuple(t.question for t in history))] = got
        return got

    def spy_capture(*args, **kwargs):
        seen["settings"].append(user_settings.settings_path())
        return real_capture(*args, **kwargs)

    build_prompts.retrieve, build_prompts.capture = spy_retrieve, spy_capture
    try:
        out = root / "out"
        build_prompts.build("train", [SEED], 12, out, TAG, allowed_roots=(root,), per_family=2)
    finally:
        build_prompts.retrieve, build_prompts.capture = real_retrieve, real_capture
    return root, out, _read(out / "train.jsonl"), seen


def test_prompt_is_exactly_what_the_app_builds(built):
    _, _, records, seen = built
    assert len(records) >= 15
    for r in records:
        kind, candidates, extracts = seen["retrieved"][(r["question"], tuple(h["question"] for h in r["history"]))]
        assert kind == r["kind"]
        assert r["messages"][0] == {"role": "system", "content": build_extract_system_prompt(model=TAG)}
        block = format_grounded_extracts(candidates=candidates, extracts=extracts)
        want = extract_user_message(r["question"], kind=r["kind"], extracts=block)
        assert r["messages"][-1] == {"role": "user", "content": want}
        assert r["pids"] == [c.pid for c in candidates[:3]]
        assert r["model_tag"] == TAG and r["split"] == "train" and r["seed"] == SEED


def test_all_three_kinds_and_every_train_family_kind_is_covered(built):
    _, _, records, _ = built
    assert {r["kind"] for r in records} == {"question", "scenario", "compare"}
    assert any(r["history"] for r in records)
    assert any(r["family"].startswith("train-absent") for r in records)


def test_follow_up_messages_carry_the_earlier_turns(built):
    _, _, records, _ = built
    follow = [r for r in records if r["history"]]
    assert {len(r["history"]) for r in follow} == {1, 2}
    for r in follow:
        turns = [PriorTurn(question=h["question"], answer=h["answer"]) for h in r["history"]]
        n = 2 * len(turns)
        assert r["messages"][1 : 1 + n] == history_messages(turns)
        assert len(r["messages"]) == n + 2
        assert r["messages"][1]["content"] == f"Earlier question: {r['history'][0]['question']}"
        assert r["messages"][2]["content"] == answer_body(r["history"][0]["answer"])
    plain = next(r for r in records if not r["history"])
    assert [m["role"] for m in plain["messages"]] == ["system", "user"]


def test_settings_stay_inside_the_work_folder(built):
    root, out, _, seen = built
    assert seen["settings"]
    work = (out / "work" / f"seed-{SEED}").resolve()
    for path in seen["settings"]:
        assert work in path.resolve().parents, path
    assert user_settings.settings_path().resolve().parent != work / "config"  # env restored afterwards


def test_same_arguments_give_identical_bytes(built, tmp_path):
    root, out, _, _ = built
    again = tmp_path / "again"
    build_prompts.build("train", [SEED], 12, again, TAG, allowed_roots=(tmp_path,), per_family=2)
    assert (again / "train.jsonl").read_bytes() == (out / "train.jsonl").read_bytes()


def _kinds_probe():
    return [
        questions.CaseFacts(pid=f"SYN00000000{i:02d}", procedure=proc, age=age, sex=sex, flag="high PIP")
        for i, (proc, age, sex) in enumerate(
            [("Thyroidectomy", 58, "F"), ("Craniotomy", 44, "M"), ("Total Knee Arthroplasty", 71, "F")]
        )
    ]


def test_every_family_question_reads_as_its_kind():
    import random

    for family in questions.FAMILIES:
        for template in family.templates:
            narrow = questions.Family(**{**family.__dict__, "templates": (template,)})
            cases = questions.pick_cases(narrow, _kinds_probe(), random.Random(1))
            assert cases is not None, family.name
            text = questions.fill(narrow, cases, random.Random(1))
            assert classify(text).kind == family.kind, (family.name, text)


def test_train_and_heldout_families_never_overlap():
    train = {f.name for f in questions.FAMILIES if f.side == "train"}
    held = {f.name for f in questions.FAMILIES if f.side == "heldout"}
    assert train and held and not train & held
    assert len(train) + len(held) == len(questions.FAMILIES), "a family name is used twice"
    wording = {side: {t for f in questions.FAMILIES if f.side == side for t in f.templates} for side in ("train", "heldout")}
    assert not wording["train"] & wording["heldout"], "a template is reused across sides"
    for side in ("train", "heldout"):
        fams = [f for f in questions.FAMILIES if f.side == side]
        assert {"question", "scenario", "compare"} <= {f.kind for f in fams}
        assert any(f.earlier for f in fams) and any(f.absent for f in fams)


def _rec(seed, pids, family):
    return {"seed": seed, "pids": pids, "family": family}


def test_clean_splits_report_nothing():
    assert splits.leaks([_rec(100, ["A"], "train-x")], [_rec(900, ["B"], "heldout-y")]) == []


def test_leaks_reports_a_shared_pid():
    found = splits.leaks([_rec(100, ["A", "S"], "train-x")], [_rec(900, ["S"], "heldout-y")])
    assert any("PID S" in p for p in found)


def test_leaks_reports_a_shared_seed():
    found = splits.leaks([_rec(100, ["A"], "train-x")], [_rec(100, ["B"], "heldout-y")])
    assert any("seed 100 is on both sides" in p for p in found)


def test_leaks_reports_a_shared_family():
    found = splits.leaks([_rec(100, ["A"], "fam")], [_rec(900, ["B"], "fam")])
    assert any("family 'fam'" in p for p in found)


@pytest.mark.parametrize("seed", [7, 8])
def test_leaks_reports_a_never_train_seed_in_train(seed):
    found = splits.leaks([_rec(seed, ["A"], "train-x")], [_rec(900, ["B"], "heldout-y")])
    assert any(f"seed {seed}" in p for p in found)


@pytest.mark.parametrize("seed", [805, 905])
def test_leaks_reports_a_held_out_seed_in_train(seed):
    found = splits.leaks([_rec(seed, ["A"], "train-x")], [_rec(900, ["B"], "heldout-y")])
    assert any(f"seed {seed}" in p for p in found)


def _no_writes(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("something was written or built")

    monkeypatch.setattr(Path, "mkdir", boom)
    monkeypatch.setattr(Path, "write_text", boom)
    monkeypatch.setattr(build_prompts, "records_for_seed", boom)


@pytest.mark.parametrize(
    "bad",
    [
        Path.home() / ".config" / "mover-suite" / "anything",
        Path("/var/mnt/games/EMR/x"),
        Path("/var/mnt/games/sis_wave_v2/x"),
    ],
)
def test_path_guard_refuses_the_real_data_folders(bad, monkeypatch, tmp_path):
    _no_writes(monkeypatch)
    # Even when the allowed root is wide enough to contain them
    with pytest.raises(build_prompts.Refused):
        build_prompts.check_out(bad, allowed_roots=(bad.parents[2], Path("/")))
    argv = ["--split", "train", "--seeds", "100", "--out", str(bad)]
    assert build_prompts.main(argv) == 2


def test_path_guard_refuses_a_folder_outside_the_allowed_root(monkeypatch, tmp_path):
    allowed, outside = tmp_path / "ok", tmp_path / "elsewhere"
    allowed.mkdir()
    _no_writes(monkeypatch)
    with pytest.raises(build_prompts.Refused):
        build_prompts.build("train", [SEED], 12, outside, TAG, allowed_roots=(allowed,))
    assert build_prompts.main(["--split", "train", "--seeds", "100", "--out", str(outside)]) == 2
    monkeypatch.undo()
    assert not outside.exists()


def test_path_guard_allows_a_folder_under_the_root(tmp_path):
    assert build_prompts.check_out(tmp_path / "a" / "b", allowed_roots=(tmp_path,)) == (tmp_path / "a" / "b").resolve()
    assert build_prompts.check_out(build_prompts.ALLOWED_ROOT / "data" / "x")


@pytest.mark.parametrize(
    ("split", "seed"),
    [("train", 7), ("train", 8), ("train", 800), ("train", 900), ("valid", 100), ("test", 805), ("valid", 7)],
)
def test_seeds_outside_the_split_are_refused(split, seed, monkeypatch, tmp_path):
    _no_writes(monkeypatch)
    with pytest.raises(build_prompts.Refused):
        build_prompts.build(split, [seed], 12, tmp_path / "o", TAG, allowed_roots=(tmp_path,))


@pytest.mark.parametrize("seed", [7, 8])
def test_never_train_seeds_are_refused_even_inside_the_split_range(seed, monkeypatch, tmp_path):
    monkeypatch.setitem(splits.SPLIT_SEEDS, "train", range(0, 200))
    _no_writes(monkeypatch)
    with pytest.raises(build_prompts.Refused):
        build_prompts.build("train", [seed], 12, tmp_path / "o", TAG, allowed_roots=(tmp_path,))


def test_seed_ranges_parse():
    assert build_prompts.parse_seeds("100-102,105") == [100, 101, 102, 105]
    with pytest.raises(build_prompts.Refused):
        build_prompts.parse_seeds("abc")


def test_manifest_counts_match_the_records(built):
    import json

    _, out, records, _ = built
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["records"] == len(records)
    assert manifest["seeds"] == [SEED] and manifest["n_cases"] == 12 and manifest["model_tag"] == TAG
    assert sum(manifest["counts"]["family"].values()) == len(records)
    assert sum(manifest["counts"]["kind"].values()) == len(records)
    assert len(manifest["commit"]) in (40, 7) or manifest["commit"] == "unknown"
    assert time.strptime(manifest["created"][:19], "%Y-%m-%dT%H:%M:%S")
