"""
Drive the desktop app offscreen through a scripted scenario and record each step.

Boots ``MainWindow`` with ``QT_QPA_PLATFORM=offscreen`` against an EMR folder,
with config, data, cache, temp files, processed output, and Ollama models redirected under
``<out>/state``. Each step writes ``<out>/<step>.png`` and one line of
``<out>/events.jsonl``.

    PYTHONPATH=. python -m src.pipeline.synthetic --out /tmp/drive/EMR --n-cases 10
    PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tools/desktop_drive.py \\
        --emr /tmp/drive/EMR --out /tmp/drive/run --scenario research-roundtrip

Exit codes: 0 every step passed, 1 a step raised, 2 the inputs were rejected.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LLM_MODES = ("scripted", "off", "ollama")
SCRIPTED_MODEL = "scripted-draft"
DEFAULT_QUESTION = "laparoscopic peak pressure"
DEFAULT_DRAFTS = (
    "In the retrieved extracts, the highest sampled PIP is reported with the "
    "minute it was recorded. The extracts do not establish why it rose.",
)
FRAME_INTERVAL_S = 0.2
FRAME_RATE = 5
STEP_TIMEOUT_S = 90.0


class InputError(Exception):
    """Rejected driver input; the message is the named reason."""


@dataclass
class Run:
    app: Any
    win: Any
    question: str
    frames_dir: Path | None
    dialogs: list[dict[str, str]] = field(default_factory=list)
    frame_count: int = 0

    def settle(self) -> None:
        for _ in range(3):
            self.app.processEvents()

    def frame(self) -> None:
        if self.frames_dir is None:
            return
        self.frame_count += 1
        self.win.grab().save(str(self.frames_dir / f"{self.frame_count:06d}.png"))

    def wait(self, ready: Callable[[], bool], what: str, timeout_s: float = STEP_TIMEOUT_S) -> None:
        deadline = time.monotonic() + timeout_s
        next_frame = time.monotonic()
        while True:
            self.settle()
            if ready():
                return
            now = time.monotonic()
            if now > deadline:
                raise TimeoutError(f"timed out after {timeout_s:g} s waiting for {what}")
            if now >= next_frame:
                self.frame()
                next_frame = now + FRAME_INTERVAL_S
            time.sleep(0.01)


@dataclass(frozen=True)
class Step:
    name: str
    act: Callable[[Run], dict[str, Any]]


@dataclass(frozen=True)
class Scenario:
    steps: tuple[Step, ...]
    # The first mode is the default; a scenario that only makes sense without a
    # model lists ("off",) so a scripted answer cannot mislabel its screenshot.
    llm_modes: tuple[str, ...] = LLM_MODES


def _research_idle(win: Any) -> bool:
    worker = win._research_worker
    case_worker = win._case_worker
    return (
        worker is not None
        and not worker.isRunning()
        and win.research.ask_button.isEnabled()
        and win._pending_case is None
        and win._queued_case is None
        and (case_worker is None or not case_worker.isRunning())
    )


def ask(run: Run) -> dict[str, Any]:
    research = run.win.research
    run.win.tabs.setCurrentWidget(research)
    research.question_edit.setText(run.question)
    research.ask_button.click()
    run.wait(lambda: _research_idle(run.win), "the research answer and first case")
    result = research.result
    return {
        "question": research.question_edit.text(),
        "candidates": len(result.candidates) if result is not None else 0,
        "rank_method": getattr(result, "rank_method", None),
        "model": research.selected_model(),
        "answer": research.answer_text.toPlainText(),
        "research_status": research.status_label.text(),
        "source": research.source_title.text(),
    }


def open_case(run: Run) -> dict[str, Any]:
    research = run.win.research
    if not research.inspect_button.isEnabled():
        raise RuntimeError("no case is selected to open in the signal detail view")
    research.inspect_button.click()
    run.wait(lambda: research.stack.currentIndex() == 1, "the signal detail view")
    return {
        "view": "detail",
        "case": research.detail_title.text(),
        "sample": research.sample_label.text(),
    }


def return_to_question(run: Run) -> dict[str, Any]:
    from PySide6.QtWidgets import QPushButton

    research = run.win.research
    back = next(
        (b for b in research.findChildren(QPushButton) if b.text().startswith("← Back")),
        None,
    )
    if back is None:
        raise RuntimeError("the signal detail view has no back button")
    back.click()
    run.wait(lambda: research.stack.currentIndex() == 0, "the evidence workspace")
    question = research.question_edit.text()
    return {
        "view": "canvas",
        "question": question,
        "question_kept": question == run.question,
        "research_status": research.status_label.text(),
    }


def run_pipeline(run: Run) -> dict[str, Any]:
    from src.runtime_paths import emr_dir

    with open(emr_dir() / "patient_information.csv", encoding="utf-8") as f:
        n_cases = max(1, sum(1 for _ in f) - 1)
    run.win._run_pipeline(n_cases=n_cases)
    run.wait(lambda: not run.win._worker.isRunning(), "the pipeline")
    run.settle()
    if run.win.cases is None:
        raise RuntimeError("the pipeline loaded no cases")
    return {"requested_cases": n_cases, "loaded_cases": len(run.win.cases)}


def open_tab(run: Run, *, label: str) -> dict[str, Any]:
    from PySide6.QtWidgets import QTableWidget

    tabs = run.win.tabs
    index = next((i for i in range(tabs.count()) if tabs.tabText(i) == label), -1)
    if index < 0:
        raise RuntimeError(f"no tab labelled {label!r}")
    tabs.setCurrentIndex(index)
    run.settle()
    page = tabs.widget(index)
    return {
        "tab": label,
        "table_rows": [t.rowCount() for t in page.findChildren(QTableWidget)],
    }


TAB_STEPS = tuple(
    Step(name, partial(open_tab, label=label))
    for name, label in (
        ("tab-research", "1 · Research"),
        ("tab-ask", "2 · Ask"),
        ("tab-summary", "3 · Summary"),
        ("tab-timeline", "4 · Case timeline"),
        ("tab-rules", "5 · Rule reference"),
    )
)

SCENARIOS: dict[str, Scenario] = {
    "research-roundtrip": Scenario(
        (Step("ask", ask), Step("case", open_case), Step("return", return_to_question))
    ),
    "open-each-tab": Scenario((Step("pipeline", run_pipeline), *TAB_STEPS)),
    "no-model": Scenario((Step("no-model", ask),), llm_modes=("off",)),
}


class ScriptedChatClient:
    """Stands in for OllamaClient; returns the drafts in order, then repeats the last."""

    def __init__(self, drafts: Sequence[str]) -> None:
        self.drafts = tuple(drafts)
        self.calls = 0

    def chat_turn(self, model: str, messages: Any, **_kwargs: Any) -> Any:
        from src.llm.ollama_client import ChatResult

        draft = self.drafts[min(self.calls, len(self.drafts) - 1)]
        self.calls += 1
        return ChatResult(content=draft, tool_calls=[])

    def chat(self, model: str, messages: Any, **kwargs: Any) -> str:
        return self.chat_turn(model, messages, **kwargs).content


def load_drafts(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return DEFAULT_DRAFTS
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"drafts file {path} could not be read: {exc}") from exc
    drafts = data.get("drafts") if isinstance(data, dict) else None
    if not drafts or not isinstance(drafts, list) or not all(isinstance(d, str) and d.strip() for d in drafts):
        raise InputError(f"drafts file {path} needs a non-empty 'drafts' list of strings")
    return tuple(drafts)


def isolate_environment(emr: Path, state: Path) -> None:
    for key in [k for k in os.environ if k.startswith("MOVER_")]:
        del os.environ[key]
    dirs = {name: state / name for name in ("config", "data", "cache", "tmp", "processed", "ollama-models")}
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "XDG_CONFIG_HOME": str(dirs["config"]),
            "XDG_DATA_HOME": str(dirs["data"]),
            "XDG_CACHE_HOME": str(dirs["cache"]),
            "TMPDIR": str(dirs["tmp"]),
            "MOVER_EMR_DIR": str(emr),
            "MOVER_DATA_DIR": str(dirs["data"]),
            "MOVER_PROCESSED_DIR": str(dirs["processed"]),
            "MOVER_ALLOW_EXTERNAL_OUTPUT": "1",
            "MOVER_SKIP_SETUP": "1",
            # Without this the settings layer probes the operator's default model
            # directories when it auto-detects where Ollama keeps models.
            "OLLAMA_MODELS": str(dirs["ollama-models"]),
        }
    )
    # tempfile caches the first temp dir it resolves; make it read TMPDIR again.
    tempfile.tempdir = None
    from src.user_settings import save_settings

    save_settings(
        {
            "setup_complete": True,
            "emr_dir": str(emr),
            "processed_dir": str(dirs["processed"]),
            "ollama_models_dir": str(dirs["ollama-models"]),
            "theme": "light",
        }
    )


def install_llm(mode: str, drafts: Sequence[str]) -> None:
    if mode == "ollama":
        return
    import src.desktop.research_workers as research_workers
    import src.llm.ollama_client as ollama_client
    from src.desktop.app import MainWindow

    models = [SCRIPTED_MODEL] if mode == "scripted" else []

    def refresh_models(self: Any, **_kwargs: Any) -> None:
        self.combo_llm_model.clear()
        self.combo_llm_model.addItems(models or ["(Ollama stopped — click Start)"])
        self.lbl_ollama_status.setText(f"Ollama: replaced by the driver (--llm {mode})")
        self.research.set_models(models, models[0] if models else None)

    def client_factory(*_args: Any, **_kwargs: Any) -> ScriptedChatClient:
        return ScriptedChatClient(drafts)

    MainWindow._refresh_ollama_models = refresh_models
    ollama_client.OllamaEmbedder.is_available = lambda self: False
    research_workers.OllamaClient = client_factory
    ollama_client.OllamaClient = client_factory


def record_dialogs(run: Run) -> None:
    """Modal dialogs would block the offscreen event loop forever; log them instead."""
    from PySide6.QtWidgets import QDialog, QMessageBox

    def static(kind: str, answer: Any) -> Callable[..., Any]:
        def show(_parent: Any, title: str, text: str, *_args: Any, **_kwargs: Any) -> Any:
            run.dialogs.append({"kind": kind, "title": title, "text": text})
            return answer

        return show

    QMessageBox.information = static("information", QMessageBox.StandardButton.Ok)
    QMessageBox.warning = static("warning", QMessageBox.StandardButton.Ok)
    QMessageBox.critical = static("critical", QMessageBox.StandardButton.Ok)
    QMessageBox.question = static("question", QMessageBox.StandardButton.Yes)

    def exec_dialog(dialog: Any) -> int:
        run.dialogs.append({"kind": "dialog", "title": dialog.windowTitle(), "text": ""})
        return 0

    QDialog.exec = exec_dialog


def _scrub(value: Any, replacements: Sequence[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, dict):
        return {k: _scrub(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, replacements) for v in value]
    return value


def make_video(frames_dir: Path, out: Path) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return "ffmpeg not found on PATH; skipped run.mp4"
    if not any(frames_dir.glob("*.png")):
        return "no frames were captured; skipped run.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-framerate", str(FRAME_RATE),
            "-i", str(frames_dir / "%06d.png"),
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-pix_fmt", "yuv420p",
            str(out / "run.mp4"),
        ],
        check=True,
    )
    return f"wrote {out / 'run.mp4'}"


def _app() -> Any:
    from PySide6.QtWidgets import QApplication

    from src.desktop.theme import apply_theme

    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([sys.argv[0]])
    apply_theme(app)
    return app


def reject_emr(out: Path, reason: str) -> None:
    """Show the setup wizard the app would open, so the failure has a screenshot."""
    from src.desktop.settings_ui import SetupWizard

    app = _app()
    wizard = SetupWizard()
    wizard.show()
    app.processEvents()
    wizard.grab().save(str(out / "setup.png"))
    wizard.close()
    event = {"step": "setup", "ok": False, "png": "setup.png", "reason": reason}
    (out / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")


def drive(args: argparse.Namespace) -> int:
    scenario = SCENARIOS[args.scenario]
    llm = args.llm or scenario.llm_modes[0]
    if llm not in scenario.llm_modes:
        raise InputError(f"scenario {args.scenario} runs only with --llm {'/'.join(scenario.llm_modes)}")
    drafts = load_drafts(args.drafts)

    emr = args.emr.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    state = out / "state"
    shutil.rmtree(state, ignore_errors=True)
    isolate_environment(emr, state)

    from src.guardrails.validate_io import validate_emr_dir

    try:
        validate_emr_dir(emr)
    except Exception as exc:
        reason = f"invalid EMR {emr}: {exc}"
        reject_emr(out, reason)
        raise InputError(reason) from exc

    frames_dir: Path | None = None
    if args.frames or args.video:
        frames_dir = out / "frames"
        shutil.rmtree(frames_dir, ignore_errors=True)
        frames_dir.mkdir()

    app = _app()
    install_llm(llm, drafts)
    from src.desktop.app import MainWindow

    win = MainWindow()
    run = Run(app=app, win=win, question=args.question, frames_dir=frames_dir)
    record_dialogs(run)
    win.show()
    run.settle()
    replacements = [(str(state), "<state>"), (str(emr), "<emr>"), (str(out), "<out>")]

    failed = False
    started = time.monotonic()
    with open(out / "events.jsonl", "w", encoding="utf-8") as events:
        for step in scenario.steps:
            dialogs_before = len(run.dialogs)
            event: dict[str, Any] = {"step": step.name}
            try:
                facts = step.act(run)
                event.update(ok=True, **facts)
            except Exception as exc:
                failed = True
                event.update(ok=False, error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
            run.settle()
            png = out / f"{step.name}.png"
            win.grab().save(str(png))
            run.frame()
            event.update(
                status_bar=win.statusBar().currentMessage(),
                dialogs=run.dialogs[dialogs_before:],
                png=png.name,
                elapsed_s=round(time.monotonic() - started, 2),
            )
            events.write(json.dumps(_scrub(event, replacements)) + "\n")
            events.flush()
            print(f"{step.name}: {'ok' if event['ok'] else event['error']}")
            if failed:
                break

    win.close()
    run.settle()
    if frames_dir is not None and args.video:
        print(make_video(frames_dir, out))
    return 1 if failed else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--emr", type=Path, required=True, help="EMR folder (use a synthetic one)")
    parser.add_argument("--out", type=Path, required=True, help="screenshots, events.jsonl, and isolated state")
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    parser.add_argument("--llm", choices=LLM_MODES, default=None, help="default: the scenario's first mode")
    parser.add_argument("--drafts", type=Path, default=None, help="YAML with a 'drafts' list for --llm scripted")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--frames", action="store_true", help="write numbered frames under <out>/frames")
    parser.add_argument("--video", action="store_true", help="also join the frames into <out>/run.mp4")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return drive(args)
    except InputError as exc:
        print(f"desktop_drive: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
