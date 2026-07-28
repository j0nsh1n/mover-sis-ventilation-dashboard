"""Ollama lifecycle: probe, start, stop, unload model (Daily Scheduler pattern)."""

from __future__ import annotations

import os
import platform
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from src.llm.ollama_client import OllamaClient, OllamaError

# Process we started this session
_started_proc: subprocess.Popen | None = None
_app_started_server: bool = False

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


@dataclass
class OllamaStatus:
    available: bool
    message: str
    models: list[str]
    base_url: str
    started_by_app: bool = False
    ollama_path: str | None = None


def find_ollama_binary() -> str | None:
    env = os.environ.get("OLLAMA_PATH") or os.environ.get("MOVER_OLLAMA_PATH")
    if env and Path(env).is_file() and os.access(env, os.X_OK):
        return env
    which = shutil.which("ollama")
    if which:
        return which
    for cand in (
        Path.home() / ".local" / "bin" / "ollama",
        Path("/usr/local/bin/ollama"),
        Path("/usr/bin/ollama"),
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _prepare_ollama_env() -> dict[str, str]:
    try:
        from src.user_settings import apply_ollama_env_from_settings

        apply_ollama_env_from_settings()
    except Exception:
        pass
    return os.environ.copy()


def _api_host_port(base_url: str = DEFAULT_BASE_URL) -> tuple[str, int]:
    u = urlparse(base_url if "://" in base_url else f"http://{base_url}")
    host = u.hostname or "127.0.0.1"
    port = u.port or 11434
    return host, port


def _port_in_use(host: str = "127.0.0.1", port: int = 11434) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def probe_ollama(
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> OllamaStatus:
    """Check API without starting the server."""
    ollama_bin = find_ollama_binary()
    client = OllamaClient(base_url=base_url, timeout_s=3.0)
    try:
        models = client.list_models()
        return OllamaStatus(
            available=True,
            message="Ollama is running",
            models=models,
            base_url=base_url,
            started_by_app=_app_started_server,
            ollama_path=ollama_bin,
        )
    except OllamaError as e:
        return OllamaStatus(
            available=False,
            message=f"Ollama API not reachable ({e})",
            models=[],
            base_url=base_url,
            ollama_path=ollama_bin,
            started_by_app=_app_started_server,
        )


def _wait_until_down(*, base_url: str = DEFAULT_BASE_URL, timeout_s: float = 8.0) -> bool:
    """Wait until API is unreachable (after stop)."""
    host, port = _api_host_port(base_url)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = probe_ollama(base_url=base_url)
        if not st.available and not _port_in_use(host, port):
            return True
        if not st.available:
            # Port may still be in TIME_WAIT; give it a moment
            time.sleep(0.25)
            if not _port_in_use(host, port):
                return True
        time.sleep(0.25)
    return not probe_ollama(base_url=base_url).available


def _wait_until_up(
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout_s: float = 30.0,
    proc: subprocess.Popen | None = None,
) -> OllamaStatus:
    deadline = time.time() + timeout_s
    last_err = "timeout"
    ollama_bin = find_ollama_binary()
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return OllamaStatus(
                available=False,
                message=(
                    f"ollama serve exited early (code={proc.returncode}). "
                    "Port may still be busy after Stop — wait a second and try Start again."
                ),
                models=[],
                base_url=base_url,
                ollama_path=ollama_bin,
                started_by_app=True,
            )
        try:
            models = OllamaClient(base_url=base_url, timeout_s=3.0).list_models()
            return OllamaStatus(
                available=True,
                message="Ollama started by the app",
                models=models,
                base_url=base_url,
                started_by_app=True,
                ollama_path=ollama_bin,
            )
        except OllamaError as e:
            last_err = str(e)
            time.sleep(0.35)
    return OllamaStatus(
        available=False,
        message=f"Started ollama but API not ready: {last_err}",
        models=[],
        base_url=base_url,
        started_by_app=True,
        ollama_path=ollama_bin,
    )


def _spawn_serve(ollama_bin: str, env: dict[str, str]) -> subprocess.Popen:
    log_path = Path(
        os.environ.get(
            "MOVER_OLLAMA_LOG",
            str(Path.home() / ".local" / "state" / "mover-sis-monitor" / "ollama.log"),
        )
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "a", encoding="utf-8")
    kwargs: dict = {
        "stdout": log_f,
        "stderr": subprocess.STDOUT,
        "env": env,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = 0x00000008 | 0x08000000
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([ollama_bin, "serve"], **kwargs)


def start_ollama(
    *,
    base_url: str = DEFAULT_BASE_URL,
    wait_s: float = 30.0,
) -> OllamaStatus:
    """
    Start ``ollama serve`` if the API is down. Retries after a recent Stop.
    """
    global _started_proc, _app_started_server

    already = probe_ollama(base_url=base_url)
    if already.available:
        return already

    env = _prepare_ollama_env()
    ollama_bin = find_ollama_binary()
    if not ollama_bin:
        return OllamaStatus(
            available=False,
            message=(
                "Ollama is not installed (or not on PATH). "
                "Install from https://ollama.com and try Start again."
            ),
            models=[],
            base_url=base_url,
        )

    host, port = _api_host_port(base_url)
    # After Stop the port can lag — wait briefly for it to free
    if _port_in_use(host, port):
        _wait_until_down(base_url=base_url, timeout_s=5.0)

    last_err = ""
    for attempt in range(1, 4):
        already = probe_ollama(base_url=base_url)
        if already.available:
            return already
        try:
            _started_proc = _spawn_serve(ollama_bin, env)
            _app_started_server = True
        except OSError as e:
            last_err = str(e)
            time.sleep(0.6 * attempt)
            continue

        st = _wait_until_up(
            base_url=base_url,
            timeout_s=max(8.0, wait_s / 2),
            proc=_started_proc,
        )
        if st.available:
            return st
        last_err = st.message
        # Early exit / port race — brief pause and retry
        time.sleep(0.8 * attempt)

    return OllamaStatus(
        available=False,
        message=last_err
        or "Could not start Ollama. If you just clicked Stop, wait 2s and try Start again.",
        models=[],
        base_url=base_url,
        ollama_path=ollama_bin,
        started_by_app=_app_started_server,
    )


def ensure_ollama(
    *,
    base_url: str = DEFAULT_BASE_URL,
    start_if_needed: bool = True,
    wait_s: float = 30.0,
) -> OllamaStatus:
    status = probe_ollama(base_url=base_url)
    if status.available:
        return status
    if not start_if_needed:
        return status
    return start_ollama(base_url=base_url, wait_s=wait_s)


# Anchored to the binary name on purpose. A bare "ollama" substring also matches
# unrelated command lines (e.g. `pytest tests/test_ollama_service.py`), and killing
# those takes down the caller instead of the server.
_OLLAMA_CMDLINE_PATTERNS = (
    r"(^|/)ollama serve( |$)",
    r"(^|/)ollama runner( |$)",
    r"(^|/)(ollama_)?llama[-_]server( |$)",
)


def _own_ancestors() -> set[int]:
    """Our parent chain (shell, terminal, `systemd --user`, …) — never signal these."""
    ancestors: set[int] = set()
    pid = os.getpid()
    for _ in range(64):  # bounded walk; /proc is not guaranteed acyclic under races
        try:
            # comm may contain spaces/parens, so split after the final ')'
            fields = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
            ppid = int(fields[1])
        except (OSError, IndexError, ValueError):
            break
        if ppid <= 1 or ppid in ancestors:
            break
        ancestors.add(ppid)
        pid = ppid
    return ancestors


def _is_safe_signal_target(pid: int) -> bool:
    """
    True when *pid* can be signalled without risking our own process tree.

    Never signal init, ourselves, an ancestor, or anything in our own process
    group: those are the app (or the test runner) that called us.
    """
    if pid <= 1 or pid == os.getpid() or pid == os.getppid():
        return False
    if pid in _own_ancestors():
        return False
    try:
        if os.getpgid(pid) == os.getpgid(0):
            return False
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _child_pgid(proc: subprocess.Popen | None) -> int | None:
    """
    Process-group id of *proc*, or None when it cannot be trusted.

    ``os.killpg(1, sig)`` is ``kill(-1, sig)`` — a broadcast to every process the
    user owns, which terminates the whole desktop session. A ``pid`` that is not a
    real int (a test double, say) coerces to 1 via ``__index__``, so validate first.
    """
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError, OSError):
        return None
    if pgid <= 1 or pgid == os.getpgid(0):
        return None
    return pgid


def _pgrep(pattern: str, *, exact: bool = False) -> list[int]:
    """PIDs owned by the current user matching *pattern* (empty on any failure)."""
    cmd = ["pgrep", "-u", str(os.getuid()), "-x" if exact else "-f", pattern]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for token in (r.stdout or "").split():
        try:
            pids.append(int(token))
        except ValueError:
            continue
    return pids


def _terminate_ollama_processes(sig: int = signal.SIGTERM) -> int:
    """Signal ollama server/runner processes; returns how many were signalled."""
    targets: set[int] = set()
    for pattern in _OLLAMA_CMDLINE_PATTERNS:
        targets.update(_pgrep(pattern))
    targets.update(_pgrep("ollama", exact=True))

    signalled = 0
    for pid in sorted(targets):
        if not _is_safe_signal_target(pid):
            continue
        try:
            os.kill(pid, sig)
            signalled += 1
        except (ProcessLookupError, PermissionError, OSError):
            continue
    return signalled


def stop_ollama(*, base_url: str = DEFAULT_BASE_URL) -> tuple[bool, str]:
    """
    Fully stop local Ollama server + model runner (frees VRAM).

    Waits until the API is down so Start can succeed without restarting the app.
    """
    global _started_proc, _app_started_server
    try:
        # Prefer terminating the process we started
        if _started_proc is not None and _started_proc.poll() is None:
            try:
                pgid = _child_pgid(_started_proc) if platform.system() != "Windows" else None
                if pgid is not None:
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        _started_proc.terminate()
                else:
                    # Unknown/untrusted pid — signal only the child we hold
                    _started_proc.terminate()
            except Exception:
                try:
                    _started_proc.terminate()
                except Exception:
                    pass
            try:
                _started_proc.wait(timeout=4)
            except Exception:
                try:
                    _started_proc.kill()
                except Exception:
                    pass

        if platform.system() == "Windows":
            no_win = 0x08000000
            killed = False
            for image in (
                "ollama app.exe",
                "ollama.exe",
                "llama-server.exe",
                "ollama_llama_server.exe",
            ):
                r = subprocess.run(
                    ["taskkill", "/F", "/T", "/IM", image],
                    capture_output=True,
                    text=True,
                    creationflags=no_win,
                )
                if "SUCCESS" in (r.stdout or ""):
                    killed = True
            _started_proc = None
            _app_started_server = False
            _wait_until_down(base_url=base_url, timeout_s=6.0)
            if killed or not probe_ollama(base_url=base_url).available:
                return True, "Ollama stopped (GPU/CPU resources released). Ready to Start again."
            return False, "Ollama wasn't running (or could not be stopped)."

        # Linux: serve + model runners, matched on the binary name only.
        _terminate_ollama_processes(signal.SIGTERM)
        # If still up, escalate to the same (still anchored) set of processes
        if probe_ollama(base_url=base_url).available:
            _terminate_ollama_processes(signal.SIGKILL)

        _started_proc = None
        _app_started_server = False
        down = _wait_until_down(base_url=base_url, timeout_s=8.0)
        if down or not probe_ollama(base_url=base_url).available:
            return True, "Ollama stopped (GPU/CPU resources released). Ready to Start again."
        return (
            False,
            "Could not fully stop Ollama. Wait a few seconds and try Stop again, "
            "or run: pkill -f 'ollama serve'",
        )
    except Exception as ex:
        _started_proc = None
        _app_started_server = False
        return False, str(ex)


def unload_ollama_model(
    model: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> tuple[bool, str]:
    model = (model or "").strip()
    if not model or model.startswith("("):
        return False, "Select a real model tag first."
    try:
        client = OllamaClient(base_url=base_url, timeout_s=15.0)
        client.unload_model(model)
        return True, f"Unloaded '{model}' from memory (server still running)."
    except OllamaError as e:
        msg = str(e)
        if "Cannot reach" in msg or "refused" in msg.lower():
            return False, "Ollama isn't running — click Start first."
        return False, msg
    except Exception as ex:
        return False, str(ex)


def ollama_status_summary(status: OllamaStatus) -> str:
    if status.available:
        n = len(status.models)
        extra = " (started by app)" if status.started_by_app else ""
        return f"Ollama OK{extra} · {n} model(s)"
    return f"Ollama unavailable: {status.message}"
