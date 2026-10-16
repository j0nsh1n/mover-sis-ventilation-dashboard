"""Benchmark CLI runs real synthetic scans into isolated calibration folders."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.pipeline.synthetic import generate_synthetic_emr


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bench_scan.sh requires bash")
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_scan.sh"


def test_bench_scan_reports_missing_emr(tmp_path):
    missing = tmp_path / "missing EMR"
    run = subprocess.run(["bash", str(SCRIPT), str(missing)], capture_output=True, text=True)
    assert run.returncode == 2
    assert f"EMR folder is missing: {missing}" in run.stderr


def test_bench_scan_runs_each_requested_worker_count(tmp_path):
    root = tmp_path / "benchmark checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
    (root / "src").symlink_to(SCRIPT.parents[1] / "src", target_is_directory=True)
    emr = generate_synthetic_emr(tmp_path / "synthetic EMR", n_cases=4, seed=5)
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
           "MOVER_ALLOW_EXTERNAL_OUTPUT": "1"}
    run = subprocess.run(
        ["bash", str(root / "scripts" / SCRIPT.name), str(emr), "1", "2"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    benches = list((root / "calibration").glob("bench-*"))
    assert len(benches) == 1
    bench = benches[0]
    report = (bench / "bench.md").read_text()
    assert "Host cores:" in report
    assert "src.pipeline.corpus_scan" in report
    assert "--emr-dir" in report and "--workers" in report
    for workers in (1, 2):
        meta = json.loads((bench / f"w{workers}" / "corpus_meta.json").read_text())
        assert meta["workers_requested"] == workers
        assert meta["workers"] == 1
        row = [workers, meta["workers"], meta["n_shards"], meta["seconds"],
               meta["peak_rss_mb"]["parent"], meta["peak_rss_mb"]["largest_worker"],
               meta["n_scanned_cases"]]
        assert "| " + " | ".join(map(str, row)) + " |" in report
    assert not (root / "data" / "processed").exists()
