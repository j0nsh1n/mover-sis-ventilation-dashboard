from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.desktop.app import MainWindow
from src.llm.service import OllamaStatus


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("MOVER_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    dialogs = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: dialogs.append(args[1:]))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: dialogs.append(args[1:]))
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win, app, dialogs
    for worker in (win._ollama_worker,):
        if worker is not None:
            worker.requestInterruption()
            assert worker.wait(5000)
    win.close()
    app.processEvents()


def pump(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


def ready():
    return OllamaStatus(True, "ready", ["synthetic:model"], "http://127.0.0.1:11434")


def test_close_during_start_is_prompt_and_responsive(window, monkeypatch):
    win, app, _ = window
    entered = threading.Event()

    def start(**kwargs):
        entered.set()
        stop = kwargs.get("should_stop", lambda: False)
        deadline = time.monotonic() + 3.5
        while not stop() and time.monotonic() < deadline:
            time.sleep(0.01)
        return ready()

    monkeypatch.setattr("src.llm.service.start_ollama", start)
    win._ollama_start()
    assert entered.wait(1)
    ticks = []
    QTimer.singleShot(20, lambda: ticks.append(time.monotonic()))
    before = time.monotonic()
    win.close()
    close_call = time.monotonic() - before
    pump(app, lambda: not win._ollama_worker.isRunning() and not win.isVisible() and not win._closing)
    assert close_call < 0.1
    assert time.monotonic() - before < 5
    pump(app, lambda: bool(ticks))
    assert ticks[0] - before < 0.2
    assert not win._closing


def test_stop_runs_off_thread_and_start_remains_disabled(window, monkeypatch):
    win, app, _ = window
    gate = threading.Event()
    threads = []

    def stop(**kwargs):
        threads.append(threading.get_ident())
        gate.wait(0.25)
        return True, "stopped"

    monkeypatch.setattr("src.llm.service.stop_ollama", stop)
    monkeypatch.setattr("src.llm.service.probe_ollama", lambda **kw: ready())
    before = time.monotonic()
    win._ollama_stop()
    elapsed = time.monotonic() - before
    try:
        assert elapsed < 0.1
        assert not win.research.start_button.isEnabled()
    finally:
        gate.set()
    pump(app, lambda: not win._ollama_worker.isRunning())
    assert threads and threads[0] != threading.get_ident()
    assert win.research.start_button.isEnabled()


@pytest.mark.parametrize("action", ["probe", "start"])
def test_failure_dialog_and_status_match_action(window, monkeypatch, action):
    win, app, dialogs = window

    def fail(**kw):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(f"src.llm.service.{'probe_ollama' if action == 'probe' else 'start_ollama'}", fail)
    win._start_ollama_worker(action)
    pump(app, lambda: not win._ollama_worker.isRunning())
    assert bool(dialogs) == (action == "start")
    assert "synthetic failure" in win.statusBar().currentMessage()
    assert "Starting" not in win.statusBar().currentMessage()


def test_probe_clears_checking_status_and_restores_original_labels(window, monkeypatch):
    win, app, _ = window
    monkeypatch.setattr("src.llm.service.probe_ollama", lambda **kw: ready())
    button = win.research.start_button
    button.setText("Launch local AI")
    win._refresh_ollama_models()
    pump(app, lambda: not win._ollama_worker.isRunning())
    assert "Checking" not in win.statusBar().currentMessage()
    assert button.text() == "Launch local AI"


def test_result_uses_emitted_action_after_worker_replacement(window):
    win, _, dialogs = window
    win._ollama_worker = SimpleNamespace(action="start", isRunning=lambda: False, requestInterruption=lambda: None, wait=lambda ms: True)
    stopped = OllamaStatus(False, "offline", [], "http://127.0.0.1:11434")
    win.statusBar().showMessage("Checking local model…")
    win._on_ollama_ready("probe", stopped)
    assert not dialogs
    assert "offline" in win.statusBar().currentMessage()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_buttons_have_visible_focus_and_start_moves_focus(window, monkeypatch, theme):
    from src.desktop.theme import apply_theme

    win, app, _ = window
    apply_theme(app, theme)
    assert "QPushButton:focus" in app.styleSheet()
    assert "border: 2px solid" in app.styleSheet()
    gate = threading.Event()
    monkeypatch.setattr("src.llm.service.start_ollama", lambda **kw: (gate.wait(1), ready())[1])
    win.research.start_button.setFocus()
    app.processEvents()
    QTest.keyClick(win.research.start_button, Qt.Key.Key_Space)
    try:
        app.processEvents()
        assert app.focusWidget() is win.research.question_edit
    finally:
        gate.set()
    pump(app, lambda: not win._ollama_worker.isRunning())


def test_start_stays_disabled_until_probe_thread_finishes(window, monkeypatch):
    from src.desktop.app import OllamaWorker

    win, app, _ = window
    gate = threading.Event()

    class PausedProbe(OllamaWorker):
        def run(self):
            self.finished_ok.emit(self.action, ready())
            gate.wait(1)

    monkeypatch.setattr("src.desktop.app.OllamaWorker", PausedProbe)
    win._refresh_ollama_models()
    try:
        pump(app, lambda: win.research.status_label.text() == "Local model ready.")
        assert win._ollama_worker.isRunning()
        assert not win.research.start_button.isEnabled()
    finally:
        gate.set()
    pump(app, lambda: not win._ollama_worker.isRunning())
    assert win.research.start_button.isEnabled()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_research_primary_button_renders_focus_outline(window, theme):
    from src.desktop.theme import apply_theme

    win, app, _ = window
    apply_theme(app, theme)
    button = win.research.ask_button
    win.research.question_edit.setFocus()
    app.processEvents()
    unfocused = button.grab().toImage()
    button.setFocus()
    app.processEvents()
    focused = button.grab().toImage()
    assert button.hasFocus()
    assert focused.pixelColor(1, focused.height() // 2) != unfocused.pixelColor(1, unfocused.height() // 2)
