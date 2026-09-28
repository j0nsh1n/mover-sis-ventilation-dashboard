"""Expose updater observations to the dependency-free Rust contract tests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from src import update


MANIFEST_URL = "https://updates.example/manifest.json"
LINUX_URL = "https://updates.example/app.tar.gz"
WINDOWS_URL = "https://updates.example/app.zip"


def emit(**values: object) -> None:
    for key, value in values.items():
        print(f"{key}={value}")


class Response(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def getcode(self) -> int:
        return self.status

    def info(self) -> dict[str, str]:
        return self.headers


class Opener:
    def __init__(self, response: Response):
        self.response = response
        self.urls: list[str] = []
        self.etags: list[str | None] = []

    def open(self, request: urllib.request.Request, timeout: float) -> Response:
        self.urls.append(request.full_url)
        self.etags.append(request.get_header("If-none-match"))
        return self.response


def manifest(version: str, linux_url: str = LINUX_URL, schema: int = 1) -> bytes:
    return json.dumps({
        "schema": schema,
        "version": version,
        "packages": {
            "linux-x86_64": {"url": linux_url, "sha256": "0" * 64, "size": 10},
            "windows-x86_64": {"url": WINDOWS_URL, "sha256": "0" * 64, "size": 10},
        },
    }).encode()


def version_case(case: str) -> None:
    if case == "invalid":
        try:
            update.compare_versions("1.2", "1.2.0")
        except update.UpdateError as exc:
            emit(error=type(exc).__name__)
    else:
        left, right = case.split(",")
        emit(comparison=update.compare_versions(left, right))


def check_case(case: str) -> None:
    if case == "inactive":
        opener = Opener(Response(b""))
        result = update.check_for_update(None, current_version="0.7.0", platform=update.PLATFORM_LINUX, opener=opener)
    else:
        body = {
            "older": manifest("0.6.0"),
            "same": manifest("0.7.0"),
            "newer": manifest("0.8.0"),
            "malformed": b"{",
            "bad_schema": manifest("0.8.0", schema=2),
            "bad_package_url": manifest("0.8.0", linux_url="http://updates.example/app.tar.gz"),
            "oversize": b"x" * (update.MAX_MANIFEST_BYTES + 1),
            "not_modified": b"",
            "http": manifest("0.8.0"),
        }[case]
        opener = Opener(Response(body, status=304 if case == "not_modified" else 200, headers={"ETag": '"rev-1"'}))
        url = MANIFEST_URL if case != "http" else "http://updates.example/manifest.json"
        result = update.check_for_update(url, current_version="0.7.0", platform=update.PLATFORM_LINUX, etag='"rev-1"' if case == "not_modified" else None, opener=opener)
    emit(status=result.status.value, remote=result.remote_version or "", package_url=result.package.url if result.package else "", etag=result.etag or "", requests=len(opener.urls), sent_etag=opener.etags[0] or "" if opener.etags else "", detail=result.detail)


def redirect_case() -> None:
    try:
        update.HttpsOnlyRedirectHandler().redirect_request(
            urllib.request.Request(MANIFEST_URL), None, 302, "Found", {},
            "http://evil.example/manifest.json",
        )
    except update.UpdateError as exc:
        emit(error=type(exc).__name__, detail=str(exc))


def download_case(case: str, root: Path) -> None:
    body = b"a complete update package"
    digest = hashlib.sha256(body if case != "checksum" else b"other").hexdigest()
    size = len(body) if case not in {"cap", "truncated"} else (4 if case == "cap" else len(body))
    response_body = body[:8] if case == "truncated" else body
    package = update.Package(update.PLATFORM_LINUX, LINUX_URL, digest, size)
    dest = root / "download"
    if case == "inside_install":
        dest.mkdir()
    opener = Opener(Response(response_body, headers={"Content-Length": str(len(body))} if case == "valid" else {}))
    original_write = os.write
    if case == "short_write":
        os.write = lambda fd, data: original_write(fd, data[:3])
    try:
        try:
            saved = update.download_package(package, dest, opener=opener, install_dir=dest if case == "inside_install" else None)
            emit(success=True, content_matches=saved.read_bytes() == body, files=len(list(dest.iterdir())), requests=len(opener.urls))
        except update.UpdateError as exc:
            emit(success=False, error=str(exc), files=len(list(dest.iterdir())) if dest.exists() else 0, requests=len(opener.urls))
    finally:
        os.write = original_write


def install(root: Path) -> Path:
    live = root / "MOVER-SIS-Monitor"
    live.mkdir()
    (live / "MOVER-SIS-Monitor").write_text("old-binary")
    (live / "marker").write_text("live")
    return live


def archive(root: Path, *, windows: bool = False, version: str = "0.8.0", bad: str = "") -> Path:
    name = "MOVER-SIS-Monitor"
    if windows or bad == "zip_symlink":
        path = root / "update.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            if bad == "zip_symlink":
                info = zipfile.ZipInfo(f"{name}/escape")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                bundle.writestr(info, "../../outside")
            else:
                bundle.writestr(f"{name}/MOVER-SIS-Monitor.exe", b"MZ-new")
                bundle.writestr(f"{name}/VERSION", version)
                bundle.writestr(f"{name}/_internal/runtime.dll", b"runtime")
        return path
    path = root / "update.tar.gz"
    members: list[tuple[str, bytes, int, bytes]] = [
        (f"{name}/MOVER-SIS-Monitor", b"new-binary", 0o755, tarfile.REGTYPE),
        (f"{name}/launch.sh", b"#!/bin/sh\n", 0o755, tarfile.REGTYPE),
        (f"{name}/VERSION", version.encode(), 0o644, tarfile.REGTYPE),
        (f"{name}/_internal/helper", b"helper", 0o755, tarfile.REGTYPE),
    ]
    if bad == "traversal":
        members.append(("../outside", b"pwned", 0o644, tarfile.REGTYPE))
    if bad == "absolute_link":
        members.append((f"{name}/escape", b"", 0o777, tarfile.SYMTYPE))
    with tarfile.open(path, "w:gz") as bundle:
        for member_name, data, mode, kind in members:
            info = tarfile.TarInfo(member_name)
            info.size = len(data) if kind == tarfile.REGTYPE else 0
            info.mode = mode
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = str(root / "outside")
            bundle.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return path


def stage_case(case: str, root: Path) -> None:
    live = install(root)
    if case in {"links", "windows"}:
        emr = root / "external-emr"
        emr.mkdir()
        (emr / "patient_information.csv").write_text("local-only")
        (live / "data/raw").mkdir(parents=True)
        (live / "data/raw/EMR").symlink_to(emr)
    if case == "internal_data":
        emr = live / "data/raw/EMR"
        emr.mkdir(parents=True)
        (emr / "patient_information.csv").write_text("research-data")
    if case == "data_root_link":
        external = root / "external-data"
        external.mkdir()
        (external / "patient_information.csv").write_text("research-data")
        (live / "data").symlink_to(external)
    is_windows = case in {"windows", "zip_symlink"}
    bad = case if case in {"traversal", "absolute_link", "zip_symlink"} else ""
    pkg = archive(root, windows=is_windows, version="0.7.0" if case == "version_mismatch" else "0.8.0", bad=bad)
    staged = live.with_name(live.name + ".staging")
    try:
        result = update.stage_package(pkg, live, platform=update.PLATFORM_WINDOWS if is_windows else update.PLATFORM_LINUX, expected_version="0.8.0")
        emit(success=True, live_marker=(live / "marker").read_text(), staged=result == staged, payload=(result / ("MOVER-SIS-Monitor.exe" if is_windows else "MOVER-SIS-Monitor")).is_file(), helper_executable=os.access(result / "_internal/helper", os.X_OK) if not is_windows else True, data_link=(result / "data/raw/EMR").is_symlink(), external_data=(root / "external-emr/patient_information.csv").read_text() if (root / "external-emr/patient_information.csv").exists() else "")
    except update.UpdateError as exc:
        emit(success=False, error=str(exc), staged=staged.exists(), live_marker=(live / "marker").read_text(), outside=(root / "outside").exists(), preserved_data=(live / "data/raw/EMR/patient_information.csv").read_text() if case == "internal_data" else (root / "external-data/patient_information.csv").read_text() if case == "data_root_link" else "")


def apply_case(case: str, root: Path) -> None:
    live = install(root)
    staged = live.with_name(live.name + ".staging")
    staged.mkdir()
    (staged / "marker").write_text("new")
    if case == "windows":
        (staged / "MOVER-SIS-Monitor.exe").write_bytes(b"MZ")
        platform = update.PLATFORM_WINDOWS
    else:
        (staged / "MOVER-SIS-Monitor").write_text("new-binary")
        (staged / "launch.sh").write_text("#!/bin/sh\nsleep 5\n")
        (staged / "launch.sh").chmod(0o755)
        platform = update.PLATFORM_LINUX
    parent = subprocess.Popen(["sleep", "30"]) if case == "wait_parent" else None
    try:
        plan = update.prepare_restart_apply(live, staged, platform=platform, parent_pid=parent.pid if parent else 999999)
        script = plan.script_path.read_text() if plan.script_path else ""
        common = dict(ready=plan.ready, script_outside=plan.script_path is not None and live not in plan.script_path.parents, backup_mentioned=str(live.with_name(live.name + ".backup")) in script)
        if case == "windows":
            started = update.spawn_restart_apply(plan)
            emit(**common, supported=plan.supported_here, spawned=started.spawned, restore_in_script="Restore-Live" in script, move_in_script="Move-Item" in script)
            return
        if case == "failed_swap":
            (staged / "launch.sh").unlink()
            result = subprocess.run(plan.command, capture_output=True, timeout=10)
            emit(**common, exit=result.returncode, live_marker=(live / "marker").read_text(), backup=live.with_name(live.name + ".backup").exists())
            return
        if case == "startup_rollback":
            (staged / "launch.sh").write_text("#!/bin/sh\nexit 1\n")
            (live / "launch.sh").write_text("#!/bin/sh\nsleep 5\n")
            (live / "launch.sh").chmod(0o755)
            result = subprocess.run(plan.command, capture_output=True, timeout=10)
            emit(**common, exit=result.returncode, live_marker=(live / "marker").read_text())
            return
        helper = subprocess.Popen(plan.command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.3)
        before = (live / "marker").read_text()
        parent.kill()
        parent.wait(timeout=5)
        code = helper.wait(timeout=8)
        emit(**common, before=before, exit=code, live_marker=(live / "marker").read_text(), backup_marker=(live.with_name(live.name + ".backup") / "marker").read_text(), staging_exists=staged.exists())
    finally:
        if parent and parent.poll() is None:
            parent.kill()


def qt_case(case: str, root: Path) -> None:
    from PySide6.QtWidgets import QApplication
    from src.desktop import updates

    app = QApplication.instance() or QApplication([])
    if case == "disabled":
        os.environ.pop("MOVER_UPDATE_MANIFEST_URL", None)
    else:
        os.environ["MOVER_UPDATE_MANIFEST_URL"] = MANIFEST_URL
        updates.is_frozen = lambda: True
    calls: list[str | None] = []
    attempts: list[int] = []
    if case in {"etag", "retry", "stage_once"}:
        status = update.UpdateStatus.UPDATE_AVAILABLE if case != "etag" else update.UpdateStatus.NOT_NEWER
        package = update.Package(update.PLATFORM_WINDOWS if case == "stage_once" else update.PLATFORM_LINUX, WINDOWS_URL if case == "stage_once" else LINUX_URL, "0" * 64, 10)
        def fake_check(*args, **kwargs):
            calls.append(kwargs["etag"])
            return update.CheckResult(status, "0.7.0", "0.8.0" if case != "etag" else "0.7.0", package if case != "etag" else None, '"rev-1"', "checked")
        updates.check_for_update = fake_check
    if case == "disabled":
        updates.check_for_update = lambda *args, **kwargs: calls.append("called")
    if case == "stage_once":
        live = root / "MOVER-SIS-Monitor"
        live.mkdir()
        (live / "MOVER-SIS-Monitor.exe").write_bytes(b"old")
        updates.running_platform = lambda: update.PLATFORM_WINDOWS
        updates.app_dir = lambda: live
        pkg = archive(root, windows=True)
        updates.download_package = lambda *args, **kwargs: (attempts.append(1), pkg)[1]
    if case == "retry":
        def failed_download(*args, **kwargs):
            attempts.append(1)
            raise OSError("network stopped")
        updates.download_package = failed_download
    controller = updates.UpdateController("0.7.0")
    checked: list[object] = []
    ready: list[tuple[str, object]] = []
    failures: list[str] = []
    controller.checked.connect(checked.append)
    controller.ready.connect(lambda version, path: ready.append((version, path)))
    controller.failed.connect(failures.append)
    def spin(target: int, bucket: list[object]) -> None:
        deadline = time.monotonic() + 2
        while len(bucket) < target and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
    try:
        controller.check()
        if case == "disabled":
            app.processEvents()
            emit(enabled=controller.enabled, active=controller.timer.isActive(), calls=len(calls))
            return
        spin(1, failures if case == "retry" else ready if case == "stage_once" else checked)
        controller._last_check = float("-inf")
        controller._retry_download_after = 0
        controller.check()
        spin(2, failures if case == "retry" else checked)
        emit(enabled=controller.enabled, interval=controller.timer.interval(), calls=len(calls), first_etag=calls[0] or "" if calls else "", second_etag=calls[1] or "" if len(calls) > 1 else "", attempts=len(attempts), ready=len(ready), staged=(root / "MOVER-SIS-Monitor.staging/MOVER-SIS-Monitor.exe").read_bytes() == b"MZ-new" if case == "stage_once" and ready else False, live=(root / "MOVER-SIS-Monitor/MOVER-SIS-Monitor.exe").read_bytes() == b"old" if case == "stage_once" else False, failures=len(failures))
    finally:
        controller.close()


def main() -> None:
    operation, case = sys.argv[1:3]
    with tempfile.TemporaryDirectory(prefix="mover-rust-contract-") as directory:
        root = Path(directory)
        {
            "version": lambda: version_case(case),
            "check": lambda: check_case(case),
            "redirect": redirect_case,
            "download": lambda: download_case(case, root),
            "stage": lambda: stage_case(case, root),
            "apply": lambda: apply_case(case, root),
            "qt": lambda: qt_case(case, root),
        }[operation]()


if __name__ == "__main__":
    main()
