"""Cloud answer services used while the local model is worked on: Claude API, Claude Code CLI, Codex CLI.

Each client answers ``chat_turn`` like ``OllamaClient``, so ``run_agent`` and the advice check run unchanged
and every cloud answer passes the same answer check as a local one. None of them takes tool calls: the app
asks them only on the extract-only answer paths.

Real MOVER data never reaches a cloud service unless the owner switched on real-data sending; then every
message goes out trimmed (``Trimmer``). Each answer is logged on this computer for training a local model.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from src.llm.grounding import CASE_NUMBER_NOT_SENT, PID_TOKEN, mask_case_numbers
from src.llm.ollama_client import ChatClient, ChatResult, OllamaError

OLLAMA = "ollama"
CLAUDE_API = "claude_api"
CLAUDE_CLI = "claude_cli"
CODEX_CLI = "codex_cli"
BACKENDS = (OLLAMA, CLAUDE_API, CLAUDE_CLI, CODEX_CLI)
CLOUD_BACKENDS = (CLAUDE_API, CLAUDE_CLI, CODEX_CLI)
BACKEND_LABELS = {
    OLLAMA: "Local (Ollama)",
    CLAUDE_API: "Claude API",
    CLAUDE_CLI: "Claude Code",
    CODEX_CLI: "Codex",
}
# A CLI's "default" leaves the model to the CLI. It is still a name, so every step that needs a model
# (the answer check among them) runs; only the CLI's command line leaves it out.
CLI_DEFAULT = "default"
DEFAULT_MODELS = {CLAUDE_API: "claude-opus-5-5", CLAUDE_CLI: CLI_DEFAULT, CODEX_CLI: CLI_DEFAULT}
API_KEY_ENV = "ANTHROPIC_API_KEY"

REAL_DATA_BLOCKED = (
    "Real MOVER cases are not sent to a cloud service. Switch on real-data sending in Settings, "
    "AI, after checking the MOVER data use agreement."
)


class CloudError(OllamaError):
    """A cloud service could not answer. The message never holds a key."""


class CloudBlocked(CloudError):
    """Refused before anything was sent."""


@dataclass(frozen=True)
class CloudConfig:
    backend: str
    model: str = ""
    real_data_allowed: bool = False

    @property
    def is_cloud(self) -> bool:
        return self.backend in CLOUD_BACKENDS

    @property
    def model_id(self) -> str:
        return self.model.strip() or DEFAULT_MODELS.get(self.backend, "")

    @property
    def label(self) -> str:
        """What a turn and the model menu call this service: the service, then the model when one is named."""
        name = BACKEND_LABELS.get(self.backend, self.backend)
        return f"{name} · {self.model_id}" if self.model_id else name


def cloud_config(settings: dict[str, Any]) -> CloudConfig:
    backend = str(settings.get("ai_backend") or OLLAMA)
    if backend not in BACKENDS:
        backend = OLLAMA
    return CloudConfig(backend, str(settings.get("cloud_model") or ""), settings.get("cloud_real_data") is True)


# ----- the Claude API key -----
# The environment variable wins, so a key set for one launch never has to touch the password store.
KEYRING_SERVICE = "MOVER Suite"
KEYRING_USER = "anthropic-api-key"
KEY_FROM_ENV = "environment"
KEY_FROM_STORE = "password store"
# The settings file only says that a key was saved, so a person who never saved one is never asked to unlock
# the password store. The key is read once per run: each read may wait on an unlock prompt.
KEY_SAVED_SETTING = "api_key_saved"
_stored: str | None = None


class KeyStoreError(CloudError):
    """The system password store could not be used. The message never holds a key."""


def _keyring() -> Any:
    import keyring
    from keyring.backends.fail import Keyring as NoBackend

    if isinstance(keyring.get_keyring(), NoBackend):
        raise KeyStoreError("No system password store was found on this computer.")
    return keyring


def _stored_key() -> str:
    global _stored
    from keyring.errors import KeyringError

    from src.user_settings import load_settings

    if _stored is None:
        if load_settings().get(KEY_SAVED_SETTING) is not True:
            return ""
        try:
            _stored = (_keyring().get_password(KEYRING_SERVICE, KEYRING_USER) or "").strip()
        except (KeyringError, KeyStoreError):
            _stored = ""  # a locked or missing store is asked once per run, not on every menu refresh
    return _stored


def api_key() -> tuple[str, str]:
    """The Claude API key and where it came from, or two empty strings."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    if key:
        return key, KEY_FROM_ENV
    key = _stored_key()
    return (key, KEY_FROM_STORE) if key else ("", "")


def save_api_key(key: str) -> None:
    from keyring.errors import KeyringError

    key = key.strip()
    if not key:
        raise KeyStoreError("Paste a key first.")
    global _stored
    from src.user_settings import update_settings

    try:
        _keyring().set_password(KEYRING_SERVICE, KEYRING_USER, key)
    except KeyringError as exc:
        raise KeyStoreError("The system password store refused to save the key (it may be locked).") from exc
    _stored = key
    update_settings(**{KEY_SAVED_SETTING: True})


def forget_api_key() -> None:
    global _stored
    from keyring.errors import KeyringError, PasswordDeleteError

    from src.user_settings import update_settings

    try:
        _keyring().delete_password(KEYRING_SERVICE, KEYRING_USER)
    except PasswordDeleteError:
        pass
    except KeyringError as exc:
        raise KeyStoreError("The system password store refused to remove the key (it may be locked).") from exc
    _stored = ""
    update_settings(**{KEY_SAVED_SETTING: None})


def key_note() -> str:
    """Where the app finds the Claude API key, for Settings. Never the key itself."""
    _key, source = api_key()
    if source == KEY_FROM_ENV:
        return (f"Using the key in {API_KEY_ENV}. It comes before a saved key and is never written anywhere "
                "by the app.")
    if source == KEY_FROM_STORE:
        return "Using the key saved in the system password store. It is never written to the settings file."
    try:
        _keyring()
    except KeyStoreError as exc:
        return f"No key. {exc} Set {API_KEY_ENV} before starting the app instead."
    return ("No key yet. Paste one and choose Save key; it goes to the system password store. "
            "It is never written to the settings file.")


def service_ready(backend: str) -> bool:
    """Whether the service can be asked at all: a key found, or the command installed."""
    if backend == CLAUDE_API:
        return bool(api_key()[0])
    if backend in (CLAUDE_CLI, CODEX_CLI):
        return shutil.which("claude" if backend == CLAUDE_CLI else "codex") is not None
    return True


def service_note(backend: str) -> str:
    """What answering from ``backend`` means, in a sentence or two, or why it cannot answer now."""
    if backend == OLLAMA:
        return "Answers come from Ollama on this computer."
    logged = f"Each answer is logged on this computer for training, in {training_log_dir()}."
    if backend == CLAUDE_API:
        if not service_ready(backend):
            return f"No Claude API key: save one in Settings, AI, or set {API_KEY_ENV}."
        return (f"Questions and their extracts go to Anthropic. If the chosen model declines a question, the API "
                f"may answer it with another Claude model; the log names the model that answered. {logged}")
    command = "claude" if backend == CLAUDE_CLI else "codex"
    if not service_ready(backend):
        return f"The '{command}' command was not found on this computer."
    if backend == CLAUDE_CLI:
        return (f"Runs '{command}' with its tools off, in an empty folder, with its own login. "
                f"Questions and their extracts go to Anthropic. {logged}")
    return (f"Runs '{command}' with its tools off, in an empty folder, with its own login; an answer that used a "
            f"tool is not used. Questions and their extracts go to OpenAI. {logged}")


def may_send(config: CloudConfig, *, real_data: bool) -> bool:
    """Whether this question may go to the configured cloud service at all."""
    return config.is_cloud and (not real_data or config.real_data_allowed)


# ----- what the AI menu offers -----
GROUP_TITLES = {
    CLAUDE_API: "Claude API",
    CLAUDE_CLI: "Claude Code (your login)",
    CODEX_CLI: "Codex (your login)",
}
# The CLIs take these short names; "" leaves the choice to the CLI's own default.
CLI_MODELS = {
    CLAUDE_CLI: ((CLI_DEFAULT, "Default model"), ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")),
    CODEX_CLI: ((CLI_DEFAULT, "Default model"),),
}
OFF_FOR_REAL_CASES = "Off for real MOVER cases: see Settings, AI"


@dataclass(frozen=True)
class Choice:
    """One row of the AI menu. A row with a reason is shown greyed out, with the reason as its text."""

    backend: str
    model: str
    text: str
    reason: str = ""

    @property
    def usable(self) -> bool:
        return not self.reason

    @property
    def shown(self) -> str:
        return self.reason if self.reason and not self.text else (
            f"{self.text} ({self.reason})" if self.reason else self.text)


@dataclass(frozen=True)
class ChoiceGroup:
    backend: str
    title: str
    choices: tuple[Choice, ...]


def cloud_choices(config: CloudConfig, *, real_data: bool, api_models: Sequence[tuple[str, str]] | None = None,
                  api_error: str = "") -> list[ChoiceGroup]:
    """The cloud groups of the AI menu.

    ``api_models`` is the Claude API's own list, or None before it has been fetched; the saved model is always
    offered so a choice never disappears while the list is loading or failing.
    """
    blocked = real_data and not config.real_data_allowed
    if blocked:
        return [ChoiceGroup("cloud", "Cloud services", (Choice("cloud", "", "", OFF_FOR_REAL_CASES),))]
    groups: list[ChoiceGroup] = []
    for backend in CLOUD_BACKENDS:
        title = GROUP_TITLES[backend]
        rows: tuple[Choice, ...]
        if not service_ready(backend):
            reason = ("No key: add one in Settings, AI" if backend == CLAUDE_API else
                      f"The '{'claude' if backend == CLAUDE_CLI else 'codex'}' command was not found")
            rows = (Choice(backend, "", "", reason),)
        elif backend == CLAUDE_API:
            listed = list(api_models) if api_models else [(DEFAULT_MODELS[CLAUDE_API], DEFAULT_MODELS[CLAUDE_API])]
            saved = config.model_id if config.backend == CLAUDE_API else ""
            if saved and saved not in {model_id for model_id, _name in listed}:
                listed.insert(0, (saved, saved))
            rows = tuple(Choice(backend, model_id, name) for model_id, name in listed)
            if api_error:
                rows += (Choice(backend, "", "", f"Could not list models: {api_error}"),)
        else:
            rows = tuple(Choice(backend, model, text) for model, text in CLI_MODELS[backend])
        groups.append(ChoiceGroup(backend, title, rows))
    return groups


# ----- the clients -----

def _split_system(messages: Sequence[dict[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    system: list[str] = []
    turns: list[dict[str, str]] = []
    for message in messages:
        role, content = str(message.get("role", "user")), str(message.get("content") or "")
        if role == "system":
            system.append(content)
        elif role in ("user", "assistant"):
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + content
            else:
                turns.append({"role": role, "content": content})
        else:
            raise CloudError(f"A cloud service cannot take a {role!r} message; it answers from extracts only.")
    return "\n\n".join(system), turns


def _no_tools(tools: Any) -> None:
    if tools:
        raise CloudError("A cloud service cannot run the app's tools; it answers from extracts only.")


def _strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured output needs every object closed to extra keys."""
    out = dict(schema)
    if out.get("type") == "object":
        out.setdefault("additionalProperties", False)
    return out


class ClaudeApiClient:
    """The Anthropic API through the official SDK, with the key from ``api_key()``."""

    def __init__(self, *, timeout_s: float = 300.0, client: Any = None):
        self.timeout_s = timeout_s
        self._client = client

    def _sdk(self) -> Any:
        if self._client is None:
            key, _source = api_key()
            if not key:
                raise CloudError(f"No Claude API key: save one in Settings, AI, or set {API_KEY_ENV}.")
            import anthropic

            self._client = anthropic.Anthropic(api_key=key, timeout=self.timeout_s, max_retries=1)
        return self._client

    def chat_turn(self, model: str, messages: Sequence[dict[str, Any]], *, tools: Any = None,
                  format: dict[str, Any] | None = None, max_tokens: int | None = None,
                  timeout_s: float | None = None, **_ignored: Any) -> ChatResult:
        # temperature and think are Ollama settings; current Claude models choose their own thinking.
        import anthropic

        _no_tools(tools)
        system, turns = _split_system(messages)
        output_config: dict[str, Any] = {"effort": "low" if format else "medium"}
        if format is not None:
            output_config["format"] = {"type": "json_schema", "schema": _strict(format)}
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max(16000, max_tokens or 0),
            "messages": turns,
            "output_config": output_config,
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        }
        if system:
            request["system"] = system
        sdk = self._sdk()
        if timeout_s is not None:
            sdk = sdk.with_options(timeout=timeout_s)
        try:
            response = sdk.beta.messages.create(**request)
        except anthropic.AuthenticationError as exc:
            raise CloudError("The Claude API refused the key.") from exc
        except anthropic.RateLimitError as exc:
            raise CloudError("The Claude API is rate limited; try again shortly.") from exc
        except anthropic.APIStatusError as exc:
            raise CloudError(f"The Claude API returned an error ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise CloudError("The Claude API could not be reached.") from exc
        if response.stop_reason == "refusal":
            raise CloudError("The Claude API declined to answer this question.")
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        return ChatResult(content=text, raw={"model": response.model, "stop_reason": response.stop_reason})


def _transcript(turns: list[dict[str, str]]) -> str:
    """A CLI takes one prompt: earlier turns are written out in order."""
    if len(turns) == 1:
        return turns[0]["content"]
    names = {"user": "Request", "assistant": "Your earlier draft"}
    return "\n\n".join(f"## {names[turn['role']]}\n{turn['content']}" for turn in turns)


def _cli_env() -> dict[str, str]:
    """A CLI answers with its own login; an exported API key would quietly bill that key instead."""
    return {name: value for name, value in os.environ.items() if name != API_KEY_ENV}


def list_api_models(*, timeout_s: float = 20.0, client: Any = None) -> list[tuple[str, str]]:
    """The Claude API's models as (id, display name), newest first, as the API lists them."""
    import anthropic

    if client is None:
        key, _source = api_key()
        if not key:
            raise CloudError(f"No Claude API key: save one in Settings, AI, or set {API_KEY_ENV}.")
        client = anthropic.Anthropic(api_key=key, timeout=timeout_s, max_retries=1)
    try:
        return [(str(model.id), str(model.display_name or model.id)) for model in client.models.list()]
    except anthropic.AuthenticationError as exc:
        raise CloudError("The Claude API refused the key.") from exc
    except anthropic.APIStatusError as exc:
        raise CloudError(f"The Claude API returned an error ({exc.status_code}).") from exc
    except anthropic.APIConnectionError as exc:
        raise CloudError("The Claude API could not be reached.") from exc


def _stop_group(proc: subprocess.Popen) -> None:
    """Stop our own child and anything it started; it was given its own process group."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32" or proc.pid <= 1:
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)  # the child's own group: start_new_session made pgid == pid
        proc.wait(timeout=3)
    except (subprocess.TimeoutExpired, ProcessLookupError, PermissionError):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)


def _mkdir_private(path: Path) -> None:
    """A directory of case text: owner-only, including one that already exists."""
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)


def _append_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)


def _create_private(path: Path):
    """An owner-only binary file for a child process's stdout or stderr."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "wb")


# Codex has no single "tools off" switch, so each tool it could use to read this computer is switched off by
# name, and an answer is used only if its event stream shows no tool ran (``_codex_used_no_tool``).
_CODEX_TOOLS_OFF = (
    "features.shell_tool=false", "features.unified_exec=false", "features.view_image=false",
    "features.apps=false", "features.plugins=false", "features.browser_use=false",
    "features.in_app_browser=false", 'web_search="disabled"',
)
_CODEX_EVENTS = {"thread.started", "turn.started", "turn.completed", "item.started", "item.updated",
                 "item.completed"}
_CODEX_ITEMS_WITHOUT_A_TOOL = {"agent_message", "reasoning", "todo_list"}


def _codex_used_no_tool(events: str) -> bool:
    """True only for a finished turn whose every event and item is a known text-only one.

    Fails closed: an event or item type this list does not know, a failed turn or an error counts as a tool.
    """
    finished = False
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") not in _CODEX_EVENTS:
            return False
        if event["type"].startswith("item."):
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") not in _CODEX_ITEMS_WITHOUT_A_TOOL:
                return False
        finished = finished or event["type"] == "turn.completed"
    return finished


class CliClient:
    """Claude Code or Codex on this computer, run once per answer in a new private folder with its tools off.

    The folder is made fresh under ``workdir`` (the system's temporary folder by default), never inside the
    app's settings folder, which holds the training log.
    """

    def __init__(self, backend: str, workdir: Path | None = None, *, timeout_s: float = 300.0,
                 should_stop: Callable[[], bool] | None = None, binary: str | None = None):
        if backend not in (CLAUDE_CLI, CODEX_CLI):
            raise ValueError(backend)
        self.backend = backend
        self.workdir = workdir
        self.timeout_s = timeout_s
        self.should_stop = should_stop or (lambda: False)
        self.binary = binary or shutil.which("claude" if backend == CLAUDE_CLI else "codex")

    def _command(self, model: str, system: str, call_dir: Path, schema: dict[str, Any] | None) -> list[str]:
        assert self.binary is not None
        if self.backend == CLAUDE_CLI:
            command = [self.binary, "-p", "--output-format", "json", "--tools", "", "--safe-mode",
                       "--strict-mcp-config", "--no-session-persistence", "--system-prompt", system]
            if schema is not None:
                command += ["--json-schema", json.dumps(schema)]
        else:
            command = [self.binary, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
                       "--ignore-user-config", "--ignore-rules", "--json", "--color", "never",
                       "-C", str(call_dir), "-o", str(call_dir / "answer.txt")]
            for setting in _CODEX_TOOLS_OFF:
                command += ["-c", setting]
            if schema is not None:
                _write_private(call_dir / "schema.json", json.dumps(schema))
                command += ["--output-schema", str(call_dir / "schema.json")]
        if model and model != CLI_DEFAULT:
            command += ["--model" if self.backend == CLAUDE_CLI else "-m", model]
        if self.backend == CODEX_CLI:
            command.append("-")
        return command

    def chat_turn(self, model: str, messages: Sequence[dict[str, Any]], *, tools: Any = None,
                  format: dict[str, Any] | None = None, timeout_s: float | None = None,
                  **_ignored: Any) -> ChatResult:
        _no_tools(tools)
        name = BACKEND_LABELS[self.backend]
        if not self.binary:
            raise CloudError(f"{name} is not installed: the '{'claude' if self.backend == CLAUDE_CLI else 'codex'}' "
                             "command was not found.")
        system, turns = _split_system(messages)
        schema = _strict(format) if format is not None else None
        prompt = _transcript(turns)
        if self.backend == CODEX_CLI and system:
            prompt = f"{system}\n\n{prompt}"
        if self.workdir is not None:
            _mkdir_private(self.workdir)
        call_dir = Path(tempfile.mkdtemp(prefix="mover-cli-", dir=self.workdir))
        try:
            _write_private(call_dir / "prompt.txt", prompt)
            with open(call_dir / "prompt.txt", "rb") as stdin, _create_private(call_dir / "out.txt") as stdout, \
                    _create_private(call_dir / "err.txt") as stderr:
                proc = subprocess.Popen(
                    self._command(model, system, call_dir, schema), stdin=stdin, stdout=stdout, stderr=stderr,
                    cwd=call_dir, start_new_session=sys.platform != "win32", env=_cli_env(),
                )
                deadline = time.monotonic() + (timeout_s or self.timeout_s)
                while proc.poll() is None:
                    if self.should_stop():
                        _stop_group(proc)
                        raise CloudError(f"{name} was stopped.")
                    if time.monotonic() > deadline:
                        _stop_group(proc)
                        raise TimeoutError(f"{name} did not answer within {timeout_s or self.timeout_s:g} s.")
                    time.sleep(0.1)
            answer_file = call_dir / "answer.txt"
            if answer_file.is_file():
                os.chmod(answer_file, 0o600)
            out = (call_dir / "out.txt").read_text(encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                raise CloudError(f"{name} exited with status {proc.returncode}.")
            text = self._answer(out, call_dir, schema is not None)
        finally:
            shutil.rmtree(call_dir, ignore_errors=True)
        if not text.strip():
            raise CloudError(f"{name} returned an empty answer.")
        return ChatResult(content=text, raw={"model": model or "default", "backend": self.backend})

    def _answer(self, out: str, call_dir: Path, structured: bool) -> str:
        name = BACKEND_LABELS[self.backend]
        if self.backend == CODEX_CLI:
            if not _codex_used_no_tool(out):
                raise CloudError(f"{name} used a tool or did not finish its turn, so its answer was not used.")
            answer = call_dir / "answer.txt"
            return answer.read_text(encoding="utf-8", errors="replace") if answer.is_file() else ""
        try:
            data = json.loads(out)
        except ValueError as exc:
            raise CloudError(f"{name} did not return JSON.") from exc
        if not isinstance(data, dict) or data.get("is_error"):
            raise CloudError(f"{name} reported an error.")
        if structured and data.get("structured_output") is not None:
            return json.dumps(data["structured_output"])
        return str(data.get("result") or "")


# ----- trimming real data -----

_DATE_TIME = re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?\b")
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_EVENT_LINE = re.compile(r"^- (\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?): (.+)$")
# Age phrases the extract and an earlier answer may use. "98.6F" can match the
# sex-letter form and be over-banded; that is harmless (a temperature is not an
# identifier, and a wider band only hides more) and is covered by a test.
_AGE_FORM = re.compile(
    r"(?<![\w.\u2013-])(?:"
    r"(?P<label>Age:\s*|aged\s+|age\s+of\s+|age\s+(?:is\s+|was\s+)?)"
    r"(?P<nlabel>\d{1,3}(?:\.\d+)?)(?:\s*[-\u2013]\s*(?P<nhigh>\d{1,3}))?"
    r"(?!\d|\.\d|\s*(?:%|\u00b0|(?:kg|g|lbs?|cm|mm|ml|mg|mcg|bpm|min|mmhg|cmh2o|l|degrees?|deg)\b))"
    # "he is 47": the number counts as an age only when nothing measurement-like follows it, so
    # "he was 120/80", "patient was 100 on room air" and "he is 3 days post-op" stay as they are.
    r"|(?P<subject>(?:he|she|they|who|patient|pt\.?|man|woman|male|female|boy|girl)\s+(?:is|was|are|were)\s+"
    r"|(?:he|she)'s\s+|(?:man|woman|male|female|boy|girl)\s+of\s+|turned\s+)"
    r"(?P<nsubject>\d{1,3})"
    r"(?=[.,;!?)](?!\d)|\s*$|\s+(?:years?|yrs?|y\.?o\.?|old|and|at|that|when|with|but|so|now|today)\b)"
    r"|(?P<n>\d{1,3}(?:\.\d+)?)(?P<tail>"
    r"-year[- ]old"
    r"|-yrs?-old"
    r"|-y-o\b"
    r"|y/o\b"
    r"|yrs?\b"
    r"|\s+years?\s+old"
    r"|\s+year-old"
    r"|\s+yrs?\s+old"
    r"|\s+y\.o\.?"
    r"|\s+yrs?-old"
    r"|\s+y-o\b"
    r"|\s+years?-old"
    r"|\s+years?\s+of\s+age"
    r"|\s+y\s+o\b"
    r"|\s+yrs?\.\s+old"
    r"|\s*/\s*(?:female|male|[FM])(?![A-Za-z])"
    r"|-[FM](?![A-Za-z])"
    r"|\s+yrs?\s+(?=[FM](?![A-Za-z]))"
    r"|\s+y/o"
    r"|\s+yo"
    r"|yo"
    r"|y(?![A-Za-z/]|\.\w)"
    r"|(?P<gap>[ \t]?)(?P<sex>[FM])(?![A-Za-z])"
    r"))",
    re.IGNORECASE,
)
# A date with no clock time. Datetimes are already "[time not sent]" by then.
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE_ONLY = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH + r"\s+\d{4}\b"
    r"|\b" + _MONTH + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
    re.IGNORECASE,
)
# A military-style clock time such as "0815h".
_CLOCK_H = re.compile(r"\b(?:[01]\d|2[0-3])[0-5]\dh\b", re.IGNORECASE)
# The waveform section is presence of a local folder. Its body is a path.
_WAVE_SECTION = re.compile(r"## Waveform data\n(?:- .*\n?)*")
# Absolute paths: POSIX with at least two segments, and Windows "C:\...".
# A slash after a scheme or another slash is a URL, not a filesystem path.
_ABS_PATH = re.compile(r"(?<![\w:/])/(?:[^/\s]+/){1,}[^/\s]+|[A-Za-z]:\\[^\s]+")
LINE_CAP = 400
# The procedure is free text and may hold a name, so only its category leaves the computer. First match wins:
# the more specific services come before the general ones that share words with them ("laparoscopic").
PROCEDURE_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (category, re.compile(r"\b(?:" + words + r")", re.IGNORECASE)) for category, words in (
        ("Transplant", r"transplant"),
        ("Cardiac", r"cabg|coronary|cardiac|heart|valve|mitral|sternotomy"),
        ("Thoracic", r"lobectomy|thoracoscop|thoracotomy|vats\b|lung|pneumonectomy|esophagectomy|mediastin"),
        ("Neurosurgery", r"craniotomy|craniectomy|ventriculo|burr hole|brain|intracranial"),
        ("Spine", r"laminectomy|laminotomy|discectomy|spin|vertebr|kyphoplasty|lumbar|foraminotomy"),
        ("Vascular", r"endarterectomy|aneurysm|evar\b|fistula|carotid|vascular|bypass graft|embolectomy"),
        ("Urology", r"prostat|cystoscop|cystectomy|nephr|ureter|bladder|kidney|turp\b|urethr|orchi"),
        ("Gynecology and obstetrics", r"hysterect|cesarean|caesarean|c-section|ovar|uter|myomect|salping"
                                      r"|dilation and curettage|d&c\b"),
        ("Head and neck", r"thyroid|parathyroid|tonsil|adenoid|sinus|laryn|tracheost|mastoid|septo|parotid"
                          r"|neck dissection|tympan"),
        ("Eye", r"cataract|vitrect|retin|ocular|strabism|cornea"),
        ("Orthopedic", r"arthroplast|arthroscop|orif\b|fractur|fixation|knee|hip\b|shoulder|joint|tendon"
                       r"|ligament|osteotomy|amputation|open reduction|closed reduction|acl\b|menisc|rotator cuff"),
        ("Breast and plastic", r"breast|mastectomy|mammoplast|flap|skin graft|plastic|reconstruct"),
        ("Endoscopy", r"endoscop|colonoscop|egd\b|bronchoscop|ercp\b"),
        ("General and abdominal", r"cholecyst|append|hernia|colectomy|bowel|gastr|whipple|pancrea|hepat|liver"
                                  r"|splen|laparotom|laparoscop|bariatric|colon|rect|abdom"),
    ))
PROCEDURE_OTHER = "Other or not classified"
# The extract states the procedure in its heading ("Procedure: X. Age: ...") and as a list item ("- Procedure: X").
_PROCEDURE_FIELD = re.compile(r"^(- )?Procedure: (.*?)(?=\. Age: |$)", re.MULTILINE)


def procedure_category(procedure: str) -> str:
    """The service a procedure belongs to, from a fixed list, so no word of the procedure text is passed on."""
    for category, pattern in PROCEDURE_CATEGORIES:
        if pattern.search(procedure):
            return category
    return PROCEDURE_OTHER
_EARLIER = "Earlier question: "


def age_band(age: float) -> str:
    low = int(age) // 5 * 5
    return f"{low}–{low + 4}"


def _band_ages(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        if match.group("label"):
            low = float(match.group("nlabel"))
            if match.group("nhigh"):
                # "age 47-52": one band from the lower one's start to the upper one's end.
                high = float(match.group("nhigh"))
                low, high = min(low, high), max(low, high)
                return match.group("label") + f"{age_band(low).split('–')[0]}–{age_band(high).split('–')[1]}"
            return match.group("label") + age_band(low)
        if match.group("subject"):
            return match.group("subject") + age_band(float(match.group("nsubject")))
        return age_band(float(match.group("n"))) + match.group("tail")

    return _AGE_FORM.sub(repl, text)


def _events_from_incision(section: str) -> str:
    """Event clock times become minutes from incision; without an incision time no time goes out."""
    lines = section.split("\n")
    stamps = [(i, _EVENT_LINE.match(line)) for i, line in enumerate(lines)]
    incision = next((datetime.fromisoformat(m.group(1)) for _, m in stamps
                     if m and m.group(2).strip().lower() == "incision"), None)
    for i, match in stamps:
        if match is None:
            continue
        if incision is None:
            lines[i] = f"- time not sent: {match.group(2)}"
        else:
            minutes = round((datetime.fromisoformat(match.group(1)) - incision).total_seconds() / 60)
            lines[i] = f"- minute {minutes:+d} from incision: {match.group(2)}"
    return "\n".join(lines)


class Trimmer:
    """What real-data sending lets out: case aliases, 5-year age bands, minutes from incision, procedure
    categories, a line cap."""

    def __init__(self, pids: Sequence[str]):
        letters = [chr(ord("A") + i) for i in range(26)]
        self.aliases = {str(pid): f"Case-{letters[i]}" for i, pid in enumerate(dict.fromkeys(pids)) if i < 26}
        self._pid = re.compile("|".join(re.escape(pid) for pid in sorted(self.aliases, key=len, reverse=True))
                               ) if self.aliases else None
        self._alias = re.compile(r"\bCase-([A-Z])\b")

    def _alias_for(self, pid: str) -> str:
        """An alias for a case number nobody listed: the safety net for history or text that names one."""
        alias = self.aliases.get(pid)
        if alias is None:
            if len(self.aliases) >= 26:
                return CASE_NUMBER_NOT_SENT
            alias = f"Case-{chr(ord('A') + len(self.aliases))}"
            self.aliases[pid] = alias
        return alias

    def outbound(self, text: str) -> str:
        text = _PROCEDURE_FIELD.sub(
            lambda m: f"{m.group(1) or ''}Procedure category: {procedure_category(m.group(2))}", text)
        if self._pid is not None:
            text = self._pid.sub(lambda m: self.aliases[m.group(0)], text)
        # Anything still shaped like a case number goes out aliased too, listed or not.
        text = PID_TOKEN.sub(lambda m: self._alias_for(m.group(0)), text)
        text = re.sub(r"(## Procedure events\n)((?:- .*\n?)*)",
                      lambda m: m.group(1) + _events_from_incision(m.group(2)), text)
        text = _DATE_TIME.sub("[time not sent]", text)
        text = _CLOCK.sub("[time not sent]", text)
        text = _band_ages(text)
        text = _DATE_ONLY.sub("[date not sent]", text)
        text = _CLOCK_H.sub("[time not sent]", text)
        text = _WAVE_SECTION.sub("## Waveform data\nWaveform data: not sent\n", text)
        text = _ABS_PATH.sub("[path not sent]", text)
        lines = text.split("\n")
        if len(lines) > LINE_CAP:
            lines = lines[:LINE_CAP] + [f"[{len(lines) - LINE_CAP} more lines not sent]"]
        return "\n".join(lines)

    def inbound(self, text: str) -> str:
        back = {alias: pid for pid, alias in self.aliases.items()}
        return self._alias.sub(lambda m: back.get(m.group(0), m.group(0)), text)


class TrimmingClient:
    """Wraps a client so every message is trimmed. ``sends_trimmed`` tells the agent
    that this call is real data leaving the computer, so only trimmed cloud turns
    may be included as history.
    """

    sends_trimmed = True
    leaves_computer = True

    def __init__(self, inner: ChatClient, trimmer: Trimmer):
        self.inner = inner
        self.trimmer = trimmer

    def chat_turn(self, model: str, messages: Sequence[dict[str, Any]], **kwargs: Any) -> ChatResult:
        sent = [{**m, "content": self.trimmer.outbound(str(m.get("content") or ""))} for m in messages]
        result = self.inner.chat_turn(model, sent, **kwargs)
        return ChatResult(content=self.trimmer.inbound(result.content), tool_calls=result.tool_calls, raw=result.raw)


# ----- the training log -----

@dataclass
class RecordingClient:
    """Keeps each call exactly as it went out and came back, for the training log."""

    leaves_computer: ClassVar[bool] = True
    inner: ChatClient
    calls: list[dict[str, Any]] = field(default_factory=list)

    def chat_turn(self, model: str, messages: Sequence[dict[str, Any]], **kwargs: Any) -> ChatResult:
        call: dict[str, Any] = {"messages": [dict(m) for m in messages],
                                "structured": kwargs.get("format") is not None}
        self.calls.append(call)
        try:
            result = self.inner.chat_turn(model, messages, **kwargs)
        except Exception as exc:
            call["error"] = str(exc)
            raise
        call["reply"] = result.content
        call["served_by"] = (result.raw or {}).get("model", model)
        return result


def training_log_dir() -> Path:
    from src.user_settings import settings_path

    return settings_path().parent / "ai-log"


def write_training_record(record: dict[str, Any], folder: Path | None = None) -> Path:
    """Append one answer to today's file. The record never holds a key: none passes through it."""
    folder = folder or training_log_dir()
    _mkdir_private(folder)
    now = datetime.now(timezone.utc)
    path = folder / f"{now:%Y-%m-%d}.jsonl"
    _append_private(path, json.dumps({"time": now.isoformat(timespec="seconds"), **record}, ensure_ascii=False) + "\n")
    return path


@dataclass
class CloudSession:
    """One question's cloud client and what it logs; built only when the question may be sent."""

    config: CloudConfig
    real_data: bool
    client: ChatClient
    recorder: RecordingClient
    trimmer: Trimmer | None = None
    # The data on screen when asked. Article questions go out untrimmed (published text is not MOVER data) but
    # are still logged under the data they were asked on.
    data_is_real: bool = False

    def log(self, *, question: str, kind: str, answer: str, trace: Sequence[str]) -> Path | None:
        """Keep this answer for training. A log that cannot be written never costs the user the answer."""
        try:
            return self._write(question=question, kind=kind, answer=answer, trace=trace)
        except OSError:
            return None

    def history_sent(self) -> list[dict[str, str]]:
        """The earlier turns on the first call, as that call actually sent them.

        Trimming has already happened: the question and answer here are the
        trimmed text, equal to what the far end received. A call that failed
        is not logged, so a cancelled or failed turn writes no history.
        """
        if not self.recorder.calls:
            return []
        messages = self.recorder.calls[0].get("messages") or []
        found: list[dict[str, str]] = []
        index = 1
        while index + 1 < len(messages):
            user, assistant = messages[index], messages[index + 1]
            content = str(user.get("content") or "")
            if user.get("role") != "user" or not content.startswith(_EARLIER):
                break
            if assistant.get("role") != "assistant":
                break
            found.append({"question": content[len(_EARLIER):], "answer": str(assistant.get("content") or "")})
            index += 2
        return found

    def _write(self, *, question: str, kind: str, answer: str, trace: Sequence[str]) -> Path:
        withheld = any(str(step).startswith("withheld:") for step in trace)
        if self.data_is_real:
            # Real data: keep only text that crossed the wire. The question as it was sent, the service's own
            # last answer as it came back, and no local trace, whose checks read the untrimmed case.
            question = self.trimmer.outbound(question) if self.trimmer is not None else mask_case_numbers(question)
            answer = self._last_reply()
            trace = []
        return write_training_record({
            "backend": self.config.backend,
            "model": self.config.model_id or "default",
            "kind": kind,
            "data": "real" if self.data_is_real else "fictional",
            "trimmed": self.trimmer is not None,
            "question": question,
            "history": self.history_sent(),
            "calls": self.recorder.calls,
            "answer": answer,
            "trace": list(trace),
            "withheld": withheld,
        })

    def _last_reply(self) -> str:
        """The last free-text reply as received: the answer or its repair, never the structured advice check."""
        for call in reversed(self.recorder.calls):
            if not call.get("structured") and "reply" in call:
                return str(call["reply"])
        return ""


def _alias_pids(pids: Sequence[str], history: Sequence[Any]) -> list[str]:
    """Current candidates, then every prior turn's cases not already included."""
    ordered = [str(pid) for pid in pids if pid]
    seen = set(ordered)
    for turn in history:
        for pid in getattr(turn, "pids", ()):
            pid = str(pid)
            if pid and pid not in seen:
                seen.add(pid)
                ordered.append(pid)
    return ordered


def open_cloud_session(config: CloudConfig, *, real_data: bool, pids: Sequence[str] = (),
                       history: Sequence[Any] = (),
                       timeout_s: float = 300.0, should_stop: Callable[[], bool] | None = None,
                       workdir: Path | None = None, articles_on_real_data: bool = False) -> CloudSession:
    """Refuses before anything is built when this question may not be sent.

    ``real_data`` is whether MOVER case text goes out, which needs real-data sending and trimming.
    ``articles_on_real_data`` marks an article question asked while real data is on screen: it sends no case
    text, so it is not trimmed, but its log keeps only what was sent.

    ``history`` is read only after the question is allowed out, and only for
    its case numbers. A refusal names the block and nothing from the history.
    """
    if not config.is_cloud:
        raise ValueError("not a cloud service")
    if not may_send(config, real_data=real_data):
        raise CloudBlocked(REAL_DATA_BLOCKED)
    inner: ChatClient
    if config.backend == CLAUDE_API:
        inner = ClaudeApiClient(timeout_s=timeout_s)
    else:
        inner = CliClient(config.backend, workdir, timeout_s=timeout_s, should_stop=should_stop)
    recorder = RecordingClient(inner)
    if not real_data:
        return CloudSession(config, real_data, recorder, recorder, data_is_real=articles_on_real_data)
    trimmer = Trimmer(_alias_pids(pids, history))
    return CloudSession(config, real_data, TrimmingClient(recorder, trimmer), recorder, trimmer, data_is_real=True)
