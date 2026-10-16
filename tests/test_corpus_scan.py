"""Full-EMR scan: same scores as the pipeline, bounded batches, cancellation."""

from __future__ import annotations

import json
import hashlib
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
from src.config import load_thresholds
from src.runtime_paths import thresholds_path


def test_scan_records_file_hash_and_keeps_merged_hash(emr, tmp_path, allow_external):
    scan_corpus(emr, tmp_path, preset="strict", workers=1)
    meta = json.loads((tmp_path / CORPUS_META).read_text())
    assert meta["thresholds_sha256"] == hashlib.sha256(thresholds_path().read_bytes()).hexdigest()
    merged = json.dumps(load_thresholds("strict"), sort_keys=True, default=str)
    assert meta["thresholds_hash"] == hashlib.sha256(merged.encode()).hexdigest()[:16]


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
    cols = [
        "anomaly_score", "n_warn", "n_critical", "n_composite", "n_minutes", "top_rules",
        "phase_source",
    ]
    a = scan.cases.set_index("PID").sort_index()[cols]
    b = sample["cases"].set_index("PID").sort_index()[cols]
    assert a.equals(b)
    assert len(scan.episodes) == len(sample["episodes"])
    # Procedure events reach every shard: same phase labels on the episodes
    key = ["PID", "rule_id", "t_start_min"]
    a_ep = scan.episodes.sort_values(key).reset_index(drop=True)[key + ["phase", "phase_end"]]
    b_ep = sample["episodes"].sort_values(key).reset_index(drop=True)[key + ["phase", "phase_end"]]
    assert a_ep.equals(b_ep)
    assert set(a["phase_source"]) == {"events"}
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


TIMING_KEYS = {"created_at", "seconds", "seconds_split", "seconds_score", "workers", "workers_requested", "peak_rss_mb"}


@pytest.mark.parametrize("requested, used", [(1, 1), (4, 1), (None, 1)])
def test_scan_measurements_and_phase_source_counts(emr, tmp_path, allow_external, requested, used):
    progress = []
    result = scan_corpus(emr, tmp_path, workers=requested, on_progress=lambda msg, *_: progress.append(msg))
    meta = json.loads((tmp_path / CORPUS_META).read_text())
    assert meta == result.meta
    assert meta["workers_requested"] == requested
    assert meta["workers"] == used
    assert meta["n_shards"] == 1
    assert meta["seconds"] >= meta["seconds_score"] >= 0
    assert set(meta["peak_rss_mb"]) == {"parent", "largest_worker"}
    if sys.platform == "win32":
        assert meta["peak_rss_mb"] == {"parent": None, "largest_worker": None}
    else:
        assert meta["peak_rss_mb"]["parent"] > 0
        assert meta["peak_rss_mb"]["largest_worker"] >= 0
    counts = meta["cases_by_phase_source"]
    assert set(counts) == {"events", "mixed", "fallback"}
    assert sum(counts.values()) == meta["n_scanned_cases"]
    assert counts["events"] == len(result.cases)
    assert not any(pid in json.dumps(counts) for pid in result.cases["PID"])
    if requested == 4:
        assert any("Requested 4 workers; using 1" in msg for msg in progress)


@pytest.mark.parametrize("source", ["events", "mixed"])
def test_scan_counts_event_mixed_and_fallback_cases(synthetic_emr, tmp_path, allow_external, source):
    path = synthetic_emr / "patient_procedure_events.csv"
    events = pd.read_csv(path)
    events.loc[events["Event_name"].eq("Extubation"), "Event_time"] = "2016-06-01 08:38:00"
    if source == "mixed":
        events = events[events["Event_name"].eq("Intubation")]
    events.to_csv(path, index=False)
    result = scan_corpus(synthetic_emr, tmp_path / "scan", workers=1)
    expected = {"events": 0, "mixed": 0, "fallback": 1}
    expected[source] = 1
    assert result.meta["cases_by_phase_source"] == expected
    assert sum(expected.values()) == result.meta["n_scanned_cases"]


@pytest.mark.parametrize("platform, scale", [("linux", 1024), ("darwin", 1024 * 1024)])
def test_peak_rss_units(monkeypatch, platform, scale):
    from types import SimpleNamespace

    calls = []

    def usage(who):
        calls.append(who)
        return SimpleNamespace(ru_maxrss=(12 if who == 0 else 34) * scale)

    monkeypatch.setitem(sys.modules, "resource", SimpleNamespace(
        RUSAGE_SELF=0, RUSAGE_CHILDREN=-1, getrusage=usage,
    ))
    monkeypatch.setattr(sys, "platform", platform)
    assert corpus_scan_module._peak_rss_mb() == {"parent": 12.0, "largest_worker": 34.0}
    assert calls == [0, -1]


def test_peak_rss_without_resource(monkeypatch):
    monkeypatch.setitem(sys.modules, "resource", None)
    assert corpus_scan_module._peak_rss_mb() == {"parent": None, "largest_worker": None}


def test_parallel_scan_matches_serial_scan(emr, tmp_path, allow_external):
    serial = scan_corpus(emr, tmp_path / "serial", cases_per_shard=7, workers=1)
    parallel = scan_corpus(emr, tmp_path / "parallel", cases_per_shard=7, workers=2)
    assert serial.meta["workers"] == 1
    assert parallel.meta["workers"] == 2
    assert parallel.meta["workers_requested"] == 2
    if sys.platform != "win32":
        assert parallel.meta["peak_rss_mb"]["largest_worker"] > 0
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


@pytest.mark.parametrize("workers", [1, None])
def test_single_shard_cancel_during_scoring_keeps_previous_files(emr, tmp_path, allow_external, workers):
    scan_corpus(emr, tmp_path, workers=1)
    before = {name: (tmp_path / name).read_bytes() for name in (CORPUS_META, CORPUS_CASES, CORPUS_EPISODES)}
    scoring_started = None

    def on_progress(message, _done, _total):
        nonlocal scoring_started
        if "CPU cores" in message:
            scoring_started = time.monotonic()

    def should_stop():
        return scoring_started is not None and time.monotonic() - scoring_started >= 0.2

    with pytest.raises(ScanCancelled, match="previous scan was kept"):
        scan_corpus(emr, tmp_path, workers=workers, on_progress=on_progress, should_stop=should_stop)
    assert scoring_started is not None
    assert time.monotonic() - scoring_started < 2
    assert {name: (tmp_path / name).read_bytes() for name in before} == before
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


def test_cancel_after_scoring_before_outputs_keeps_previous(emr, tmp_path, allow_external, monkeypatch):
    scan_corpus(emr, tmp_path, workers=1)
    before = {name: (tmp_path / name).read_bytes() for name in (CORPUS_META, CORPUS_CASES, CORPUS_EPISODES)}
    finished = False
    run_parallel = corpus_scan_module._run_parallel

    def finish_scoring(*args, **kwargs):
        nonlocal finished
        result = run_parallel(*args, **kwargs)
        finished = True
        return result

    monkeypatch.setattr(corpus_scan_module, "_run_parallel", finish_scoring)
    with pytest.raises(ScanCancelled, match="previous scan was kept"):
        scan_corpus(emr, tmp_path, workers=1, should_stop=lambda: finished)
    assert {name: (tmp_path / name).read_bytes() for name in before} == before
