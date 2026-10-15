"""Catalog coverage and agreement with the unchanged flag evaluator."""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest
import yaml

import src.rules as rules
from src.config import load_thresholds, THRESHOLDS_PATH
from src.guardrails.exceptions import ConfigValidationError
from src.llm.tools import SessionState, tool_rule_reference
from src.pipeline.flags import flag_anomalies, COMPOSITE_RULES
from src.pipeline.profile import profile_processed, render_markdown
from src.rules import load_rule_catalog


@pytest.mark.parametrize("module", [
    "src.rules", "src.llm.tools", "src.pipeline.profile",
    "src.guardrails.validate_config", "src.dashboard.app", "src.desktop.app",
])
def test_cold_import(module, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", MOVER_CONFIG_DIR=str(tmp_path))
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), env.get("PYTHONPATH"))))
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("preset", ["default", "strict", "lenient"])
def test_catalog_matches_yaml_in_both_directions(preset):
    cfg = load_thresholds(preset)
    catalog = load_rule_catalog(preset)
    assert catalog.preset == preset
    assert len(catalog.specs) == len({spec.id for spec in catalog.specs}) == 25
    assert [spec.id for spec in catalog.specs] == list(cfg["rules"])
    assert set(rules.RULE_METADATA) == set(cfg["rules"])
    for spec in catalog.specs:
        source = cfg["rules"][spec.id]
        assert spec.skip_phases == tuple(source.get("skip_phases", []))
        assert spec.description == source["description"]
        assert spec.label and spec.kind and spec.signals and spec.severities
        assert spec.details() == {
            **{key: value for key, value in source.items() if key not in ("description", "unit", "skip_phases")},
            "unit": spec.unit, "skip_phases": list(spec.skip_phases),
        }
        if "unit" in source:
            assert spec.unit == source["unit"]
    assert {spec.id for spec in catalog.specs if spec.kind == "composite"} == COMPOSITE_RULES


@pytest.mark.parametrize("preset", ["default", "strict", "lenient"])
def test_every_numeric_parameter_has_public_unit_metadata(preset):
    for spec in load_rule_catalog(preset).specs:
        for name in spec.params:
            metadata = rules.PARAMETER_METADATA[name]
            assert metadata.label
            assert isinstance(metadata.unit, str) or metadata.unit is None
            assert isinstance(metadata.unit if metadata.unit is not None else spec.unit, str)


def test_presets_apply_exactly_the_yaml_overlays():
    yaml_cfg = yaml.safe_load(THRESHOLDS_PATH.read_text())
    baseline = load_rule_catalog("default")
    for preset, overlay in yaml_cfg["presets"].items():
        catalog = load_rule_catalog(preset)
        for spec in catalog.specs:
            expected = dict(baseline.get(spec.id).params)
            expected.update(overlay.get(spec.id, {}))
            assert dict(spec.params) == expected
            assert (spec.params != baseline.get(spec.id).params) == (spec.id in overlay)


@pytest.mark.parametrize("query", ["PIP_HIGH", "ETCO2", "cmH2O", "slope", "induction", "emergence", "window_min", "tidal volume"])
def test_search_covers_metadata_and_parameter_names(query):
    catalog = load_rule_catalog()
    hits = catalog.search(query)
    assert hits
    assert hits == catalog.search(query.swapcase())
    assert catalog.search("") == catalog.specs == catalog.search("  ")
    assert catalog.search("no-such-rule-or-signal") == ()
    if query == "ETCO2":
        assert {"etco2_high", "etco2_low", "etco2_zero_vent"} <= {spec.id for spec in hits}
    if query == "slope":
        assert catalog.get("pip_rising") in hits
    if query == "emergence":
        assert hits == tuple(
            spec for spec in catalog.specs
            if "emergence" in " ".join((
                spec.id, spec.label, spec.description, spec.kind, *spec.signals,
                spec.unit, *spec.params, *spec.skip_phases,
            )).casefold()
        )


def test_catalog_is_deeply_read_only():
    catalog = load_rule_catalog()
    with pytest.raises(FrozenInstanceError):
        catalog.preset = "strict"
    with pytest.raises(FrozenInstanceError):
        catalog.get("pip_high").unit = "wrong"
    with pytest.raises(TypeError):
        catalog.get("pip_high").params["warn"] = 100
    with pytest.raises(TypeError):
        catalog.get("agent_drift").params["delta_vol_pct"]["S"] = 100
    details = catalog.get("agent_drift").details()
    details["delta_vol_pct"]["S"] = 100
    assert catalog.get("agent_drift").params["delta_vol_pct"]["S"] == 0.5
    with pytest.raises(KeyError, match="unknown_rule"):
        catalog.get("unknown_rule")


@pytest.mark.parametrize("missing_side", ["metadata", "rule"])
def test_missing_metadata_or_rule_names_the_rule(monkeypatch, missing_side):
    if missing_side == "metadata":
        monkeypatch.delitem(rules.RULE_METADATA, "pip_high")
    else:
        cfg = load_thresholds()
        del cfg["rules"]["pip_high"]
        monkeypatch.setattr("src.config.load_thresholds", lambda preset: cfg)
    with pytest.raises(ConfigValidationError, match="pip_high"):
        load_rule_catalog()


@pytest.mark.parametrize("rule_id", [
    rule_id for rule_id, rule in load_thresholds()["rules"].items() if "unit" in rule
])
def test_yaml_unit_drift_names_the_rule(monkeypatch, rule_id):
    cfg = load_thresholds()
    cfg["rules"][rule_id]["unit"] = "wrong unit"
    monkeypatch.setattr("src.config.load_thresholds", lambda preset: cfg)
    with pytest.raises(ConfigValidationError, match=rule_id):
        load_rule_catalog()


def _frame():
    n = 40
    return pd.DataFrame({
        "PID": ["SYN0000000001"] * n,
        "Obs_time": pd.date_range("2020-01-01", periods=n, freq="min"),
        "t_min": range(20, 20 + n), "phase": "maintenance", "Age": 40,
        "TV": 500., "TV_mlkg": 7., "RR": 12., "PIP": 20., "PEEP": 5.,
        "ETCO2": 38., "FIO2": 40., "SPO2": 98., "HR": 75., "nMAP": 80.,
        "MAC_Et": 1., "MAC_total_Et": 1., "Agent": "S", "ETN2O": 0.,
        "Agent_Et_drift": 0., "Agent_Fi_Et_gap": 0., "PIP_slope": 0.,
        "case_frac": .5, "likely_mech_vent": True,
    })


_POINTWISE = [
    spec.id for spec in load_rule_catalog().specs
    if "warn" in spec.params or "warn_mac" in spec.params
]


@pytest.mark.parametrize("rule_id", _POINTWISE)
@pytest.mark.parametrize("preset", ["default", "strict", "lenient"])
def test_threshold_signal_and_direction_agree_with_flags(rule_id, preset):
    spec = load_rule_catalog(preset).get(rule_id)
    cfg = load_thresholds(preset)
    baseline = _frame()
    assert flag_anomalies(baseline, cfg).empty
    signal = spec.signals[0]
    delta = 1 if spec.direction == "high" else -1
    warn = spec.params.get("warn", spec.params.get("warn_mac"))
    critical = spec.params.get("critical", spec.params.get("critical_mac"))
    assert spec.direction == ("high" if warn > baseline[signal].iloc[0] else "low")
    for value, severity in ((warn - delta, None), (warn, "warn"), (critical + delta, "critical")):
        frame = baseline.copy()
        frame[signal] = value
        fired = flag_anomalies(frame, cfg)
        hit = fired[fired["rule_id"].eq(rule_id)]
        if severity is None:
            assert hit.empty
        else:
            assert set(hit["severity"]) == {severity}
            assert len(hit) == len(frame)
            assert hit["value"].eq(value).all()
            assert set(fired["rule_id"]) == {rule_id}


@pytest.mark.parametrize("rule_id", [
    spec.id for spec in load_rule_catalog().specs
    if spec.kind != "composite" and spec.id not in _POINTWISE
])
def test_other_rule_signals_directions_and_severities_agree_with_flags(rule_id):
    spec = load_rule_catalog().get(rule_id)
    frame = _frame()
    assert rule_id not in set(flag_anomalies(frame)["rule_id"])
    signal = spec.signals[0]
    targets = {
        "pip_rising": spec.params.get("min_slope_cmh2o_per_min"),
        "peep_zero_long": spec.params.get("max_peep"),
        "tv_abs_extreme": spec.params.get("high", 0) + 1,
        "etco2_zero_vent": 0,
        "fio2_high_long": spec.params.get("fio2_min"),
        "fio2_room_air_vent": spec.params.get("fio2_max"),
        "agent_low_maint": spec.params.get("max_mac", 0) / 2,
        "agent_drift": spec.params.get("delta_vol_pct", {}).get("S"),
        "agent_fi_et_gap": spec.params.get("gap_vol_pct", {}).get("S"),
    }
    target = targets[rule_id]
    if rule_id == "tv_abs_extreme":
        assert spec.direction is None
    else:
        assert spec.direction == ("high" if target > frame[signal].iloc[0] else "low")
    frame[signal] = target
    flags = flag_anomalies(frame, load_thresholds())
    hit = flags[flags["rule_id"].eq(rule_id)]
    assert not hit.empty
    assert hit["value"].eq(target).all()
    assert set(hit["severity"]) == set(spec.severities)
    for skipped in spec.skip_phases:
        frame["phase"] = skipped
        assert rule_id not in set(flag_anomalies(frame, load_thresholds())["rule_id"])


def test_agent_high_fallback_signal():
    frame = _frame().drop(columns="MAC_total_Et")
    spec = load_rule_catalog().get("agent_high")
    frame[spec.signals[1]] = spec.params["critical_mac"]
    assert "agent_high" in set(flag_anomalies(frame)["rule_id"])


# Each OR branch needs its own witness so another branch cannot hide a lost input.
_SIGNAL_SCENARIOS = {
    "pip_high": [({"PIP": 40}, ("PIP",), ())],
    "pip_rising": [({"PIP_slope": 2, "PIP": 25}, ("PIP_slope", "PIP"), ())],
    "peep_high": [({"PEEP": 20}, ("PEEP",), ())],
    "peep_zero_long": [({"PEEP": 0}, ("PEEP", "TV", "RR"), ())],
    "tv_low_mlkg": [({"TV_mlkg": 2}, ("TV_mlkg", "likely_mech_vent"), ())],
    "tv_high_mlkg": [({"TV_mlkg": 15}, ("TV_mlkg",), ())],
    "tv_abs_extreme": [({"TV": 1500}, ("TV", "Age"), ())],
    "rr_low": [({"RR": 3}, ("RR", "TV"), ())],
    "rr_high": [({"RR": 40}, ("RR",), ())],
    "etco2_high": [({"ETCO2": 70}, ("ETCO2",), ())],
    "etco2_low": [({"ETCO2": 15}, ("ETCO2",), ())],
    "etco2_zero_vent": [({"ETCO2": 0}, ("ETCO2", "TV", "RR"), ())],
    "fio2_high_long": [({"FIO2": 100}, ("FIO2", "t_min"), ())],
    "fio2_room_air_vent": [({"FIO2": 21}, ("FIO2", "TV", "RR"), ())],
    "agent_high": [
        ({"MAC_total_Et": 3}, ("MAC_total_Et",), ()),
        ({"MAC_Et": 3}, ("MAC_Et",), ("MAC_total_Et",)),
    ],
    "agent_low_maint": [({"MAC_Et": .1}, ("MAC_Et", "Agent", "ETN2O", "case_frac"), ())],
    "agent_drift": [({"Agent_Et_drift": 2}, ("Agent_Et_drift", "Agent"), ())],
    "agent_fi_et_gap": [({"Agent_Fi_Et_gap": 4}, ("Agent_Fi_Et_gap", "Agent", "case_frac"), ())],
    "spo2_low": [({"SPO2": 80}, ("SPO2",), ())],
    "hr_high": [({"HR": 160}, ("HR",), ())],
    "hr_low": [({"HR": 30}, ("HR",), ())],
    "map_low": [({"nMAP": 40}, ("nMAP",), ())],
    "compliance_concern": [({"PIP_slope": 2, "PIP": 25}, ("PIP_slope", "PIP", "TV"), ())],
    "hypoventilation_pattern": [
        ({"ETCO2": 70, "TV_mlkg": 2}, ("ETCO2", "TV_mlkg", "likely_mech_vent"), ()),
        ({"ETCO2": 70, "RR": 3}, ("ETCO2", "RR", "TV"), ()),
    ],
    "desat_with_vent_issue": [
        ({"SPO2": 80, "PIP": 40}, ("SPO2", "PIP"), ()),
        ({"SPO2": 80, "ETCO2": 0}, ("SPO2", "ETCO2", "TV", "RR"), ()),
        ({"SPO2": 80, "TV_mlkg": 2}, ("SPO2", "TV_mlkg", "likely_mech_vent"), ()),
    ],
}

_OPTIONAL_INPUTS = {
    ("agent_high", "MAC_Et"): "Volatile MAC is read only when total MAC is absent.",
    ("tv_abs_extreme", "Age"): "Missing age defaults to an adult; a child disables this rule.",
    ("agent_low_maint", "ETN2O"): "Missing N2O defaults to zero; high N2O exempts the row.",
    ("compliance_concern", "TV"): "Blank TV slope defaults to stable; the column must exist.",
}


@pytest.mark.parametrize("preset", ["default", "strict", "lenient"])
def test_only_composites_carry_composition_text(preset):
    for spec in load_rule_catalog(preset).specs:
        assert bool(spec.composition.strip()) == (spec.id in COMPOSITE_RULES), spec.id
        assert (spec.kind == "composite") == (spec.id in COMPOSITE_RULES), spec.id


@pytest.mark.parametrize("preset", ["default", "strict", "lenient"])
def test_composition_names_every_rule_the_engine_combines(preset):
    catalog = load_rule_catalog(preset)
    combined = {
        "compliance_concern": {"pip_rising"},
        "hypoventilation_pattern": {"etco2_high", "tv_low_mlkg", "rr_low"},
        "desat_with_vent_issue": {"spo2_low", "pip_high", "etco2_zero_vent", "tv_low_mlkg"},
    }
    for rule_id, parts in combined.items():
        text = catalog.get(rule_id).composition
        assert all(f"({part})" in text for part in parts), rule_id
        assert all(catalog.get(part) for part in parts)
    assert "5 mL/min" in catalog.get("compliance_concern").composition
    assert "10-min" in catalog.get("compliance_concern").composition


def _tv_slope_frame(slope: float):
    frame = _frame().assign(PIP_slope=2., PIP=25.)
    frame["TV"] = 400. + slope * pd.Series(range(len(frame)), dtype=float)
    return frame


@pytest.mark.parametrize("preset", ["default", "strict", "lenient"])
def test_compliance_concern_tv_slope_cutoff_matches_its_composition_text(preset):
    cfg = load_thresholds(preset)
    under = flag_anomalies(_tv_slope_frame(4.9), cfg)
    over = flag_anomalies(_tv_slope_frame(5.1), cfg)
    # The first minutes have no TV slope yet (fewer than 5 readings) and count as flat.
    assert "pip_rising" in set(over["rule_id"])
    assert len(under[under["rule_id"] == "compliance_concern"]) == 40
    fired = over[over["rule_id"] == "compliance_concern"]
    assert len(fired) == 4
    assert fired["t_min"].max() < 20 + 4
    assert "at most 5 mL/min" in load_rule_catalog(preset).get("compliance_concern").composition


@pytest.mark.parametrize("rule_id", _SIGNAL_SCENARIOS)
@pytest.mark.parametrize("preset", ["default", "strict", "lenient"])
def test_every_declared_signal_matches_flag_behavior(rule_id, preset):
    spec = load_rule_catalog(preset).get(rule_id)
    cfg = load_thresholds(preset)
    scenarios = _SIGNAL_SCENARIOS[rule_id]
    assert set(spec.signals) == {signal for _, signals, _ in scenarios for signal in signals}
    if rule_id in COMPOSITE_RULES or rule_id == "tv_abs_extreme":
        assert spec.direction is None
    for values, signals, absent in scenarios:
        frame = _frame().drop(columns=list(absent)).assign(**values)
        assert rule_id in set(flag_anomalies(frame, cfg)["rule_id"])
        for signal in signals:
            disabled = frame.copy()
            if (rule_id, signal) in _OPTIONAL_INPUTS:
                assert _OPTIONAL_INPUTS[rule_id, signal]
            if signal == "Age":
                disabled[signal] = 0
            elif signal == "ETN2O":
                disabled[signal] = 50
            elif signal == "likely_mech_vent":
                disabled[signal] = False
            elif rule_id == "compliance_concern" and signal == "TV":
                disabled = disabled.drop(columns=signal)
            else:
                disabled[signal] = float("nan")
            assert rule_id not in set(flag_anomalies(disabled, cfg)["rule_id"]), (rule_id, signal)


@pytest.fixture
def preset_window(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from src.desktop.app import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "EMR"))
    monkeypatch.setattr("src.desktop.app.load_settings", lambda: {"preset": "default"})
    for method in ("_autoload_processed", "_load_corpus_scan", "_refresh_tables_and_selectors", "_refresh_charts_if_needed", "_refresh_ollama_models"):
        monkeypatch.setattr(MainWindow, method, lambda *a, **k: None)
    window = MainWindow()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def test_settings_theme_change_keeps_loaded_preset_during_scan(preset_window, monkeypatch, tmp_path):
    from src.desktop.app import CorpusScanWorker, PipelineWorker, ResearchWorker

    window = preset_window
    window._worker = PipelineWorker(50, "strict", True, parent=window)
    window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
    worker = CorpusScanWorker(tmp_path / "EMR", tmp_path / "processed", preset="strict", parent=window)
    window._worker = worker
    monkeypatch.setattr(worker, "isRunning", lambda: True)
    window._on_settings_applied({"theme": "dark", "preset": "default"})
    assert window._active_preset == "strict"
    assert window._make_session_snapshot().preset == "strict"
    assert "preset: strict" in window.rules_text.toPlainText()
    monkeypatch.setattr("src.guardrails.validate_io.validate_emr_dir", lambda path: path)
    monkeypatch.setattr(ResearchWorker, "start", lambda self: None)
    window._start_research_search("synthetic pressure")
    assert window._research_engine.preset == "strict"
    assert window._research_worker.session.preset == "strict"


@pytest.mark.parametrize("explicit_path", [True, False])
def test_loaded_preset_uses_workers_resolved_folder(preset_window, monkeypatch, tmp_path, explicit_path):
    from src.desktop.app import DataLoadWorker

    loaded = tmp_path / "loaded"
    other = tmp_path / "other"
    for folder, preset in ((loaded, "strict"), (other, "lenient")):
        folder.mkdir()
        (folder / "run_meta.json").write_text('{"preset": "' + preset + '"}')
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(loaded))
    worker = DataLoadWorker(processed=loaded if explicit_path else None, parent=preset_window)
    data = tuple(pd.DataFrame() for _ in range(5))
    monkeypatch.setattr("src.services.data.load_processed", lambda *a, **k: data)
    worker.run()
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(other))
    preset_window._worker = worker
    preset_window._on_pipeline_ok(data)
    assert preset_window._make_session_snapshot().preset == "strict"
    assert "preset: strict" in preset_window.rules_text.toPlainText()


def test_uncached_case_fetch_uses_active_preset(preset_window, monkeypatch):
    from src.desktop.research_workers import CaseFetchWorker

    window = preset_window
    window._active_preset = "strict"
    monkeypatch.setattr(CaseFetchWorker, "start", lambda self: None)
    monkeypatch.setattr(window.research, "candidate_for", lambda pid: None)
    monkeypatch.setattr(window, "_on_research_case_loaded", lambda result: None)
    calls = []
    monkeypatch.setattr("src.desktop.research_workers.fetch_cases", lambda *a, **k: calls.append((a, k)))
    window._load_research_case("SYN0000000001", False, False)
    window._case_worker.run()
    assert calls[0][1]["preset"] == "strict"


@pytest.mark.parametrize("saved", ["lenient", None])
def test_new_computation_uses_saved_preset(preset_window, monkeypatch, saved):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from src.desktop.app import CorpusScanWorker, PipelineWorker

    window = preset_window
    window._worker = PipelineWorker(50, "strict", True, parent=window)
    window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
    monkeypatch.setattr("src.desktop.app.load_settings", lambda: {"preset": saved})
    monkeypatch.setattr(PipelineWorker, "start", lambda self: None)
    window._run_pipeline_menu()
    assert window._worker.preset == (saved or "default")
    assert window._active_preset == "strict"
    window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
    assert window._active_preset == (saved or "default")
    assert window._make_session_snapshot().preset == (saved or "default")

    window._active_preset = "strict"
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(CorpusScanWorker, "start", lambda self: None)
    window._run_corpus_scan()
    assert window._worker.preset == (saved or "default")
    window._on_corpus_scan_ok(SimpleNamespace(
        cases=pd.DataFrame(), episodes=pd.DataFrame(), meta={"preset": saved or "default"},
    ))
    assert window.corpus_scan.meta["preset"] == (saved or "default")
    assert window._make_session_snapshot().preset == "strict"


@pytest.mark.parametrize("metadata", [None, "invalid json", "[]", "{}", '{"preset": "unknown"}', '{"preset": []}'])
def test_unrecorded_cache_resets_previous_preset(preset_window, tmp_path, metadata):
    from src.desktop.app import DataLoadWorker, PipelineWorker

    window = preset_window
    data = tuple(pd.DataFrame() for _ in range(5))
    window._worker = PipelineWorker(50, "strict", True, parent=window)
    window._on_pipeline_ok(data)
    assert "not recorded" not in window.rules_text.toPlainText()
    if metadata is not None:
        (tmp_path / "run_meta.json").write_text(metadata)
    window._worker = DataLoadWorker(processed=tmp_path, parent=window)
    window._on_pipeline_ok(data)
    assert window._make_session_snapshot().preset == "default"
    assert window.rules_text.toPlainText().startswith(
        "Threshold rules (preset: default, not recorded with this data)"
    )
    window._worker = PipelineWorker(50, "lenient", True, parent=window)
    window._on_pipeline_ok(data)
    assert "not recorded" not in window.rules_text.toPlainText()


@pytest.mark.parametrize("new_preset", ["default", "strict"])
def test_reload_rejects_pending_research_results(preset_window, monkeypatch, new_preset):
    from types import SimpleNamespace
    from src.desktop.app import CaseFetchWorker, PipelineWorker, ResearchWorker
    from src.services.retrieval import RetrievalResult

    window = preset_window
    monkeypatch.setattr("src.guardrails.validate_io.validate_emr_dir", lambda path: path)
    monkeypatch.setattr(ResearchWorker, "start", lambda self: None)
    monkeypatch.setattr(CaseFetchWorker, "start", lambda self: None)
    accepted = []
    monkeypatch.setattr(window.research, "set_case_data", lambda *a, **k: accepted.append(a))
    window._start_research_search("synthetic pressure")
    search = window._research_worker
    window._load_research_case("SYN0000000001", False, True)
    fetch = window._case_worker
    window._queued_case = ("SYN0000000002", False, True)
    window._worker = PipelineWorker(50, new_preset, True, parent=window)
    window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
    status = window.research.status_label.text()
    answer = window.research.answer_text.toPlainText()
    fetch.loaded.emit(SimpleNamespace(timeseries=_frame(), flags=pd.DataFrame()))
    fetch.failed.emit("stale case failure")
    fetch.finished.emit()
    search.progress.emit(SimpleNamespace(message="stale progress"))
    search.shortlist.emit(RetrievalResult(candidates=()))
    search.answer.emit(SimpleNamespace(answer="stale answer"))
    search.failed.emit("stale search failure")
    search.finished.emit()
    assert not accepted
    assert window._pending_case is None and window._queued_case is None
    assert window.research.status_label.text() == status
    assert window.research.answer_text.toPlainText() == answer
    assert window.research.ask_button.isEnabled()


@pytest.mark.parametrize("new_preset", ["default", "strict"])
def test_reload_returns_detail_to_board(preset_window, monkeypatch, new_preset):
    from src.desktop.app import PipelineWorker
    from src.services.retrieval import RetrievalResult, SearchCandidate

    window = preset_window
    candidate = SearchCandidate(
        pid="SYN0000000001", source_label="analyzed case", analyzed=True,
        procedure="Synthetic procedure", age=55, gender="M", source_text="Synthetic case",
        rank_method="keyword", score=1.0,
    )
    window.research.set_results(RetrievalResult(candidates=(candidate,)))
    window.research.question_edit.setText("synthetic pressure")
    window.research.set_case_data(candidate.pid, _frame(), pd.DataFrame(), open_detail=True)
    assert window.research.stack.currentIndex() == 1
    window._worker = PipelineWorker(50, new_preset, True, parent=window)
    window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
    assert window.research.stack.currentIndex() == 0
    assert window.research.question_edit.text() == "synthetic pressure"
    assert f"Data was reloaded with preset {new_preset}" in window.research.status_label.text()
    assert "reopen" in window.research.status_label.text().lower()
    assert not window.research._case_views
    monkeypatch.setattr(window, "_load_research_case", lambda *a: None)
    window.research.inspect_button.click()
    assert window.research.stack.currentIndex() == 0


def test_catalog_hash_is_the_profile_header_hash(tmp_path):
    _frame().to_parquet(tmp_path / "timeseries.parquet")
    profile = profile_processed(tmp_path, load_thresholds("strict"))
    catalog = load_rule_catalog("strict")
    assert profile["metadata"]["thresholds_sha256"] == catalog.thresholds_sha256
    assert catalog.thresholds_sha256 in render_markdown(profile).split("## Coverage")[0]


def test_session_default_does_not_read_settings(monkeypatch):
    def unexpected_read():
        pytest.fail("SessionState must not read settings")

    monkeypatch.setattr("src.user_settings.load_settings", unexpected_read)
    monkeypatch.setattr("src.llm.tools.load_settings", unexpected_read, raising=False)
    assert SessionState().preset == "default"


def test_llm_reference_uses_active_preset_and_includes_every_rule():
    text = tool_rule_reference(SessionState(preset="strict"))
    assert "Preset: strict" in text
    assert "'warn': 28" in text and "'critical': 32" in text
    for spec in load_rule_catalog("strict").specs:
        assert f"- {spec.id}:" in text
        assert str(spec.details()) in text
    assert text != tool_rule_reference(SessionState(preset="default"))


def test_llm_reference_clips_oversized_catalog(monkeypatch):
    from dataclasses import replace
    from src.llm.tools import MAX_TOOL_CHARS

    catalog = load_rule_catalog()
    catalog = replace(catalog, specs=tuple(
        replace(spec, description="expanded description " * MAX_TOOL_CHARS)
        for spec in catalog.specs
    ))
    monkeypatch.setattr("src.llm.tools.load_rule_catalog", lambda preset: catalog)
    text = tool_rule_reference(SessionState())
    assert len(text) <= MAX_TOOL_CHARS
    assert text.endswith("[tool output truncated]")


def test_desktop_reference_switches_preset(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from src.desktop.app import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(MainWindow, "_autoload_processed", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_ollama_models", lambda *a, **k: None)
    monkeypatch.setattr("src.desktop.app.load_settings", lambda: {"preset": "default"})
    window = MainWindow()
    try:
        window._refresh_rule_reference()
        assert "'warn': 30" in window.rules_text.toPlainText()
        window._on_settings_applied({"preset": "strict"})
        text = window.rules_text.toPlainText()
        assert "preset: strict" in text and "'warn': 28" in text
        assert window._make_session_snapshot().preset == "strict"
        for spec in load_rule_catalog("strict").specs:
            assert f"{spec.id}:" in text
            assert str(spec.details()) in text
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("metadata, expected", [
    ('{"preset": "strict"}', "strict"), ("invalid json", "default"), ("[]", "default"),
])
def test_desktop_loaded_cache_preset(monkeypatch, tmp_path, metadata, expected):
    from PySide6.QtWidgets import QApplication
    from src.desktop.app import DataLoadWorker, MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path))
    monkeypatch.setattr("src.desktop.app.load_settings", lambda: {})
    for method in ("_autoload_processed", "_load_corpus_scan", "_refresh_tables_and_selectors", "_refresh_charts_if_needed", "_refresh_ollama_models"):
        monkeypatch.setattr(MainWindow, method, lambda *a, **k: None)
    (tmp_path / "run_meta.json").write_text(metadata)
    window = MainWindow()
    try:
        assert "preset: default" in window.rules_text.toPlainText()
        assert "'warn': 30" in window.rules_text.toPlainText()
        window._research_engine = object()
        window._worker = DataLoadWorker(processed=tmp_path, parent=window)
        window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
        assert window._make_session_snapshot().preset == expected
        text = window.rules_text.toPlainText()
        assert f"preset: {expected}" in text
        assert str(load_rule_catalog(expected).get("pip_high").details()) in text
        assert window._research_engine is None
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_same_preset_reload_without_open_case_leaves_status_alone(preset_window):
    from src.desktop.app import PipelineWorker

    window = preset_window
    before = window.research.status_label.text()
    window._worker = PipelineWorker(50, "default", True, parent=window)
    window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
    assert window.research.status_label.text() == before
    assert "Reopen" not in window.research.status_label.text()


def test_ask_answer_started_before_reload_is_discarded(preset_window, monkeypatch):
    from types import SimpleNamespace
    from src.desktop.app import LLMChatWorker, PipelineWorker

    window = preset_window
    window.cases = pd.DataFrame({"PID": ["SYN0000000001"]})
    monkeypatch.setattr(LLMChatWorker, "start", lambda self: None)
    monkeypatch.setattr(window.combo_llm_model, "currentText", lambda: "scripted")
    window._send_chat_question("which rules fired?")
    worker = window._llm_worker
    window._worker = PipelineWorker(50, "strict", True, parent=window)
    window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
    worker.finished_ok.emit(SimpleNamespace(answer="stale default-preset answer"))
    worker.failed.emit("stale failure")
    history = window.chat_history.toPlainText()
    assert "stale default-preset answer" not in history
    assert "stale failure" not in history
    assert "data was reloaded" in history


def test_settings_preset_change_without_data_rejects_pending_fetch(preset_window, monkeypatch):
    from types import SimpleNamespace
    from src.desktop.research_workers import CaseFetchWorker

    window = preset_window
    monkeypatch.setattr(CaseFetchWorker, "start", lambda self: None)
    monkeypatch.setattr("src.guardrails.validate_io.validate_emr_dir", lambda path: path)
    accepted = []
    monkeypatch.setattr(window.research, "set_case_data", lambda *a, **k: accepted.append(a))
    window._load_research_case("SYN0000000001", False, True)
    fetch = window._case_worker
    assert fetch.preset == "default"
    monkeypatch.setattr("src.desktop.app.load_settings", lambda: {"preset": "strict"})
    window._on_settings_applied({"preset": "strict"})
    assert window._active_preset == "strict"
    fetch.loaded.emit(SimpleNamespace(timeseries=_frame(), flags=pd.DataFrame()))
    assert not accepted
    assert "preset: strict" in window.rules_text.toPlainText()


def test_settings_apply_with_same_preset_keeps_pending_fetch(preset_window, monkeypatch):
    from src.desktop.research_workers import CaseFetchWorker

    window = preset_window
    monkeypatch.setattr(CaseFetchWorker, "start", lambda self: None)
    monkeypatch.setattr("src.guardrails.validate_io.validate_emr_dir", lambda path: path)
    window._load_research_case("SYN0000000001", False, True)
    request = window._case_request_id
    window._on_settings_applied({"theme": "dark"})
    assert window._case_request_id == request


def test_repeat_reload_without_open_case_updates_preset_notice(preset_window):
    from src.desktop.app import PipelineWorker

    window = preset_window
    window.research.set_status("Data was reloaded with preset default. Reopen the case to refresh its flags.")
    window._worker = PipelineWorker(50, "strict", True, parent=window)
    window._on_pipeline_ok(tuple(pd.DataFrame() for _ in range(5)))
    assert window.research.status_label.text() == "Data was reloaded with preset strict."
