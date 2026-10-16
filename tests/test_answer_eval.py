"""The answer gate, measured on recorded answers over a seeded synthetic EMR."""

from __future__ import annotations

import json

import pytest

import src.llm.agent as agent
import tools.eval_answers as eval_answers
from src.llm.ollama_client import OllamaClient


def _run(tmp_path, *extra: str):
    out = tmp_path / "out"
    code = eval_answers.main(["--out", str(out), *extra])
    path = out / "metrics.json"
    return code, json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@pytest.fixture(scope="module")
def scripted(tmp_path_factory):
    code, metrics = _run(tmp_path_factory.mktemp("answer_eval"))
    return code, metrics


def test_every_entry_matches_its_expectation(scripted):
    code, metrics = scripted
    assert metrics["mismatches"] == {}
    assert code == 0


def test_gate_catches_every_flawed_answer_and_flags_no_clean_one(scripted):
    _, metrics = scripted
    assert metrics["gate_catch_rate"] == 1.0
    assert metrics["false_flag_rate"] == 0.0
    assert metrics["grounding_rate"] == 1.0
    assert metrics["draft_grounding_rate"] < 1.0, "the corpus must contain drafts with invented numbers"


def test_corpus_covers_each_kind_of_answer(scripted):
    _, metrics = scripted
    assert metrics["n_entries"] >= 20
    assert metrics["n_expected_to_fail"] >= 8
    assert metrics["n_clean"] >= 8
    assert 0 < metrics["rewrite_rate"] < 1
    assert metrics["gate_latency_ms_p95"] >= metrics["gate_latency_ms_mean"] > 0
    assert (metrics["seed"], metrics["n_cases"]) == (8, 30)


def test_prototype_entries_replay_the_driver_drafts():
    _, entries = eval_answers.load_entries()
    prototype = {e.id for e in entries if e.prototype}
    assert prototype == {"proto-peak", "proto-scenario", "proto-missing", "proto-flags", "proto-cause"}


def test_a_gate_that_finds_nothing_fails_the_eval(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "answer_problems", lambda *args, **kwargs: [])
    monkeypatch.setattr(agent, "verify_answer", lambda text, *args, **kwargs: (text, []))
    code, metrics = _run(tmp_path)
    assert code == 1
    assert metrics["gate_catch_rate"] == 0.0
    assert "fabricated-number-rewritten" in metrics["mismatches"]


def test_live_mode_skips_without_ollama(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(OllamaClient, "is_available", lambda self: False)
    code, metrics = _run(tmp_path, "--live", "some-model")
    assert code == 0
    assert metrics is None
    assert "not running" in capsys.readouterr().out


def test_live_mode_records_the_model_and_time(tmp_path, monkeypatch):
    reply = "The retrieved extracts record airway pressure for these cases."
    monkeypatch.setattr(eval_answers, "_live_client", lambda model: eval_answers.RecordedClient([reply]))
    code, metrics = _run(tmp_path, "--live", "some-model")
    assert code == 0
    assert metrics["model"] == "some-model"
    assert metrics["timestamp"].endswith("+00:00")
    assert "gate_catch_rate" not in metrics
    assert metrics["rewrite_rate"] == 0.0
    assert metrics["grounding_rate"] == 1.0
