"""Full-EMR scan: same scores as the pipeline, bounded batches, cancellation."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.pipeline.corpus_scan import (
    CORPUS_CASES,
    CORPUS_META,
    ScanCancelled,
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
