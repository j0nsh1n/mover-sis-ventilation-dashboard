"""Cloud answer services: what leaves this computer, when, in what form, and what is kept for training."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.llm import cloud
from src.llm.cloud import (
    CLAUDE_API,
    CLAUDE_CLI,
    CODEX_CLI,
    REAL_DATA_BLOCKED,
    CliClient,
    ClaudeApiClient,
    CloudBlocked,
    CloudConfig,
    CloudError,
    RecordingClient,
    Trimmer,
    TrimmingClient,
    age_band,
    cloud_config,
    open_cloud_session,
)
from src.llm.ollama_client import ChatResult
from src.llm.retrieve import answer_from_retrieval
from src.services.retrieval import SearchCandidate

_REAL_KILLPG = getattr(os, "killpg", None)
KEY = "sk-ant-TESTKEY-DO-NOT-LEAK-0123456789"
PID = "SYNQ7Z3K9M2P"
EXTRACT = f"""SOURCE EXTRACT (analyzed case) PID={PID}
Procedure: Lumbar Laminectomy. Age: 47. Sex: F. Ventilator rows: yes. Record scope: analyzed case.
CASE PID: {PID}

## Demographics & procedure
- Age: 47
- Sex: F
- OR / data span start: 2016-03-01 09:00:00
- OR / data span end: 2016-03-01 10:44:00

## Flag episodes (contiguous runs)
- agent_high [critical]: t=28–37 min, during maintenance

## Procedure events
- 2016-03-01 09:04:00: Intubation
- 2016-03-01 09:15:00: Incision
- 2016-03-01 10:32:00: Extubation

## Waveform data
- Wave root not configured in the app.
Case facts: PID {PID}, age 47"""


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv(cloud.API_KEY_ENV, raising=False)
    emr = tmp_path / "EMR"
    emr.mkdir()
    monkeypatch.setenv("MOVER_EMR_DIR", str(emr))
    return tmp_path / "config"


def _labels(kwargs: dict) -> ChatResult:
    return ChatResult(content=json.dumps(dict.fromkeys(kwargs["format"]["required"], "description")))


class Remote:
    """Stands in for the far end: keeps every message list it receives and answers with ``reply``."""

    def __init__(self, reply: str):
        self.reply = reply
        self.received: list[list[dict]] = []

    def chat_turn(self, model, messages, **kwargs):
        self.received.append([dict(m) for m in messages])
        if kwargs.get("format"):
            return _labels(kwargs)
        return ChatResult(content=self.reply, raw={"model": "served-model"})


# ----- which service, and whether a question may go -----

def test_the_local_model_is_the_default_and_unknown_values_fall_back_to_it():
    assert cloud_config({}).backend == "ollama" and not cloud_config({}).is_cloud
    assert cloud_config({"ai_backend": "something-else"}).backend == "ollama"
    assert cloud_config({"ai_backend": CLAUDE_API}).model_id == "claude-opus-5-5"
    assert cloud_config({"ai_backend": CLAUDE_CLI, "cloud_model": " sonnet "}).label == "Claude Code · sonnet"
    assert cloud_config({"ai_backend": CODEX_CLI}).label == "Codex · default", "the CLI chooses; the label says so"


@pytest.mark.parametrize("value", [None, False, "true", 1, "on"])
def test_real_data_sending_is_on_only_when_the_owner_saved_it_on(value):
    assert cloud_config({"ai_backend": CLAUDE_API, "cloud_real_data": value}).real_data_allowed is False
    assert cloud_config({"ai_backend": CLAUDE_API, "cloud_real_data": True}).real_data_allowed is True


@pytest.mark.parametrize("backend", [CLAUDE_API, CLAUDE_CLI, CODEX_CLI])
def test_real_cases_are_refused_before_any_client_exists_when_sending_is_off(backend, monkeypatch):
    built = []
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kw: built.append(kw))
    monkeypatch.setattr(cloud, "CliClient", lambda *a, **kw: built.append(a))
    with pytest.raises(CloudBlocked) as refused:
        open_cloud_session(CloudConfig(backend), real_data=True, pids=[PID])
    assert str(refused.value) == REAL_DATA_BLOCKED and built == []


def test_fictional_data_goes_as_is_and_real_data_only_trimmed(monkeypatch):
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kw: Remote("x"))
    demo = open_cloud_session(CloudConfig(CLAUDE_API), real_data=False, pids=[PID])
    assert isinstance(demo.client, RecordingClient)
    real = open_cloud_session(CloudConfig(CLAUDE_API, real_data_allowed=True), real_data=True, pids=[PID])
    assert isinstance(real.client, TrimmingClient) and real.client.inner is real.recorder


def test_the_desktop_never_builds_a_cloud_client_for_real_cases_with_sending_off(monkeypatch):
    from src.desktop.research_workers import _answer_client

    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kw: pytest.fail("a client was built"))
    with pytest.raises(CloudBlocked):
        _answer_client(CloudConfig(CLAUDE_API), real_data=True, pids=[PID], should_stop=lambda: False)
    local, session = _answer_client(None, real_data=True, pids=[PID], should_stop=lambda: False)
    assert session is None and type(local).__name__ == "OllamaClient"


# ----- trimming real data -----

def test_trimmed_text_has_aliases_age_bands_and_minutes_from_incision_only():
    sent = Trimmer([PID]).outbound(EXTRACT)
    assert PID not in sent and "Case-A" in sent
    assert "47" not in sent and sent.count("45–49") == 3
    assert "2016" not in sent and "09:" not in sent and "10:" not in sent
    assert "- minute -11 from incision: Intubation" in sent
    assert "- minute +0 from incision: Incision" in sent and "- minute +77 from incision: Extubation" in sent
    assert "t=28–37 min" in sent, "minutes from incision are what the analysis needs"


def test_event_times_do_not_go_out_without_an_incision_to_count_from():
    sent = Trimmer([PID]).outbound("## Procedure events\n- 2016-03-01 09:04:00: Intubation\n")
    assert sent == "## Procedure events\n- time not sent: Intubation\n"


@pytest.mark.parametrize("age, band", [(0, "0–4"), (4.9, "0–4"), (45, "45–49"), (49, "45–49"), (50, "50–54")])
def test_ages_go_out_in_five_year_bands(age, band):
    assert age_band(age) == band


def test_aliases_come_back_as_whole_tokens_only():
    trimmer = Trimmer(["pid-one", "pid-two"])
    assert trimmer.outbound("pid-two then pid-one") == "Case-B then Case-A"
    assert trimmer.inbound("Case-A and Case-B, not Case-AB or Case-Z") == "pid-one and pid-two, not Case-AB or Case-Z"


def test_a_long_message_is_cut_at_the_line_cap():
    def rows(n: int) -> str:
        return "\n".join(f"row {i}" for i in range(n))

    assert Trimmer([]).outbound(rows(cloud.LINE_CAP)) == rows(cloud.LINE_CAP)
    lines = Trimmer([]).outbound(rows(cloud.LINE_CAP + 1)).split("\n")
    assert lines == [*rows(cloud.LINE_CAP).split("\n"), "[1 more lines not sent]"]


def _candidate() -> SearchCandidate:
    return SearchCandidate(pid=PID, source_label="analyzed case", analyzed=True, procedure="Lumbar Laminectomy",
                           age=47.0, gender="F", source_text=f"PID {PID}.", rank_method="embedding", score=0.9)


def test_every_call_of_a_real_answer_goes_out_trimmed_and_the_answer_names_the_real_case(monkeypatch):
    remote = Remote("Case-A had a critical agent_high episode at t=28–37 min.")
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kw: remote)
    session = open_cloud_session(CloudConfig(CLAUDE_API, real_data_allowed=True), real_data=True, pids=[PID])
    result = answer_from_retrieval(question=f"What happened in {PID}?", model="claude-opus-5-5",
                                   candidates=[_candidate()], extracts=[EXTRACT], client=session.client)
    assert len(remote.received) >= 2, "the answer and the advice check both went out"
    for messages in remote.received:
        text = json.dumps(messages, ensure_ascii=False)
        assert PID not in text and "age 47" not in text and "Age: 47" not in text and "2016-03-01" not in text
    assert PID in result.answer and "Case-A" not in result.answer


def test_a_fictional_answer_goes_out_unchanged(monkeypatch):
    remote = Remote(f"{PID} had a critical agent_high episode at t=28–37 min.")
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kw: remote)
    session = open_cloud_session(CloudConfig(CLAUDE_API), real_data=False, pids=[PID])
    answer_from_retrieval(question="Which case?", model="m", candidates=[_candidate()], extracts=[EXTRACT],
                          client=session.client)
    assert PID in json.dumps(remote.received[0])


# ----- the Claude API client -----

class FakeSdk:
    def __init__(self, response=None, error=None):
        self.requests: list[dict] = []
        self.response = response or SimpleNamespace(
            stop_reason="end_turn", model="claude-opus-5-5", content=[SimpleNamespace(type="text", text="An answer.")])
        self.error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, **kw):
        return self

    def _create(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def test_the_api_request_carries_the_system_text_turns_and_closed_schema_and_no_ollama_settings():
    sdk = FakeSdk()
    result = ClaudeApiClient(client=sdk).chat_turn(
        "claude-opus-5-5",
        [{"role": "system", "content": "Rules."}, {"role": "user", "content": "Q?"},
         {"role": "assistant", "content": "Draft."}, {"role": "user", "content": "Fix it."}],
        temperature=0.0, think=False, format={"type": "object", "properties": {}, "required": []}, max_tokens=40,
    )
    request = sdk.requests[0]
    assert result.content == "An answer." and result.raw == {"model": "claude-opus-5-5", "stop_reason": "end_turn"}
    assert request["system"] == "Rules." and [m["role"] for m in request["messages"]] == ["user", "assistant", "user"]
    assert request["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": {
        "type": "object", "properties": {}, "required": [], "additionalProperties": False}}}
    assert request["max_tokens"] >= 16000 and "temperature" not in request and "thinking" not in request
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]


def test_without_a_key_nothing_is_sent_and_the_message_names_the_variable():
    with pytest.raises(CloudError) as missing:
        ClaudeApiClient().chat_turn("m", [{"role": "user", "content": "Q?"}])
    assert cloud.API_KEY_ENV in str(missing.value)


def test_a_refused_key_is_reported_without_the_key(monkeypatch):
    import anthropic
    import httpx2

    monkeypatch.setenv(cloud.API_KEY_ENV, KEY)
    response = httpx2.Response(401, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    sdk = FakeSdk(error=anthropic.AuthenticationError(f"invalid x-api-key {KEY}", response=response, body=None))
    with pytest.raises(CloudError) as refused:
        ClaudeApiClient(client=sdk).chat_turn("m", [{"role": "user", "content": "Q?"}])
    assert KEY not in str(refused.value) and "refused the key" in str(refused.value)


def test_a_declined_answer_is_an_error_not_an_empty_answer():
    declined = SimpleNamespace(stop_reason="refusal", model="m", content=[])
    with pytest.raises(CloudError, match="declined"):
        ClaudeApiClient(client=FakeSdk(response=declined)).chat_turn("m", [{"role": "user", "content": "Q?"}])


@pytest.mark.parametrize("client", [ClaudeApiClient(client=FakeSdk()), CliClient(CLAUDE_CLI, Path("."), binary="x")])
def test_no_cloud_service_takes_tool_calls(client):
    with pytest.raises(CloudError, match="tools"):
        client.chat_turn("m", [{"role": "user", "content": "Q?"}], tools=[{"name": "t"}])


# ----- the command-line clients, against stand-in commands -----

def _fake_command(tmp_path: Path, body: str) -> str:
    """A stand-in for 'claude' or 'codex' that records what it was given, then runs ``body``."""
    script = tmp_path / "fake-cli.py"
    record = tmp_path / "record.json"
    script.write_text(
        "import json, os, sys, time\n"
        "stdin = sys.stdin.read()\n"
        "env = sorted(name for name in os.environ if name in ('ANTHROPIC_API_KEY', 'PATH', 'HOME'))\n"
        f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'files': sorted(os.listdir('.')), 'stdin': stdin,"
        f" 'env': env}},"
        f" open({str(record)!r}, 'w'))\n" + body,
        encoding="utf-8",
    )
    launcher = tmp_path / "fake-cli"
    launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n", encoding="utf-8")
    launcher.chmod(0o755)
    return str(launcher)


def _record(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "record.json").read_text(encoding="utf-8"))


pytestmark_posix = pytest.mark.skipif(sys.platform == "win32", reason="stand-in commands are shell scripts")


@pytestmark_posix
def test_claude_code_runs_with_its_tools_off_in_an_empty_folder_and_reads_the_prompt_from_stdin(tmp_path):
    binary = _fake_command(tmp_path, "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'Done.'}))\n")
    work = tmp_path / "work"
    result = CliClient(CLAUDE_CLI, work, binary=binary).chat_turn(
        "sonnet", [{"role": "system", "content": "Rules."}, {"role": "user", "content": "SECRET-QUESTION"}])
    seen = _record(tmp_path)
    argv = seen["argv"]
    assert result.content == "Done."
    assert argv[argv.index("--tools") + 1] == ""
    for flag in ("-p", "--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv, flag
    assert argv[argv.index("--system-prompt") + 1] == "Rules." and argv[argv.index("--model") + 1] == "sonnet"
    assert "SECRET-QUESTION" not in " ".join(argv) and seen["stdin"] == "SECRET-QUESTION"
    assert Path(seen["cwd"]).parent == work and seen["files"] == ["err.txt", "out.txt", "prompt.txt"]
    assert list(work.iterdir()) == [], "the call's folder is removed afterwards"


@pytestmark_posix
def test_claude_code_returns_structured_output_for_a_schema(tmp_path):
    binary = _fake_command(tmp_path, "print(json.dumps({'result': '', 'structured_output': {'1': 'description'}}))\n")
    result = CliClient(CLAUDE_CLI, tmp_path / "w", binary=binary).chat_turn(
        "", [{"role": "user", "content": "Label."}], format={"type": "object", "properties": {}, "required": []})
    argv = _record(tmp_path)["argv"]
    assert json.loads(argv[argv.index("--json-schema") + 1])["additionalProperties"] is False
    assert "--model" not in argv and json.loads(result.content) == {"1": "description"}


@pytestmark_posix
def test_codex_runs_read_only_in_an_empty_folder_and_answers_through_its_output_file(tmp_path):
    body = ("out = sys.argv[sys.argv.index('-o') + 1]\n"
            "open(out, 'w').write('From codex.')\n")
    binary = _fake_command(tmp_path, body)
    result = CliClient(CODEX_CLI, tmp_path / "w", binary=binary).chat_turn(
        "", [{"role": "system", "content": "Rules."}, {"role": "user", "content": "Q?"}])
    argv = _record(tmp_path)["argv"]
    assert result.content == "From codex."
    assert argv[:1] == ["exec"] and argv[argv.index("--sandbox") + 1] == "read-only" and argv[-1] == "-"
    assert "--ephemeral" in argv and _record(tmp_path)["stdin"] == "Rules.\n\nQ?"


@pytestmark_posix
@pytest.mark.parametrize("body, wanted", [
    ("print('not json')\n", "did not return JSON"),
    ("print(json.dumps({'is_error': True, 'result': 'x'}))\n", "reported an error"),
    ("print(json.dumps({'result': '  '}))\n", "empty answer"),
    ("sys.exit(3)\n", "status 3"),
])
def test_a_failing_command_is_an_error_with_a_reason(tmp_path, body, wanted):
    binary = _fake_command(tmp_path, body)
    with pytest.raises(CloudError, match=wanted):
        CliClient(CLAUDE_CLI, tmp_path / "w", binary=binary).chat_turn("", [{"role": "user", "content": "Q?"}])


def test_a_missing_command_says_so_and_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud.shutil, "which", lambda name: None)
    with pytest.raises(CloudError, match="'codex' command was not found"):
        CliClient(CODEX_CLI, tmp_path).chat_turn("", [{"role": "user", "content": "Q?"}])


def _allow_our_child_group(monkeypatch, stopped: list[int]) -> None:
    """The suite forbids process-group signals; allow exactly the stand-in's own group and record it."""
    def killpg(pgid, sig):
        cmdline = Path(f"/proc/{pgid}/cmdline").read_bytes()
        assert pgid > 1 and b"fake-cli" in cmdline, "only the stand-in command's own group"
        stopped.append(pgid)
        _REAL_KILLPG(pgid, sig)

    monkeypatch.setattr(cloud.os, "killpg", killpg)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_a_command_past_its_time_is_stopped(tmp_path, monkeypatch):
    stopped: list[int] = []
    _allow_our_child_group(monkeypatch, stopped)
    binary = _fake_command(tmp_path, "time.sleep(60)\n")
    begun = time.monotonic()
    with pytest.raises(TimeoutError):
        CliClient(CLAUDE_CLI, tmp_path / "w", binary=binary).chat_turn(
            "", [{"role": "user", "content": "Q?"}], timeout_s=1.0)
    assert time.monotonic() - begun < 10 and len(stopped) == 1
    assert not Path(f"/proc/{stopped[0]}").exists() or b"Z" in Path(f"/proc/{stopped[0]}/stat").read_bytes()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_cancel_stops_a_running_command(tmp_path, monkeypatch):
    stopped: list[int] = []
    _allow_our_child_group(monkeypatch, stopped)
    binary = _fake_command(tmp_path, "time.sleep(60)\n")
    begun = time.monotonic()
    with pytest.raises(CloudError, match="stopped"):
        CliClient(CLAUDE_CLI, tmp_path / "w", binary=binary,
                  should_stop=lambda: time.monotonic() - begun > 1.0).chat_turn("", [{"role": "user", "content": "Q?"}])
    assert len(stopped) == 1 and time.monotonic() - begun < 10


# ----- the training log -----

def test_each_answer_is_logged_with_what_went_out_and_never_the_key(monkeypatch, config_dir):
    monkeypatch.setenv(cloud.API_KEY_ENV, KEY)
    remote = Remote("Case-A had a critical episode at t=28–37 min.")
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kw: remote)
    session = open_cloud_session(CloudConfig(CLAUDE_API, real_data_allowed=True), real_data=True, pids=[PID])
    result = answer_from_retrieval(question="What happened?", model="claude-opus-5-5", candidates=[_candidate()],
                                   extracts=[EXTRACT], client=session.client)
    path = session.log(question="What happened?", kind="question", answer=result.answer, trace=result.tool_trace)
    assert path == config_dir / "ai-log" / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
    text = path.read_text(encoding="utf-8")
    record = json.loads(text)
    assert KEY not in text
    assert set(record) == {"time", "backend", "model", "kind", "data", "trimmed", "question", "history", "calls",
                           "answer", "trace", "withheld"}
    assert record["history"] == []
    assert (record["backend"], record["model"], record["data"], record["trimmed"]) == (
        CLAUDE_API, "claude-opus-5-5", "real", True)
    assert record["calls"][0]["messages"] == remote.received[0], "exactly what went out"
    assert record["calls"][0]["served_by"] == "served-model" and record["answer"] == result.answer
    session.log(question="Again?", kind="question", answer="b", trace=["withheld:x"])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["withheld"] is True


def test_a_log_that_cannot_be_written_does_not_cost_the_answer(monkeypatch):
    monkeypatch.setattr(cloud, "ClaudeApiClient", lambda **kw: Remote("x"))
    session = open_cloud_session(CloudConfig(CLAUDE_API), real_data=False)

    def full_disk(record, folder=None):
        raise OSError("disk full")

    monkeypatch.setattr(cloud, "write_training_record", full_disk)
    assert session.log(question="q", kind="question", answer="a", trace=[]) is None


def test_a_failed_call_is_logged_with_its_error():
    class Broken:
        def chat_turn(self, model, messages, **kwargs):
            raise CloudError("The Claude API could not be reached.")

    recorder = RecordingClient(Broken())
    with pytest.raises(CloudError):
        recorder.chat_turn("m", [{"role": "user", "content": "Q?"}])
    assert recorder.calls == [{"messages": [{"role": "user", "content": "Q?"}], "structured": False,
                               "error": "The Claude API could not be reached."}]


@pytestmark_posix
def test_a_cli_answers_with_its_own_login_even_when_an_api_key_is_exported(tmp_path, monkeypatch):
    monkeypatch.setenv(cloud.API_KEY_ENV, KEY)
    binary = _fake_command(tmp_path, "print(json.dumps({'type': 'result', 'is_error': False, 'result': 'Done.'}))\n")
    CliClient(CLAUDE_CLI, tmp_path / "work", binary=binary).chat_turn("", [{"role": "user", "content": "Q?"}])
    assert _record(tmp_path)["env"] == ["HOME", "PATH"], "the key is withheld; the rest of the environment is not"
    assert os.environ[cloud.API_KEY_ENV] == KEY, "the app's own environment is left as it was"


# ----- the key, the model list and the AI menu's rows -----

def test_the_api_client_uses_the_saved_key_when_none_is_exported(password_store, monkeypatch):
    import anthropic

    built: list[dict] = []

    def capture(**kwargs):
        built.append(kwargs)
        return FakeSdk()

    monkeypatch.setattr(anthropic, "Anthropic", capture)
    cloud.save_api_key(KEY)
    ClaudeApiClient().chat_turn("m", [{"role": "user", "content": "Q?"}])
    assert built and built[0]["api_key"] == KEY
    monkeypatch.setenv(cloud.API_KEY_ENV, "sk-ant-from-env")
    built.clear()
    ClaudeApiClient().chat_turn("m", [{"role": "user", "content": "Q?"}])
    assert built[0]["api_key"] == "sk-ant-from-env", "an exported key comes before the saved one"


def test_a_locked_password_store_reads_as_no_key_and_never_raises(password_store, monkeypatch):
    cloud.save_api_key(KEY)
    monkeypatch.setattr(cloud, "_stored", None)  # a new run, with the store locked this time
    password_store.broken = True
    assert cloud.api_key() == ("", "") and not cloud.service_ready(CLAUDE_API)
    with pytest.raises(cloud.KeyStoreError) as refused:
        cloud.save_api_key(KEY)
    assert KEY not in str(refused.value)
    with pytest.raises(cloud.KeyStoreError):
        cloud.save_api_key("   ")


def test_forgetting_a_key_that_was_never_saved_is_not_an_error(password_store):
    cloud.forget_api_key()
    assert password_store.secrets == {}


class FakeModels:
    def __init__(self, models=None, error=None):
        self.models, self.error = models or [], error

    def list(self):
        if self.error is not None:
            raise self.error
        return iter(self.models)


def test_the_model_list_is_the_apis_own_in_its_order():
    listed = [SimpleNamespace(id="claude-opus-5-5", display_name="Claude Opus 5.5"),
              SimpleNamespace(id="claude-haiku-5-5", display_name="")]
    sdk = SimpleNamespace(models=FakeModels(listed))
    assert cloud.list_api_models(client=sdk) == [("claude-opus-5-5", "Claude Opus 5.5"),
                                                 ("claude-haiku-5-5", "claude-haiku-5-5")]


def test_a_failed_model_list_says_why_without_the_key():
    import anthropic
    import httpx2

    request = httpx2.Request("GET", "https://api.anthropic.com/v1/models")
    refused = anthropic.AuthenticationError(f"bad key {KEY}", response=httpx2.Response(401, request=request), body=None)
    with pytest.raises(CloudError) as error:
        cloud.list_api_models(client=SimpleNamespace(models=FakeModels(error=refused)))
    assert str(error.value) == "The Claude API refused the key." and KEY not in str(error.value)
    offline = anthropic.APIConnectionError(request=request)
    with pytest.raises(CloudError, match="could not be reached"):
        cloud.list_api_models(client=SimpleNamespace(models=FakeModels(error=offline)))
    with pytest.raises(CloudError, match="No Claude API key"):
        cloud.list_api_models()


def _rows(groups) -> dict[str, list[tuple[str, str, bool]]]:
    return {group.backend: [(c.model, c.shown, c.usable) for c in group.choices] for group in groups}


def test_every_cloud_service_is_offered_and_an_unusable_one_says_why(monkeypatch):
    monkeypatch.setattr(cloud.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    rows = _rows(cloud.cloud_choices(CloudConfig("ollama"), real_data=False))
    assert list(rows) == [CLAUDE_API, CLAUDE_CLI, CODEX_CLI]
    assert rows[CLAUDE_API] == [("", "No key: add one in Settings, AI", False)]
    assert rows[CLAUDE_CLI] == [("default", "Default model", True), ("opus", "Opus", True), ("sonnet", "Sonnet", True),
                                ("haiku", "Haiku", True)]
    assert rows[CODEX_CLI] == [("", "The 'codex' command was not found", False)]


def test_with_real_cases_and_sending_off_every_cloud_row_is_off(monkeypatch, password_store):
    cloud.save_api_key(KEY)
    monkeypatch.setattr(cloud.shutil, "which", lambda name: f"/usr/bin/{name}")
    groups = cloud.cloud_choices(CloudConfig(CLAUDE_API), real_data=True)
    assert [(group.backend, group.title) for group in groups] == [("cloud", "Cloud services")]
    assert _rows(groups) == {"cloud": [("", cloud.OFF_FOR_REAL_CASES, False)]}
    rows = _rows(cloud.cloud_choices(CloudConfig(CLAUDE_API, real_data_allowed=True), real_data=True))
    assert rows[CLAUDE_API] == [("claude-opus-5-5", "claude-opus-5-5", True)]
    assert all(usable for row in rows.values() for _m, _s, usable in row)


def test_the_api_rows_follow_the_list_and_keep_the_saved_model(password_store):
    cloud.save_api_key(KEY)
    listed = [("claude-opus-5-5", "Claude Opus 5.5"), ("claude-sonnet-5-5", "Claude Sonnet 5.5")]
    rows = _rows(cloud.cloud_choices(CloudConfig(CLAUDE_API, "claude-sonnet-5-5"), real_data=False,
                                     api_models=listed))
    assert rows[CLAUDE_API] == [("claude-opus-5-5", "Claude Opus 5.5", True),
                                ("claude-sonnet-5-5", "Claude Sonnet 5.5", True)]
    rows = _rows(cloud.cloud_choices(CloudConfig(CLAUDE_API, "claude-old"), real_data=False, api_models=listed,
                                     api_error="The Claude API could not be reached."))
    assert rows[CLAUDE_API][0] == ("claude-old", "claude-old", True)
    assert rows[CLAUDE_API][-1] == ("", "Could not list models: The Claude API could not be reached.", False)
    rows = _rows(cloud.cloud_choices(CloudConfig(CLAUDE_CLI, "claude-old"), real_data=False, api_models=listed))
    assert [m for m, _s, _u in rows[CLAUDE_API]] == ["claude-opus-5-5", "claude-sonnet-5-5"], \
        "another service's saved model is not added to the API's rows"


def test_a_key_is_looked_for_in_the_store_only_after_one_was_saved_and_only_once(password_store, monkeypatch):
    from src.user_settings import load_settings

    reads = []
    real_get = password_store.get_password
    monkeypatch.setattr(password_store, "get_password", lambda *a: reads.append(a) or real_get(*a))
    import keyring

    monkeypatch.setattr(keyring, "get_password", password_store.get_password)
    password_store.secrets[(cloud.KEYRING_SERVICE, cloud.KEYRING_USER)] = KEY  # put there by something else
    assert cloud.api_key() == ("", "") and reads == [], "no saved-key note: the store is never asked"
    cloud.save_api_key(KEY)
    assert load_settings()[cloud.KEY_SAVED_SETTING] is True and KEY not in json.dumps(load_settings())
    monkeypatch.setattr(cloud, "_stored", None)  # the next run
    assert cloud.api_key() == (KEY, cloud.KEY_FROM_STORE) and cloud.api_key()[0] == KEY
    assert len(reads) == 1, "read once per run"
    cloud.forget_api_key()
    assert cloud.KEY_SAVED_SETTING not in load_settings() and cloud.api_key() == ("", "")


@pytestmark_posix
@pytest.mark.parametrize("backend, flag", [(CLAUDE_CLI, "--model"), (CODEX_CLI, "-m")])
def test_a_cli_default_leaves_the_model_to_the_cli(tmp_path, backend, flag):
    out = ("print(json.dumps({'type': 'result', 'is_error': False, 'result': 'Done.'}))\n" if backend == CLAUDE_CLI
           else "open('answer.txt', 'w').write('Done.')\n")
    binary = _fake_command(tmp_path, out)
    CliClient(backend, tmp_path / "work", binary=binary).chat_turn(
        CloudConfig(backend).model_id, [{"role": "user", "content": "Q?"}])
    assert flag not in _record(tmp_path)["argv"]


@pytest.mark.parametrize("backend", [CLAUDE_API, CLAUDE_CLI, CODEX_CLI])
def test_every_cloud_answer_has_a_model_name_so_the_answer_check_runs(backend):
    from src.llm.advice_check import NotRun, check_advice

    model = CloudConfig(backend).model_id
    remote = Remote("unused")
    assert model and not isinstance(check_advice("MAP fell below 65.", remote, model), NotRun)
    assert remote.received, "the check asked the service"
