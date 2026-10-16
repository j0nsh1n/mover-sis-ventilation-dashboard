"""
Drive the desktop app offscreen through a scripted scenario and record each step.

Boots ``MainWindow`` with ``QT_QPA_PLATFORM=offscreen`` against an EMR folder,
with config, data, cache, temp files, processed output, and Ollama models redirected under
``<out>/state``. Each step writes ``<out>/<step>.png`` and one line of
``<out>/events.jsonl``.

    PYTHONPATH=. python -m src.pipeline.synthetic --out ~/mover-scratch/drive/EMR --n-cases 10
    PYTHONPATH=. QT_QPA_PLATFORM=offscreen python tools/desktop_drive.py \\
        --emr ~/mover-scratch/drive/EMR --out ~/mover-scratch/drive/run --scenario research-roundtrip

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
STEP_HOLD_FRAMES = 5
STEP_TIMEOUT_S = 90.0
OUT_MARKER = ".desktop_drive"


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

    def frame(self, hold: int = 1) -> None:
        """Write the window as it is now; ``hold`` repeats it so a brief state stays visible in the video."""
        if self.frames_dir is None:
            return
        image = self.win.grab()
        for _ in range(hold):
            self.frame_count += 1
            image.save(str(self.frames_dir / f"{self.frame_count:06d}.png"))

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
    # A warning or critical dialog fails the step unless the step expects one.
    expects_alert: bool = False


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
    research.question_edit.setText(run.question)
    research.ask_button.click()
    run.wait(lambda: _research_idle(run.win), "the research answer and first case")
    result = research.result
    status = research.status_label.text()
    if "failed" in status.lower():
        raise RuntimeError(status)
    if result is None or not result.candidates:
        raise RuntimeError(f"research returned no candidates: {status}")
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


def run_pipeline(run: Run, *, limit: int | None = None) -> dict[str, Any]:
    from src.runtime_paths import emr_dir

    with open(emr_dir() / "patient_information.csv", encoding="utf-8") as f:
        n_cases = max(1, sum(1 for _ in f) - 1)
    if limit is not None:
        n_cases = min(n_cases, limit)
    run.win._run_pipeline(n_cases=n_cases)
    run.wait(lambda: not run.win._worker.isRunning(), "the pipeline")
    run.settle()
    if run.win.cases is None:
        raise RuntimeError("the pipeline loaded no cases")
    return {"requested_cases": n_cases, "loaded_cases": len(run.win.cases)}


def slow_ollama_start(run: Run) -> dict[str, Any]:
    import src.llm.service as service
    from PySide6.QtCore import QTimer

    def start(**_kwargs: Any) -> Any:
        time.sleep(2)
        return service.OllamaStatus(True, "Synthetic local model ready", [], service.DEFAULT_BASE_URL)

    service.start_ollama = start
    ticks: list[float] = []
    QTimer.singleShot(10, lambda: ticks.append(time.monotonic()))
    started = time.monotonic()
    run.win.research.start_button.click()
    run.win._ollama_start()
    run.wait(lambda: bool(ticks), "a GUI timer tick", timeout_s=0.1)
    assert ticks[0] - started < 0.1
    assert not run.win.research.start_button.isEnabled()
    return {"timer_ms": round((ticks[0] - started) * 1000), "status": run.win.research.status_label.text()}


def ollama_ready(run: Run) -> dict[str, Any]:
    run.wait(lambda: not run.win._ollama_worker.isRunning(), "the fake Ollama start")
    assert run.win.research.start_button.isEnabled()
    return {"status": run.win.research.status_label.text()}


def start_slow_search(run: Run) -> dict[str, Any]:
    from types import SimpleNamespace
    from src.services.retrieval import RetrievalEngine

    def search(self: Any, question: str, on_progress: Any) -> Any:
        while True:
            on_progress(SimpleNamespace(message="Searching synthetic records…"))
            time.sleep(0.01)

    RetrievalEngine.search = search
    run.win.resize(900, 860)
    run.win.research.question_edit.setText("A question to cancel")
    run.win.research.ask_button.click()
    run.win.research.cancel_button.setFocus()
    run.wait(lambda: "Searching synthetic" in run.win.research.status_label.text(), "slow search progress")
    assert run.win.research.reset_button.isEnabled()
    assert run.win.width() == 900
    return {"cancel_visible": run.win.research.cancel_button.isVisible(), "width": run.win.width()}


def cancel_search(run: Run) -> dict[str, Any]:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    research = run.win.research
    QTest.keyClick(research.cancel_button, Qt.Key.Key_Space)
    run.wait(lambda: _research_idle(run.win), "cooperative search cancellation")
    assert research.question_edit.text() == run.question
    assert research.result is not None
    return {"restored_question": research.question_edit.text(), "answer": research.answer_text.toPlainText()}


def research_failure(run: Run) -> dict[str, Any]:
    from src.services.retrieval import RetrievalEngine

    def search(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Synthetic model connection timed out; retry the question after starting the local model.")

    RetrievalEngine.search = search
    run.win.resize(900, 860)
    research = run.win.research
    research.question_edit.setText(run.question)
    research.ask_button.click()
    run.wait(lambda: not run.win._research_worker.isRunning(), "research failure")
    run.win.statusBar().showMessage("Another status update")
    assert "Synthetic model connection timed out" in research.answer_text.toPlainText()
    assert run.win.width() == 900
    return {"answer": research.answer_text.toPlainText(), "width": run.win.width()}


def corrupt_scan(run: Run) -> dict[str, Any]:
    from src.runtime_paths import processed_dir

    root = processed_dir()
    root.mkdir(parents=True, exist_ok=True)
    cases = root / "corpus_cases.parquet"
    meta = root / "corpus_meta.json"
    cases.write_bytes(b"not parquet")
    meta.write_text("{}")
    try:
        run.win._load_corpus_scan()
        assert run.win.corpus_scan is None
        assert str(cases) in run.dialogs[-1]["text"]
        return {"message": run.dialogs[-1]["text"]}
    finally:
        cases.unlink()
        meta.unlink()


def missing_data(run: Run) -> dict[str, Any]:
    from src.runtime_paths import processed_dir

    run.win._autoload_processed()
    assert str(processed_dir()) in run.dialogs[-1]["text"]
    return {"message": run.dialogs[-1]["text"]}


def start_scan(run: Run) -> dict[str, Any]:
    import pandas as pd
    from src.runtime_paths import emr_dir

    assert len(pd.read_csv(emr_dir() / "patient_information.csv")) == 300
    run.win.resize(900, 860)
    run.win._run_corpus_scan()
    run.wait(lambda: "Scoring" in run.win.statusBar().currentMessage(), "scan scoring")
    run.win._scan_cancel_button.setFocus()
    return {"cancel_visible": run.win._scan_cancel_button.isVisible(), "status": run.win.statusBar().currentMessage()}


def cancel_scan(run: Run) -> dict[str, Any]:
    run.win._scan_cancel_button.click()
    started = time.monotonic()
    run.wait(lambda: not run.win._worker.isRunning(), "full-EMR scan cancellation", timeout_s=2)
    elapsed = time.monotonic() - started
    message = "Full-EMR scan cancelled; the previous scan was kept."
    assert not any(dialog["kind"] == "critical" for dialog in run.dialogs)
    assert message in run.win.statusBar().currentMessage()
    return {"status": run.win.statusBar().currentMessage(), "cancel_ms": round(elapsed * 1000)}


def partial_index(run: Run) -> dict[str, Any]:
    import src.services.retrieval as retrieval
    from src.runtime_paths import emr_dir
    from src.user_settings import settings_path

    class InterruptedEmbedder:
        model_id = "driver-partial"

        def __init__(self) -> None:
            self.documents = 0

        def is_available(self) -> bool:
            return True

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            if texts and texts[0].startswith("Procedure:"):
                if self.documents >= 18:
                    raise RuntimeError("synthetic interruption after 18 records")
                self.documents += len(texts)
            return [[1.0, 0.0] for _ in texts]

    retrieval.DEFAULT_EMBED_BATCH = 1
    engine = retrieval.RetrievalEngine(
        emr=emr_dir(), cache_path=settings_path().parent / "partial.sqlite",
        embedder=InterruptedEmbedder(),
    )
    assert engine.prepare() == 18
    run.win._research_engine = engine
    details = ask(run)
    label = run.win.research.scope_label.text()
    assert "embedding, 18 of 30 records indexed; the rest ranked by keywords" in label
    return {**details, "scope": label}


def _focus_name(run: Run) -> str:
    from PySide6.QtWidgets import QApplication

    widget = QApplication.focusWidget() or run.win.focusWidget()
    if widget is None:
        return "none"
    label = widget.objectName() or getattr(widget, "text", lambda: "")()
    return f"{type(widget).__name__}:{label}"


def _drawer_facts(run: Run) -> dict[str, Any]:
    drawer = run.win.research.rules_drawer
    return {
        "open": drawer.is_open,
        "header": drawer.header_label.text(),
        "search": drawer.search_text(),
        "count": drawer.count_label.text(),
        "visible_ids": drawer.visible_rule_ids(),
        "empty_state": drawer.empty_label.text() if drawer.empty_state.isVisible() else "",
        "notice": drawer.notice_label.text() if drawer.notice_label.isVisible() else "",
        "focus": _focus_name(run),
    }


def open_rules_from_button(run: Run) -> dict[str, Any]:
    research = run.win.research
    run.win.activateWindow()
    run.settle()
    research.rules_button.click()
    run.settle()
    assert research.rules_drawer.is_open
    return _drawer_facts(run)


def search_rules(run: Run, *, text: str) -> dict[str, Any]:
    drawer = run.win.research.rules_drawer
    drawer.search_edit.setText(text)
    run.settle()
    return _drawer_facts(run)


def clear_rule_search(run: Run) -> dict[str, Any]:
    drawer = run.win.research.rules_drawer
    assert drawer.clear_button.isVisible()
    drawer.clear_button.click()
    run.settle()
    return _drawer_facts(run)


def show_rule(run: Run, *, rule_id: str) -> dict[str, Any]:
    research = run.win.research
    research.show_rule(rule_id)
    run.settle()
    drawer = research.rules_drawer
    row = drawer._rows[rule_id]
    assert row.property("highlight") is True
    return {**_drawer_facts(run), "highlighted": rule_id, "scroll": drawer.rule_scroll.verticalScrollBar().value()}


def escape_rules(run: Run) -> dict[str, Any]:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    drawer = run.win.research.rules_drawer
    QTest.keyClick(drawer.search_edit, Qt.Key.Key_Escape)
    run.settle()
    assert not drawer.is_open
    return _drawer_facts(run)


def escape_rules_from_question(run: Run) -> dict[str, Any]:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    research = run.win.research
    research.question_edit.setFocus()
    QTest.keyClick(research.question_edit, Qt.Key.Key_Escape)
    run.settle()
    assert not research.rules_drawer.is_open
    assert research.question_edit.hasFocus()
    return _drawer_facts(run)


def _has_no_horizontal_scroll(view: Any) -> bool:
    bar = view.horizontalScrollBar()
    return not bar.isVisible() or bar.maximum() == 0


def _wait_layout_settled(run: Run, scroll: Any, table: Any, quiet_s: float = 0.3) -> None:
    """Wait until the narrow layout stops changing, so a transient scrollbar is not read."""

    def layout() -> tuple[Any, ...]:
        return (
            run.win.width(),
            run.win.research.rules_drawer.width(),
            scroll.horizontalScrollBar().maximum(),
            scroll.horizontalScrollBar().isVisible(),
            table.horizontalScrollBar().maximum(),
            table.horizontalScrollBar().isVisible(),
            tuple(table.columnWidth(i) for i in range(table.columnCount())),
        )

    state = {"seen": layout(), "since": time.monotonic()}

    def stable() -> bool:
        now = layout()
        if now != state["seen"]:
            state["seen"], state["since"] = now, time.monotonic()
        return time.monotonic() - state["since"] >= quiet_s

    run.wait(stable, "the narrow layout to stop changing", timeout_s=10.0)


def open_rules_narrow(run: Run) -> dict[str, Any]:
    research = run.win.research
    run.win.resize(900, 860)
    run.settle()
    facts = open_rules_from_button(run)
    scroll = research.stack.widget(0)
    table = research.candidate_table
    _wait_layout_settled(run, scroll, table)
    assert run.win.width() == 900, f"the open drawer widened the window to {run.win.width()}"
    # A bar can show for a moment with nothing to scroll; only a bar that scrolls is a failure.
    assert _has_no_horizontal_scroll(scroll), "the board scrolls horizontally"
    assert _has_no_horizontal_scroll(table), "the shortlist scrolls horizontally"
    assert sum(table.columnWidth(i) for i in range(table.columnCount())) <= table.viewport().width()
    assert all(table.item(row, col).toolTip() == table.item(row, col).text()
               for row in range(table.rowCount()) for col in range(table.columnCount()))
    return {
        **facts,
        "window_width": run.win.width(),
        "drawer_width": research.rules_drawer.width(),
        "board_h_scroll": not _has_no_horizontal_scroll(scroll),
        "shortlist_h_scroll": not _has_no_horizontal_scroll(table),
        "shortlist_columns": [table.columnWidth(i) for i in range(table.columnCount())],
    }


def focus_question(run: Run) -> dict[str, Any]:
    research = run.win.research
    run.win.activateWindow()
    research.question_edit.setFocus()
    run.settle()
    return {"focus": _focus_name(run)}


def show_narrow_shortlist(run: Run) -> dict[str, Any]:
    research = run.win.research
    table = research.candidate_table
    research.stack.widget(0).ensureWidgetVisible(table)
    run.settle()
    assert not table.horizontalScrollBar().isVisible()
    return {"shortlist_columns": [table.columnWidth(i) for i in range(table.columnCount())]}


def ctrl_r(run: Run) -> dict[str, Any]:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(run.win.research.question_edit, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    run.settle()
    assert run.win.research.rules_drawer.is_open
    return _drawer_facts(run)


def type_rule_search(run: Run, *, text: str) -> dict[str, Any]:
    from PySide6.QtTest import QTest

    QTest.keyClicks(run.win.research.rules_drawer.search_edit, text)
    run.settle()
    return _drawer_facts(run)


def f_facts(run: Run) -> dict[str, Any]:
    research = run.win.research
    run.wait(lambda: not research.detail_chart._resize_timer.isActive(), "F chart resize")
    pid = research._current_pid
    return {
        "pid": pid,
        "episode_rows_shown": research._episodes.to_dict("records"),
        "selected_rule_id": research.selected_rule_id,
        "cursor_minute": float(research._minute_rows.iloc[research.minute_slider.value()]["t_min"]) if pid else None,
        "chart_minute": research.detail_chart._minute,
        "rule_detail": research.rule_detail.text(),
        "signal_statistics": research.signal_statistics,
        "chart_track_names": research.chart_track_names,
        "lane_marks": len(research.detail_chart.lane_marks),
        "window_width": run.win.width(),
    }


def f_load(run: Run) -> dict[str, Any]:
    facts = run_pipeline(run)
    return {**facts, **f_facts(run)}


def f_open(run: Run, *, kind: str) -> dict[str, Any]:
    from src.services.retrieval import RetrievalResult, SearchCandidate

    win, research = run.win, run.win.research
    if kind == "spo2":
        matches = win.episodes[win.episodes["rule_id"].eq("spo2_low")]
        assert not matches.empty, "no spo2_low episode in this synthetic EMR"
        pid = str(matches.iloc[0]["PID"])
    elif kind == "clean":
        flagged = set(win.flags["PID"].astype(str))
        pid = next(str(pid) for pid in win.ts["PID"].unique() if str(pid) not in flagged)
    else:
        pid = str(win.flags.groupby("PID").size().idxmax())
    candidate = SearchCandidate(pid=pid, source_label="analyzed case", analyzed=True,
                                procedure="Synthetic research case", age=None, gender="",
                                source_text="Synthetic source", rank_method="keyword", score=1.)
    research.set_results(RetrievalResult(candidates=(candidate,)))
    if kind == "spo2":
        win.resize(900, 860)
    win._load_research_case(pid, False, True)
    run.wait(lambda: research.stack.currentIndex() == 1, "F with full case data")
    research.signal_scroll.verticalScrollBar().setValue(0)
    if kind == "clean":
        assert research.episode_list.item(0).text() == "No flags in this case"
        assert not research.detail_chart.lane_marks
    return f_facts(run)


def f_select(run: Run, *, rule: str = "") -> dict[str, Any]:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    research = run.win.research
    episodes = research._episodes
    matches = episodes[episodes.rule_id.eq(rule)] if rule else episodes[episodes.rule_id.str.contains("etco2")]
    index = int(matches.index[0]) if not matches.empty else 0
    item = research.episode_list.item(index)
    research.signal_scroll.ensureWidgetVisible(research.episode_list)
    research.episode_list.scrollToItem(item)
    run.settle()
    QTest.mouseClick(research.episode_list.viewport(), Qt.MouseButton.LeftButton,
                     pos=research.episode_list.visualItemRect(item).center())
    run.settle()
    assert research.selected_rule_id == episodes.iloc[index].rule_id
    facts = f_facts(run)
    assert facts["cursor_minute"] == episodes.iloc[index].t_start_min
    assert facts["signal_statistics"]
    if rule == "spo2_low":
        assert "SPO2" in facts["chart_track_names"]
    research.signal_scroll.ensureWidgetVisible(research.rule_detail)
    return facts


def f_clear(run: Run) -> dict[str, Any]:
    research = run.win.research
    research.clear_episode_button.click()
    run.settle()
    assert research.selected_rule_id is None
    assert "SPO2" not in research.chart_track_names
    research.signal_scroll.ensureWidgetVisible(research.detail_chart)
    return f_facts(run)


def f_drawer_link(run: Run) -> dict[str, Any]:
    research = run.win.research
    research.rule_link.click()
    run.settle()
    assert research.rules_drawer.is_open
    assert research.rules_drawer._rows[research.selected_rule_id].property("highlight") is True
    return {**f_facts(run), **_drawer_facts(run)}
def _case_loaded(win: Any) -> bool:
    worker = win._case_worker
    return win._pending_case is None and win._queued_case is None and (worker is None or not worker.isRunning())


def _overview_facts(run: Run, rows: int = 3) -> dict[str, Any]:
    research = run.win.research
    overview = research._cases_overview
    if overview is None:
        raise RuntimeError("the Cases overview was never opened")
    return {
        "page": research.stack.currentIndex(),
        "scope": overview.scope_label.text(),
        "scope_switch_visible": overview.scope_combo.isVisibleTo(overview),
        "scope_choices": [overview.scope_combo.itemText(i) for i in range(overview.scope_combo.count())],
        "table_visible": overview.table.isVisibleTo(overview),
        "empty_message": overview.empty_label.text() if overview.empty_label.isVisibleTo(overview) else "",
        "rows": overview._proxy.rowCount(),
        "first_rows": [
            {"pid": row["PID"], "score": row["anomaly_score"]} for row in overview.visible_rows(rows)
        ],
        "message": overview.status_label.text(),
    }


def open_cases(run: Run) -> dict[str, Any]:
    run.win.resize(900, 860)
    run.settle()
    research = run.win.research
    research.cases_button.click()
    run.settle()
    if research.stack.currentIndex() != 2:
        raise RuntimeError("one click on Cases did not open the overview")
    return _overview_facts(run)


def _click_header(run: Run, column: int) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    header = run.win.research._cases_overview.table.horizontalHeader()
    x = header.sectionViewportPosition(column) + header.sectionSize(column) // 2
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, header.height() // 2))
    run.settle()
    run.frame(STEP_HOLD_FRAMES)


def _click_row(run: Run, row: int) -> str:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    table = run.win.research._cases_overview.table
    pid = table.model().index(row, 0).data()
    center = table.visualRect(table.model().index(row, 0)).center()
    run.frame(STEP_HOLD_FRAMES)
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    return str(pid)


def sort_cases_by_score(run: Run) -> dict[str, Any]:
    from src.desktop.cases_overview import COLUMNS

    score = next(i for i, c in enumerate(COLUMNS) if c.key == "anomaly_score")
    _click_header(run, 0)
    by_pid = [row["PID"] for row in run.win.research._cases_overview.visible_rows(5)]
    if by_pid != sorted(by_pid):
        raise RuntimeError(f"clicking PID did not sort by PID: {by_pid}")
    _click_header(run, score)
    facts = {**_overview_facts(run, rows=5), "first_pids_when_sorted_by_pid": by_pid}
    scores = [row["score"] for row in facts["first_rows"]]
    if scores != sorted(scores, reverse=True) or len(set(scores)) < 2:
        raise RuntimeError(f"rows are not sorted by score: {scores}")
    return facts


def open_top_case(run: Run) -> dict[str, Any]:
    research = run.win.research
    pid = _click_row(run, 0)
    run.wait(lambda: research.stack.currentIndex() == 1 and _case_loaded(run.win), "the signal detail view")
    if pid not in research.detail_title.text():
        raise RuntimeError(f"F opened {research.detail_title.text()!r}, not {pid}")
    return {"pid": pid, "detail": research.detail_title.text(), "back": research.back_button.text()}


def sample_pipeline(run: Run) -> dict[str, Any]:
    return run_pipeline(run, limit=30)


def full_scan(run: Run) -> dict[str, Any]:
    run.win.resize(900, 860)
    run.win._run_corpus_scan()
    run.wait(lambda: not run.win._worker.isRunning() and run.win.corpus_scan is not None, "the full-EMR scan", timeout_s=300)
    scan = run.win.corpus_scan
    return {"scored": len(scan.cases), "meta": {k: scan.meta.get(k) for k in ("n_indexed_surgeries", "n_ventilated_surgeries", "n_scanned_cases")}}


def switch_to_scan_scope(run: Run) -> dict[str, Any]:
    run.win.research.cases_button.click()
    run.settle()
    overview = run.win.research._cases_overview
    if overview.scope_combo.count() < 2:
        raise RuntimeError("the scope switch does not offer the scan")
    overview.scope_combo.setCurrentIndex(1)
    run.settle()
    return _overview_facts(run)


def request_scan_only_case(run: Run) -> dict[str, Any]:
    win = run.win
    overview = win.research._cases_overview
    sample = set(win.cases["PID"].astype(str))
    row = next(
        (r for r in range(overview._proxy.rowCount()) if overview.cell_text(r, 0) not in sample), None
    )
    if row is None:
        raise RuntimeError("every scanned case is also in the loaded sample")
    pid = _click_row(run, row)
    facts = {
        "pid": pid,
        "in_sample": pid in sample,
        "progress": win.research.status_label.text(),
        "overview_message": overview.status_label.text(),
    }
    if facts["progress"] != f"Loading this case: {pid}…" or facts["overview_message"] != facts["progress"]:
        raise RuntimeError(f"progress text not shown: {facts}")
    return facts


def scan_case_detail(run: Run) -> dict[str, Any]:
    research = run.win.research
    run.wait(lambda: research.stack.currentIndex() == 1 and _case_loaded(run.win), "the scan-only case in F")
    return {"detail": research.detail_title.text(), "context": research.detail_context.text()}


def back_to_cases(run: Run) -> dict[str, Any]:
    research = run.win.research
    research.back_button.click()
    run.settle()
    if research.stack.currentIndex() != 2:
        raise RuntimeError("Back from F did not return to the overview")
    return _overview_facts(run)


def empty_overview(run: Run) -> dict[str, Any]:
    if run.win.cases is not None:
        raise RuntimeError("this scenario needs a window with no processed data")
    facts = open_cases(run)
    if not facts["empty_message"] or facts["table_visible"]:
        raise RuntimeError(f"the empty overview does not say why: {facts}")
    return facts


def conversation_facts(run: Run) -> dict[str, Any]:
    research = run.win.research
    return {
        "turn_count": len(research.conversation.turns),
        "turns": [
            {"question": turn.question, "state": turn.state, "badge": turn.badge,
             "answer": turn.answer[:120], "cited_pids": list(turn.cited_pids)}
            for turn in research.conversation.turns
        ],
        "shortlist_pids": [candidate.pid for candidate in research.result.candidates] if research.result else [],
        "scope": research.scope_label.text(),
    }


def conversation_load_data(run: Run) -> dict[str, Any]:
    return {**run_pipeline(run), **conversation_facts(run)}


def conversation_ask(run: Run, *, question: str, narrow: bool = False, expect_empty: bool = False) -> dict[str, Any]:
    from PySide6.QtCore import Qt

    if narrow:
        run.win.resize(1000, 1000)
    run.win.research.question_edit.setText(question)
    run.win.research.ask_button.click()
    run.wait(lambda: _research_idle(run.win), "the conversation turn and board")
    research = run.win.research
    turn = research.conversation.turns[-1]
    assert turn.question == question
    assert turn.state == ("answered" if turn.model else "no_model")
    assert research.result is not None
    assert bool(research.result.candidates) != expect_empty
    widget = research.conversation.turn_widgets[turn.request_id]
    research.conversation.ensureWidgetVisible(widget)
    run.settle()
    facts = conversation_facts(run)
    if narrow:
        from PySide6.QtCore import QPoint

        geometry: tuple[int, ...] | None = None
        stable_since = time.monotonic()

        def stable_geometry() -> bool:
            nonlocal geometry, stable_since
            current = tuple(value for child in (research.board_scroll, research.conversation)
                            for value in (*child.mapTo(research, QPoint(0, 0)).toTuple(), child.width(), child.height()))
            if current != geometry:
                geometry, stable_since = current, time.monotonic()
            return time.monotonic() - stable_since >= 0.3

        run.wait(stable_geometry, "stable stacked conversation geometry", timeout_s=10)
        board_top = research.board_scroll.mapTo(research, QPoint(0, 0)).y()
        panel_top = research.conversation.mapTo(research, QPoint(0, 0)).y()
        board_bottom = board_top + research.board_scroll.height()
        assert run.win.width() == 1000
        assert research.board_splitter.orientation() == Qt.Orientation.Vertical
        assert research.board_splitter.indexOf(research.conversation) > research.board_splitter.indexOf(research.board_scroll)
        assert panel_top >= board_bottom - 2, (panel_top, board_bottom, geometry)
        assert research.conversation.height() >= 180
        assert research.board_scroll.height() >= 250
        assert widget.show_button.isVisible() and widget.show_button.isEnabled()
        assert research.question_edit.isEnabled()
        facts.update(width=run.win.width(), panel_below_board=True, panel_top=panel_top, board_bottom=board_bottom, geometry=list(geometry or ()))
    return facts


def conversation_restore(run: Run) -> dict[str, Any]:
    research = run.win.research
    first = research.conversation.turns[0]
    assert first.board and first.board.result
    expected = [candidate.pid for candidate in first.board.result.candidates]
    scope = first.board.labels[1]
    research.conversation.turn_widgets[first.request_id].show_button.click()
    run.settle()
    facts = conversation_facts(run)
    assert facts["shortlist_pids"] == expected
    assert facts["scope"] == scope
    assert research.question_edit.text() == first.question
    return {**facts, "restored_first_shortlist": True}


def conversation_no_model(run: Run) -> dict[str, Any]:
    from src.desktop.conversation_panel import NO_MODEL_ANSWER

    facts = conversation_ask(run, question=DEFAULT_QUESTION)
    turn = run.win.research.conversation.turns[0]
    assert turn.state == "no_model" and turn.answer == NO_MODEL_ANSWER
    assert turn.badge == "No AI answer"
    return facts


def prototype_ask(run: Run) -> dict[str, Any]:
    from src.desktop.conversation_panel import NO_MODEL_ANSWER
    from src.llm.agent import find_directive_phrases

    facts = conversation_ask(run, question=run.question)
    research = run.win.research
    candidates = research.result.candidates
    labels = [candidate.source_label for candidate in candidates]
    analyzed = set(run.win.cases["PID"].astype(str)) if run.win.cases is not None else set()
    assert labels
    for candidate, label in zip(candidates, labels, strict=True):
        expected = ({"analyzed case"} if candidate.pid in analyzed
                    else {"indexed surgery", "indexed surgery · no ventilator data"})
        assert label in expected, (candidate.pid, label)
    assert [research.candidate_table.item(row, 2).text() for row in range(len(candidates))] == labels
    research.candidate_table.setCurrentCell(0, 0)
    research._select_candidate(0)
    run.wait(lambda: _case_loaded(run.win), "the top prototype candidate")
    turn = research.conversation.turns[-1]
    if turn.model:
        assert not find_directive_phrases(turn.answer), turn.answer
        if run.question == "Why did pressure rise?":
            assert "record cannot establish cause" in turn.answer.lower(), turn.answer
    else:
        assert turn.badge == "No AI answer" and turn.answer == NO_MODEL_ANSWER
    return {**facts, "source_labels": labels, "top_pid": candidates[0].pid,
            "answer": turn.answer, "window_width": run.win.width()}


def intent_ask(run: Run, *, kind: str) -> dict[str, Any]:
    """Ask a scenario or comparison question built from the loaded cases and check the board's scope line."""
    from src.llm.intent import AGE_TOLERANCE, classify

    cases = run.win.cases.copy()
    cases["PID"] = cases["PID"].astype(str)
    research = run.win.research
    if kind == "scenario":
        for row in cases.itertuples():
            words = classify(f"55y {row.Procedure_short}").procedures
            if words and row.primary_agent_name in ("sevoflurane", "desflurane", "isoflurane") and row.Gender in ("F", "M"):
                break
        else:
            raise RuntimeError("no loaded case has a procedure word and an agent")
        sex = "woman" if row.Gender == "F" else "man"
        question = f"{int(row.Age)}-year-old {sex}, {words[0]}, {row.primary_agent_name}, elevated PIP"
        intent = classify(question)
        assert intent.kind == "scenario" and intent.age == float(int(row.Age)), intent
        facts = conversation_ask(run, question=question)
        candidates = research.result.candidates
        assert candidates and research.scope_label.text().startswith("Scenario: "), research.scope_label.text()
        assert "matching case" in research.scope_label.text()
        for candidate in candidates:
            assert abs(candidate.age - row.Age) <= AGE_TOLERANCE and candidate.gender == row.Gender, candidate
            assert words[0] in candidate.procedure.lower() and candidate.pid in set(cases.PID), candidate
            agent = cases.loc[cases.PID.eq(candidate.pid), "primary_agent_name"].iloc[0]
            assert agent == row.primary_agent_name, (candidate.pid, agent)
        research.board_scroll.ensureWidgetVisible(research.scope_label)
        run.settle()
        return {**facts, "question": question, "scope_line": research.scope_label.text(),
                "shortlist": [candidate.pid for candidate in candidates]}
    first, second = cases.PID.iloc[0], cases.PID.iloc[1]
    question = f"Compare {first} and {second.lower()} on pressure"
    assert classify(question).kind == "compare"
    facts = conversation_ask(run, question=question)
    assert [candidate.pid for candidate in research.result.candidates] == [first, second]
    assert research.scope_label.text() == f"Comparing {first} and {second}", research.scope_label.text()
    research.board_scroll.ensureWidgetVisible(research.scope_label)
    run.settle()
    return {**facts, "question": question, "scope_line": research.scope_label.text(),
            "shortlist": [first, second]}


def prototype_open(run: Run, *, signal: str) -> dict[str, Any]:
    open_case(run)
    research = run.win.research
    top_pid = research.result.candidates[0].pid
    assert research._current_pid == top_pid
    ts = run.win.ts[run.win.ts.PID.astype(str).eq(top_pid)].sort_values("t_min")
    facts = f_facts(run)
    minute = facts["cursor_minute"]
    assert facts["chart_minute"] == minute, (facts["chart_minute"], minute)
    selected = ts[ts.t_min.eq(minute)]
    assert not selected.empty, f"F selected minute {minute} absent from the timeseries"
    if signal == "PIP":
        maximum = ts.PIP.max()
        assert selected.PIP.eq(maximum).any(), (minute, maximum)
        facts.update(max_pip=float(maximum), selected_pip=float(selected.PIP.iloc[0]))
    else:
        first_missing = float(ts.loc[ts.ETCO2.isna(), "t_min"].iloc[0])
        assert minute == first_missing and selected.ETCO2.isna().all()
        assert research.metric_values["ETCO2"].text() == "missing"
        gaps = {}
        for pid, rows in run.win.ts.groupby("PID"):
            rows = rows.sort_values("t_min")
            seen = rows.ETCO2.notna()
            inside = rows.loc[~seen & seen.cummax() & seen.iloc[::-1].cummax().iloc[::-1], "t_min"]
            if not inside.empty:
                gaps[str(pid)] = [float(value) for value in inside]
        facts.update(first_missing_minute=first_missing, internal_etco2_gaps=gaps)
    research.signal_scroll.ensureWidgetVisible(research.minute_label)
    return facts


def prototype_flags(run: Run) -> dict[str, Any]:
    open_case(run)
    research = run.win.research
    assert research._current_pid == research.result.candidates[0].pid
    matches = research._episodes[research._episodes.rule_id.str.contains("etco2")]
    assert not matches.empty, "the top candidate has no ETCO2 episode"
    rule_id = str(matches.iloc[0].rule_id)
    facts = f_select(run, rule=rule_id)
    rule = research._rule_catalog.get(rule_id)
    assert "ETCO2" in rule.signals
    assert rule_id in facts["rule_detail"] and rule.label in facts["rule_detail"]
    run.settle()
    assert not research.rule_detail.visibleRegion().isEmpty()
    return facts


def conversation_start_cancel(run: Run) -> dict[str, Any]:
    import threading
    from types import SimpleNamespace
    from src.services.retrieval import RetrievalEngine

    research = run.win.research
    gate = threading.Event()
    if research.selected_model():
        original = ScriptedChatClient.chat_turn

        def slow_answer(self: Any, *args: Any, **kwargs: Any) -> Any:
            gate.set()
            time.sleep(0.4)
            return original(self, *args, **kwargs)

        ScriptedChatClient.chat_turn = slow_answer
    else:
        def slow_search(self: Any, question: str, on_progress: Any) -> Any:
            gate.set()
            while True:
                on_progress(SimpleNamespace(message="Searching synthetic records…"))
                time.sleep(0.01)

        RetrievalEngine.search = slow_search
    research.question_edit.setText("laparoscopic question to cancel")
    research.ask_button.click()
    run.wait(gate.is_set, "the scripted answer" if research.selected_model() else "the search")
    assert research.conversation.turns[-1].state == "running"
    return conversation_facts(run)


def conversation_cancel(run: Run) -> dict[str, Any]:
    research = run.win.research
    previous = research.conversation.turns[0]
    assert previous.board and previous.board.result
    expected = [candidate.pid for candidate in previous.board.result.candidates]
    research.cancel_button.click()
    run.wait(lambda: _research_idle(run.win), "the cancelled conversation request")
    assert research.conversation.turns[-1].state == "cancelled"
    assert research.question_edit.text() == previous.question
    facts = conversation_facts(run)
    assert facts["shortlist_pids"] == expected
    assert facts["scope"] == previous.board.labels[1]
    return {**facts, "board_restored": True}


def import_articles(run: Run) -> dict[str, Any]:
    from PySide6.QtWidgets import QFileDialog

    original = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = lambda *args, **kwargs: (str(ROOT / "tests/fixtures/articles_sample.jsonl"), "")
    try:
        menu = next(a.menu() for a in run.win.menuBar().actions() if a.text() == "&File")
        next(a for a in menu.actions() if a.text() == "Import articles…").trigger()
    finally:
        QFileDialog.getOpenFileName = original
    research = run.win.research
    research.scope_combo.setCurrentText("Articles")
    assert run.win._article_catalog.count == 12
    assert research.scope_label.text() == "Articles: 12 in the local catalog"
    return {"scope": research.scope_label.text(), "imported": 12}


def ask_articles(run: Run) -> dict[str, Any]:
    research = run.win.research
    research.question_edit.setText("pressure")
    research.ask_button.click()
    run.wait(lambda: _research_idle(run.win), "the article search and answer")
    result = research.article_result
    assert result is not None and result.articles
    assert research.result is None and run.win._case_worker is None
    turn = research.conversation.turns[-1]
    assert turn.scope == "Articles"
    if research.selected_model():
        assert turn.state == "answered" and "[A1]" in turn.answer
    else:
        assert turn.state == "no_model"
    research.board_scroll.verticalScrollBar().setValue(research.board_scroll.verticalScrollBar().maximum())
    return {"scope": research.scope_label.text(), "articles": len(result.articles),
            "titles": [a.title for a in result.articles], "answer": turn.answer, "state": turn.state}


def workspace_charts(run: Run) -> dict[str, Any]:
    research = run.win.research
    research.show_cases()
    overview = research._cases_overview
    assert not overview.charts_button.isChecked()
    overview.charts_button.click()
    run.settle()
    assert overview.charts_panel.isVisible() and not overview._charts_dirty
    return {"view": "cases", "charts_expanded": True, "scope": overview.scope_label.text()}


def workspace_signal(run: Run) -> dict[str, Any]:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QCheckBox, QDialog

    research = run.win.research
    research.close_cases()
    open_case(run)

    def choose() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        next(box for box in dialog.findChildren(QCheckBox) if box.text() == "SPO2").setChecked(True)
        dialog.accept()

    QTimer.singleShot(0, choose)
    research.more_signals_button.click()
    assert research.chart_track_names == ["PIP", "ETCO2", "HR", "SPO2"]
    return {"view": "detail", **f_facts(run)}


def workspace_scoring(run: Run) -> dict[str, Any]:
    research = run.win.research
    research.open_rules()
    drawer = research.rules_drawer
    drawer.scoring_button.click()
    run.settle()
    drawer.rule_scroll.ensureWidgetVisible(drawer.scoring_text)
    assert drawer.scoring_text.isVisible() and "warn_minute_weight" in drawer.scoring_text.text()
    return {"view": "rules", "scoring_expanded": True, "scoring": drawer.scoring_text.text()}


# The overview never asks the model anything, so a scripted client cannot mislabel its screenshots
OVERVIEW_LLM_MODES = ("off", "scripted")

SCENARIOS: dict[str, Scenario] = {
    "articles": Scenario((Step("import-articles", import_articles), Step("article-answer", ask_articles)),
                         llm_modes=("scripted", "off")),
    "proto-peak": Scenario((Step("load-data", run_pipeline), Step("ask", prototype_ask),
                             Step("proto-peak", partial(prototype_open, signal="PIP"))), llm_modes=("scripted", "off")),
    "proto-scenario": Scenario((Step("load-data", run_pipeline), Step("proto-scenario", prototype_ask)),
                                llm_modes=("scripted", "off")),
    "intent-scenario": Scenario((Step("load-data", run_pipeline), Step("intent-scenario", partial(intent_ask, kind="scenario"))),
                                 llm_modes=("scripted", "off")),
    "intent-compare": Scenario((Step("load-data", run_pipeline), Step("intent-compare", partial(intent_ask, kind="compare"))),
                                llm_modes=("scripted", "off")),
    "proto-missing": Scenario((Step("load-data", run_pipeline), Step("ask", prototype_ask),
                                Step("proto-missing", partial(prototype_open, signal="ETCO2"))), llm_modes=("scripted", "off")),
    "proto-flags": Scenario((Step("load-data", run_pipeline), Step("ask", prototype_ask),
                              Step("proto-flags", prototype_flags)), llm_modes=("scripted", "off")),
    "proto-cause": Scenario((Step("load-data", run_pipeline), Step("proto-cause", prototype_ask)),
                             llm_modes=("scripted", "off")),
    "f-episodes": Scenario((Step("data", f_load), Step("case", partial(f_open, kind="most")),
                             Step("episode", f_select))),
    "f-extra-signal": Scenario((Step("data", f_load), Step("case-900px", partial(f_open, kind="spo2")),
                                 Step("spo2", partial(f_select, rule="spo2_low")), Step("cleared", f_clear))),
    "f-drawer-link": Scenario((Step("data", f_load), Step("case", partial(f_open, kind="most")),
                                Step("episode", f_select), Step("drawer", f_drawer_link))),
    "f-no-flags": Scenario((Step("data", f_load), Step("no-flags", partial(f_open, kind="clean")))),
    "conversation-thread": Scenario((
        Step("load-data", conversation_load_data),
        Step("pressure", partial(conversation_ask, question="Where does pressure peak?")),
        Step("etco2-flags", partial(conversation_ask, question="Which cases have ETCO2 flags?")),
        Step("missing-etco2", partial(conversation_ask, question="Which cases have missing ETCO2?")),
        Step("empty-shortlist", partial(conversation_ask, question="xylophone tuning", expect_empty=True)),
        Step("first-board", conversation_restore),
    ), llm_modes=("scripted", "off")),
    "conversation-no-model": Scenario(
        (Step("load-data", conversation_load_data), Step("no-model", conversation_no_model)), llm_modes=("off",)
    ),
    "conversation-cancel": Scenario((
        Step("load-data", conversation_load_data),
        Step("previous-question", partial(conversation_ask, question=DEFAULT_QUESTION)),
        Step("answering", conversation_start_cancel),
        Step("cancelled", conversation_cancel),
    ), llm_modes=("scripted", "off")),
    "conversation-narrow": Scenario((
        Step("load-data", conversation_load_data),
        Step("pressure", partial(conversation_ask, question="Where does pressure peak?", narrow=True)),
        Step("etco2-flags", partial(conversation_ask, question="Which cases have ETCO2 flags?", narrow=True)),
    ), llm_modes=("scripted", "off")),
    "partial-index": Scenario((Step("partial-index", partial_index),), llm_modes=("off",)),
    "search-cancel": Scenario((Step("previous-question", ask), Step("searching", start_slow_search), Step("cancelled", cancel_search)), llm_modes=("off",)),
    "scan-cancel": Scenario((Step("scanning", start_scan), Step("cancelled", cancel_scan)), llm_modes=("off",)),
    "research-failure": Scenario((Step("failure", research_failure), Step("corrupt-scan", corrupt_scan, expects_alert=True), Step("missing-data", missing_data, expects_alert=True)), llm_modes=("off",)),
    "ollama-start": Scenario((Step("starting", slow_ollama_start), Step("ready", ollama_ready)), llm_modes=("off",)),
    "research-roundtrip": Scenario(
        (Step("ask", ask), Step("case", open_case), Step("return", return_to_question))
    ),
    "workspace-walk": Scenario((Step("load-data", run_pipeline), Step("question", ask),
                                  Step("charts", workspace_charts), Step("extra-signal", workspace_signal),
                                  Step("scoring", workspace_scoring))),
    "no-model": Scenario((Step("no-model", ask),), llm_modes=("off",)),
    "rules-drawer": Scenario((
        Step("data", run_pipeline),
        Step("open", open_rules_from_button),
        Step("search-etco2", partial(search_rules, text="etco2")),
        Step("search-empty", partial(search_rules, text="zzz")),
        Step("cleared", clear_rule_search),
        Step("show-rule", partial(show_rule, rule_id="spo2_low")),
        Step("escape", escape_rules),
    )),
    "rules-drawer-narrow": Scenario((
        Step("ask", ask), Step("narrow-open", open_rules_narrow), Step("shortlist", show_narrow_shortlist),
    )),
    "rules-drawer-keyboard": Scenario((
        Step("focus-question", focus_question),
        Step("ctrl-r", ctrl_r),
        Step("typed", partial(type_rule_search, text="peep")),
        Step("escape", escape_rules_from_question),
    )),
    "cases-overview": Scenario(
        (
            Step("load-data", run_pipeline),
            Step("open-cases", open_cases),
            Step("sorted-by-score", sort_cases_by_score),
            Step("top-case-in-f", open_top_case),
        ),
        llm_modes=OVERVIEW_LLM_MODES,
    ),
    "cases-overview-scan": Scenario(
        (
            Step("sample", sample_pipeline),
            Step("scan", full_scan),
            Step("scan-scope", switch_to_scan_scope),
            Step("scan-only-case-loading", request_scan_only_case),
            Step("scan-only-case-in-f", scan_case_detail),
            Step("back-to-cases", back_to_cases),
        ),
        llm_modes=OVERVIEW_LLM_MODES,
    ),
    "cases-overview-empty": Scenario((Step("empty", empty_overview),), llm_modes=OVERVIEW_LLM_MODES),
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


def load_drafts(path: Path | None, *, scenario: str | None = None) -> tuple[str, ...]:
    if path is None:
        return DEFAULT_DRAFTS
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"drafts file {path} could not be read: {exc}") from exc
    if scenario is not None:
        data = data.get(scenario) if isinstance(data, dict) else None
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
        self.research.model_combo.clear()
        self.research.model_combo.addItems(models or ["(Ollama stopped — click Start)"])
        self.research.model_status.setText(f"Ollama: replaced by the driver (--llm {mode})")
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

    real_exec = QDialog.exec

    def exec_dialog(dialog: Any) -> int:
        run.dialogs.append({"kind": "dialog", "title": dialog.windowTitle(), "text": ""})
        if dialog.windowTitle() == "More signals":
            return real_exec(dialog)
        return 0

    QDialog.exec = exec_dialog

    kinds = {QMessageBox.Icon.Warning: "warning", QMessageBox.Icon.Critical: "critical"}

    def open_box(box: Any) -> None:
        kind = kinds.get(box.icon(), "information")
        run.dialogs.append({"kind": kind, "title": box.windowTitle(), "text": box.text()})
        box.close()

    QMessageBox.open = open_box


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
    prototype = args.scenario.startswith(("proto-", "intent-"))
    fixture = ROOT / "tests" / "fixtures" / "drive_prototype.yaml"
    if prototype:
        import yaml

        example = yaml.safe_load(fixture.read_text(encoding="utf-8"))[args.scenario]
        args.question = example.get("question", args.question)
    drafts = ()
    if llm == "scripted":
        drafts = load_drafts(args.drafts or (fixture if prototype else None),
                             scenario=args.scenario if prototype and args.drafts is None else None)
    if args.scenario == "articles" and llm == "scripted" and args.drafts is None:
        drafts = ("The fictional abstract described pressure observations [A1].",)
    steps = scenario.steps
    if args.scenario.startswith("proto-") and llm == "off":
        steps = (Step("load-data", run_pipeline), Step(args.scenario, prototype_ask))

    emr = args.emr.expanduser().resolve()
    out = args.out.expanduser().resolve()
    marker = out / OUT_MARKER
    if out.exists() and any(out.iterdir()) and not marker.exists():
        raise InputError(f"--out {out} is not empty and was not written by this driver")
    out.mkdir(parents=True, exist_ok=True)
    marker.touch()
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
    if args.width is not None:
        win.resize(args.width, 1000)
    run = Run(app=app, win=win, question=args.question, frames_dir=frames_dir)
    record_dialogs(run)
    win.show()
    run.settle()
    replacements = [(str(state), "<state>"), (str(emr), "<emr>"), (str(out), "<out>")]

    failed = False
    started = time.monotonic()
    with open(out / "events.jsonl", "w", encoding="utf-8") as events:
        for step in steps:
            dialogs_before = len(run.dialogs)
            event: dict[str, Any] = {"step": step.name}
            try:
                facts = step.act(run)
                alerts = [d for d in run.dialogs[dialogs_before:] if d["kind"] in ("warning", "critical")]
                if alerts and not step.expects_alert:
                    raise RuntimeError(f"unexpected {alerts[0]['kind']} dialog: {alerts[0]['text']}")
                event.update(ok=True, **facts)
            except Exception as exc:
                failed = True
                event.update(ok=False, error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
            run.settle()
            png = out / f"{step.name}.png"
            win.grab().save(str(png))
            run.frame(STEP_HOLD_FRAMES)
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
    parser.add_argument("--width", type=int, default=None, help="window width in pixels")
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
