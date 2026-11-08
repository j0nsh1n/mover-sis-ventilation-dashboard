"""Persist lightweight user preferences (data directories, theme, LLM paths, etc.)."""

from __future__ import annotations

import json
import os
import shutil
from datetime import date
from pathlib import Path
from typing import Any

# Known preference keys (not exhaustive — extra keys are preserved)
KNOWN_KEYS = frozenset(
    {
        "data_dir",
        "emr_dir",
        "processed_dir",
        "wave_dir",
        "ollama_models_dir",
        "ollama_model",
        "ollama_embed_model",
        "ollama_base_url",
        "theme",  # light | dark | system
        "setup_complete",
        "n_cases",
        "preset",
    }
)

THEME_LIGHT = "light"
THEME_DARK = "dark"
THEME_SYSTEM = "system"
THEME_CHOICES = (THEME_LIGHT, THEME_DARK, THEME_SYSTEM)

# Sensible defaults for this host layout (optional auto-detect)
_DEFAULT_LLM_CANDIDATES = (
    "/var/mnt/games/LLM_Models",
    str(Path.home() / ".ollama" / "models"),
)


CONFIG_DIR_NAME = "mover-suite"
OLD_CONFIG_DIR_NAME = "mover-sis-monitor"
MIGRATION_MARKER = "migrated-from.txt"
_config_roots: dict[Path, Path] = {}
_migration_note: str | None = None


def settings_path() -> Path:
    base = os.environ.get("MOVER_CONFIG_DIR")
    if base:
        root = Path(base).expanduser().resolve()
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        if xdg not in _config_roots:
            _config_roots[xdg] = _config_root(xdg)
        root = _config_roots[xdg]
    root.mkdir(parents=True, exist_ok=True)
    return root / "settings.json"


def settings_migration_note() -> str | None:
    """Why this session still uses the old settings folder, or None."""
    return _migration_note


def _config_root(xdg: Path) -> Path:
    """The suite's settings folder, copied once from the MOVER SIS Monitor folder when only that one exists.

    The old folder is left as it was, so an older version still finds its settings.
    """
    global _migration_note
    new, old = xdg / CONFIG_DIR_NAME, xdg / OLD_CONFIG_DIR_NAME
    if (new / "settings.json").is_file() or not (old / "settings.json").is_file():
        return new
    try:
        _copy_forward(old, new)
    except OSError as exc:
        if (new / "settings.json").is_file():
            return new  # another instance finished the copy first
        _migration_note = (
            f"Settings stay in {old} for this session. Copying them to {new} failed: {exc.strerror or exc}."
        )
        return old
    return new


def _copy_forward(old: Path, new: Path) -> None:
    staging = new.with_name(f".{new.name}-copying-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(old, staging, symlinks=True)
    (staging / MIGRATION_MARKER).write_text(f"Copied from {old} on {date.today().isoformat()}.\n", encoding="utf-8")
    if not new.exists():
        staging.rename(new)
        return
    # settings.json moves last, so an interrupted copy is retried on the next start.
    for item in sorted(staging.iterdir(), key=lambda path: path.name == "settings.json"):
        target = new / item.name
        if not target.exists():
            item.rename(target)
    shutil.rmtree(staging, ignore_errors=True)


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(data: dict[str, Any]) -> Path:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)
    return path


def update_settings(**kwargs: Any) -> dict[str, Any]:
    data = load_settings()
    for k, v in kwargs.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    save_settings(data)
    return data


def get_theme() -> str:
    raw = str(load_settings().get("theme") or THEME_SYSTEM).strip().lower()
    if raw not in THEME_CHOICES:
        return THEME_SYSTEM
    return raw


def needs_first_run_setup() -> bool:
    """
    True when the user has never completed setup and has no persisted EMR path.

    Existing installs that already saved paths skip the forced wizard.
    """
    cfg = load_settings()
    if cfg.get("setup_complete"):
        return False
    if cfg.get("emr_dir"):
        # Migrate older installs: treat as configured
        if not cfg.get("setup_complete"):
            update_settings(setup_complete=True)
        return False
    return True


def mark_setup_complete() -> dict[str, Any]:
    return update_settings(setup_complete=True)


def detect_default_ollama_models_dir() -> str | None:
    """Return a likely models directory if one exists on disk."""
    env = os.environ.get("OLLAMA_MODELS")
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return str(p.resolve())
    for cand in _DEFAULT_LLM_CANDIDATES:
        p = Path(cand).expanduser()
        if p.is_dir():
            return str(p.resolve())
    return None


def apply_ollama_env_from_settings() -> str | None:
    """
    Export OLLAMA_MODELS from settings (or env / auto-detect).

    Call before starting ``ollama serve`` so the server reads the right tree.
    Returns the path applied, or None.
    """
    cfg = load_settings()
    raw = cfg.get("ollama_models_dir") or os.environ.get("OLLAMA_MODELS")
    if not raw:
        raw = detect_default_ollama_models_dir()
    if not raw:
        return None
    p = Path(str(raw)).expanduser()
    try:
        if p.is_dir():
            resolved = str(p.resolve())
            os.environ["OLLAMA_MODELS"] = resolved
            return resolved
    except OSError:
        return None
    return None


def get_ollama_models_dir() -> str | None:
    cfg = load_settings()
    raw = cfg.get("ollama_models_dir")
    if raw:
        return str(raw)
    return detect_default_ollama_models_dir()
