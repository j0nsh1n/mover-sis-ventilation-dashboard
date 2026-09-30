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
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from src.desktop import app as desktop_app
    except ImportError as e:
        # CI hosts without libEGL/Qt system libs
        if "libEGL" in str(e) or "libGL" in str(e):
            pytest.skip(f"Qt system libs missing: {e}")
        raise
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


def test_spec_bundles_xcb_cursor():
    spec = (ROOT / "packaging" / "mover_sis_monitor.spec").read_text(encoding="utf-8")
    assert "libxcb-cursor.so.0" in spec


def _generated_launch_script() -> str:
    text = (ROOT / "scripts" / "build_executable.sh").read_text(encoding="utf-8")
    start = text.index("cat > \"$APP_DIR/launch.sh\" <<'EOF'\n") + len("cat > \"$APP_DIR/launch.sh\" <<'EOF'\n")
    return text[start : text.index("\nEOF\n", start) + 1]


def _run_launcher(tmp_path, ldconfig_libs: list[str], bundled: list[str] = ()):
    app = tmp_path / "app"
    (app / "_internal").mkdir(parents=True)
    for lib in bundled:
        (app / "_internal" / lib).write_text("")
    launch = app / "launch.sh"
    launch.write_text(_generated_launch_script())
    launch.chmod(0o755)
    binary = app / "MOVER-SIS-Monitor"
    binary.write_text("#!/bin/sh\necho started\n")
    binary.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    lines = "".join(f"\t{lib} (libc6,x86-64) => /usr/lib/{lib}\\n" for lib in ldconfig_libs)
    (fake_bin / "ldconfig").write_text(f"#!/bin/sh\nprintf '{lines}'\n")
    (fake_bin / "ldconfig").chmod(0o755)
    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "QT_QPA_PLATFORM": "xcb", "HOME": str(tmp_path)}
    return subprocess.run([str(launch)], env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(sys.platform != "linux", reason="launch.sh is the Linux launcher")
def test_launcher_names_missing_system_libraries(tmp_path):
    result = _run_launcher(tmp_path, ["libxkbcommon.so.0"], bundled=["libxcb-cursor.so.0"])
    assert result.returncode == 1
    assert "libxkbcommon-x11.so.0" in result.stderr
    assert "libxcb-cursor.so.0" not in result.stderr.split("installed:")[1].splitlines()[0]
    assert "sudo apt install" in result.stderr and "sudo dnf install" in result.stderr
    assert "started" not in result.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="launch.sh is the Linux launcher")
def test_launcher_starts_app_when_libraries_present(tmp_path):
    result = _run_launcher(
        tmp_path,
        ["libxkbcommon.so.0", "libxkbcommon-x11.so.0"],
        bundled=["libxcb-cursor.so.0"],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "started"
