from pathlib import Path
import yaml

CONFIG_DIR = Path(__file__).resolve().parent
THRESHOLDS_PATH = CONFIG_DIR / "thresholds.yaml"


def load_thresholds(preset: str = "default") -> dict:
    """Load thresholds YAML and apply optional preset overlays."""
    with open(THRESHOLDS_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    presets = cfg.get("presets", {})
    overlay = presets.get(preset) or {}
    rules = cfg.setdefault("rules", {})
    for rule_id, updates in overlay.items():
        if rule_id in rules and isinstance(updates, dict):
            rules[rule_id].update(updates)
        elif isinstance(updates, dict):
            rules[rule_id] = updates
    cfg["_preset"] = preset
    return cfg
