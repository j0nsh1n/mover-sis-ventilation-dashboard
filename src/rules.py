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
    "compliance_concern": RuleMetadata("Compliance pattern", "composite", ("PIP_slope", "PIP", "TV"), None, "", ("warn",)),
    "hypoventilation_pattern": RuleMetadata("Hypoventilation pattern", "composite", ("ETCO2", "TV_mlkg", "likely_mech_vent", "RR", "TV"), None, "", ("warn",)),
    "desat_with_vent_issue": RuleMetadata("Desaturation with ventilation anomaly", "composite", ("SPO2", "PIP", "ETCO2", "TV", "RR", "TV_mlkg", "likely_mech_vent"), None, "", ("critical",)),
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
