"""Background update checks do not interrupt desktop work."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from src.desktop import updates
from src.update import CheckResult, Package, UpdateStatus


@pytest.fixture
def app():
    existing = QApplication.instance()
    if existing is not None:
        return existing
    return QApplication([])


def test_no_host_makes_no_update_request(app, monkeypatch):
    monkeypatch.delenv("MOVER_UPDATE_MANIFEST_URL", raising=False)
    called = []
    monkeypatch.setattr(updates, "check_for_update", lambda *args, **kwargs: called.append(args))
    controller = updates.UpdateController("0.7.0")
    controller.check()
    app.processEvents()
    assert controller.enabled is False
    assert controller.timer.isActive() is False
    assert called == []
    controller.close()


def test_enabled_checker_keeps_the_ui_responsive_and_reuses_etag(app, monkeypatch):
    monkeypatch.setenv("MOVER_UPDATE_MANIFEST_URL", "https://updates.example.test/latest.json")
    monkeypatch.setattr(updates, "is_frozen", lambda: True)
    calls = []

    def check(url, *, current_version, platform, etag):
        calls.append((url, current_version, platform, etag))
        return CheckResult(UpdateStatus.NOT_NEWER, current_version, "0.7.0", None, '"rev-1"', "same")

    monkeypatch.setattr(updates, "check_for_update", check)
    controller = updates.UpdateController("0.7.0")
    seen = []
    controller.checked.connect(seen.append)
    controller.check()
    deadline = time.monotonic() + 2
    while not seen and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert seen and calls[0][-1] is None
    assert controller.timer.interval() == 60_000

    controller._last_check = 0
    controller.check()
    deadline = time.monotonic() + 2
    while len(seen) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert len(seen) == 2
    assert calls[1][-1] == '"rev-1"'
    controller.close()


def test_new_version_is_staged_once_and_offered_for_restart(app, monkeypatch, tmp_path):
    monkeypatch.setenv("MOVER_UPDATE_MANIFEST_URL", "https://updates.example.test/latest.json")
    monkeypatch.setattr(updates, "is_frozen", lambda: True)
    monkeypatch.setattr(updates, "running_platform", lambda: "windows-x86_64")
    live = tmp_path / "MOVER-SIS-Monitor"
    live.mkdir()
    (live / "MOVER-SIS-Monitor.exe").write_bytes(b"old")
    monkeypatch.setattr(updates, "app_dir", lambda: live)
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("MOVER-SIS-Monitor/MOVER-SIS-Monitor.exe", b"new")
        bundle.writestr("MOVER-SIS-Monitor/VERSION", b"0.8.0")
    package = Package("windows-x86_64", "https://updates.example.test/update.zip", "0" * 64, archive.stat().st_size)
    checks = []
    downloads = []

    def check(*args, **kwargs):
        checks.append(kwargs)
        return CheckResult(UpdateStatus.UPDATE_AVAILABLE, "0.7.0", "0.8.0", package, '"rev-2"', "new")

    def download(*args, **kwargs):
        downloads.append(args)
        return archive

    monkeypatch.setattr(updates, "check_for_update", check)
    monkeypatch.setattr(updates, "download_package", download)
    controller = updates.UpdateController("0.7.0")
    ready = []
    controller.ready.connect(lambda version, path: ready.append((version, Path(path))))
    controller.check()
    deadline = time.monotonic() + 2
    while not ready and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert ready == [("0.8.0", live.with_name(live.name + ".staging"))]
    assert (ready[0][1] / "MOVER-SIS-Monitor.exe").read_bytes() == b"new"
    assert (live / "MOVER-SIS-Monitor.exe").read_bytes() == b"old"

    controller._last_check = float("-inf")
    controller.check()
    deadline = time.monotonic() + 2
    while len(checks) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert len(downloads) == 1
    controller.close()


def test_failed_package_download_can_retry_after_manifest_etag(app, monkeypatch):
    monkeypatch.setenv("MOVER_UPDATE_MANIFEST_URL", "https://updates.example.test/latest.json")
    monkeypatch.setattr(updates, "is_frozen", lambda: True)
    package = Package("linux-x86_64", "https://updates.example.test/update.tar.gz", "0" * 64, 10)
    seen_etags = []
    attempts = []

    def check(*args, **kwargs):
        seen_etags.append(kwargs["etag"])
        return CheckResult(UpdateStatus.UPDATE_AVAILABLE, "0.7.0", "0.8.0", package, '"rev-2"', "new")

    def download(*args, **kwargs):
        attempts.append(1)
        raise OSError("network stopped")

    monkeypatch.setattr(updates, "check_for_update", check)
    monkeypatch.setattr(updates, "download_package", download)
    controller = updates.UpdateController("0.7.0")
    failures = []
    controller.failed.connect(failures.append)
    controller.check()
    deadline = time.monotonic() + 2
    while not failures and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert failures and seen_etags == [None]

    controller._retry_download_after = 0
    controller._last_check = float("-inf")
    controller.check()
    deadline = time.monotonic() + 2
    while len(failures) < 2 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert seen_etags == [None, None]
    assert len(attempts) == 2
    controller.close()
