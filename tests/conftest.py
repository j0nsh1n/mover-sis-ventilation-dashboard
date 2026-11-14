"""Shared fixtures: tiny synthetic SIS EMR and cleaned frames."""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

from src.config import load_thresholds

# Temporary, Windows CI only: the Windows job crashes before pytest prints why its tests failed, so each failure,
# each test's start and the widgets left alive before a theme change are written out as they happen.
_DIAGNOSE_WINDOWS_CI = bool(os.environ.get("CI")) and sys.platform == "win32"


# The real terminal, opened while pytest's capture is paused: pytest captures file descriptor 2 itself, so neither
# sys.stderr nor sys.__stderr__ reaches the log while a test runs.
_REAL_ERR: list = []


def _diagnose(line: str) -> None:
    out = _REAL_ERR[0] if _REAL_ERR else sys.__stderr__
    out.write(line + "\n")
    out.flush()


if _DIAGNOSE_WINDOWS_CI:
    import faulthandler

    _fonts_told: list[bool] = []

    def pytest_configure(config):
        capture = config.pluginmanager.getplugin("capturemanager")
        with capture.global_and_fixture_disabled():
            _REAL_ERR.append(os.fdopen(os.dup(2), "w", buffering=1))

    def pytest_runtest_setup(item):
        # past pytest's capture, so the line shows even when the run hangs or crashes
        _diagnose(f">>> {item.nodeid}")
        # a test that runs ten minutes is stuck: write every thread's stack and end the run, not wait six hours
        faulthandler.dump_traceback_later(600, exit=True, file=_REAL_ERR[0] if _REAL_ERR else sys.__stderr__)
        if not _fonts_told:
            from PySide6.QtGui import QFontDatabase, QFontInfo, QGuiApplication

            app = QGuiApplication.instance()
            if isinstance(app, QGuiApplication):
                _fonts_told.append(True)
                font = app.font()
                screen = QGuiApplication.primaryScreen()
                bundled = [name for name in QFontDatabase.families() if name in ("Inter", "Charter")]
                _diagnose(
                    f"### font {font.family()!r} pt {font.pointSizeF()} px {font.pixelSize()}"
                    f" resolved {QFontInfo(font).family()!r}; bundled loaded {bundled};"
                    f" dpi {screen.logicalDotsPerInch() if screen else None}"
                    f" ratio {screen.devicePixelRatio() if screen else None}")

    def pytest_runtest_logreport(report):
        if report.failed:
            _diagnose(f"=== FAILED {report.nodeid} ({report.when})\n{report.longreprtext[-4000:]}\n=== END")


@pytest.fixture(scope="session")
def thresholds():
    return load_thresholds("default", validate=True)


@pytest.fixture(autouse=True)
def isolate_ollama_process_state(monkeypatch):
    """
    Keep Ollama lifecycle tests from touching real processes.

    ``src.llm.service`` keeps the spawned server in a module global. Tests that
    patch ``Popen`` leave a mock there, and a later ``stop_ollama()`` used to feed
    that mock's pid to ``os.killpg`` — where a non-int pid coerces to 1 and
    ``killpg(1, …)`` is ``kill(-1, …)``: SIGTERM to every process the user owns.
    Reset the state per test, block real matches, and fail loudly on a broadcast.
    """
    import src.llm.service as svc

    def _guarded_killpg(pgid, sig):
        raise AssertionError(
            f"test attempted a real os.killpg({pgid}, {sig}) — "
            "signalling process groups is not allowed under pytest"
        )

    # raising=False: os.killpg is POSIX-only and absent on Windows, where patching it
    # unconditionally errors every test at setup.
    monkeypatch.setattr(svc.os, "killpg", _guarded_killpg, raising=False)
    monkeypatch.setattr(svc, "_pgrep", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_started_proc", None, raising=False)
    monkeypatch.setattr(svc, "_app_started_server", False, raising=False)
    yield
    svc._started_proc = None
    svc._app_started_server = False


@pytest.fixture(scope="session", autouse=True)
def isolate_user_folders(tmp_path_factory):
    """Point the settings, data and state folders and the Ollama log at temp paths for the whole run.

    Without this, code that logs errors or reads settings resolved ~/.config on the real
    machine, and from 0.15.0 that also copied the user's settings into a new folder. The
    paths stay set for the session, not per test, because a worker thread can log after
    its test has finished. A test may still override them. HOME itself is not redirected,
    so a direct Path.home() still reaches the real home.
    """
    patch = pytest.MonkeyPatch()
    patch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg-config")))
    patch.setenv("XDG_DATA_HOME", str(tmp_path_factory.mktemp("xdg-data")))
    patch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("xdg-state")))
    patch.setenv("MOVER_OLLAMA_LOG", str(tmp_path_factory.mktemp("ollama-log") / "ollama.log"))
    yield
    patch.undo()


@pytest.fixture(autouse=True)
def fresh_settings_root(monkeypatch):
    """Each test resolves the settings folder from its own environment."""
    import src.user_settings as user_settings

    monkeypatch.setattr(user_settings, "_config_roots", {})
    monkeypatch.setattr(user_settings, "_migration_note", None)


class MemoryPasswordStore:
    """Stands in for KWallet / Credential Manager, so no test reads or writes the real password store."""

    priority = 1

    def __init__(self):
        self.secrets: dict[tuple[str, str], str] = {}
        self.broken = False

    def _check(self):
        from keyring.errors import KeyringLocked

        if self.broken:
            raise KeyringLocked("locked")

    def get_password(self, service, user):
        self._check()
        return self.secrets.get((service, user))

    def set_password(self, service, user, password):
        self._check()
        self.secrets[(service, user)] = password

    def delete_password(self, service, user):
        from keyring.errors import PasswordDeleteError

        self._check()
        if self.secrets.pop((service, user), None) is None:
            raise PasswordDeleteError("not found")


@pytest.fixture(autouse=True)
def password_store(monkeypatch):
    """Every test gets an empty in-memory password store and no Claude API key in its environment."""
    import keyring

    store = MemoryPasswordStore()
    monkeypatch.setattr(keyring, "get_keyring", lambda: store)
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, getattr(store, name))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from src.llm import cloud

    monkeypatch.setattr(cloud, "_stored", None)

    def no_network(*_args, **_kwargs):
        raise AssertionError("a test tried to build a real Claude API client")

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", no_network)
    return store


@pytest.fixture
def synthetic_emr(tmp_path: Path) -> Path:
    """
    Minimal EMR directory with 2 surgeries, ~20 minutes of vent+vitals each.
    Includes artifacts (\\N, high PIP ramp, high ETCO2) for flag tests.
    """
    emr = tmp_path / "EMR"
    emr.mkdir()

    cases = pd.DataFrame(
        [
            {
                "PID": "caseA",
                "Age": 55,
                "Ht": 170,
                "Wt": 80,
                "Gender": "M",
                "OR_start": "2016-06-01 08:00:00",
                "OR_end": "2016-06-01 10:00:00",
                "Surgery_start": "2016-06-01 08:15:00",
                "Surgery_end": "2016-06-01 09:45:00",
                "Procedure": "Laparoscopic Cholecystectomy",
            },
            {
                "PID": "caseB",
                "Age": 40,
                "Ht": 160,
                "Wt": 60,
                "Gender": "F",
                "OR_start": "2016-06-02 09:00:00",
                "OR_end": "2016-06-02 10:30:00",
                "Surgery_start": "2016-06-02 09:10:00",
                "Surgery_end": "2016-06-02 10:20:00",
                "Procedure": "Appendectomy",
            },
        ]
    )
    cases.to_csv(emr / "patient_information.csv", index=False)

    vent_rows = []
    vit_rows = []
    # caseA: rising PIP + high ETCO2 + sevo
    for i in range(25):
        t = pd.Timestamp("2016-06-01 08:15:00") + pd.Timedelta(minutes=i)
        pip = 18 + i * 0.8  # rising
        etco2 = 55 if i > 10 else 38
        vent_rows.append(
            {
                "PID": "caseA",
                "Obs_time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "Agent": "S",
                "Agent_Fi": 2.2,
                "Agent_Et": 2.0 if i < 15 else 2.8,  # drift
                "ETC02": etco2,
                "FIC02": 0.1,
                "TV": 520,
                "RR": 12,
                "PEEP": 5,
                "PIP": pip,
                "FIN20": 0,
                "ETN20": 0,
                "FI02": 50,
                "ET02": 45,
            }
        )
        vit_rows.append(
            {
                "PID": "caseA",
                "Obs_time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "HRe": 78,
                "HRp": 78,
                "nSBP": 120,
                "nMAP": 85,
                "nDBP": 70,
                "SP02": 99 if i < 20 else 90,
            }
        )

    # caseB: normal-ish + some \\N
    for i in range(20):
        t = pd.Timestamp("2016-06-02 09:10:00") + pd.Timedelta(minutes=i)
        vent_rows.append(
            {
                "PID": "caseB",
                "Obs_time": t.strftime("%m/%d/%y %H:%M"),
                "Agent": "S" if i > 0 else "N",
                "Agent_Fi": "\\N" if i == 0 else 1.8,
                "Agent_Et": "\\N" if i == 0 else 1.5,
                "ETC02": 36,
                "FIC02": 0,
                "TV": 450,
                "RR": 10,
                "PEEP": 4,
                "PIP": 20,
                "FIN20": 0,
                "ETN20": 0,
                "FI02": 40,
                "ET02": 38,
            }
        )
        vit_rows.append(
            {
                "PID": "caseB",
                "Obs_time": t.strftime("%Y-%m-%d %H:%M:%S"),
                "HRe": "\\N" if i == 0 else 70,
                "HRp": 72,
                "nSBP": "\\N",
                "nMAP": "\\N",
                "nDBP": "\\N",
                "SP02": 98,
            }
        )

    pd.DataFrame(vent_rows).to_csv(emr / "patient_ventilator.csv", index=False)
    pd.DataFrame(vit_rows).to_csv(emr / "patient_vitals.csv", index=False)

    events = pd.DataFrame(
        [
            {"PID": "caseA", "Event_time": "2016-06-01 08:16:00", "Event_name": "Intubation"},
            {"PID": "caseA", "Event_time": "2016-06-01 09:40:00", "Event_name": "Extubation"},
        ]
    )
    events.to_csv(emr / "patient_procedure_events.csv", index=False)

    return emr


@pytest.fixture
def pipeline_result(synthetic_emr, tmp_path, monkeypatch):
    # Allow writing processed artifacts under pytest tmp (outside repo)
    monkeypatch.setenv("MOVER_ALLOW_EXTERNAL_OUTPUT", "1")
    from src.pipeline.run import run_pipeline

    out = tmp_path / "processed"
    return run_pipeline(
        emr_dir=synthetic_emr,
        output_dir=out,
        pids=["caseA", "caseB"],
        preset="default",
        write_sample_csv=False,
        validate=True,
    )


@pytest.fixture(scope="module", params=["light", "dark"])
def applied_theme(request, qapp):
    """Apply each theme once for a module's tests, then go back to light.

    Applying a theme repolishes every live widget, including the ones earlier tests
    left behind, so doing it once per test makes a full run crawl.
    """
    from src.desktop.theme import apply_theme

    apply_theme(qapp, request.param)
    yield request.param
    if _DIAGNOSE_WINDOWS_CI:
        from PySide6.QtWidgets import QApplication

        _diagnose(f"--- before the theme goes back to light: {len(QApplication.allWidgets())} widgets alive")
        for widget in QApplication.topLevelWidgets():
            _diagnose(f"--- top level {type(widget).__name__} {widget.objectName()!r} visible={widget.isVisible()}")
    apply_theme(qapp, "light")


@pytest.fixture
def word_is_whole():
    """``word_is_whole(table, row, column)``: whether the missing-cell delegate paints the text uncut at that column's width.

    Worked out from font metrics, so it holds for any font and style on any platform.
    """
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

    from src.desktop.marks import _CELL_PADDING

    def check(table, row: int, column: int) -> bool:
        index = table.model().index(row, column)
        option = QStyleOptionViewItem()
        option.widget = table
        table.itemDelegate().initStyleOption(option, index)
        option.rect = QRect(0, 0, table.columnWidth(column), table.rowHeight(row))
        room = table.style().subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, table).width() - _CELL_PADDING
        italic = QFont(option.font)
        italic.setItalic(True)
        return QFontMetrics(italic).elidedText(option.text, Qt.TextElideMode.ElideRight, room) == option.text

    return check


@pytest.fixture
def error_cards(monkeypatch):
    """Record every error card instead of showing it. Each entry is the ErrorCard, so a test reads what it needs."""
    from src.desktop.error_card import ErrorCard

    shown: list = []
    monkeypatch.setattr(ErrorCard, "exec", lambda box: shown.append(box) or 0)
    monkeypatch.setattr(ErrorCard, "open", lambda box: shown.append(box))
    return shown


def _qt_app():
    # Importing PySide6 from a test that never uses it loads Qt for the rest of the run.
    if "PySide6.QtWidgets" not in sys.modules:
        return None
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    return app if isinstance(app, QApplication) else None


def _live_toplevels() -> list:
    """Windows with no parent widget. A menu or popup made with a parent is a window to Qt but goes with its parent."""
    app = _qt_app()
    if app is None:
        return []
    found = []
    for widget in app.topLevelWidgets():
        try:
            if widget.parentWidget() is not None:
                continue
        except RuntimeError:  # already deleted
            continue
        found.append(widget)
    return found


def _discard_so_close_cannot_ask(widget) -> None:
    # MainWindow.close asks a modal question when Settings is dirty. Nothing clicks it, so the run would wait.
    finder = getattr(widget, "_settings_page", None)
    if not callable(finder):
        return
    try:
        page = finder()
        if page is not None and page.is_dirty():
            # Mark the edits as the saved state; load() would read the settings again and ask Ollama for models.
            page._baseline = page.values()
    except RuntimeError:
        return


def _thread_still_running(widget) -> bool:
    from PySide6.QtCore import QThread

    try:
        return any(thread.isRunning() for thread in widget.findChildren(QThread))
    except RuntimeError:
        return False


def _flush_deferred_deletes() -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()


def _destroy_toplevels(widgets) -> list:
    """Close, deleteLater, and flush. Returns windows left up because a worker thread is still running.

    A closed window stays alive, and the next theme change restyles it. Deleting one while its
    QThread is in a socket read aborts the process, so those wait for the window's own idle delete.
    """
    left: list = []
    for widget in reversed(widgets):
        try:
            _discard_so_close_cannot_ask(widget)
            widget.close()
            if _thread_still_running(widget):
                left.append(widget)
                continue
            widget.deleteLater()
        except RuntimeError:  # already deleted by its owner
            pass
    _flush_deferred_deletes()
    still: list = []
    for widget in left:
        try:
            if _thread_still_running(widget):
                still.append(widget)
                continue
            widget.deleteLater()
        except RuntimeError:
            pass
    if still or left:
        _flush_deferred_deletes()
    return still


# Windows whose worker was still running at the last reap. The next reap tries again, because a
# snapshot taken while they are alive would otherwise treat them as someone else's and keep them.
_deferred_toplevels: list = []


def _reap_new_toplevels(before_ids: set[int], nodeid: str) -> None:
    global _deferred_toplevels
    live = _live_toplevels()
    live_ids = {id(widget) for widget in live}
    _deferred_toplevels = [widget for widget in _deferred_toplevels if id(widget) in live_ids]
    deferred_ids = {id(widget) for widget in _deferred_toplevels}
    leaked = [widget for widget in live if id(widget) not in before_ids or id(widget) in deferred_ids]
    fresh = [widget for widget in leaked if id(widget) not in deferred_ids]
    if fresh:
        names = ", ".join(sorted({type(widget).__name__ for widget in fresh}))
        message = (
            f"{nodeid} left {len(fresh)} top-level widget(s) ({names}); "
            "closed and deleted them so a later theme change does not restyle them"
        )
        # A failure here would bury an assertion that already failed, and Qt also opens menus on its own.
        warnings.warn(message, UserWarning, stacklevel=2)
        if _DIAGNOSE_WINDOWS_CI:
            _diagnose("--- " + message)
    if leaked:
        _deferred_toplevels = _destroy_toplevels(leaked)


@pytest.fixture(autouse=True, scope="module")
def _reap_module_toplevels(request):
    """Drop windows a module fixture left up, once the module's tests have finished."""
    held = _live_toplevels()
    before = {id(widget) for widget in held}
    yield
    _reap_new_toplevels(before, request.node.nodeid)
    held.clear()


@pytest.fixture(autouse=True)
def _reap_test_toplevels(request):
    """Drop top-level widgets this test created. Ones that already existed (the app, a module page) stay."""
    held = _live_toplevels()
    before = {id(widget) for widget in held}
    yield
    _reap_new_toplevels(before, request.node.nodeid)
    held.clear()


@pytest.fixture
def dispose():
    """Return ``keep(widget)``: every widget passed to it is closed and really deleted when the test ends.

    A closed top-level widget is not deleted by Qt, and a window holds Python callbacks that point back at
    itself, so left alone every window of every test stays alive and each later theme change restyles it too.
    """
    made: list = []

    def keep(widget):
        made.append(widget)
        return widget

    yield keep
    _destroy_toplevels(made)
