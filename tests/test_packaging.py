"""Packaging / executable build guardrails (no full PyInstaller run in CI by default)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_packaging_files_exist():
    assert (ROOT / "packaging" / "entrypoint.py").is_file()
    assert (ROOT / "packaging" / "mover_sis_monitor.spec").is_file()
    assert (ROOT / "packaging" / "rthook_paths.py").is_file()
    assert (ROOT / "packaging" / "rthook_qt.py").is_file()
    assert (ROOT / "scripts" / "build_executable.sh").is_file()


def test_entrypoint_source_sets_wayland_fallback():
    text = (ROOT / "packaging" / "entrypoint.py").read_text(encoding="utf-8")
    assert "QT_QPA_PLATFORM" in text
    assert "xcb" in text
    assert "desktop_main" in text or "src.desktop" in text


def test_spec_bundles_thresholds_and_shiboken():
    spec = (ROOT / "packaging" / "mover_sis_monitor.spec").read_text(encoding="utf-8")
    assert "thresholds.yaml" in spec
    assert "shiboken6" in spec
    assert "numpy.libs" in spec
    assert "rthook_paths.py" in spec
    assert "console=False" in spec


def test_entrypoint_imports_without_gui_show(monkeypatch):
    """Import chain for frozen entry must resolve (offscreen)."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    # Ensure project importable
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # Import desktop main without executing event loop
    from src.desktop import app as desktop_app

    assert callable(desktop_app.main)
    assert hasattr(desktop_app, "MainWindow")


def test_build_script_is_executable_and_sane():
    script = ROOT / "scripts" / "build_executable.sh"
    text = script.read_text(encoding="utf-8")
    assert "PyInstaller" in text or "pyinstaller" in text
    assert "MOVER-SIS-Monitor" in text
    assert os.access(script, os.X_OK)


@pytest.mark.skipif(
    not (ROOT / "dist" / "MOVER-SIS-Monitor" / "MOVER-SIS-Monitor").exists(),
    reason="Frozen binary not built in this workspace",
)
def test_frozen_binary_smoke_offscreen():
    """If a build exists, the binary should start and stay up briefly."""
    binary = ROOT / "dist" / "MOVER-SIS-Monitor" / "MOVER-SIS-Monitor"
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    proc = subprocess.Popen(
        [str(binary)],
        cwd=str(binary.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        try:
            proc.wait(timeout=4)
            out = proc.stdout.read() if proc.stdout else ""
            # If it exited within 4s, that is a failure (should keep running)
            pytest.fail(
                f"Frozen binary exited early code={proc.returncode}\n{out[:2000]}"
            )
        except subprocess.TimeoutExpired:
            # Still running after 4s — success for smoke
            pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
