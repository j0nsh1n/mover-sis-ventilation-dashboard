"""Full-EMR scan: same scores as the pipeline, bounded batches, cancellation."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

from src.guardrails.exceptions import PipelineError
from src.pipeline import corpus_scan as corpus_scan_module
from src.pipeline.corpus_scan import (
    CORPUS_CASES,
    CORPUS_EPISODES,
    CORPUS_META,
    MAX_DEFAULT_WORKERS,
    ScanCancelled,
    default_workers,
    load_corpus_scan,
    scan_corpus,
)
from src.pipeline.run import run_pipeline
from src.pipeline.synthetic import generate_synthetic_emr


@pytest.fixture(scope="module")
def emr(tmp_path_factory):
    return generate_synthetic_emr(tmp_path_factory.mktemp("scan") / "EMR", n_cases=40, seed=5)


@pytest.fixture
def allow_external(monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")


def test_scan_matches_pipeline_scores_in_small_batches(emr, tmp_path, allow_external):
    scan = scan_corpus(emr, tmp_path / "scan", cases_per_shard=7)
    sample = run_pipeline(
        emr_dir=emr, output_dir=tmp_path / "sample", n_cases=40, write_sample_csv=False
    )
    cols = ["anomaly_score", "n_warn", "n_critical", "n_composite", "n_minutes", "top_rules"]
    a = scan.cases.set_index("PID").sort_index()[cols]
    b = sample["cases"].set_index("PID").sort_index()[cols]
    assert a.equals(b)
    assert len(scan.episodes) == len(sample["episodes"])
    assert scan.meta["n_shards"] > 1


def test_scan_covers_ventilated_surgeries_only(emr, tmp_path, allow_external):
    truth = json.loads((emr / "synthetic_truth.json").read_text())
    ventilated = {p for p, c in truth["cases"].items() if c["ventilated"]}
    scan = scan_corpus(emr, tmp_path, cases_per_shard=10)
    assert set(scan.cases["PID"]) == ventilated
    assert scan.meta["n_indexed_surgeries"] == len(truth["cases"])
    assert scan.meta["n_ventilated_surgeries"] == len(ventilated)
    assert scan.meta["n_scanned_cases"] == len(ventilated)
    assert not (tmp_path / "_corpus_scan_tmp").exists()


def test_saved_scan_round_trips(emr, tmp_path, allow_external):
    scan_corpus(emr, tmp_path, cases_per_shard=20)
    loaded = load_corpus_scan(tmp_path)
    assert loaded is not None
    assert loaded.meta["scope"] == "full_emr_scan"
    assert len(loaded.cases) == loaded.meta["n_scanned_cases"]
    assert load_corpus_scan(tmp_path / "missing") is None


def test_cancel_keeps_previous_scan(emr, tmp_path, allow_external):
    scan_corpus(emr, tmp_path, cases_per_shard=20)
    before = (tmp_path / CORPUS_META).read_text()
    with pytest.raises(ScanCancelled):
        scan_corpus(emr, tmp_path, cases_per_shard=5, should_stop=lambda: True)
    assert (tmp_path / CORPUS_META).read_text() == before
    assert (tmp_path / CORPUS_CASES).is_file()
    assert not (tmp_path / "_corpus_scan_tmp").exists()
    assert isinstance(pd.read_parquet(tmp_path / CORPUS_CASES), pd.DataFrame)


TIMING_KEYS = {"created_at", "seconds", "seconds_split", "seconds_score", "workers"}


def test_parallel_scan_matches_serial_scan(emr, tmp_path, allow_external):
    serial = scan_corpus(emr, tmp_path / "serial", cases_per_shard=7, workers=1)
    parallel = scan_corpus(emr, tmp_path / "parallel", cases_per_shard=7, workers=2)
    assert serial.meta["workers"] == 1
    assert parallel.meta["workers"] == 2
    pd.testing.assert_frame_equal(serial.cases, parallel.cases)
    pd.testing.assert_frame_equal(serial.episodes, parallel.episodes)
    assert {k: v for k, v in serial.meta.items() if k not in TIMING_KEYS} == {
        k: v for k, v in parallel.meta.items() if k not in TIMING_KEYS
    }
    for name in (CORPUS_CASES, CORPUS_EPISODES):
        pd.testing.assert_frame_equal(
            pd.read_parquet(tmp_path / "serial" / name),
            pd.read_parquet(tmp_path / "parallel" / name),
        )
    assert not (tmp_path / "parallel" / "_corpus_scan_tmp").exists()


def test_parallel_progress_is_reported_from_the_calling_thread(emr, tmp_path, allow_external):
    import threading

    calls: list[tuple[str, int, int, int]] = []
    caller = threading.get_ident()
    scan_corpus(
        emr,
        tmp_path,
        cases_per_shard=10,
        workers=2,
        on_progress=lambda m, d, t: calls.append((m, d, t, threading.get_ident())),
    )
    assert {c[3] for c in calls} == {caller}
    totals = [c for c in calls if c[2] > 0]
    assert totals[-1][1] == totals[-1][2]
    dones = [c[1] for c in totals]
    assert dones == sorted(dones)
    assert any("CPU cores" in c[0] for c in calls)


def test_parallel_cancel_keeps_previous_scan_and_stops_workers(emr, tmp_path, allow_external):
    scan_corpus(emr, tmp_path, cases_per_shard=20, workers=1)
    before = (tmp_path / CORPUS_META).read_text()
    started = time.monotonic()
    scoring = {"started": False}

    def on_progress(message: str, _done: int, _total: int) -> None:
        if "CPU cores" in message:
            scoring["started"] = True

    with pytest.raises(ScanCancelled):
        scan_corpus(
            emr,
            tmp_path,
            cases_per_shard=2,
            workers=2,
            on_progress=on_progress,
            should_stop=lambda: scoring["started"],
        )
    assert time.monotonic() - started < 60
    assert (tmp_path / CORPUS_META).read_text() == before
    assert not (tmp_path / "_corpus_scan_tmp").exists()


def test_worker_error_names_the_shard(emr, tmp_path, allow_external, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(corpus_scan_module, "_score_shard", boom)
    with pytest.raises(PipelineError, match=r"shard \d+ failed.*synthetic failure"):
        scan_corpus(emr, tmp_path, cases_per_shard=20, workers=1)
    assert not (tmp_path / "_corpus_scan_tmp").exists()


def test_parallel_worker_error_surfaces_without_hanging(emr, tmp_path, allow_external):
    # A non-numeric pad raises TypeError inside the spawned worker process
    started = time.monotonic()
    with pytest.raises(PipelineError, match=r"shard \d+ failed: \w+Error"):
        scan_corpus(emr, tmp_path, cases_per_shard=20, workers=2, pad_minutes="bad")  # type: ignore[arg-type]
    assert time.monotonic() - started < 120
    assert not (tmp_path / "_corpus_scan_tmp").exists()


def test_default_workers_bounds(monkeypatch):
    monkeypatch.setattr(corpus_scan_module.os, "cpu_count", lambda: 1)
    assert default_workers(50) == 1
    monkeypatch.setattr(corpus_scan_module.os, "cpu_count", lambda: 64)
    assert default_workers(50) == MAX_DEFAULT_WORKERS
    assert default_workers(3) == 3
    monkeypatch.setattr(corpus_scan_module.os, "cpu_count", lambda: None)
    assert default_workers(50) == 1
    monkeypatch.setattr(corpus_scan_module.os, "cpu_count", lambda: 4)
    assert default_workers(50) == 3


def test_invalid_worker_count_is_rejected(emr, tmp_path, allow_external):
    with pytest.raises(PipelineError, match="workers"):
        scan_corpus(emr, tmp_path, workers=0)


def test_scan_module_does_not_import_qt():
    code = (
        "import sys; import src.pipeline.corpus_scan; "
        "sys.exit(any(m.startswith('PySide6') for m in sys.modules))"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_frozen_entry_point_calls_freeze_support_first():
    entry = (Path(__file__).resolve().parents[1] / "packaging" / "entrypoint.py").read_text()
    guard = entry.index('if __name__ == "__main__":')
    assert entry.index("multiprocessing.freeze_support()", guard) < entry.index("main()", guard)


def test_desktop_scan_action_feeds_copilot_session(emr, tmp_path, monkeypatch, allow_external):
    import os
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_SKIP_SETUP", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    from src.desktop import app as desktop_app

    monkeypatch.setattr(
        desktop_app.MainWindow, "_refresh_ollama_models", lambda self, **kwargs: None
    )
    win = desktop_app.MainWindow()
    win._run_corpus_scan()
    deadline = time.monotonic() + 120
    while win.corpus_scan is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
    assert win.corpus_scan is not None
    assert "full-EMR scan:" in win.lbl_corpus.text()
    session = win._make_session_snapshot()
    assert session.has_corpus_scan()
    assert session.corpus_meta["n_scanned_cases"] == len(session.corpus)
    win.close()
