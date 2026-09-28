"""Safety boundaries for the onedir update engine."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import tarfile
import time
import zipfile
from http.client import HTTPMessage
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import urllib.request
import urllib.response

from src.update import (
    PLATFORM_LINUX,
    PLATFORM_WINDOWS,
    UpdateError,
    UpdateStatus,
    check_for_update,
    compare_versions,
    download_package,
    parse_manifest,
    prepare_restart_apply,
    spawn_restart_apply,
    stage_package,
    HttpsOnlyRedirectHandler,
    Package,
)

MANIFEST_URL = "https://updates.example/manifest.json"
LINUX_URL = "https://updates.example/MOVER-SIS-Monitor-linux.tar.gz"
WINDOWS_URL = "https://updates.example/MOVER-SIS-Monitor-windows.zip"


def test_version_comparison_is_numeric_and_refuses_downgrade():
    assert compare_versions("1.10.0", "1.9.0") == 1
    assert compare_versions("1.9.0", "1.10.0") == -1
    assert compare_versions("0.7.0", "0.7.0") == 0
    with pytest.raises(UpdateError):
        compare_versions("1.2", "1.2.0")

    older = _check("0.7.0", _manifest("0.6.0"))
    same = _check("0.7.0", _manifest("0.7.0"))
    newer = _check("0.7.0", _manifest("0.8.0"))
    assert older.status is UpdateStatus.NOT_NEWER
    assert older.package is None
    assert same.status is UpdateStatus.NOT_NEWER
    assert newer.status is UpdateStatus.UPDATE_AVAILABLE
    assert newer.package is not None
    assert newer.package.url == LINUX_URL
    assert newer.remote_version == "0.8.0"


def test_missing_url_is_inactive_and_does_not_use_the_network():
    opener, seen = _opener(lambda req: _fail("network used"))
    result = check_for_update(
        None,
        current_version="0.7.0",
        platform=PLATFORM_LINUX,
        opener=opener,
    )
    blank = check_for_update(
        "  ",
        current_version="0.7.0",
        platform=PLATFORM_LINUX,
        opener=opener,
    )
    assert result.status is UpdateStatus.INACTIVE
    assert result.package is None
    assert blank.status is UpdateStatus.INACTIVE
    assert seen.urls == []


def test_not_modified_keeps_the_etag_and_does_not_offer_a_package():
    def respond(req):
        assert req.get_header("If-none-match") == '"v1"'
        return _response(req.full_url, 304, headers={"ETag": '"v1"'})

    opener, seen = _opener(respond)
    result = check_for_update(
        MANIFEST_URL,
        current_version="0.7.0",
        platform=PLATFORM_LINUX,
        etag='"v1"',
        opener=opener,
    )
    assert result.status is UpdateStatus.NOT_MODIFIED
    assert result.package is None
    assert result.etag == '"v1"'
    assert seen.urls == [MANIFEST_URL]


def test_malformed_manifest_is_an_error_and_not_an_update():
    cases = [
        b"{",
        b"[]",
        b'{"version": "1.0", "packages": {}}',
        b'{"schema": 2, "version": "1.0.0", "packages": {}}',
        _manifest("1.0.0", package_url="http://updates.example/pkg.tar.gz"),
        b"x" * (64 * 1024 + 1),
    ]
    for body in cases:
        result = _check("0.7.0", body)
        assert result.status is UpdateStatus.ERROR
        assert result.package is None
    with pytest.raises(UpdateError):
        parse_manifest(b"{")


def test_http_manifest_and_insecure_redirect_are_refused():
    opener, seen = _opener(lambda req: _fail(f"fetched {req.full_url}"))
    http = check_for_update(
        "http://updates.example/manifest.json",
        current_version="0.7.0",
        platform=PLATFORM_LINUX,
        opener=opener,
    )
    assert http.status is UpdateStatus.ERROR
    assert "HTTPS" in http.detail
    assert seen.urls == []

    def redirect(req):
        return _response(
            req.full_url,
            302,
            headers={"Location": "http://evil.example/manifest.json"},
        )

    _redirect_opener, seen_redirect = _opener(redirect)
    result = check_for_update(
        MANIFEST_URL,
        current_version="0.7.0",
        platform=PLATFORM_LINUX,
        opener=urllib.request.build_opener(HttpsOnlyRedirectHandler, seen_redirect),
    )
    assert result.status is UpdateStatus.ERROR
    assert "HTTPS" in result.detail
    assert seen_redirect.urls == [MANIFEST_URL]
    assert all(urlsplit(url).scheme != "http" for url in seen_redirect.urls)

    with pytest.raises(UpdateError):
        HttpsOnlyRedirectHandler().redirect_request(
            urllib.request.Request(MANIFEST_URL),
            None,
            302,
            "Found",
            {"Location": "http://evil.example/manifest.json"},
            "http://evil.example/manifest.json",
        )


def test_matching_download_keeps_only_the_verified_archive(tmp_path):
    body = b"verified-archive-bytes"
    package = Package(
        platform=PLATFORM_LINUX,
        url=LINUX_URL,
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body),
    )
    opener, seen = _opener(lambda req: _response(req.full_url, 200, body, {"Content-Length": str(len(body))}))
    saved = download_package(package, tmp_path, opener=opener)
    assert seen.urls == [LINUX_URL]
    assert saved.is_file()
    assert saved.read_bytes() == body
    assert list(tmp_path.iterdir()) == [saved]


def test_short_disk_writes_still_save_the_exact_verified_package(tmp_path, monkeypatch):
    body = b"a complete update package"
    package = Package(
        platform=PLATFORM_LINUX,
        url=LINUX_URL,
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body),
    )
    opener, _seen = _opener(lambda req: _response(req.full_url, 200, body))
    actual_write = os.write

    def short_write(fd, data):
        return actual_write(fd, data[:3])

    monkeypatch.setattr(os, "write", short_write)
    saved = download_package(package, tmp_path, opener=opener)
    assert saved.read_bytes() == body


def test_checksum_mismatch_and_size_cap_leave_no_archive(tmp_path):
    body = b"not-the-package"
    package = Package(
        platform=PLATFORM_LINUX,
        url=LINUX_URL,
        sha256=hashlib.sha256(b"other").hexdigest(),
        size=len(body),
    )
    opener, _seen = _opener(lambda req: _response(req.full_url, 200, body))
    with pytest.raises(UpdateError):
        download_package(package, tmp_path, opener=opener)
    assert list(tmp_path.iterdir()) == []

    capped = Package(
        platform=PLATFORM_LINUX,
        url=LINUX_URL,
        sha256=hashlib.sha256(body).hexdigest(),
        size=4,
    )
    with pytest.raises(UpdateError):
        download_package(capped, tmp_path, opener=opener)
    assert list(tmp_path.iterdir()) == []


def test_interrupted_download_leaves_no_file(tmp_path):
    payload = b"abcdefghijklmnopqrstuvwxyz"
    package = Package(
        platform=PLATFORM_LINUX,
        url=LINUX_URL,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )
    opener, _seen = _opener(lambda req: _response(req.full_url, 200, payload[:8]))
    with pytest.raises(UpdateError):
        download_package(package, tmp_path, opener=opener)
    assert list(tmp_path.iterdir()) == []


def test_download_refuses_to_write_inside_the_live_install(tmp_path):
    install = tmp_path / "app"
    install.mkdir()
    body = b"archive-bytes"
    package = Package(
        platform=PLATFORM_LINUX,
        url=LINUX_URL,
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body),
    )
    opener, _seen = _opener(lambda req: _response(req.full_url, 200, body))
    with pytest.raises(UpdateError):
        download_package(package, install, opener=opener, install_dir=install)
    assert list(install.iterdir()) == []


def test_stage_keeps_live_tree_and_user_data_symlinks(tmp_path):
    install = _install(tmp_path)
    emr = tmp_path / "real-emr"
    processed = tmp_path / "real-processed"
    emr.mkdir()
    processed.mkdir()
    (emr / "patient_information.csv").write_text("local-only", encoding="utf-8")
    (install / "data" / "raw").mkdir(parents=True)
    (install / "data" / "raw" / "EMR").symlink_to(emr)
    (install / "data" / "processed").symlink_to(processed)
    archive = _linux_archive(
        tmp_path,
        extra={"data/raw/EMR/patient_information.csv": b"from-archive"},
    )

    staging = stage_package(archive, install, platform=PLATFORM_LINUX)

    assert staging == install.with_name(install.name + ".staging")
    assert (install / "marker").read_text(encoding="utf-8") == "live"
    assert (emr / "patient_information.csv").read_text(encoding="utf-8") == "local-only"
    staged_emr = staging / "data" / "raw" / "EMR"
    staged_processed = staging / "data" / "processed"
    assert staged_emr.is_symlink()
    assert staged_processed.is_symlink()
    assert os.readlink(staged_emr) == os.readlink(install / "data" / "raw" / "EMR")
    assert os.readlink(staged_processed) == os.readlink(install / "data" / "processed")
    assert not (staging / "data" / "raw" / "EMR" / "patient_information.csv").is_symlink()
    assert (staging / "MOVER-SIS-Monitor").is_file()
    assert os.access(staging / "launch.sh", os.X_OK)
    assert (install / "MOVER-SIS-Monitor").read_text(encoding="utf-8") == "old-binary"


def test_stage_refuses_to_hide_research_data_stored_inside_the_install(tmp_path):
    install = _install(tmp_path)
    emr = install / "data" / "raw" / "EMR"
    emr.mkdir(parents=True)
    (emr / "patient_information.csv").write_text("research-data", encoding="utf-8")
    archive = _linux_archive(tmp_path)

    with pytest.raises(UpdateError, match="external folder"):
        stage_package(archive, install, platform=PLATFORM_LINUX)

    assert (emr / "patient_information.csv").read_text(encoding="utf-8") == "research-data"
    assert not install.with_name(install.name + ".staging").exists()


def test_stage_refuses_to_replace_a_data_directory_link(tmp_path):
    install = _install(tmp_path)
    external = tmp_path / "research-data"
    external.mkdir()
    (external / "patient_information.csv").write_text("research-data", encoding="utf-8")
    (install / "data").symlink_to(external)

    with pytest.raises(UpdateError, match="manual migration"):
        stage_package(_linux_archive(tmp_path), install, platform=PLATFORM_LINUX)

    assert (external / "patient_information.csv").read_text(encoding="utf-8") == "research-data"


def test_stage_refuses_an_archive_with_a_different_version(tmp_path):
    install = _install(tmp_path)
    archive = _linux_archive(tmp_path, extra={"VERSION": b"0.7.0"})

    with pytest.raises(UpdateError, match="version does not match"):
        stage_package(archive, install, platform=PLATFORM_LINUX, expected_version="0.8.0")

    assert (install / "MOVER-SIS-Monitor").read_text(encoding="utf-8") == "old-binary"


def test_windows_portable_zip_stages_without_touching_the_live_install(tmp_path):
    install = _install(tmp_path)
    external_emr = tmp_path / "emr"
    external_emr.mkdir()
    (install / "data" / "raw").mkdir(parents=True)
    (install / "data" / "raw" / "EMR").symlink_to(external_emr)
    archive = tmp_path / "windows.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("MOVER-SIS-Monitor/MOVER-SIS-Monitor.exe", b"MZ-new")
        bundle.writestr("MOVER-SIS-Monitor/VERSION", b"0.8.0")
        bundle.writestr("MOVER-SIS-Monitor/_internal/runtime.dll", b"runtime")

    staging = stage_package(
        archive,
        install,
        platform=PLATFORM_WINDOWS,
        expected_version="0.8.0",
    )

    assert (staging / "MOVER-SIS-Monitor.exe").read_bytes() == b"MZ-new"
    assert (staging / "_internal" / "runtime.dll").read_bytes() == b"runtime"
    assert (staging / "data" / "raw" / "EMR").resolve() == external_emr
    assert (install / "MOVER-SIS-Monitor").read_text(encoding="utf-8") == "old-binary"


def test_linux_archive_keeps_helper_executable_permissions(tmp_path):
    install = _install(tmp_path)
    archive = tmp_path / "exec.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, body, mode in (
            ("MOVER-SIS-Monitor/MOVER-SIS-Monitor", b"app", 0o755),
            ("MOVER-SIS-Monitor/launch.sh", b"#!/bin/sh\n", 0o755),
            ("MOVER-SIS-Monitor/_internal/helper", b"helper", 0o755),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = mode
            tar.addfile(info, io.BytesIO(body))

    staging = stage_package(archive, install, platform=PLATFORM_LINUX)

    assert os.access(staging / "_internal" / "helper", os.X_OK)


def test_unsafe_archive_paths_and_symlinks_are_not_materialized(tmp_path):
    install = _install(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = outside / "escaped.txt"
    linked = outside / "linked.txt"

    traversal = tmp_path / "traversal.tar.gz"
    _tar(
        traversal,
        [
            ("../outside/escaped.txt", b"pwned", tarfile.REGTYPE, ""),
            ("MOVER-SIS-Monitor/../../outside/escaped.txt", b"pwned", tarfile.REGTYPE, ""),
        ],
    )
    with pytest.raises(UpdateError):
        stage_package(traversal, install, platform=PLATFORM_LINUX)
    assert not escaped.exists()
    assert (install / "marker").read_text(encoding="utf-8") == "live"

    absolute = tmp_path / "absolute-link.tar.gz"
    _tar(
        absolute,
        [
            ("MOVER-SIS-Monitor", b"", tarfile.DIRTYPE, ""),
            ("MOVER-SIS-Monitor/escape", str(linked).encode(), tarfile.SYMTYPE, str(linked)),
        ],
    )
    with pytest.raises(UpdateError):
        stage_package(absolute, install, platform=PLATFORM_LINUX)
    assert not linked.exists()
    assert not (install / "escape").exists()

    zip_link = tmp_path / "link.zip"
    _zip_symlink(zip_link, "MOVER-SIS-Monitor/escape", str(linked))
    with pytest.raises(UpdateError):
        stage_package(zip_link, install, platform=PLATFORM_WINDOWS)
    assert not linked.exists()
    assert (install / "marker").read_text(encoding="utf-8") == "live"


def test_apply_script_rolls_back_a_failed_swap_and_stays_outside_the_app(tmp_path):
    install, staging = _swap_trees(tmp_path, complete=False)
    (install / "marker").write_text("original", encoding="utf-8")
    plan = prepare_restart_apply(
        install,
        _complete_staging(staging),
        platform=PLATFORM_LINUX,
        parent_pid=_dead_pid(),
    )
    assert plan.ready is True
    assert plan.script_path is not None
    assert install.resolve() not in plan.script_path.resolve().parents
    assert staging.resolve() not in plan.script_path.resolve().parents
    script = plan.script_path.read_text(encoding="utf-8")
    assert str(install) in script
    assert str(install.with_name(install.name + ".backup")) in script
    assert "restore" in script

    (staging / "launch.sh").unlink()
    completed = subprocess.run(plan.command, check=False, text=True, capture_output=True, timeout=10)
    assert completed.returncode != 0
    assert (install / "marker").read_text(encoding="utf-8") == "original"
    assert not install.with_name(install.name + ".backup").exists()
    assert (install / "MOVER-SIS-Monitor").is_file()


def test_apply_waits_for_parent_then_swaps_and_relaunches(tmp_path):
    install, staging = _swap_trees(tmp_path, complete=True)
    stamp = tmp_path / "launched"
    (staging / "launch.sh").write_text(
        f"#!/bin/sh\necho launched > {stamp}\nsleep 5\n",
        encoding="utf-8",
    )
    (staging / "launch.sh").chmod(0o755)
    parent = subprocess.Popen(["sleep", "30"])
    plan = prepare_restart_apply(
        install,
        staging,
        platform=PLATFORM_LINUX,
        parent_pid=parent.pid,
    )
    helper = subprocess.Popen(plan.command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.6)
        assert (install / "marker").read_text(encoding="utf-8") == "old"
        assert not stamp.exists()
        parent.kill()
        parent.wait(timeout=5)
        assert helper.wait(timeout=5) == 0
        for _ in range(20):
            if stamp.exists():
                break
            time.sleep(0.1)
    finally:
        if helper.poll() is None:
            helper.kill()
        if parent.poll() is None:
            parent.kill()
    assert (install / "marker").read_text(encoding="utf-8") == "new"
    assert (install.with_name(install.name + ".backup") / "marker").read_text(encoding="utf-8") == "old"
    assert stamp.read_text(encoding="utf-8").strip() == "launched"
    assert not staging.exists()

    again = subprocess.run(plan.command, check=False, capture_output=True, timeout=10)
    assert again.returncode != 0
    assert (install / "marker").read_text(encoding="utf-8") == "new"


def test_apply_restores_previous_install_when_new_app_exits_during_startup(tmp_path):
    install, staging = _swap_trees(tmp_path, complete=True)
    old_launched = tmp_path / "old-launched"
    (install / "launch.sh").write_text(
        f"#!/bin/sh\necho old > {old_launched}\nsleep 5\n",
        encoding="utf-8",
    )
    (install / "launch.sh").chmod(0o755)
    (staging / "launch.sh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (staging / "launch.sh").chmod(0o755)
    plan = prepare_restart_apply(install, staging, platform=PLATFORM_LINUX, parent_pid=_dead_pid())

    completed = subprocess.run(plan.command, check=False, capture_output=True, timeout=10)
    for _ in range(20):
        if old_launched.exists():
            break
        time.sleep(0.1)

    assert completed.returncode != 0
    assert (install / "marker").read_text(encoding="utf-8") == "old"
    assert old_launched.read_text(encoding="utf-8").strip() == "old"


def test_windows_helper_is_written_but_not_started_on_this_host(tmp_path):
    install = _install(tmp_path)
    staging = install.with_name(install.name + ".staging")
    staging.mkdir()
    (staging / "MOVER-SIS-Monitor.exe").write_bytes(b"MZ")
    plan = prepare_restart_apply(install, staging, platform=PLATFORM_WINDOWS, parent_pid=_dead_pid())
    assert plan.ready is True
    assert plan.supported_here is False
    assert plan.spawned is False
    assert plan.script_path is not None
    assert install.resolve() not in plan.script_path.resolve().parents
    script = plan.script_path.read_text(encoding="utf-8")
    assert "Restore-Live" in script
    assert "Move-Item" in script
    assert "MOVER-SIS-Monitor.exe" in script
    assert str(install) in script

    started = spawn_restart_apply(plan)
    assert started.spawned is False
    assert started.helper_pid is None
    assert "not started" in started.detail
    assert (install / "marker").read_text(encoding="utf-8") == "live"


def test_spawned_linux_helper_does_not_swap_while_parent_is_alive(tmp_path):
    install, staging = _swap_trees(tmp_path, complete=True)
    plan = prepare_restart_apply(install, staging, platform=PLATFORM_LINUX, parent_pid=os.getpid())
    started = spawn_restart_apply(plan)
    assert started.spawned is True
    assert started.helper_pid is not None
    try:
        time.sleep(0.4)
        assert (install / "marker").read_text(encoding="utf-8") == "old"
        os.kill(started.helper_pid, 0)
    finally:
        os.kill(started.helper_pid, 15)
        try:
            os.waitpid(started.helper_pid, 0)
        except ChildProcessError:
            pass


def _fail(message: str):
    raise AssertionError(message)


def _manifest(version: str, package_url: str = LINUX_URL) -> bytes:
    linux = {
        "url": package_url,
        "sha256": "a" * 64,
        "size": 12,
    }
    windows = {
        "url": WINDOWS_URL,
        "sha256": "b" * 64,
        "size": 12,
    }
    import json

    return json.dumps(
        {"schema": 1, "version": version, "packages": {PLATFORM_LINUX: linux, PLATFORM_WINDOWS: windows}}
    ).encode()


def _check(current: str, body: bytes):
    opener, _seen = _opener(lambda req: _response(req.full_url, 200, body, {"ETag": '"next"'}))
    return check_for_update(
        MANIFEST_URL,
        current_version=current,
        platform=PLATFORM_LINUX,
        opener=opener,
    )


class _Handler(urllib.request.HTTPSHandler):
    def __init__(self, respond):
        super().__init__()
        self.respond = respond
        self.urls: list[str] = []

    def https_open(self, req):
        self.urls.append(req.full_url)
        return self.respond(req)


def _opener(respond) -> tuple[urllib.request.OpenerDirector, _Handler]:
    handler = _Handler(respond)
    return urllib.request.build_opener(handler), handler


def _response(url: str, code: int, body: bytes = b"", headers: dict[str, str] | None = None):
    message = HTTPMessage()
    for key, value in (headers or {}).items():
        message[key] = value
    resp = urllib.response.addinfourl(io.BytesIO(body), message, url, code)
    resp.msg = "OK"
    return resp


def _install(tmp_path: Path) -> Path:
    install = tmp_path / "mover-sis-monitor"
    install.mkdir()
    (install / "marker").write_text("live", encoding="utf-8")
    (install / "MOVER-SIS-Monitor").write_text("old-binary", encoding="utf-8")
    (install / "launch.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return install


def _linux_archive(tmp_path: Path, extra: dict[str, bytes] | None = None) -> Path:
    path = tmp_path / "good.tar.gz"
    members = [
        ("MOVER-SIS-Monitor/MOVER-SIS-Monitor", b"new-binary", tarfile.REGTYPE, ""),
        ("MOVER-SIS-Monitor/launch.sh", b"#!/bin/sh\n", tarfile.REGTYPE, ""),
    ]
    for name, data in (extra or {}).items():
        members.append((f"MOVER-SIS-Monitor/{name}", data, tarfile.REGTYPE, ""))
    _tar(path, members)
    return path


def _tar(path: Path, members: list[tuple[str, bytes, bytes, str]]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data, kind, link in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.DIRTYPE:
                info.size = 0
                tar.addfile(info)
                continue
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.type = kind
                info.linkname = link
                info.size = 0
                tar.addfile(info)
                continue
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _zip_symlink(path: Path, name: str, target: str) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(info, target)
        exe = zipfile.ZipInfo("MOVER-SIS-Monitor/MOVER-SIS-Monitor.exe")
        bundle.writestr(exe, b"MZ")


def _swap_trees(tmp_path: Path, complete: bool) -> tuple[Path, Path]:
    install = _install(tmp_path)
    (install / "marker").write_text("old", encoding="utf-8")
    staging = install.with_name(install.name + ".staging")
    staging.mkdir()
    (staging / "marker").write_text("new", encoding="utf-8")
    (staging / "MOVER-SIS-Monitor").write_text("new-binary", encoding="utf-8")
    if complete:
        (staging / "launch.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return install, staging


def _complete_staging(staging: Path) -> Path:
    if not (staging / "launch.sh").exists():
        (staging / "launch.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return staging


def _dead_pid() -> int:
    proc = subprocess.Popen(["sleep", "30"])
    pid = proc.pid
    proc.kill()
    proc.wait(timeout=5)
    return pid
