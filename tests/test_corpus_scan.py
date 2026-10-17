"""Full-EMR scan: same scores as the pipeline, bounded batches, cancellation."""

from __future__ import annotations

import json
import hashlib
import subprocess
import os
import sys
import time
import traceback
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
    SCAN_ERROR_LOG,
    ScanCancelled,
    ShardFailure,
    _failure_code,
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


def test_saved_scan_for_another_emr_is_not_loaded(emr, tmp_path, allow_external):
    scan_corpus(emr, tmp_path / "out", cases_per_shard=20)
    other = generate_synthetic_emr(tmp_path / "other" / "EMR", n_cases=6, seed=11)
    meta_text = (tmp_path / "out" / CORPUS_META).read_text()
    assert str(emr) not in meta_text
    assert load_corpus_scan(tmp_path / "out", emr) is not None
    assert load_corpus_scan(tmp_path / "out", other) is None
    # An EMR that cannot be read right now does not count as a different one
    assert load_corpus_scan(tmp_path / "out", tmp_path / "unmounted") is not None
    # Scans written before the fingerprint existed still load
    meta = json.loads(meta_text)
    del meta["emr_fingerprint"]
    (tmp_path / "out" / CORPUS_META).write_text(json.dumps(meta))
    assert load_corpus_scan(tmp_path / "out", other) is not None


def test_an_emr_replaced_during_the_scan_writes_nothing(tmp_path, allow_external):
    from src.pipeline.corpus_scan import EmrChangedDuringScan, scan_failure_message

    emr = generate_synthetic_emr(tmp_path / "EMR", n_cases=6, seed=4)
    info = emr / "patient_information.csv"
    replaced = []

    def replace_info(message, done, total):
        if not replaced:  # the first progress call comes after the EMR was read
            info.write_text(info.read_text() + info.read_text().splitlines()[-1] + "\n")
            replaced.append(message)

    with pytest.raises(EmrChangedDuringScan) as caught:
        scan_corpus(emr, tmp_path / "out", workers=1, on_progress=replace_info)
    assert not (tmp_path / "out" / CORPUS_META).exists()
    message = scan_failure_message(caught.value, tmp_path / "out")
    assert "changed while the full-EMR scan ran" in message and str(emr) not in message


class _BusyScan:
    """Stands in for a running CorpusScanWorker."""

    def __init__(self, processed):
        self.processed = processed

    def isRunning(self):  # noqa: N802
        return True

    def wait(self):
        return True


def test_desktop_switching_folders_during_a_scan(emr, tmp_path, monkeypatch, allow_external):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from src.desktop import app as desktop_app
    from src.desktop.app import MainWindow

    QApplication.instance() or QApplication([])
    first, second = tmp_path / "first", tmp_path / "second"
    scan = scan_corpus(emr, first, cases_per_shard=20)
    other = generate_synthetic_emr(tmp_path / "other" / "EMR", n_cases=6, seed=11)
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr(desktop_app, "CorpusScanWorker", _BusyScan)
    for name, value in {"XDG_CONFIG_HOME": tmp_path / "config", "XDG_DATA_HOME": tmp_path / "data",
                        "MOVER_SKIP_SETUP": "1", "MOVER_PROCESSED_DIR": first, "MOVER_EMR_DIR": emr}.items():
        monkeypatch.setenv(name, str(value))
    win = MainWindow()
    try:
        win._load_corpus_scan()
        assert win.corpus_scan is not None
        (first / "cases.parquet").write_bytes(b"")  # a sample exists, so the reload would start a worker
        busy = _BusyScan(first)
        win._worker = busy
        win._research_request_id += 1
        win._active_research_request = win._research_request_id
        win.research.begin_question("elevated PIP", win._research_request_id)
        monkeypatch.setenv("MOVER_EMR_DIR", str(other))
        win._autoload_processed()
        assert win.corpus_scan is None and win._reload_after_scan
        # A search that started with the old scan can no longer answer
        assert win._active_research_request is None and not win.research._searching

        # A scan of the previous processed folder finishing now is not installed
        monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
        monkeypatch.setenv("MOVER_PROCESSED_DIR", str(second))
        win._on_corpus_scan_ok(scan, first)
        assert win.corpus_scan is None
        assert "previous data folder" in win.statusBar().currentMessage()
        win._on_corpus_scan_ok(scan, second)
        assert win.corpus_scan is not None

        # The reload skipped during the scan runs when it ends
        reloads = []
        monkeypatch.setattr(MainWindow, "_autoload_processed", lambda self: reloads.append(True))
        win._after_corpus_scan(busy)
        assert reloads == [True] and not win._reload_after_scan
        # but not while the window is closing
        win._reload_after_scan, win._closing = True, True
        win._after_corpus_scan(busy)
        assert reloads == [True] and not win._reload_after_scan
        win._closing = False
    finally:
        win._worker = None
        win.close()


def test_desktop_reload_of_another_emr_drops_the_old_scan(emr, tmp_path, monkeypatch, allow_external):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from src.desktop.app import MainWindow

    QApplication.instance() or QApplication([])
    processed = tmp_path / "processed"
    scan_corpus(emr, processed, cases_per_shard=20)
    other = generate_synthetic_emr(tmp_path / "other" / "EMR", n_cases=6, seed=11)
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("MOVER_SKIP_SETUP", "1")
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(processed))
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    win = MainWindow()
    try:
        win._load_corpus_scan()
        assert win.corpus_scan is not None
        monkeypatch.setenv("MOVER_EMR_DIR", str(other))
        win._autoload_processed()  # no analyzed sample here: the early-return path
        assert win.corpus_scan is None
        win.research.show_cases()
        scopes = win.research._cases_overview.scope_combo
        labels = [scopes.itemText(i) for i in range(scopes.count())]
        assert not any(label.startswith("Full-EMR scan") for label in labels)
        assert not any("indexed" in label for label in labels)
    finally:
        win.close()


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
    with pytest.raises(PipelineError, match=r"Shard \d+ could not be scored \(scan:RuntimeError\)"):
        scan_corpus(emr, tmp_path, cases_per_shard=20, workers=1)
    assert not (tmp_path / "_corpus_scan_tmp").exists()


def test_parallel_worker_error_surfaces_without_hanging(emr, tmp_path, allow_external):
    # A non-numeric pad raises TypeError inside the spawned worker process
    started = time.monotonic()
    with pytest.raises(PipelineError, match=r"Shard \d+ could not be scored \(\w+:\w+Error\)"):
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
    win.research.show_cases()
    scopes = win.research._cases_overview.scope_combo
    assert any(scopes.itemText(i).startswith("Full-EMR scan,") for i in range(scopes.count()))
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


LEAK_PATH = "/home/dr_smith/secret/emr_export_77.csv"
LEAK_PID = "SYN9f3a7c01de"
LEAK_VALUE = "98765.4321"
LEAK_TEXT = f"bad cell {LEAK_VALUE} for {LEAK_PID} in {LEAK_PATH}"
LEAKS = (LEAK_PATH, LEAK_PID, LEAK_VALUE, "dr_smith")


def _fail_first_shard(monkeypatch, exc: Exception) -> None:
    real = corpus_scan_module.process_frames
    calls: list[int] = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise exc
        return real(*args, **kwargs)

    monkeypatch.setattr(corpus_scan_module, "process_frames", flaky)


def _assert_log_holds_detail(out_dir: Path) -> None:
    log = (out_dir / SCAN_ERROR_LOG).read_text(encoding="utf-8")
    assert all(leak in log for leak in LEAKS)
    assert "Traceback" in log
    if sys.platform != "win32":
        assert (out_dir / SCAN_ERROR_LOG).stat().st_mode & 0o077 == 0


def test_failed_shard_message_and_meta_hold_a_code_not_the_exception(
    emr, tmp_path, allow_external, monkeypatch
):
    _fail_first_shard(monkeypatch, ValueError(LEAK_TEXT))
    scan_corpus(emr, tmp_path, cases_per_shard=20, workers=1)
    meta_text = (tmp_path / CORPUS_META).read_text()
    failed = json.loads(meta_text)["failed_shards"]
    assert len(failed) == 1
    assert failed[0]["error"] == (
        f"Shard {failed[0]['shard'] + 1} could not be scored (scan:ValueError). "
        f"Details are in {SCAN_ERROR_LOG}."
    )
    assert not any(leak in meta_text for leak in LEAKS)
    _assert_log_holds_detail(tmp_path)


def test_hard_shard_failure_message_is_a_code_and_detail_goes_to_the_log(
    emr, tmp_path, allow_external, monkeypatch
):
    def boom(*args, **kwargs):
        raise RuntimeError(LEAK_TEXT)

    monkeypatch.setattr(corpus_scan_module, "_score_shard", boom)
    with pytest.raises(ShardFailure) as raised:
        scan_corpus(emr, tmp_path, cases_per_shard=20, workers=1)
    shown = "".join(traceback.format_exception(raised.value))
    assert not any(leak in shown for leak in LEAKS)
    assert str(raised.value) == (
        f"Shard 1 could not be scored (scan:RuntimeError). Details are in {SCAN_ERROR_LOG}."
    )
    _assert_log_holds_detail(tmp_path)
    assert not (tmp_path / CORPUS_META).exists()


def test_all_shards_failing_still_writes_the_log(emr, tmp_path, allow_external, monkeypatch):
    def always(*args, **kwargs):
        raise ValueError(LEAK_TEXT)

    monkeypatch.setattr(corpus_scan_module, "process_frames", always)
    with pytest.raises(PipelineError, match=SCAN_ERROR_LOG) as raised:
        scan_corpus(emr, tmp_path, workers=1)
    assert not any(leak in str(raised.value) for leak in LEAKS)
    _assert_log_holds_detail(tmp_path)


def test_parallel_failure_keeps_worker_text_out_of_the_message(emr, tmp_path, allow_external):
    with pytest.raises(ShardFailure) as raised:
        scan_corpus(emr, tmp_path, cases_per_shard=20, workers=2, pad_minutes="bad")  # type: ignore[arg-type]
    assert str(raised.value) == (
        f"Shard {raised.value.args[0].split()[1]} could not be scored (clean:TypeError). "
        f"Details are in {SCAN_ERROR_LOG}."
    )
    shown = "".join(traceback.format_exception(raised.value))
    assert "_RemoteTraceback" not in shown and "TypeError" not in shown.replace("clean:TypeError", "")
    log = (tmp_path / SCAN_ERROR_LOG).read_text()
    assert "_RemoteTraceback" in log and "TypeError" in log


def test_passing_scan_writes_no_log(emr, tmp_path, allow_external):
    scan_corpus(emr, tmp_path, workers=1)
    assert not (tmp_path / SCAN_ERROR_LOG).exists()


def test_log_appends_with_timestamps(emr, tmp_path, allow_external, monkeypatch):
    for _ in range(2):
        with monkeypatch.context() as patched:
            _fail_first_shard(patched, ValueError(LEAK_TEXT))
            scan_corpus(emr, tmp_path, cases_per_shard=20, workers=1)
    log = (tmp_path / SCAN_ERROR_LOG).read_text()
    assert log.count("could not be scored (scan:ValueError)") == 2
    assert log.count(LEAK_PATH) >= 2
    assert log.startswith("[20")


@pytest.mark.parametrize(
    ("module", "function", "stage"),
    [
        ("load", "load_ventilator", "load"),
        ("clean", "clean_vitals", "clean"),
        ("merge", "merge_vent_vitals", "merge"),
        ("features", "add_features", "features"),
        ("phases", "assign_phases", "features"),
        ("flags", "flag_anomalies", "flags"),
        ("run", "process_frames", "scan"),
    ],
)
@pytest.mark.parametrize("exc_type", [KeyError, FileNotFoundError, MemoryError])
def test_failure_codes_name_the_pipeline_stage_and_exception_class(
    tmp_path, module, function, stage, exc_type
):
    source = f"def {function}():\n    raise exc_type({LEAK_TEXT!r})\n"
    namespace = {"exc_type": exc_type}
    exec(compile(source, f"/opt/app/src/pipeline/{module}.py", "exec"), namespace)
    try:
        namespace[function]()
    except exc_type as exc:
        trace = "".join(traceback.format_exception(exc))
        assert _failure_code(trace, exc) == f"{stage}:{exc_type.__name__}"


def test_failure_code_without_a_pipeline_frame_is_scan():
    try:
        raise MemoryError(LEAK_TEXT)
    except MemoryError as exc:
        trace = "".join(traceback.format_exception(exc))
        assert _failure_code(trace, exc) == "scan:MemoryError"


def test_failure_code_reads_windows_style_paths():
    trace = 'Traceback:\n  File "C:\\app\\src\\pipeline\\clean.py", line 4, in clean_vitals\n'
    assert _failure_code(trace, KeyError("x")) == "clean:KeyError"


def test_desktop_scan_failure_shows_only_the_code(emr, tmp_path, monkeypatch, allow_external):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    processed = tmp_path / "processed"
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(processed))
    monkeypatch.setenv("MOVER_SKIP_SETUP", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialogs: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: dialogs.append(a[2]))
    from src.desktop import app as desktop_app

    monkeypatch.setattr(
        desktop_app.MainWindow, "_refresh_ollama_models", lambda self, **kwargs: None
    )
    win = desktop_app.MainWindow()

    def fail(*args, **kwargs):
        raise ValueError(LEAK_TEXT)

    monkeypatch.setattr(corpus_scan_module, "scan_corpus", fail)
    win._run_corpus_scan()
    deadline = time.monotonic() + 60
    while not dialogs and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert dialogs
    shown = [dialogs[0], win.statusBar().currentMessage(), win.statusBar().toolTip()]
    assert dialogs[0] == (
        f"The full-EMR scan could not finish (scan:ValueError). Details are in {SCAN_ERROR_LOG}."
    )
    for text in shown:
        assert not any(leak in text for leak in LEAKS)
    _assert_log_holds_detail(processed)
    win.close()


def test_desktop_failed_shards_show_only_the_code(emr, tmp_path, monkeypatch, allow_external):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel

    QApplication.instance() or QApplication([])
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_SKIP_SETUP", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from src.desktop import app as desktop_app

    monkeypatch.setattr(
        desktop_app.MainWindow, "_refresh_ollama_models", lambda self, **kwargs: None
    )
    _fail_first_shard(monkeypatch, ValueError(LEAK_TEXT))
    result = scan_corpus(emr, tmp_path / "scan", cases_per_shard=20, workers=1)
    win = desktop_app.MainWindow()
    win._on_corpus_scan_ok(result)
    error = result.meta["failed_shards"][0]["error"]
    assert win.statusBar().toolTip() == error
    texts = [win.statusBar().currentMessage(), win.statusBar().toolTip()]
    for widget in win.research.findChildren(QLabel):
        texts.append(widget.text())
    for combo in win.research.findChildren(QComboBox):
        texts.extend(combo.itemText(i) for i in range(combo.count()))
    assert any("could not be scored" in text for text in texts)
    for text in texts:
        assert not any(leak in text for leak in LEAKS)
    win.close()
