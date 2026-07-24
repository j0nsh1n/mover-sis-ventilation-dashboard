from pathlib import Path
import yaml

from src.guardrails.exceptions import ConfigValidationError
from src.guardrails.limits import ALLOWED_PRESETS
from src.guardrails.validate_config import validate_thresholds

CONFIG_DIR = Path(__file__).resolve().parent
THRESHOLDS_PATH = CONFIG_DIR / "thresholds.yaml"


def load_thresholds(preset: str = "default", *, validate: bool = True) -> dict:
    """Load thresholds YAML, apply preset overlay, and validate schema."""
    if preset not in ALLOWED_PRESETS:
        raise ConfigValidationError(
            f"Unknown preset {preset!r}; allowed: {sorted(ALLOWED_PRESETS)}"
        )

    if not THRESHOLDS_PATH.is_file():
        raise ConfigValidationError(f"thresholds file not found: {THRESHOLDS_PATH}")

    with open(THRESHOLDS_PATH, encoding="utf-8") as f:
        try:
            cfg = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigValidationError(f"Invalid YAML in {THRESHOLDS_PATH}: {e}") from e

    if not isinstance(cfg, dict):
        raise ConfigValidationError("thresholds.yaml root must be a mapping")

    presets = cfg.get("presets") or {}
    if not isinstance(presets, dict):
        raise ConfigValidationError("presets must be a mapping")

    overlay = presets.get(preset) or {}
    if overlay is None:
        overlay = {}
    if not isinstance(overlay, dict):
        raise ConfigValidationError(f"preset {preset!r} overlay must be a mapping")

    # Deep-copy rules before mutating so callers cannot corrupt cached YAML state
    import copy

    cfg = copy.deepcopy(cfg)
    rules = cfg.setdefault("rules", {})
    if not isinstance(rules, dict):
        raise ConfigValidationError("rules must be a mapping")

    for rule_id, updates in overlay.items():
        if not isinstance(updates, dict):
            raise ConfigValidationError(
                f"preset {preset!r} rule {rule_id!r} overlay must be a mapping"
            )
        if rule_id in rules and isinstance(rules[rule_id], dict):
            rules[rule_id] = {**rules[rule_id], **updates}
        else:
            rules[rule_id] = dict(updates)

    cfg["_preset"] = preset

    if validate:
        validate_thresholds(cfg, preset=preset)
    return cfg
