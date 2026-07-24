"""Threshold config loading and validation."""

from __future__ import annotations

import copy

import pytest

from src.config import load_thresholds
from src.guardrails.exceptions import ConfigValidationError
from src.guardrails.validate_config import validate_thresholds


def test_default_thresholds_load_and_validate():
    cfg = load_thresholds("default")
    assert cfg["_preset"] == "default"
    assert "pip_high" in cfg["rules"]
    assert cfg["rules"]["pip_high"]["warn"] == 30


def test_strict_preset_overrides():
    default = load_thresholds("default")
    strict = load_thresholds("strict")
    assert strict["rules"]["pip_high"]["warn"] < default["rules"]["pip_high"]["warn"]
    assert strict["rules"]["spo2_low"]["warn"] > default["rules"]["spo2_low"]["warn"]


def test_lenient_preset_overrides():
    default = load_thresholds("default")
    lenient = load_thresholds("lenient")
    assert lenient["rules"]["pip_high"]["warn"] > default["rules"]["pip_high"]["warn"]


def test_unknown_preset_rejected():
    with pytest.raises(ConfigValidationError):
        load_thresholds("not_a_preset")


def test_presets_do_not_mutate_shared_state():
    a = load_thresholds("default")
    b = load_thresholds("strict")
    # default object must not pick up strict overrides
    assert a["rules"]["pip_high"]["warn"] == 30
    assert b["rules"]["pip_high"]["warn"] == 28


def test_higher_is_worse_critical_must_be_ge_warn():
    cfg = load_thresholds("default", validate=False)
    cfg = copy.deepcopy(cfg)
    cfg["rules"]["pip_high"]["critical"] = 10
    cfg["rules"]["pip_high"]["warn"] = 30
    with pytest.raises(ConfigValidationError, match="critical"):
        validate_thresholds(cfg, preset="default")


def test_lower_is_worse_critical_must_be_le_warn():
    cfg = load_thresholds("default", validate=False)
    cfg = copy.deepcopy(cfg)
    cfg["rules"]["spo2_low"]["critical"] = 95
    cfg["rules"]["spo2_low"]["warn"] = 92
    with pytest.raises(ConfigValidationError, match="critical"):
        validate_thresholds(cfg, preset="default")


def test_clean_ranges_low_lt_high():
    cfg = load_thresholds("default", validate=False)
    cfg = copy.deepcopy(cfg)
    cfg["clean_ranges"]["TV"] = [1500, 50]
    with pytest.raises(ConfigValidationError, match="clean_ranges"):
        validate_thresholds(cfg, preset="default")


def test_mac_null_for_none_agent():
    cfg = load_thresholds("default", validate=False)
    cfg = copy.deepcopy(cfg)
    cfg["mac_age40"]["N"] = 1.0
    with pytest.raises(ConfigValidationError, match="mac_age40.N"):
        validate_thresholds(cfg, preset="default")


def test_scoring_weights_non_negative():
    cfg = load_thresholds("default", validate=False)
    cfg = copy.deepcopy(cfg)
    cfg["scoring"]["warn_minute_weight"] = -1
    with pytest.raises(ConfigValidationError, match="scoring"):
        validate_thresholds(cfg, preset="default")
