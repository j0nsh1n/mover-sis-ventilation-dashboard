"""Poll a public manifest and stage a verified onedir update.

The engine does not read case data and does not contact any host other than
the manifest and package URLs the caller passes in. With no URL it stays idle.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

MAX_MANIFEST_BYTES = 64 * 1024
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_UNPACK_BYTES = MAX_PACKAGE_BYTES
MAX_ARCHIVE_MEMBERS = 200_000
MANIFEST_TIMEOUT_SECONDS = 10.0
DOWNLOAD_TIMEOUT_SECONDS = 60.0
APPLY_WAIT_SECONDS = 120
STARTUP_HEALTH_SECONDS = 3
APP_ROOT = "MOVER-SIS-Monitor"
USER_AGENT = "MOVER-SIS-Monitor-updater"
PLATFORM_LINUX = "linux-x86_64"
PLATFORM_WINDOWS = "windows-x86_64"
PLATFORMS = (PLATFORM_LINUX, PLATFORM_WINDOWS)
LINUX_PAYLOAD = ("MOVER-SIS-Monitor", "launch.sh")
WINDOWS_PAYLOAD = ("MOVER-SIS-Monitor.exe",)
USER_DATA_LINKS = (Path("data/raw/EMR"), Path("data/processed"))

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class UpdateError(Exception):
    """A manifest, package, or apply step failed a safety check."""


class UpdateStatus(StrEnum):
    INACTIVE = "inactive"
    NOT_MODIFIED = "not_modified"
    NOT_NEWER = "not_newer"
    UPDATE_AVAILABLE = "update_available"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Package:
    platform: str
    url: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise UpdateError(f"unsupported platform {self.platform!r}")
        require_https(self.url)
        digest = self.sha256.lower()
        if _SHA_RE.fullmatch(digest) is None:
            raise UpdateError("package sha256 must be 64 hex characters")
        if digest != self.sha256:
            object.__setattr__(self, "sha256", digest)
        if type(self.size) is not int or self.size < 1 or self.size > MAX_PACKAGE_BYTES:
            raise UpdateError("package size is missing or above the 2GiB cap")


@dataclass(frozen=True, slots=True)
class Manifest:
    version: tuple[int, int, int]
    packages: Mapping[str, Package]

    def __post_init__(self) -> None:
        if len(self.version) != 3 or any(type(part) is not int or part < 0 for part in self.version):
            raise UpdateError("manifest version must be a numeric x.y.z tuple")
        found = {key: pkg for key, pkg in self.packages.items()}
        if set(found) != set(PLATFORMS):
            raise UpdateError("manifest packages must be linux-x86_64 and windows-x86_64")
        for key, pkg in found.items():
            if pkg.platform != key:
                raise UpdateError(f"package platform does not match key {key}")
        object.__setattr__(self, "packages", MappingProxyType(dict(found)))

    @property
    def version_text(self) -> str:
        return format_version(self.version)


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: UpdateStatus
    current_version: str
    remote_version: str | None
    package: Package | None
    etag: str | None
    detail: str

    def __post_init__(self) -> None:
        available = self.status is UpdateStatus.UPDATE_AVAILABLE
        if available and self.package is None:
            raise UpdateError("update_available requires a package")
        if not available and self.package is not None:
            raise UpdateError("package is only set when an update is available")


@dataclass(frozen=True, slots=True)
class ApplyResult:
    ready: bool
    spawned: bool
    supported_here: bool
    platform: str
    script_path: Path | None
    command: tuple[str, ...]
    helper_pid: int | None
    detail: str

    def __post_init__(self) -> None:
        if self.spawned and (self.helper_pid is None or not self.supported_here):
            raise UpdateError("spawned apply requires a helper pid on a supported host")
        if not self.ready and self.spawned:
            raise UpdateError("cannot spawn an apply that is not ready")


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def parse_version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise UpdateError(f"version must be semver x.y.z, got {value!r}")
    major, minor, patch = value.split(".")
    return (int(major), int(minor), int(patch))


def compare_versions(left: str, right: str) -> int:
    """Return -1, 0, or 1 by numeric semver, not text order."""
    left_v = parse_version(left)
    right_v = parse_version(right)
    if left_v < right_v:
        return -1
    if left_v > right_v:
        return 1
    return 0


def require_https(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.hostname.endswith(".")
    ):
        raise UpdateError("URL must be HTTPS and must not carry credentials")
    return url


class HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            require_https(newurl)
        except UpdateError as exc:
            raise UpdateError(f"refusing non-HTTPS redirect to {newurl}") from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def https_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(HttpsOnlyRedirectHandler)


def parse_manifest(raw: bytes) -> Manifest:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("manifest exceeds 64KiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise UpdateError("manifest is not UTF-8") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UpdateError("malformed manifest") from exc
    if not isinstance(data, dict):
        raise UpdateError("malformed manifest")
    schema = data.get("schema")
    if type(schema) is not int or schema != 1:
        raise UpdateError(f"unsupported manifest schema {schema!r}")
    version = parse_version(data.get("version"))
    packages_raw = data.get("packages")
    if not isinstance(packages_raw, dict):
        raise UpdateError("malformed manifest")
    packages: dict[str, Package] = {}
    for key in PLATFORMS:
        item = packages_raw.get(key)
        if not isinstance(item, dict):
            raise UpdateError(f"missing package {key}")
        packages[key] = _parse_package(key, item)
    if set(packages_raw) != set(PLATFORMS):
        raise UpdateError("manifest packages must be linux-x86_64 and windows-x86_64")
    return Manifest(version=version, packages=packages)


def check_for_update(
    manifest_url: str | None,
    *,
    current_version: str,
    platform: str,
    etag: str | None = None,
    timeout: float = MANIFEST_TIMEOUT_SECONDS,
    opener: urllib.request.OpenerDirector | None = None,
) -> CheckResult:
    """Poll one manifest URL. No URL means the checker stays inactive."""
    current_text = current_version.strip()
    if manifest_url is None or not str(manifest_url).strip():
        return CheckResult(
            status=UpdateStatus.INACTIVE,
            current_version=current_text,
            remote_version=None,
            package=None,
            etag=etag,
            detail="update check inactive: no manifest URL",
        )
    url = str(manifest_url).strip()
    try:
        require_https(url)
        current = parse_version(current_text)
        if platform not in PLATFORMS:
            raise UpdateError(f"unsupported platform {platform!r}")
    except UpdateError as exc:
        return _error(current_text, str(exc), etag)

    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers, method="GET")
    client = opener or https_opener()
    try:
        response = client.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return CheckResult(
                status=UpdateStatus.NOT_MODIFIED,
                current_version=current_text,
                remote_version=None,
                package=None,
                etag=_header(exc, "ETag") or etag,
                detail="manifest not modified",
            )
        return _error(current_text, f"manifest request failed with HTTP {exc.code}", etag)
    except UpdateError as exc:
        return _error(current_text, str(exc), etag)
    except Exception as exc:
        return _error(current_text, f"manifest request failed: {exc}", etag)

    try:
        status = int(response.getcode())
        new_etag = _header(response, "ETag") or etag
        if status == 304:
            return CheckResult(
                status=UpdateStatus.NOT_MODIFIED,
                current_version=current_text,
                remote_version=None,
                package=None,
                etag=new_etag,
                detail="manifest not modified",
            )
        if status != 200:
            return _error(current_text, f"manifest request failed with HTTP {status}", new_etag)
        raw = response.read(MAX_MANIFEST_BYTES + 1)
    finally:
        response.close()

    if len(raw) > MAX_MANIFEST_BYTES:
        return _error(current_text, "manifest exceeds 64KiB", new_etag)
    try:
        manifest = parse_manifest(raw)
    except UpdateError as exc:
        return _error(current_text, str(exc), new_etag)
    remote = manifest.version_text
    if manifest.version <= current:
        return CheckResult(
            status=UpdateStatus.NOT_NEWER,
            current_version=current_text,
            remote_version=remote,
            package=None,
            etag=new_etag,
            detail="remote version is not newer",
        )
    return CheckResult(
        status=UpdateStatus.UPDATE_AVAILABLE,
        current_version=current_text,
        remote_version=remote,
        package=manifest.packages[platform],
        etag=new_etag,
        detail="update available",
    )


def download_package(
    package: Package,
    dest_dir: Path,
    *,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
    opener: urllib.request.OpenerDirector | None = None,
    install_dir: Path | None = None,
) -> Path:
    """Stream a package to dest_dir. The file remains only after size and SHA256 match."""
    require_https(package.url)
    dest = Path(dest_dir).expanduser().resolve()
    if install_dir is not None and _is_inside(dest, Path(install_dir).expanduser().resolve()):
        raise UpdateError("refusing to download into the live install")
    dest.mkdir(parents=True, exist_ok=True)
    suffix = _archive_suffix(package.url)
    fd, temp_name = tempfile.mkstemp(prefix=".mover-download-", suffix=suffix, dir=dest)
    temp_path = Path(temp_name)
    hasher = hashlib.sha256()
    received = 0
    try:
        request = urllib.request.Request(
            package.url,
            headers={"User-Agent": USER_AGENT},
            method="GET",
        )
        client = opener or https_opener()
        response = client.open(request, timeout=timeout)
        try:
            status = int(response.getcode())
            if status != 200:
                raise UpdateError(f"package request failed with HTTP {status}")
            declared = _header(response, "Content-Length")
            if declared is not None and int(declared) != package.size:
                raise UpdateError("Content-Length does not match the manifest size")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > package.size:
                    raise UpdateError("download exceeded the declared size")
                hasher.update(chunk)
                _write_all(fd, chunk)
        finally:
            response.close()
        os.close(fd)
        fd = -1
        digest = hasher.hexdigest()
        if received != package.size or digest != package.sha256:
            raise UpdateError("package size or sha256 does not match the manifest")
        final = dest / f"{APP_ROOT}-{digest[:12]}{suffix}"
        os.replace(temp_path, final)
        return final
    except Exception:
        if fd >= 0:
            os.close(fd)
        temp_path.unlink(missing_ok=True)
        raise


def stage_package(
    archive: Path,
    install_dir: Path,
    *,
    platform: str,
    expected_version: str | None = None,
) -> Path:
    """Unpack a verified archive into a sibling staging directory. The live tree is not written."""
    if platform not in PLATFORMS:
        raise UpdateError(f"unsupported platform {platform!r}")
    live = Path(install_dir).expanduser().resolve()
    if not live.is_dir() or live.is_symlink():
        raise UpdateError("install directory is missing")
    _assert_swap_root(live)
    staging = live.with_name(live.name + ".staging")
    if staging.parent != live.parent:
        raise UpdateError("staging directory must be a sibling of the live install")
    unpack_root = Path(tempfile.mkdtemp(prefix=".mover-unpack-", dir=live.parent))
    cleared = False
    try:
        _extract_archive(Path(archive), unpack_root)
        app_root = unpack_root / APP_ROOT
        if not app_root.is_dir() or app_root.is_symlink():
            raise UpdateError("archive must contain one MOVER-SIS-Monitor directory")
        _require_payload(app_root, platform)
        if expected_version is not None:
            version_file = app_root / "VERSION"
            if not version_file.is_file() or version_file.is_symlink():
                raise UpdateError("archive is missing its version marker")
            if version_file.read_text(encoding="utf-8").strip() != expected_version:
                raise UpdateError("archive version does not match the manifest")
        _assert_no_in_bundle_user_data(live)
        _preserve_user_links(live, app_root)
        if platform == PLATFORM_LINUX:
            _make_executable(app_root, LINUX_PAYLOAD)
        _discard(staging)
        cleared = True
        shutil.move(str(app_root), str(staging))
    except Exception:
        if cleared:
            _discard(staging)
        raise
    finally:
        _discard(unpack_root)
    return staging


def prepare_restart_apply(
    install_dir: Path,
    staging_dir: Path,
    *,
    platform: str,
    parent_pid: int | None = None,
) -> ApplyResult:
    """Write a detached helper outside the app directory. It does not run until spawn."""
    if platform not in PLATFORMS:
        return _apply_refused(platform, f"unsupported platform {platform!r}")
    live = Path(install_dir).expanduser().resolve()
    staged = Path(staging_dir).expanduser().resolve()
    try:
        _assert_swap_pair(live, staged)
        _require_payload(staged, platform)
        pid = os.getpid() if parent_pid is None else parent_pid
        if type(pid) is not int or pid <= 0:
            raise UpdateError("parent pid must be a positive integer")
    except UpdateError as exc:
        return _apply_refused(platform, str(exc))

    supported = _host_can_spawn(platform)
    suffix = ".ps1" if platform == PLATFORM_WINDOWS else ".sh"
    script_path = _helper_path(live, staged, suffix)
    backup = live.with_name(live.name + ".backup")
    if platform == PLATFORM_WINDOWS:
        script_path.write_text(_windows_script(pid, live, staged, backup), encoding="utf-8", newline="\n")
        command = ("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path))
    else:
        script_path.write_text(_linux_script(pid, live, staged, backup), encoding="utf-8", newline="\n")
        command = ("bash", str(script_path))
    os.chmod(script_path, 0o700)
    if supported:
        detail = "apply helper is ready and will swap only after the parent process exits"
    else:
        detail = (
            "apply helper was written, but this host cannot run it; "
            "auto-install was not started"
        )
    return ApplyResult(
        ready=True,
        spawned=False,
        supported_here=supported,
        platform=platform,
        script_path=script_path,
        command=command,
        helper_pid=None,
        detail=detail,
    )


def spawn_restart_apply(plan: ApplyResult) -> ApplyResult:
    """Start the helper in a new session. A mismatched host is a refusal, not a success."""
    if not plan.ready or plan.script_path is None:
        return ApplyResult(
            ready=False,
            spawned=False,
            supported_here=plan.supported_here,
            platform=plan.platform,
            script_path=plan.script_path,
            command=plan.command,
            helper_pid=None,
            detail=plan.detail or "apply helper is not ready",
        )
    if not plan.supported_here or not _host_can_spawn(plan.platform):
        return ApplyResult(
            ready=True,
            spawned=False,
            supported_here=False,
            platform=plan.platform,
            script_path=plan.script_path,
            command=plan.command,
            helper_pid=None,
            detail="refusing to spawn the apply helper on this host; auto-install was not started",
        )
    if not plan.script_path.is_file():
        return _apply_refused(plan.platform, "apply helper script is missing")
    proc = subprocess.Popen(
        list(plan.command),
        cwd=str(plan.script_path.parent),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return ApplyResult(
        ready=True,
        spawned=True,
        supported_here=True,
        platform=plan.platform,
        script_path=plan.script_path,
        command=plan.command,
        helper_pid=proc.pid,
        detail="apply helper started and is waiting for the parent process to exit",
    )


def _parse_package(platform: str, item: dict[str, object]) -> Package:
    url = item.get("url")
    digest = item.get("sha256")
    size = item.get("size")
    if not isinstance(url, str) or not isinstance(digest, str) or type(size) is not int:
        raise UpdateError(f"malformed package {platform}")
    return Package(platform=platform, url=url, sha256=digest, size=size)


def _error(current: str, detail: str, etag: str | None) -> CheckResult:
    return CheckResult(
        status=UpdateStatus.ERROR,
        current_version=current,
        remote_version=None,
        package=None,
        etag=etag,
        detail=detail,
    )


def _header(response: object, name: str) -> str | None:
    info = response.info() if hasattr(response, "info") else getattr(response, "headers", {})
    value = info.get(name) if info is not None else None
    if value is None:
        return None
    return str(value)


def _archive_suffix(url: str) -> str:
    path = urlsplit(url).path.lower()
    if path.endswith(".tar.gz") or path.endswith(".tgz"):
        return ".tar.gz"
    if path.endswith(".zip"):
        return ".zip"
    raise UpdateError("package URL must end in .tar.gz or .zip")


def _host_can_spawn(platform: str) -> bool:
    if platform == PLATFORM_LINUX:
        return os.name == "posix"
    if platform == PLATFORM_WINDOWS:
        return os.name == "nt"
    return False


def _apply_refused(platform: str, detail: str) -> ApplyResult:
    return ApplyResult(
        ready=False,
        spawned=False,
        supported_here=_host_can_spawn(platform),
        platform=platform,
        script_path=None,
        command=(),
        helper_pid=None,
        detail=detail,
    )


def _assert_swap_root(path: Path) -> None:
    if path == Path("/") or len(path.parts) < 3 or path.name in {"", ".", ".."}:
        raise UpdateError("refusing to swap an unsafe install path")


def _assert_swap_pair(live: Path, staged: Path) -> None:
    _assert_swap_root(live)
    _assert_swap_root(staged)
    if not live.is_dir() or live.is_symlink():
        raise UpdateError("install directory is missing")
    if not staged.is_dir() or staged.is_symlink():
        raise UpdateError("staging directory is missing")
    if staged.parent != live.parent or staged == live:
        raise UpdateError("staging directory must be a sibling of the live install")
    if _is_inside(staged, live) or _is_inside(live, staged):
        raise UpdateError("staging directory must be a sibling of the live install")


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _helper_path(live: Path, staged: Path, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix="mover-apply-", suffix=suffix)
    os.close(fd)
    path = Path(name).resolve()
    if _is_inside(path, live) or _is_inside(path, staged):
        path.unlink(missing_ok=True)
        raise UpdateError("refusing to write the apply helper inside the app directory")
    return path


def _payload_names(platform: str) -> tuple[str, ...]:
    if platform == PLATFORM_LINUX:
        return LINUX_PAYLOAD
    return WINDOWS_PAYLOAD


def _require_payload(root: Path, platform: str) -> None:
    missing = []
    for name in _payload_names(platform):
        path = root / name
        if path.is_symlink() or not path.is_file():
            missing.append(name)
    if missing:
        raise UpdateError("staged install is missing " + ", ".join(missing))


def _make_executable(root: Path, names: tuple[str, ...]) -> None:
    for name in names:
        path = root / name
        if path.is_symlink():
            raise UpdateError(f"refusing to change permissions through symlink {name}")
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _preserve_user_links(live: Path, staged_root: Path) -> None:
    for rel in USER_DATA_LINKS:
        source = live / rel
        if not source.is_symlink():
            continue
        target = os.readlink(source)
        dest = staged_root / rel
        if dest.exists() or dest.is_symlink():
            _discard_inside(staged_root, dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, dest)


def _assert_no_in_bundle_user_data(live: Path) -> None:
    """Do not replace a bundle that also holds the user's research files."""
    data = live / "data"
    if data.is_symlink():
        raise UpdateError("bundle data link needs manual migration before updating")
    if not data.is_dir():
        return
    placeholders = {
        Path("data/README.md"),
        Path("data/raw/EMR/README.txt"),
    }
    for path in data.rglob("*"):
        rel = path.relative_to(live)
        if path.is_symlink():
            if rel in USER_DATA_LINKS and not _is_inside(path.resolve(), live):
                continue
            raise UpdateError(f"bundle contains a data link that needs manual migration: {rel}")
        if path.is_file() and rel not in placeholders:
            raise UpdateError(
                "bundle contains research data or a processed cache; move it to an external folder in Setup before updating"
            )


def _discard(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _discard_inside(root: Path, path: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    if path.exists() and not _is_inside(path, root):
        raise UpdateError("refusing to delete a path outside staging")
    _discard(path)


def _extract_archive(archive: Path, unpack_root: Path) -> None:
    kind = _archive_kind(archive)
    if kind == "zip":
        _extract_zip(archive, unpack_root)
    else:
        _extract_tar(archive, unpack_root)


def _archive_kind(archive: Path) -> str:
    with archive.open("rb") as handle:
        magic = handle.read(4)
    if magic[:2] == b"\x1f\x8b":
        return "tar.gz"
    if magic[:2] == b"PK":
        return "zip"
    raise UpdateError("archive must be tar.gz or zip")


def _member_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    if not normalized or "\x00" in name or _DRIVE_RE.match(normalized):
        raise UpdateError(f"unsafe archive path {name!r}")
    parts = tuple(part for part in PurePosixPath(normalized).parts if part != ".")
    if not parts or ".." in parts or parts[0] != APP_ROOT or PurePosixPath(normalized).is_absolute():
        raise UpdateError(f"unsafe archive path {name!r}")
    return parts


def _link_escapes(member_parts: tuple[str, ...], target: str) -> bool:
    normalized = target.replace("\\", "/")
    if not normalized or normalized.startswith("/") or _DRIVE_RE.match(normalized):
        return True
    combined = list(member_parts[:-1])
    for part in PurePosixPath(normalized).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not combined:
                return True
            combined.pop()
            continue
        combined.append(part)
    return not combined or combined[0] != APP_ROOT


def _destination(unpack_root: Path, parts: tuple[str, ...]) -> Path:
    dest = unpack_root.joinpath(*parts)
    if not _is_inside(dest, unpack_root):
        raise UpdateError("unsafe archive path")
    cursor = unpack_root
    for part in parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise UpdateError("refusing to follow a symlink while unpacking")
        if cursor.exists() and not cursor.is_dir():
            raise UpdateError("archive path conflicts with a file")
        if not cursor.exists():
            cursor.mkdir()
    if dest.is_symlink():
        raise UpdateError("refusing to follow a symlink while unpacking")
    return dest


def _extract_tar(archive: Path, unpack_root: Path) -> None:
    written = 0
    count = 0
    seen: set[tuple[str, ...]] = set()
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            count += 1
            if count > MAX_ARCHIVE_MEMBERS:
                raise UpdateError("archive has too many members")
            if member.issym() or member.islnk():
                parts = _member_parts(member.name)
                if parts in seen or _link_escapes(parts, member.linkname or ""):
                    raise UpdateError(f"unsafe archive symlink {member.name!r}")
                dest = _destination(unpack_root, parts)
                os.symlink(member.linkname, dest)
                seen.add(parts)
                continue
            if member.isdir():
                parts = _member_parts(member.name)
                dest = _destination(unpack_root, parts)
                dest.mkdir(exist_ok=True)
                seen.add(parts)
                continue
            if not member.isfile():
                raise UpdateError(f"unsafe archive member {member.name!r}")
            parts = _member_parts(member.name)
            if parts in seen:
                raise UpdateError(f"duplicate archive path {member.name!r}")
            dest = _destination(unpack_root, parts)
            source = tar.extractfile(member)
            if source is None:
                raise UpdateError(f"unreadable archive member {member.name!r}")
            written = _write_member(source, dest, written)
            dest.chmod(member.mode & 0o777 or 0o644)
            seen.add(parts)
    _require_single_root(seen)


def _extract_zip(archive: Path, unpack_root: Path) -> None:
    written = 0
    count = 0
    seen: set[tuple[str, ...]] = set()
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            count += 1
            if count > MAX_ARCHIVE_MEMBERS:
                raise UpdateError("archive has too many members")
            name = info.filename
            if name.endswith("/"):
                parts = _member_parts(name)
                _destination(unpack_root, parts).mkdir(exist_ok=True)
                seen.add(parts)
                continue
            mode = info.external_attr >> 16
            parts = _member_parts(name)
            if parts in seen:
                raise UpdateError(f"duplicate archive path {name!r}")
            if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
                if not stat.S_ISLNK(mode):
                    raise UpdateError(f"unsafe archive member {name!r}")
                target = bundle.read(info).decode("utf-8")
                if _link_escapes(parts, target):
                    raise UpdateError(f"unsafe archive symlink {name!r}")
                os.symlink(target, _destination(unpack_root, parts))
                seen.add(parts)
                continue
            dest = _destination(unpack_root, parts)
            with bundle.open(info, "r") as source:
                written = _write_member(source, dest, written)
            permissions = mode & 0o777
            if permissions:
                dest.chmod(permissions)
            seen.add(parts)
    _require_single_root(seen)


def _write_member(source, dest: Path, written: int) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(dest, flags, 0o644)
    try:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UNPACK_BYTES:
                raise UpdateError("unpacked archive exceeds the size cap")
            _write_all(fd, chunk)
    finally:
        os.close(fd)
    return written


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise UpdateError("could not write the complete update")
        view = view[written:]


def _require_single_root(seen: set[tuple[str, ...]]) -> None:
    if not seen or any(parts[0] != APP_ROOT for parts in seen):
        raise UpdateError("archive must contain exactly one MOVER-SIS-Monitor root")


def _linux_script(parent: int, live: Path, staged: Path, backup: Path) -> str:
    live_q = _sh_quote(str(live))
    staged_q = _sh_quote(str(staged))
    backup_q = _sh_quote(str(backup))
    return f"""#!/bin/bash
set -u
parent={parent}
live={live_q}
staged={staged_q}
backup={backup_q}
launch="$live/launch.sh"
binary="$live/MOVER-SIS-Monitor"
case "$live" in
  ""|"/"|"/usr"|"/bin"|"/home"|"/tmp") echo "unsafe live path" >&2; exit 1;;
esac
deadline=$((SECONDS + {APPLY_WAIT_SECONDS}))
while kill -0 "$parent" 2>/dev/null; do
  if (( SECONDS >= deadline )); then
    echo "parent still running" >&2
    exit 2
  fi
  sleep 0.2
done
restore() {{
  if [[ -d "$backup" ]]; then
    rm -rf "$live"
    mv "$backup" "$live"
  fi
}}
if [[ ! -d "$staged" ]]; then
  echo "staging directory missing" >&2
  exit 1
fi
if [[ -d "$live" ]]; then
  rm -rf "$backup"
  if ! mv "$live" "$backup"; then
    echo "could not move live install aside" >&2
    exit 1
  fi
fi
if ! mv "$staged" "$live"; then
  restore
  echo "could not move staged install into place" >&2
  exit 1
fi
if [[ ! -f "$binary" || ! -f "$launch" ]]; then
  restore
  echo "staged install failed verification" >&2
  exit 1
fi
chmod +x "$launch" "$binary" 2>/dev/null || true
"$launch" >/dev/null 2>&1 < /dev/null &
child=$!
sleep {STARTUP_HEALTH_SECONDS}
if ! kill -0 "$child" 2>/dev/null; then
  restore
  "$launch" >/dev/null 2>&1 < /dev/null &
  echo "new app exited during startup; restored previous install" >&2
  exit 1
fi
exit 0
"""


def _windows_script(parent: int, live: Path, staged: Path, backup: Path) -> str:
    live_q = str(live).replace("'", "''")
    staged_q = str(staged).replace("'", "''")
    backup_q = str(backup).replace("'", "''")
    return f"""$ErrorActionPreference = 'Stop'
$parent = {parent}
$live = '{live_q}'
$staged = '{staged_q}'
$backup = '{backup_q}'
$launch = Join-Path $live 'MOVER-SIS-Monitor.exe'
if (-not $live -or $live -eq '\\' -or $live -eq '/') {{
  Write-Error 'unsafe live path'
  exit 1
}}
$deadline = (Get-Date).AddSeconds({APPLY_WAIT_SECONDS})
while (Get-Process -Id $parent -ErrorAction SilentlyContinue) {{
  if ((Get-Date) -gt $deadline) {{ exit 2 }}
  Start-Sleep -Milliseconds 200
}}
function Restore-Live {{
  if (Test-Path -LiteralPath $backup) {{
    if (Test-Path -LiteralPath $live) {{
      Remove-Item -LiteralPath $live -Recurse -Force
    }}
    Move-Item -LiteralPath $backup -Destination $live
  }}
}}
if (-not (Test-Path -LiteralPath $staged -PathType Container)) {{
  Write-Error 'staging directory missing'
  exit 1
}}
if (Test-Path -LiteralPath $live) {{
  if (Test-Path -LiteralPath $backup) {{
    Remove-Item -LiteralPath $backup -Recurse -Force
  }}
  Move-Item -LiteralPath $live -Destination $backup
}}
try {{
  Move-Item -LiteralPath $staged -Destination $live
}} catch {{
  Restore-Live
  exit 1
}}
if (-not (Test-Path -LiteralPath $launch -PathType Leaf)) {{
  Restore-Live
  Write-Error 'staged install failed verification'
  exit 1
}}
try {{
  $child = Start-Process -FilePath $launch -PassThru
  Start-Sleep -Seconds {STARTUP_HEALTH_SECONDS}
  $child.Refresh()
  if ($child.HasExited) {{
    throw 'new app exited during startup'
  }}
}} catch {{
  Restore-Live
  Start-Process -FilePath $launch
  exit 1
}}
exit 0
"""


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
