"""Portable launcher and staging behavior without a packaging-tool dependency."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="AppImage packaging runs on Linux")


def test_apprun_forwards_arguments_and_disables_directory_updates(tmp_path):
    app = tmp_path / "app with spaces"
    app.mkdir()
    shutil.copy(ROOT / "packaging/appimage/AppRun", app / "AppRun")
    launch = app / "launch.sh"
    launch.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$MOVER_APPIMAGE" "$MOVER_UPDATE_MANIFEST_URL" "$@"\n'
    )
    launch.chmod(0o755)
    env = {**os.environ, "MOVER_UPDATE_MANIFEST_URL": "https://example.org/old-updates"}
    result = subprocess.run(
        [str(app / "AppRun"), "--case", "a case with spaces"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout.splitlines() == ["1", "", "--case", "a case with spaces"]


def _packaging_tree(tmp_path):
    root = tmp_path / "repo with spaces"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/build_appimage.sh", root / "scripts/build_appimage.sh")
    shutil.copytree(ROOT / "packaging/appimage", root / "packaging/appimage")
    version = (ROOT / "VERSION").read_text()
    (root / "VERSION").write_text(version)
    bundle = root / "dist/MOVER-SIS-Monitor"
    (bundle / "_internal").mkdir(parents=True)
    for name in ("MOVER-SIS-Monitor", "launch.sh"):
        path = bundle / name
        path.write_text("#!/bin/bash\nexit 0\n")
        path.chmod(0o755)
    (bundle / "VERSION").write_text(version)
    (bundle / "data/raw/EMR").mkdir(parents=True)
    (bundle / "data/raw/EMR/fictional.csv").write_text("must not be distributed")
    tool = tmp_path / "fake appimagetool"
    tool.write_text(
        '#!/bin/bash\nset -eu\n'
        'source="${@: -2:1}"\noutput="${@: -1}"\n'
        'cp -a "$source" "$MOVER_TEST_CAPTURE"\n'
        'printf "fake image" > "$output"\n'
    )
    tool.chmod(0o755)
    runtime = tmp_path / "runtime"
    runtime.touch()
    capture = tmp_path / "staged"
    env = {
        **os.environ, "APPIMAGETOOL": str(tool), "APPIMAGE_RUNTIME": str(runtime),
        "MOVER_TEST_CAPTURE": str(capture),
    }
    return root, bundle, capture, env


def test_appimage_staging_excludes_research_data_and_keeps_launcher(tmp_path):
    root, _, capture, env = _packaging_tree(tmp_path)
    result = subprocess.run(
        [str(root / "scripts/build_appimage.sh"), "--skip-build"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert "AppImage ready:" in result.stdout
    assert not (capture / "data").exists()
    assert (capture / "_internal").is_dir()
    assert (capture / "AppRun").stat().st_mode & 0o111
    desktop = (capture / "mover-sis-monitor.desktop").read_text()
    assert "Exec=AppRun" in desktop
    assert str(root) not in desktop
    image = next((root / "dist").glob("*.AppImage"))
    assert image.read_text() == "fake image"
    sidecar = Path(str(image) + ".sha256")
    relocated = tmp_path / "moved image"
    relocated.mkdir()
    shutil.move(str(image), relocated / image.name)
    shutil.move(str(sidecar), relocated / sidecar.name)
    subprocess.run(
        ["sha256sum", "--check", sidecar.name], cwd=relocated,
        capture_output=True, text=True, check=True,
    )


def test_appimage_refuses_a_stale_frozen_bundle(tmp_path):
    root, bundle, capture, env = _packaging_tree(tmp_path)
    (bundle / "VERSION").write_text("0.0.1\n")
    result = subprocess.run(
        [str(root / "scripts/build_appimage.sh"), "--skip-build"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "version does not match VERSION" in result.stderr
    assert not capture.exists()


def test_appimage_refuses_to_replace_an_existing_candidate(tmp_path):
    root, _, capture, env = _packaging_tree(tmp_path)
    version = (root / "VERSION").read_text().strip()
    image = root / f"dist/MOVER-SIS-Monitor-v{version}-x86_64.AppImage"
    image.write_text("previous candidate")
    result = subprocess.run(
        [str(root / "scripts/build_appimage.sh"), "--skip-build"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Refusing to replace" in result.stderr
    assert image.read_text() == "previous candidate"
    assert not capture.exists()
