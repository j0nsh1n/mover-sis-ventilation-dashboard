"""Full-EMR scan profile: mergeable shard histograms, suppression, no identifiers."""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from src.config import load_thresholds
from src.pipeline import corpus_scan as corpus_scan_module
from src.pipeline.corpus_scan import (
    CORPUS_CASES,
    CORPUS_META,
    CORPUS_PROFILE_JSON,
    CORPUS_PROFILE_MD,
    scan_corpus,
)
from src.pipeline.run import run_pipeline
from src.pipeline.scan_profile import (
    N_BINS,
    SCAN_PERCENTILES,
    binned_percentile,
    build_corpus_profile,
    merge_histograms,
    render_corpus_profile_md,
    shard_histogram,
    signal_ranges,
)
from src.pipeline.synthetic import generate_synthetic_emr

THRESHOLDS = load_thresholds("default")


@pytest.fixture(scope="module")
def emr(tmp_path_factory):
    return generate_synthetic_emr(tmp_path_factory.mktemp("scanprofile") / "EMR", n_cases=40, seed=5)


@pytest.fixture
def allow_external(monkeypatch):
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")


@pytest.fixture(scope="module")
def whole(emr, tmp_path_factory):
    """The sample pipeline's frames for every case: the whole-data reference."""
    out = tmp_path_factory.mktemp("whole")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
        return run_pipeline(emr_dir=emr, output_dir=out, n_cases=40, write_sample_csv=False)


def _histogram(frames: dict, pids: set[str] | None = None) -> dict:
    ts, flags, episodes = frames["timeseries"], frames["flags"], frames["episodes"]
    if pids is not None:
        ts = ts[ts["PID"].isin(pids)]
        flags = flags[flags["PID"].isin(pids)]
        episodes = episodes[episodes["PID"].isin(pids)]
    return shard_histogram(ts, flags, episodes, THRESHOLDS)


def _assert_same(a, b, path: str = "") -> None:
    if isinstance(a, dict):
        assert a.keys() == b.keys(), path
        for key in a:
            _assert_same(a[key], b[key], f"{path}/{key}")
    else:
        assert np.array_equal(a, b), path


@pytest.mark.parametrize("n_parts", [1, 3, 7])
def test_merged_shard_histograms_equal_whole_data_histogram(whole, n_parts):
    pids = np.array(sorted(whole["timeseries"]["PID"].unique()))
    parts = [set(chunk) for chunk in np.array_split(pids, n_parts)]
    merged = merge_histograms([_histogram(whole, p) for p in parts])
    _assert_same(merged, _histogram(whole))


@pytest.mark.parametrize("n_parts", [1, 3, 7])
def test_merged_percentiles_equal_whole_data_binned_and_stay_within_a_bin_of_exact(whole, n_parts):
    ts = whole["timeseries"]
    pids = np.array(sorted(ts["PID"].unique()))
    merged = merge_histograms(
        [_histogram(whole, set(chunk)) for chunk in np.array_split(pids, n_parts)]
    )
    whole_profile = build_corpus_profile(_histogram(whole), THRESHOLDS, min_cell=1)
    merged_profile = build_corpus_profile(merged, THRESHOLDS, min_cell=1)
    assert merged_profile == whole_profile

    observed = ts[[c for c in ("TV", "RR", "PEEP", "PIP", "ETCO2", "FIO2", "HR", "SPO2") if c in ts]]
    observed = observed.notna().any(axis=1)
    checked = 0
    for col, stats in merged_profile["signals"].items():
        values = pd.to_numeric(ts.loc[observed, col], errors="coerce").dropna().to_numpy()
        low, high = stats["range"]
        values = values[(values >= low) & (values <= high)]
        for p in SCAN_PERCENTILES:
            estimate = stats[f"p{p}"]
            if len(values) and isinstance(estimate, float):
                assert abs(estimate - np.percentile(values, p)) <= stats["bin_width"] + 1e-9, (col, p)
                checked += 1
    assert checked > 40


def test_values_outside_the_range_land_in_under_and_over_not_in_a_bin():
    low, high = signal_ranges(THRESHOLDS)["PIP"]
    values = [low - 5, low - 0.01, low, high, high + 0.01, high + 20, (low + high) / 2]
    ts = pd.DataFrame(
        {
            "PID": ["A"] * len(values),
            "Obs_time": pd.date_range("2024-01-01", periods=len(values), freq="min"),
            "PIP": values,
            "phase": "maintenance",
            "phase_source": "events",
        }
    )
    hist = shard_histogram(ts, pd.DataFrame(), pd.DataFrame(), THRESHOLDS)
    pip = hist["signals"]["PIP"]
    assert (pip["under"], pip["over"]) == (2, 2)
    assert int(pip["bins"].sum()) == 3 and pip["minutes"] == 7
    assert pip["bins"][0] == 1 and pip["bins"][N_BINS - 1] == 1  # the edges stay inside

    assert binned_percentile({**pip, "bins": np.zeros(N_BINS, dtype=np.int64)}, low, high, 5) == f"<{low:g}"
    assert binned_percentile({**pip, "bins": np.zeros(N_BINS, dtype=np.int64)}, low, high, 95) == f">{high:g}"
    profile = build_corpus_profile(hist, THRESHOLDS, min_cell=1)
    assert profile["signals"]["PIP"]["below_range_minutes"] == 2
    assert profile["signals"]["PIP"]["above_range_minutes"] == 2


def _hist(rule_cases: int, signal_cases: int, n_cases: int = 100) -> dict:
    bins = np.zeros(N_BINS, dtype=np.int64)
    bins[10] = 500
    return {
        "n_cases": n_cases,
        "observed_minutes": 1000,
        "signals": {
            "PIP": {"bins": bins, "under": 0, "over": 0, "minutes": 500, "cases": signal_cases}
        },
        "rules": {"pip_high": {"minutes": 7, "cases": rule_cases, "episodes": 3}},
        "phases": {
            "maintenance": {"minutes": 990, "cases": 100},
            "emergence": {"minutes": 10, "cases": 4},
        },
        "phase_source": {"events": 97, "mixed": 3},
    }


def test_small_cells_are_suppressed():
    profile = build_corpus_profile(_hist(rule_cases=4, signal_cases=5), THRESHOLDS)
    rule = profile["rules"]["pip_high"]
    assert rule["cases_flagged"] == "<11"
    assert rule["flagged_minutes"] is None and rule["episodes"] is None
    assert rule["cases_flagged_pct"] is None
    assert profile["signals"]["PIP"] == {
        "unit": "cmH2O",
        "range": [0.0, 50.0],
        "bin_width": 0.5,
        "n_cases": "<11",
        "suppressed": True,
    }
    assert profile["phases"]["emergence"] == {"cases": "<11", "minutes": None, "minutes_pct": None}
    assert profile["phase_source"]["mixed"] == {"cases": "<11", "cases_pct": None}
    # the complement of a small cell is withheld as well
    assert profile["phase_source"]["events"]["cases_pct"] is None
    md = render_corpus_profile_md(profile)
    assert "<11" in md and "suppressed" in md
    assert "| pip_high | <11 | suppressed | suppressed | suppressed |" in md


def test_large_cells_are_shown():
    profile = build_corpus_profile(_hist(rule_cases=40, signal_cases=60), THRESHOLDS)
    assert profile["rules"]["pip_high"]["cases_flagged"] == 40
    assert profile["rules"]["pip_high"]["flagged_minutes"] == "<11"
    assert profile["signals"]["PIP"]["n_cases"] == 60
    assert profile["signals"]["PIP"]["p50"] == pytest.approx(5.25, abs=0.01)


IDENTIFIER = re.compile(
    r"\bSYN[0-9a-f]+\b|\b[0-9a-f]{16}\b|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}(?::\d{2})?\b",
    re.IGNORECASE,
)


def test_scan_outputs_hold_no_pid_or_timestamp(emr, tmp_path, allow_external):
    scan = scan_corpus(emr, tmp_path, cases_per_shard=12, workers=1)
    pids = set(scan.cases["PID"])
    for name in (CORPUS_PROFILE_JSON, CORPUS_PROFILE_MD):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert not IDENTIFIER.findall(text), name
        assert not any(pid in text for pid in pids), name
    assert "n_cases" in json.loads((tmp_path / CORPUS_PROFILE_JSON).read_text())["coverage"]


def test_profile_does_not_depend_on_the_number_of_shards(emr, tmp_path, allow_external, whole):
    n_ventilated = None
    profiles = []
    for k in (1, 3, 7):
        if n_ventilated is None:
            n_ventilated = scan_corpus(emr, tmp_path / "probe", workers=1).meta["n_ventilated_surgeries"]
        out = tmp_path / f"k{k}"
        meta = scan_corpus(emr, out, cases_per_shard=-(-n_ventilated // k), workers=1).meta
        assert meta["n_shards"] == k
        profiles.append(json.loads((out / CORPUS_PROFILE_JSON).read_text()))
    assert profiles[0] == profiles[1] == profiles[2]
    expected = build_corpus_profile(_histogram(whole), THRESHOLDS)
    assert profiles[0] == json.loads(json.dumps(expected))
    # the parallel path gives the same file
    out = tmp_path / "parallel"
    scan_corpus(emr, out, cases_per_shard=-(-n_ventilated // 3), workers=2)
    assert json.loads((out / CORPUS_PROFILE_JSON).read_text()) == profiles[0]


def test_failed_shard_is_left_out_and_counted(emr, tmp_path, allow_external, monkeypatch):
    real = corpus_scan_module.process_frames
    calls: list[int] = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("bad cell")
        return real(*args, **kwargs)

    monkeypatch.setattr(corpus_scan_module, "process_frames", flaky)
    scan = scan_corpus(emr, tmp_path, cases_per_shard=12, workers=1)
    meta = json.loads((tmp_path / CORPUS_META).read_text())
    failed = meta["failed_shards"]
    assert len(failed) == 1
    profile = json.loads((tmp_path / CORPUS_PROFILE_JSON).read_text())
    scanned = int(scan.cases["PID"].nunique())
    assert profile["coverage"]["n_cases"] == scanned
    assert profile["coverage"]["failed_shards"] == 1
    left_out = failed[0]["n_pids"]
    assert meta["cases_left_out_by_failed_shards"] == left_out
    assert profile["coverage"]["cases_left_out_by_failed_shards"] == (
        "<11" if 0 < left_out < 11 else left_out
    )
    assert scanned + left_out == meta["n_ventilated_surgeries"]
    md = (tmp_path / CORPUS_PROFILE_MD).read_text()
    assert "1 shard(s) could not be scored" in md
    assert (tmp_path / CORPUS_CASES).is_file()
