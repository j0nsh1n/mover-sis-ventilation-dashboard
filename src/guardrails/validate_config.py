"""Validate thresholds.yaml structure and clinical ordering invariants."""

from __future__ import annotations

import re
from typing import Any

from src.guardrails.exceptions import ConfigValidationError
from src.guardrails.limits import ALLOWED_PRESETS

# Rules that use warn/critical with higher-is-worse semantics
HIGHER_IS_WORSE_RULES = {
    "pip_high",
    "peep_high",
    "tv_high_mlkg",
    "rr_high",
    "etco2_high",
    "hr_high",
}

# Rules that use warn/critical with lower-is-worse semantics
LOWER_IS_WORSE_RULES = {
    "tv_low_mlkg",
    "rr_low",
    "etco2_low",
    "spo2_low",
    "hr_low",
    "map_low",
}

MAC_HIGHER_RULES = {"agent_high"}  # warn_mac / critical_mac

# Anesthesia phases a rule's skip_phases may name (see src/pipeline/phases.py)
VALID_PHASES = {"pre_induction", "induction", "maintenance", "emergence", "post_emergence"}


def _require_dict(obj: Any, name: str) -> dict:
    if not isinstance(obj, dict):
        raise ConfigValidationError(f"{name} must be a mapping, got {type(obj).__name__}")
    return obj


def _require_number(obj: Any, name: str) -> float:
    if isinstance(obj, bool) or not isinstance(obj, (int, float)):
        raise ConfigValidationError(f"{name} must be a number, got {obj!r}")
    if obj != obj:  # NaN
        raise ConfigValidationError(f"{name} must not be NaN")
    return float(obj)


def validate_clean_ranges(ranges: dict) -> None:
    for key, bounds in ranges.items():
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise ConfigValidationError(
                f"clean_ranges.{key} must be [low, high], got {bounds!r}"
            )
        low = _require_number(bounds[0], f"clean_ranges.{key}[0]")
        high = _require_number(bounds[1], f"clean_ranges.{key}[1]")
        if low >= high:
            raise ConfigValidationError(
                f"clean_ranges.{key}: low ({low}) must be < high ({high})"
            )


def validate_mac(mac: dict) -> None:
    for agent, val in mac.items():
        if agent not in {"S", "D", "I", "N"}:
            raise ConfigValidationError(f"mac_age40 unknown agent code: {agent!r}")
        if agent == "N":
            if val is not None:
                raise ConfigValidationError("mac_age40.N must be null")
            continue
        v = _require_number(val, f"mac_age40.{agent}")
        if not (0.1 <= v <= 20):
            raise ConfigValidationError(
                f"mac_age40.{agent}={v} outside plausible range [0.1, 20]"
            )


def validate_rules(rules: dict) -> None:
    if not rules:
        raise ConfigValidationError("rules section is empty")

    for rule_id, spec in rules.items():
        if not isinstance(rule_id, str) or not rule_id:
            raise ConfigValidationError(f"Invalid rule_id: {rule_id!r}")
        spec = _require_dict(spec, f"rules.{rule_id}")

        if rule_id in HIGHER_IS_WORSE_RULES:
            if "warn" not in spec or "critical" not in spec:
                raise ConfigValidationError(
                    f"rules.{rule_id} requires warn and critical thresholds"
                )
            w = _require_number(spec["warn"], f"rules.{rule_id}.warn")
            c = _require_number(spec["critical"], f"rules.{rule_id}.critical")
            if c < w:
                raise ConfigValidationError(
                    f"rules.{rule_id}: critical ({c}) must be >= warn ({w}) "
                    f"for higher-is-worse rules"
                )

        if rule_id in LOWER_IS_WORSE_RULES:
            if "warn" not in spec or "critical" not in spec:
                raise ConfigValidationError(
                    f"rules.{rule_id} requires warn and critical thresholds"
                )
            w = _require_number(spec["warn"], f"rules.{rule_id}.warn")
            c = _require_number(spec["critical"], f"rules.{rule_id}.critical")
            if c > w:
                raise ConfigValidationError(
                    f"rules.{rule_id}: critical ({c}) must be <= warn ({w}) "
                    f"for lower-is-worse rules"
                )

        if rule_id in MAC_HIGHER_RULES:
            w = _require_number(spec.get("warn_mac"), f"rules.{rule_id}.warn_mac")
            c = _require_number(spec.get("critical_mac"), f"rules.{rule_id}.critical_mac")
            if c < w:
                raise ConfigValidationError(
                    f"rules.{rule_id}: critical_mac ({c}) must be >= warn_mac ({w})"
                )
            if w <= 0 or c <= 0:
                raise ConfigValidationError(f"rules.{rule_id} MAC thresholds must be > 0")

        if "skip_phases" in spec:
            skip = spec["skip_phases"]
            if skip is not None:
                if not isinstance(skip, list) or not all(isinstance(p, str) for p in skip):
                    raise ConfigValidationError(
                        f"rules.{rule_id}.skip_phases must be a list of phase names, got {skip!r}"
                    )
                unknown = sorted(set(skip) - VALID_PHASES)
                if unknown:
                    raise ConfigValidationError(
                        f"rules.{rule_id}.skip_phases has unknown phase(s) {unknown}; "
                        f"allowed: {sorted(VALID_PHASES)}"
                    )

        # Duration rules: non-negative integers
        for key in ("min_duration_min", "window_min", "skip_first_min"):
            if key in spec:
                v = _require_number(spec[key], f"rules.{rule_id}.{key}")
                if v < 0:
                    raise ConfigValidationError(
                        f"rules.{rule_id}.{key} must be >= 0, got {v}"
                    )


def validate_phases(phases: dict) -> None:
    for key in ("intubation_patterns", "extubation_patterns"):
        pats = phases.get(key)
        if not isinstance(pats, list) or not all(isinstance(p, str) and p.strip() for p in pats):
            raise ConfigValidationError(
                f"phases.{key} must be a list of non-empty strings, got {pats!r}"
            )
        for p in pats:
            try:
                re.compile(p)
            except re.error as e:
                raise ConfigValidationError(
                    f"phases.{key}: {p!r} is not a valid regular expression ({e})"
                ) from e
    for key in ("induction_after_min", "emergence_before_min", "event_pad_min"):
        if key in phases:
            v = _require_number(phases[key], f"phases.{key}")
            if v < 0:
                raise ConfigValidationError(f"phases.{key} must be >= 0, got {v}")
    unknown = sorted(
        set(phases)
        - {"intubation_patterns", "extubation_patterns", "induction_after_min",
           "emergence_before_min", "event_pad_min"}
    )
    if unknown:
        raise ConfigValidationError(f"phases has unknown key(s) {unknown}")


def validate_scoring(scoring: dict) -> None:
    for key in ("warn_minute_weight", "critical_minute_weight", "composite_episode_weight"):
        if key not in scoring:
            raise ConfigValidationError(f"scoring missing {key}")
        v = _require_number(scoring[key], f"scoring.{key}")
        if v < 0:
            raise ConfigValidationError(f"scoring.{key} must be >= 0")


def validate_thresholds(cfg: dict, preset: str | None = None) -> dict:
    """
    Validate a loaded thresholds config.

    Returns the same dict if valid; raises ConfigValidationError otherwise.
    """
    cfg = _require_dict(cfg, "thresholds")

    if preset is not None and preset not in ALLOWED_PRESETS:
        raise ConfigValidationError(
            f"Unknown preset {preset!r}; allowed: {sorted(ALLOWED_PRESETS)}"
        )

    presets = _require_dict(cfg.get("presets", {}), "presets")
    for name in ALLOWED_PRESETS:
        if name not in presets:
            raise ConfigValidationError(f"presets missing required key {name!r}")

    clean = _require_dict(cfg.get("clean_ranges"), "clean_ranges")
    validate_clean_ranges(clean)

    mac = _require_dict(cfg.get("mac_age40"), "mac_age40")
    validate_mac(mac)

    coef = cfg.get("mac_age_coef")
    _require_number(coef, "mac_age_coef")

    if cfg.get("agent_clean_max") is not None:
        caps = _require_dict(cfg["agent_clean_max"], "agent_clean_max")
        shared = clean.get("Agent_Et", [0, float("inf")])[1]
        for agent, cap in caps.items():
            if agent not in {"S", "I", "D", "N"}:
                raise ConfigValidationError(f"agent_clean_max unknown agent code: {agent!r}")
            v = _require_number(cap, f"agent_clean_max.{agent}")
            if not (0 < v <= shared):
                raise ConfigValidationError(
                    f"agent_clean_max.{agent}={v} must be > 0 and <= clean_ranges.Agent_Et "
                    f"upper bound ({shared})"
                )

    if cfg.get("n2o_mac_age40") is not None:
        n2o = _require_number(cfg["n2o_mac_age40"], "n2o_mac_age40")
        if not (50 <= n2o <= 200):
            raise ConfigValidationError(
                f"n2o_mac_age40={n2o} outside plausible range [50, 200]"
            )

    rules = _require_dict(cfg.get("rules"), "rules")
    validate_rules(rules)

    if cfg.get("phases") is not None:
        validate_phases(_require_dict(cfg["phases"], "phases"))

    scoring = _require_dict(cfg.get("scoring"), "scoring")
    validate_scoring(scoring)

    return cfg
