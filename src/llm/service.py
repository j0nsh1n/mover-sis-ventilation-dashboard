"""Ensure a local Ollama server is available for the desktop app."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from src.llm.ollama_client import OllamaClient, OllamaError

# Process we started this session (do not kill servers we did not start)
_started_proc: subprocess.Popen | None = None


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
    # Common install locations (Linux / mac-style)
    for cand in (
        Path.home() / ".local" / "bin" / "ollama",
        Path("/usr/local/bin/ollama"),
        Path("/usr/bin/ollama"),
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def ensure_ollama(
    *,
    base_url: str = "http://127.0.0.1:11434",
    start_if_needed: bool = True,
    wait_s: float = 25.0,
) -> OllamaStatus:
    """
    Check Ollama API; optionally start ``ollama serve`` in the background.

    Returns status including model list when available.
    """
    global _started_proc
    client = OllamaClient(base_url=base_url, timeout_s=5.0)
    ollama_bin = find_ollama_binary()

    try:
        models = client.list_models()
        return OllamaStatus(
            available=True,
            message="Ollama is running",
            models=models,
            base_url=base_url,
            started_by_app=False,
            ollama_path=ollama_bin,
        )
    except OllamaError:
        pass

    if not start_if_needed:
        return OllamaStatus(
            available=False,
            message="Ollama API not reachable",
            models=[],
            base_url=base_url,
            ollama_path=ollama_bin,
        )

    if not ollama_bin:
        return OllamaStatus(
            available=False,
            message=(
                "Ollama is not installed (or not on PATH). "
                "Install from https://ollama.com and restart the app."
            ),
            models=[],
            base_url=base_url,
        )

    # Start server
    try:
        log_path = Path(
            os.environ.get(
                "MOVER_OLLAMA_LOG",
                str(Path.home() / ".local" / "state" / "mover-sis-monitor" / "ollama.log"),
            )
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "a", encoding="utf-8")
        _started_proc = subprocess.Popen(
            [ollama_bin, "serve"],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
    except OSError as e:
        return OllamaStatus(
            available=False,
            message=f"Failed to start ollama serve: {e}",
            models=[],
            base_url=base_url,
            ollama_path=ollama_bin,
        )

    # Wait until API answers
    deadline = time.time() + wait_s
    last_err = "timeout"
    while time.time() < deadline:
        if _started_proc.poll() is not None:
            return OllamaStatus(
                available=False,
                message=(
                    f"ollama serve exited early (code={_started_proc.returncode}). "
                    "Check that no other process conflicts on port 11434."
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
            time.sleep(0.4)

    return OllamaStatus(
        available=False,
        message=f"Started ollama but API not ready: {last_err}",
        models=[],
        base_url=base_url,
        started_by_app=True,
        ollama_path=ollama_bin,
    )


def ollama_status_summary(status: OllamaStatus) -> str:
    if status.available:
        n = len(status.models)
        extra = " (started by app)" if status.started_by_app else ""
        return f"Ollama OK{extra} · {n} model(s)"
    return f"Ollama unavailable: {status.message}"
