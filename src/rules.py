"""Preset-specific rule metadata shared by reports and reference readers."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from src.runtime_paths import thresholds_path

RuleKind = Literal["threshold", "slope", "duration", "band", "composite"]
Direction = Literal["high", "low"]
Parameter = int | float | Mapping[str, "Parameter"]


@dataclass(frozen=True)
class ParameterMetadata:
    label: str
    unit: str | None
    directional: bool = False

    def phrase(self, direction: Direction | None) -> str:
        if self.directional:
            return f"{self.label} {'at least' if direction == 'high' else 'at most'}"
        return self.label


# None inherits the rule's unit; an empty string is dimensionless.
PARAMETER_METADATA: Mapping[str, ParameterMetadata] = MappingProxyType({
    "warn": ParameterMetadata("warn", None, directional=True),
    "critical": ParameterMetadata("critical", None, directional=True),
    "warn_mac": ParameterMetadata("warn", "MAC", directional=True),
    "critical_mac": ParameterMetadata("critical", "MAC", directional=True),
    "max_mac": ParameterMetadata("below", "MAC"),
    "window_min": ParameterMetadata("window", "min"),
    "min_slope_cmh2o_per_min": ParameterMetadata("slope at least", "cmH2O/min"),
    "min_duration_min": ParameterMetadata("lasting at least", "min"),
    "skip_first_min": ParameterMetadata("from minute", "min"),
    "min_pip": ParameterMetadata("PIP at least", "cmH2O"),
    "max_peep": ParameterMetadata("PEEP at most", "cmH2O"),
    "min_tv": ParameterMetadata("TV at least", "mL"),
    "min_rr": ParameterMetadata("RR at least", "/min"),
    "adult_age_min": ParameterMetadata("adult from age", "years"),
    "low": ParameterMetadata("below", None),
    "high": ParameterMetadata("above", None),
    "fio2_min": ParameterMetadata("FIO2 at least", "%"),
    "fio2_max": ParameterMetadata("FIO2 at most", "%"),
    "case_frac_start": ParameterMetadata("case fraction at least", ""),
    "case_frac_end": ParameterMetadata("case fraction at most", ""),
    "n2o_et_exempt": ParameterMetadata("exempt when end-tidal N2O is at least", "%"),
    "delta_vol_pct": ParameterMetadata("drift at least by agent", "vol %"),
    "gap_vol_pct": ParameterMetadata("gap at least by agent", "vol %"),
})


def thresholds_sha256() -> str:
    return hashlib.sha256(thresholds_path().read_bytes()).hexdigest()


@dataclass(frozen=True)
class RuleSpec:
    id: str
    label: str
    description: str
    kind: RuleKind
    signals: tuple[str, ...]
    direction: Direction | None
    unit: str
    params: Mapping[str, Parameter]
    skip_phases: tuple[str, ...]
    severities: tuple[str, ...]
    composition: str = ""

    def details(self) -> dict:
        """Plain values for the text and dashboard reference views."""
        def plain(value: Parameter):
            return {key: plain(item) for key, item in value.items()} if isinstance(value, Mapping) else value

        return {
            **{key: plain(value) for key, value in self.params.items()},
            "unit": self.unit,
            "skip_phases": list(self.skip_phases),
        }


@dataclass(frozen=True)
class RuleMetadata:
    label: str
    kind: RuleKind
    signals: tuple[str, ...]
    direction: Direction | None
    unit: str
    severities: tuple[str, ...] = ("warn", "critical")
    composition: str = ""


RULE_METADATA = {
    "pip_high": RuleMetadata("High PIP", "threshold", ("PIP",), "high", "cmH2O"),
    "pip_rising": RuleMetadata("Rising PIP", "slope", ("PIP_slope", "PIP"), "high", "cmH2O/min", ("warn",)),
    "peep_high": RuleMetadata("High PEEP", "threshold", ("PEEP",), "high", "cmH2O"),
    "peep_zero_long": RuleMetadata("Prolonged zero PEEP", "duration", ("PEEP", "TV", "RR"), "low", "cmH2O", ("info",)),
    "tv_low_mlkg": RuleMetadata("Low tidal volume per IBW", "threshold", ("TV_mlkg", "likely_mech_vent"), "low", "mL/kg IBW"),
    "tv_high_mlkg": RuleMetadata("High tidal volume per IBW", "threshold", ("TV_mlkg",), "high", "mL/kg IBW"),
    "tv_abs_extreme": RuleMetadata("Extreme adult tidal volume", "band", ("TV", "Age"), None, "mL", ("warn",)),
    "rr_low": RuleMetadata("Low respiratory rate", "threshold", ("RR", "TV"), "low", "/min"),
    "rr_high": RuleMetadata("High respiratory rate", "threshold", ("RR",), "high", "/min"),
    "etco2_high": RuleMetadata("High end-tidal CO2", "threshold", ("ETCO2",), "high", "mmHg"),
    "etco2_low": RuleMetadata("Low end-tidal CO2", "threshold", ("ETCO2",), "low", "mmHg"),
    "etco2_zero_vent": RuleMetadata("Zero CO2 while ventilated", "duration", ("ETCO2", "TV", "RR"), "low", "mmHg", ("critical",)),
    "fio2_high_long": RuleMetadata("Prolonged high inspired oxygen", "duration", ("FIO2", "t_min"), "high", "%", ("info",)),
    "fio2_room_air_vent": RuleMetadata("Room-air oxygen while ventilated", "duration", ("FIO2", "TV", "RR"), "low", "%", ("warn",)),
    "agent_high": RuleMetadata("High total agent MAC", "threshold", ("MAC_total_Et", "MAC_Et"), "high", "MAC"),
    "agent_low_maint": RuleMetadata("Low maintenance agent MAC", "threshold", ("MAC_Et", "Agent", "ETN2O", "case_frac"), "low", "MAC", ("warn",)),
    "agent_drift": RuleMetadata("Agent concentration drift", "duration", ("Agent_Et_drift", "Agent"), "high", "vol %", ("info",)),
    "agent_fi_et_gap": RuleMetadata("Inspired to end-tidal agent gap", "duration", ("Agent_Fi_Et_gap", "Agent", "case_frac"), "high", "vol %", ("info",)),
    "spo2_low": RuleMetadata("Low oxygen saturation", "threshold", ("SPO2",), "low", "%"),
    "hr_high": RuleMetadata("High heart rate", "threshold", ("HR",), "high", "/min"),
    "hr_low": RuleMetadata("Low heart rate", "threshold", ("HR",), "low", "/min"),
    "map_low": RuleMetadata("Low mean arterial pressure", "threshold", ("nMAP",), "low", "mmHg"),
    "compliance_concern": RuleMetadata("Compliance pattern", "composite", ("PIP_slope", "PIP", "TV"), None, "", ("warn",),
        # The TV slope cutoff is hardcoded in flags._add_composites; test_rules pins it.
        "rising PIP (pip_rising) while the 10-min TV slope is at most 5 mL/min "
        "(inclusive; with fewer than 5 TV readings in the window the slope counts as flat)"),
    "hypoventilation_pattern": RuleMetadata("Hypoventilation pattern", "composite", ("ETCO2", "TV_mlkg", "likely_mech_vent", "RR", "TV"), None, "", ("warn",),
        "high end-tidal CO2 (etco2_high) together with low tidal volume per IBW (tv_low_mlkg) "
        "or low respiratory rate (rr_low) in the same minute; any severity of each counts"),
    "desat_with_vent_issue": RuleMetadata("Desaturation with ventilation anomaly", "composite", ("SPO2", "PIP", "ETCO2", "TV", "RR", "TV_mlkg", "likely_mech_vent"), None, "", ("critical",),
        "low oxygen saturation (spo2_low) together with at least one of high PIP (pip_high), "
        "zero CO2 while ventilated (etco2_zero_vent) or low tidal volume per IBW (tv_low_mlkg) "
        "in the same minute; any severity of each counts"),
}


def _freeze_params(values: Mapping) -> Mapping[str, Parameter]:
    return MappingProxyType({
        key: _freeze_params(value) if isinstance(value, Mapping) else value
        for key, value in values.items()
        if isinstance(value, (int, float, Mapping)) and not isinstance(value, bool)
    })


@dataclass(frozen=True)
class RuleCatalog:
    specs: tuple[RuleSpec, ...]
    preset: str
    thresholds_sha256: str

    @classmethod
    def from_thresholds(cls, thresholds: dict) -> RuleCatalog:
        from src.guardrails.exceptions import ConfigValidationError

        rules = thresholds["rules"]
        missing = rules.keys() - RULE_METADATA.keys()
        extra = RULE_METADATA.keys() - rules.keys()
        if missing or extra:
            raise ConfigValidationError(
                f"Rule metadata mismatch; missing metadata: {sorted(missing)}; "
                f"metadata without rule: {sorted(extra)}"
            )
        for rule_id, rule in rules.items():
            unit = RULE_METADATA[rule_id].unit
            if "unit" in rule and rule["unit"] != unit:
                raise ConfigValidationError(
                    f"Rule {rule_id!r} unit mismatch: YAML {rule['unit']!r}, catalog {unit!r}"
                )
        specs = tuple(
            RuleSpec(
                id=rule_id, label=meta.label, description=rule.get("description", ""),
                kind=meta.kind, signals=meta.signals, direction=meta.direction,
                unit=meta.unit, params=_freeze_params(rule),
                skip_phases=tuple(rule.get("skip_phases") or ()), severities=meta.severities,
                composition=meta.composition,
            )
            for rule_id, rule in rules.items()
            for meta in (RULE_METADATA[rule_id],)
        )
        return cls(specs, thresholds["_preset"], thresholds_sha256())

    def get(self, rule_id: str) -> RuleSpec:
        for spec in self.specs:
            if spec.id == rule_id:
                return spec
        raise KeyError(rule_id)

    def search(self, text: str) -> tuple[RuleSpec, ...]:
        needle = text.strip().casefold()
        return tuple(
            spec for spec in self.specs
            if needle in " ".join((
                spec.id, spec.label, spec.description, spec.kind, *spec.signals,
                spec.unit, *spec.params, *spec.skip_phases,
            )).casefold()
        )


def load_rule_catalog(preset: str = "default") -> RuleCatalog:
    from src.config import load_thresholds

    return RuleCatalog.from_thresholds(load_thresholds(preset))
