from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from src.runtime_paths import app_dir, is_frozen
from src.update import (
    CheckResult,
    UpdateStatus,
    check_for_update,
    download_package,
    stage_package,
)


CHECK_INTERVAL_MS = 60_000


def running_platform() -> str | None:
    if sys.platform.startswith("linux"):
        return "linux-x86_64"
    if sys.platform == "win32":
        return "windows-x86_64"
    return None


class UpdateController(QObject):
    """Keep network and archive work off the Qt event loop."""

    ready = Signal(str, object)
    found = Signal(str)
    failed = Signal(str)
    checked = Signal(object)
    _work_finished = Signal(object, object, object)

    def __init__(self, current_version: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._url = os.environ.get("MOVER_UPDATE_MANIFEST_URL", "").strip()
        self._platform = running_platform()
        self._current_version = current_version
        self._etag: str | None = None
        self._ready_version: str | None = None
        self._retry_download_after = 0.0
        self._busy = False
        self._closed = False
        self._last_check = float("-inf")
        self._staged: Path | None = None
        self._work_finished.connect(self._finish)
        self.timer = QTimer(self)
        self.timer.setInterval(CHECK_INTERVAL_MS)
        self.timer.timeout.connect(self.check)
        if self.enabled:
            self.timer.start()
            QTimer.singleShot(0, self.check)
            app = QApplication.instance()
            if app is not None:
                app.applicationStateChanged.connect(self._on_app_state)

    @property
    def enabled(self) -> bool:
        return bool(self._url and self._platform and is_frozen())

    @property
    def staged(self) -> Path | None:
        return self._staged

    @property
    def platform(self) -> str | None:
        return self._platform

    def check(self) -> None:
        now = time.monotonic()
        if not self.enabled or self._busy or self._closed or now - self._last_check < 60:
            return
        self._last_check = now
        self._busy = True
        thread = threading.Thread(target=self._run_check, daemon=True)
        thread.start()

    def _run_check(self) -> None:
        result: CheckResult | None = None
        staged: Path | None = None
        error: str | None = None
        try:
            result = check_for_update(
                self._url,
                current_version=self._current_version,
                platform=self._platform,
                etag=self._etag,
            )
            if (
                result.status is UpdateStatus.UPDATE_AVAILABLE
                and (
                    result.remote_version != self._ready_version
                    or self._staged is None
                    or not self._staged.is_dir()
                )
                and result.package is not None
                and time.monotonic() >= self._retry_download_after
            ):
                self.found.emit(result.remote_version)
                live = app_dir()
                archive = download_package(
                    result.package,
                    live.parent / ".mover-update-downloads",
                    install_dir=live,
                )
                try:
                    staged = stage_package(
                        archive,
                        live,
                        platform=self._platform,
                        expected_version=result.remote_version,
                    )
                finally:
                    archive.unlink(missing_ok=True)
        except Exception as exc:
            error = str(exc)
        if not self._closed:
            try:
                self._work_finished.emit(result, staged, error)
            except RuntimeError:
                pass

    def _on_app_state(self, state: Qt.ApplicationState) -> None:
        if state is Qt.ApplicationState.ApplicationActive:
            self.check()

    def _finish(self, result: CheckResult | None, staged: Path | None, error: str | None) -> None:
        self._busy = False
        if self._closed:
            return
        if result is not None:
            self._etag = None if error is not None or result.status is UpdateStatus.ERROR else result.etag
            self.checked.emit(result)
        if error is not None:
            self._retry_download_after = time.monotonic() + 300
            self.failed.emit(error)
        if staged is not None and result is not None and result.remote_version is not None:
            self._staged = staged
            self._ready_version = result.remote_version
            self.ready.emit(result.remote_version, staged)

    def close(self) -> None:
        self._closed = True
        self.timer.stop()
