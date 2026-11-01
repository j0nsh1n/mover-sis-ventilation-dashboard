"""Shared data context: source identity, preset invalidation, and job generations."""

from __future__ import annotations

import pandas as pd
import pytest

from src.services import case_fetch
from src.services.data_context import current_context, reset_context
from src.services.retrieval import RetrievalEngine, RetrievalPhase


class _UnavailableEmbedder:
    model_id = "missing-embed"

    def is_available(self) -> bool:
        return False

    def embed(self, texts):
        raise AssertionError("a missing model must not embed")


@pytest.fixture
def preset_window(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from src.desktop.app import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setattr("src.desktop.app.load_settings", lambda: {"preset": "default"})
    for method in ("_autoload_processed", "_load_corpus_scan", "_refresh_ollama_models"):
        monkeypatch.setattr(MainWindow, method, lambda *a, **k: None)
    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def _vent_source(path) -> None:
    path.mkdir()
    (path / "patient_ventilator.csv").write_text("PID\ncaseA\n")


def test_same_surgery_id_in_two_sources_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr("src.rules.thresholds_sha256", lambda: "ab" * 32)

    def run(_pids, _preset, emr):
        return {
            "cases": pd.DataFrame({"PID": ["caseA"], "source": [str(emr)]}),
            "timeseries": pd.DataFrame(),
            "flags": pd.DataFrame(),
            "episodes": pd.DataFrame(),
            "events": pd.DataFrame(),
        }

    monkeypatch.setattr(case_fetch, "_run_for", run)
    first_source = tmp_path / "emr-a"
    second_source = tmp_path / "emr-b"
    _vent_source(first_source)
    _vent_source(second_source)

    first = case_fetch.fetch_cases(["caseA"], emr=first_source)
    second = case_fetch.fetch_cases(["caseA"], emr=second_source)
    assert first.computed == ["caseA"]
    assert second.reused == []
    assert second.computed == ["caseA"]
    assert first.cases["source"].iloc[0] != second.cases["source"].iloc[0]

    again = case_fetch.fetch_cases(["caseA"], emr=first_source)
    assert again.reused == ["caseA"]
    assert again.cases["source"].iloc[0] == first.cases["source"].iloc[0]


def test_preset_and_threshold_changes_drop_the_fetch_cache_and_old_generations(tmp_path):
    context = reset_context()
    emr = tmp_path / "emr"
    processed = tmp_path / "processed"
    context.note_paths(emr, processed)
    context.note_preset("default", recorded=True, thresholds_sha256="a" * 64)
    case_fetch._case_cache[("stale", "caseA", "default", "a" * 64)] = ({}, "a" * 64)
    started = context.stamp()

    assert context.note_preset("strict", recorded=True, thresholds_sha256="b" * 64)
    assert context.generation == started.generation + 1
    assert case_fetch._case_cache == {}
    assert not context.accepts(started)

    restarted = context.stamp()
    assert context.note_preset("strict", recorded=True, thresholds_sha256="c" * 64)
    assert not context.accepts(restarted)
    assert context.accepts(context.stamp())


def test_switching_away_and_back_rejects_the_original_generation(tmp_path):
    context = reset_context()
    home_emr = tmp_path / "home-emr"
    home_processed = tmp_path / "home-processed"
    context.note_paths(home_emr, home_processed)
    started = context.stamp()
    assert context.note_paths(tmp_path / "away-emr", tmp_path / "away-processed")
    assert context.note_paths(home_emr, home_processed)
    assert context.emr == home_emr.resolve()
    assert not context.accepts(started)


def test_context_source_keeps_keyword_fallback_and_the_extract_bound(
    synthetic_emr, tmp_path, monkeypatch
):
    context = reset_context()
    context.note_paths(synthetic_emr, tmp_path / "processed")
    seen: list[str] = []

    def fake_fetch(pids, **_kwargs):
        from src.services.case_fetch import FetchResult

        seen.extend(pids)
        pid = pids[0]
        return FetchResult(cases=pd.DataFrame([{"PID": pid, "Procedure": "x"}]), computed=[pid])

    monkeypatch.setattr("src.services.case_fetch.fetch_cases", fake_fetch)
    engine = RetrievalEngine(
        emr=context.emr,
        cache_path=tmp_path / "embeddings.sqlite3",
        embedder=_UnavailableEmbedder(),
        extract_limit=1,
    )
    phases: list[str] = []
    assert engine.prepare(on_progress=lambda progress: phases.append(progress.phase.value)) == 0
    result = engine.retrieve(
        "cholecystectomy appendectomy",
        on_progress=lambda progress: phases.append(progress.phase.value),
    )
    assert context.emr == synthetic_emr.resolve()
    assert result.rank_method == "keyword"
    assert result.model_available is False
    assert RetrievalPhase.MODEL_UNAVAILABLE.value in phases
    assert len(seen) == 1
    assert result.fetched_pids == (seen[0],)


def test_embedding_model_change_keeps_the_generation_and_fetch_cache(tmp_path):
    context = reset_context()
    context.note_paths(tmp_path / "emr", tmp_path / "processed")
    context.note_models(chat_model=None, embed_model="nomic-embed-text")
    case_fetch._case_cache[("kept",)] = ({}, "hash")
    started = context.stamp()
    assert not context.note_models(chat_model="gemma", embed_model="nomic-embed-text")
    assert context.chat_model == "gemma"
    assert context.note_models(chat_model="gemma", embed_model="other-embed")
    assert context.embed_model == "other-embed"
    assert context.accepts(started)
    assert case_fetch._case_cache


def test_search_uses_the_context_embedding_model(preset_window, monkeypatch):
    from src.desktop.research_workers import ResearchWorker

    seen: list[str] = []

    class RecordingEmbedder:
        def __init__(self, model: str) -> None:
            seen.append(model)

        def is_available(self) -> bool:
            return False

        def embed(self, _texts):
            raise AssertionError("a missing model must not embed")

    monkeypatch.setattr("src.llm.ollama_client.OllamaEmbedder", RecordingEmbedder)
    monkeypatch.setattr(
        "src.desktop.app.load_settings",
        lambda: {"preset": "default", "ollama_embed_model": "test-embed", "ollama_model": "test-chat"},
    )
    monkeypatch.setattr("src.guardrails.validate_io.validate_emr_dir", lambda path: path)
    monkeypatch.setattr(ResearchWorker, "start", lambda self: None)
    preset_window._start_research_search("synthetic pressure")
    assert seen == ["test-embed"]
    assert current_context().embed_model == "test-embed"
    assert current_context().chat_model == "test-chat"


def test_source_round_trip_drops_a_late_pipeline_result(preset_window, tmp_path):
    from src.desktop.app import PipelineWorker

    window = preset_window
    home_emr = tmp_path / "EMR"
    home_processed = tmp_path / "processed"
    worker = PipelineWorker(
        50, "default", True, emr=home_emr, processed=home_processed, parent=window
    )
    window._worker = worker
    started = worker.job_stamp
    context = current_context()
    assert context.note_paths(tmp_path / "away-emr", tmp_path / "away-processed")
    assert context.note_paths(home_emr, home_processed)
    assert not context.accepts(started)
    worker.finished_ok.connect(window._on_pipeline_ok)
    worker.finished_ok.emit(tuple(pd.DataFrame({"PID": ["late"]}) for _ in range(5)))
    assert window.cases is None


def test_source_round_trip_drops_a_late_scan(preset_window, tmp_path):
    from types import SimpleNamespace

    from src.desktop.app import CorpusScanWorker

    window = preset_window
    home_emr = tmp_path / "EMR"
    home_processed = tmp_path / "processed"
    worker = CorpusScanWorker(home_emr, home_processed, parent=window)
    window._worker = worker
    context = current_context()
    context.note_paths(tmp_path / "away-emr", tmp_path / "away-processed")
    context.note_paths(home_emr, home_processed)
    window._on_corpus_scan_ok(
        SimpleNamespace(meta={"n_scanned_cases": 1, "n_ventilated_surgeries": 1}),
        worker.processed,
        worker,
    )
    assert window.corpus_scan is None
    assert "previous data folder" in window.statusBar().currentMessage()


def test_a_replaced_worker_does_not_accept_an_older_scan(preset_window, tmp_path):
    from types import SimpleNamespace

    from src.desktop.app import CorpusScanWorker

    window = preset_window
    home_emr = tmp_path / "EMR"
    home_processed = tmp_path / "processed"
    older = CorpusScanWorker(home_emr, home_processed, parent=window)
    window._worker = older
    context = current_context()
    context.note_paths(tmp_path / "away-emr", tmp_path / "away-processed")
    context.note_paths(home_emr, home_processed)
    window._worker = CorpusScanWorker(home_emr, home_processed, parent=window)
    window._on_corpus_scan_ok(
        SimpleNamespace(meta={"n_scanned_cases": 1, "n_ventilated_surgeries": 1}),
        older.processed,
        older,
    )
    assert window.corpus_scan is None
    assert "previous data folder" in window.statusBar().currentMessage()


def test_direct_scan_call_applies_when_the_current_worker_is_stale(preset_window, tmp_path):
    from types import SimpleNamespace

    from src.desktop.app import CorpusScanWorker

    window = preset_window
    home_emr = tmp_path / "EMR"
    home_processed = tmp_path / "processed"
    window._worker = CorpusScanWorker(home_emr, home_processed, parent=window)
    context = current_context()
    context.note_paths(tmp_path / "away-emr", tmp_path / "away-processed")
    context.note_paths(home_emr, home_processed)
    window._on_corpus_scan_ok(
        SimpleNamespace(meta={"n_scanned_cases": 4, "n_ventilated_surgeries": 4})
    )
    assert window.corpus_scan is not None
    assert window.corpus_scan.meta["n_scanned_cases"] == 4


def test_in_flight_fetch_does_not_refill_the_cache_after_a_generation_change(tmp_path, monkeypatch):
    reset_context()
    monkeypatch.setattr("src.rules.thresholds_sha256", lambda: "ab" * 32)
    emr = tmp_path / "EMR"
    _vent_source(emr)
    (emr / "patient_ventilator.csv").write_text("PID\ncaseA\ncaseB\n")
    context = current_context()
    context.note_paths(emr, tmp_path / "processed")
    held_frames = {
        name: pd.DataFrame({"PID": ["caseA"]}) if name == "cases" else pd.DataFrame()
        for name in ("cases", "timeseries", "flags", "episodes", "events")
    }
    case_fetch._case_cache[case_fetch._case_key(emr, "caseA", "default", "ab" * 32)] = (
        held_frames,
        "ab" * 32,
    )

    def run(_pids, _preset, _emr):
        assert context.note_paths(tmp_path / "away-emr", tmp_path / "away-processed")
        return {
            "cases": pd.DataFrame({"PID": ["caseB"]}),
            "timeseries": pd.DataFrame(),
            "flags": pd.DataFrame(),
            "episodes": pd.DataFrame(),
            "events": pd.DataFrame(),
        }

    monkeypatch.setattr(case_fetch, "_run_for", run)
    result = case_fetch.fetch_cases(["caseA", "caseB"], emr=emr)
    assert result.reused == ["caseA"]
    assert result.computed == ["caseB"]
    assert set(result.cases["PID"]) == {"caseA", "caseB"}
    assert case_fetch._case_cache == {}


def test_missing_threshold_hash_does_not_bump(tmp_path):
    context = reset_context()
    context.note_paths(tmp_path / "emr", tmp_path / "processed")
    assert not context.note_preset("default", recorded=True, thresholds_sha256=None)
    assert context.generation == 0
    assert not context.note_preset("default", recorded=True, thresholds_sha256="a" * 64)
    assert context.generation == 0
    assert context.thresholds_sha256 == "a" * 64
    started = context.stamp()
    assert not context.note_preset("default", recorded=False, thresholds_sha256=None)
    assert context.accepts(started)
    assert context.thresholds_sha256 == "a" * 64
    assert context.note_preset("default", recorded=True, thresholds_sha256="b" * 64)
    assert not context.accepts(started)


def test_fetch_pins_the_emr_path_it_started_with(tmp_path, monkeypatch):
    from pathlib import Path

    reset_context()
    monkeypatch.setattr("src.rules.thresholds_sha256", lambda: "ab" * 32)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _vent_source(first)
    (first / "patient_ventilator.csv").write_text("PID\ncaseA\n")
    current = {"path": first}
    monkeypatch.setattr(case_fetch, "emr_dir", lambda: current["path"])

    def run(_pids, _preset, emr):
        assert Path(emr).resolve() == first.resolve()
        current["path"] = second
        return {
            "cases": pd.DataFrame({"PID": ["caseA"]}),
            "timeseries": pd.DataFrame(),
            "flags": pd.DataFrame(),
            "episodes": pd.DataFrame(),
            "events": pd.DataFrame(),
        }

    monkeypatch.setattr(case_fetch, "_run_for", run)
    result = case_fetch.fetch_cases(["caseA"])
    assert result.computed == ["caseA"]
    assert case_fetch._case_key(first, "caseA", "default", "ab" * 32) in case_fetch._case_cache
    assert case_fetch._case_key(second, "caseA", "default", "ab" * 32) not in case_fetch._case_cache


def test_reload_during_the_first_pipeline_runs_again_when_it_finishes(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication

    from src.desktop.app import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setattr("src.desktop.app.load_settings", lambda: {"preset": "default"})
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_corpus_scan", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_ollama_models", lambda *a, **k: None)

    def fail_load(*_args, **_kwargs):
        raise AssertionError("a running pipeline is not a missing cache")

    monkeypatch.setattr(MainWindow, "_on_load_fail", fail_load)
    window = MainWindow()
    try:
        context = reset_context()
        context.note_paths(tmp_path / "EMR", tmp_path / "processed")
        context.note_preset("default", recorded=True, thresholds_sha256="a" * 64)
        window._thresholds_sha256 = "a" * 64
        window._active_preset = "default"
        generation = context.generation

        class _Running:
            def isRunning(self) -> bool:
                return True

        window._worker = _Running()
        window._autoload_processed()
        assert window._reload_after_scan is True
        assert context.generation == generation
        assert "No processed cache" in window.statusBar().currentMessage()
    finally:
        window._worker = None
        window.close()
        window.deleteLater()
        app.processEvents()
