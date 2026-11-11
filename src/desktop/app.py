"""
MOVER SIS desktop research workspace (PySide6).

Launch:
  python -m src.desktop
  ./scripts/run_desktop.sh
"""

from __future__ import annotations

import html
import json
import os
import sys
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from PySide6.QtCore import QEvent, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.desktop.research_workspace import ResearchWorkspace
from src.desktop.command_palette import Command, CommandPalette
from src.desktop.error_card import ErrorCard, show_failure
from src.desktop.settings_page import SettingsPage
from src.desktop.setup_flow import SetupFlow
from src.desktop.shell import DEMO_SOURCE, DEMO_SOURCE_TIP, FooterFields, NavRail, PracticeTabs, RunningHead
from src.desktop.suite_chrome import install_suite_chrome, set_page_title
from src.desktop.conversation_panel import NO_MODEL_ANSWER
from src.desktop.research_workers import CaseFetchWorker, ResearchWorker
from src.services.articles import ArticleCatalog, ArticleCatalogError, ArticleSearchResult
from src.services.data_context import current_context
from src.desktop.theme import apply_theme
from src.desktop.updates import UpdateController
from src.guardrails.exceptions import GuardrailError
from src.guardrails.limits import ALLOWED_PRESETS, MAX_N_CASES, MIN_N_CASES
from src.__version__ import get_version
from src.runtime_paths import (
    apply_persisted_settings,
    configure_emr_directory,
    configure_from_user_directory,
    configure_wave_directory,
    demo_data_in_use,
    emr_dir,
    processed_dir,
    wave_dir,
)
from src.user_settings import (
    apply_ollama_env_from_settings,
    get_theme,
    load_settings,
    needs_first_run_setup,
    settings_migration_note,
    settings_path,
)

if TYPE_CHECKING:
    from src.services.retrieval import RetrievalEngine

# Practice tools in tab order: module key and the page name used in titles and the breadcrumb.
PRACTICE_TOOLS = (
    ("handoff", "Handoff practice"),
    ("medications", "Medication and vitals"),
    ("who", "WHO checklist"),
)
_PRACTICE_TITLES = dict(PRACTICE_TOOLS)
SIGNAL_NEEDS_CASE = "Signal needs an open case. Open one from Research or Cases first."
SIGNAL_TIP = "Signal: the open case's signals, rule episodes and source rows"

NO_VENT_MESSAGE = (
    "this surgery has no ventilator rows in the EMR export, so there are no "
    "ventilation signals or flags to show. Vitals alone are not analyzed."
)

class DataLoadWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, processed: Path | None = None, parent=None):
        super().__init__(parent)
        self.processed = processed
        self.emr = emr_dir()
        self.job_stamp = current_context().stamp()

    def run(self) -> None:
        try:
            from src.services.data import load_processed

            if self.processed is None:
                self.processed = processed_dir()
            data = load_processed(self.processed, validate=False, expected_emr=self.emr)
            self.finished_ok.emit(data)
        except Exception as e:
            from src.desktop.errors import error_message

            self.failed.emit(error_message("load", e))


class PipelineWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        n_cases: int,
        preset: str,
        force: bool,
        emr: Path | None = None,
        processed: Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.n_cases = n_cases
        self.preset = preset
        self.force = force
        self.emr = emr if emr is not None else emr_dir()
        self.processed = processed
        self.job_stamp = current_context().stamp()

    def run(self) -> None:
        try:
            from src.services.data import ensure_data

            data = ensure_data(
                n_cases=self.n_cases,
                preset=self.preset,
                force=self.force,
                emr_dir=self.emr,
                processed_dir=self.processed,
            )
            self.finished_ok.emit(data)
        except Exception as e:
            from src.desktop.errors import error_message

            self.failed.emit(error_message("pipeline", e))


class CorpusScanWorker(QThread):
    """Score every ventilated surgery in the EMR (bounded memory, can take minutes)."""

    progress = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        emr: Path,
        processed: Path,
        preset: str = "default",
        workers: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.emr = emr
        self.processed = processed
        self.preset = preset
        self.workers = workers  # None = automatic (CPU cores minus one)
        self.job_stamp = current_context().stamp()

    def run(self) -> None:
        from src.pipeline.corpus_scan import ScanCancelled, scan_corpus, scan_failure_message

        try:
            result = scan_corpus(
                self.emr,
                self.processed,
                preset=self.preset,
                workers=self.workers,
                on_progress=lambda message, _done, _total: self.progress.emit(message),
                should_stop=self.isInterruptionRequested,
            )
            self.finished_ok.emit(result)
        except ScanCancelled:
            self.cancelled.emit()
        except Exception as e:
            # Exception text can carry paths and patient data: it goes to the log only
            self.failed.emit(scan_failure_message(e, self.processed))


class OllamaWorker(QThread):
    finished_ok = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, action: str, parent=None):
        super().__init__(parent)
        self.action = action

    def run(self) -> None:
        try:
            from src.llm.service import ensure_ollama, probe_ollama, start_ollama, stop_ollama

            if self.action == "start":
                status = start_ollama(wait_s=35.0, should_stop=self.isInterruptionRequested)
            elif self.action == "ensure":
                status = ensure_ollama(start_if_needed=True, wait_s=20.0, should_stop=self.isInterruptionRequested)
            elif self.action == "stop":
                ok, message = stop_ollama()
                if not ok:
                    raise RuntimeError(message)
                status = probe_ollama()
            else:
                status = probe_ollama()
            if not self.isInterruptionRequested():
                self.finished_ok.emit(self.action, status)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(self.action, str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._version = get_version()
        self.resize(1380, 860)

        apply_persisted_settings()
        apply_ollama_env_from_settings()
        self._active_preset = str(load_settings().get("preset") or "default")
        self._preset_recorded = True
        self._thresholds_sha256: str | None = None
        self._bind_data_context()

        self.cases: pd.DataFrame | None = None
        self.ts: pd.DataFrame | None = None
        self.flags: pd.DataFrame | None = None
        self._analysis_facts: dict[str, str] = {}
        self.episodes: pd.DataFrame | None = None
        self.events: pd.DataFrame | None = None
        # Full-EMR scan results (src.pipeline.corpus_scan.ScanResult), if any,
        # and the processed folder they belong to
        self.corpus_scan = None
        self._corpus_scan_dir: Path | None = None
        # A data reload asked for while a scan ran; it runs when the scan ends
        self._reload_after_scan = False
        # Whether the data on screen is the fictional demo data. It is taken when loaded data is dropped for a
        # reload, not read from the folder setting each time: between a folder change and the end of its reload
        # the old data is still on screen and must keep its own name.
        self._shown_data_is_demo = demo_data_in_use()
        # What the Medication page was last given: (case, demo) for an open case, None for its invented record.
        self._medication_shown: tuple[str, bool] | None | bool = False
        self._worker: QThread | None = None
        self._ollama_worker: OllamaWorker | None = None
        self._ollama_available = False
        self._ollama_button_label: str | None = None
        self._closing = False
        self._research_worker: ResearchWorker | None = None
        self._case_worker: CaseFetchWorker | None = None
        self._research_request_id = 0
        self._active_research_request: int | None = None
        self._research_workers: dict[int, ResearchWorker] = {}
        self._case_request_id = 0
        self._research_engine = None
        self._pending_case: tuple[str, bool, bool, bool] | None = None
        self._queued_case: tuple[str, bool, bool, bool] | None = None
        self._current_module = "monitoring"
        self._last_practice = PRACTICE_TOOLS[0][0]
        # Where Escape on the Settings page goes back to
        self._settings_return = "monitoring"
        # The first-time setup flow while it fills the window
        self._setup_flow: SetupFlow | None = None
        # unchecked, busy:<words>, ready, stopped or failed
        self._model_state = "unchecked"
        self._palette: CommandPalette | None = None
        self._first_focus_done = False

        self._build_actions()
        install_suite_chrome(self, self._version)
        # The footer sits inside the page column, so the rail runs the full window height beside it
        self._footer_bar = QStatusBar()
        self._build_ui()
        self._refresh_rule_reference()
        from src.desktop.accessibility import announce

        self.statusBar().messageChanged.connect(lambda message: announce(self.statusBar(), message))
        self._set_status("Ready")
        self._updates = UpdateController(self._version, self)
        self._updates.found.connect(self._on_update_found)
        self._updates.ready.connect(self._on_update_ready)
        self._updates.failed.connect(self._on_update_failed)
        self._update_button = QPushButton("Restart to update", self)
        self._update_button.setAutoDefault(True)
        self._update_button.clicked.connect(self._restart_for_update)
        self._update_button.hide()
        self.statusBar().addPermanentWidget(self._update_button)
        self._scan_cancel_button = QPushButton("Cancel scan", self)
        self._scan_cancel_button.setAutoDefault(True)
        self._scan_cancel_button.clicked.connect(self._cancel_corpus_scan)
        self._scan_cancel_button.hide()
        self.statusBar().addPermanentWidget(self._scan_cancel_button)
        settings_path()
        note = settings_migration_note()
        self._settings_note = QLabel(note or "", self)
        self._settings_note.setVisible(bool(note))
        self.statusBar().addPermanentWidget(self._settings_note)

        QTimer.singleShot(0, self._startup)

    # ----- actions -----
    def _build_actions(self) -> None:
        """Every command is an action on the window.

        There is no menu bar. The actions stay on the window so their shortcuts work, and
        Ctrl+K lists these same objects, so a command and its shortcut cannot drift apart.
        """
        self._commands: list[Command] = []
        self._module_actions: dict[str, QAction] = {}
        self._theme_actions: dict[str, QAction] = {}

        def add(name: str, text: str, handler, *, group: str, shortcut: str = "",
                keywords: str = "", shortcut_hint: str = "") -> QAction:
            action = QAction(text, self)
            action.setObjectName(name)
            if shortcut:
                action.setShortcut(shortcut)
                action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            # triggered carries a "checked" flag that none of the handlers take
            action.triggered.connect(lambda _checked=False, run=handler, key=name: self._run_command(key, run))
            self.addAction(action)
            self._commands.append(Command(action, group, keywords, shortcut_hint))
            return action

        places = "Go to"
        self._module_actions["monitoring"] = add(
            "suite-module-monitoring", "Research", self._resume_research, group=places,
            shortcut="Ctrl+1", keywords="monitoring evidence question board home",
        )
        self._nav_cases = add(
            "suite-nav-cases", "Cases", partial(self._go, "cases"), group=places,
            keywords="overview table list analyzed indexed",
        )
        self._nav_signal = add(
            "suite-nav-signal", "Signal", partial(self._go, "signal"), group=places,
            keywords="case detail chart episodes minute source rows",
        )
        self._nav_signal.setToolTip(SIGNAL_NEEDS_CASE)
        self._nav_signal.setEnabled(False)
        for key, label, shortcut in (
            ("handoff", "Handoff practice", "Ctrl+2"),
            ("medications", "Medication and vitals", "Ctrl+3"),
            ("who", "WHO checklist", "Ctrl+4"),
        ):
            self._module_actions[key] = add(
                f"suite-module-{key}", label, partial(self._go, key), group=places,
                shortcut=shortcut, keywords="practice fictional",
            )
        self._nav_rules = add(
            "suite-nav-rules", "Rules", partial(self._go, "rules"), group=places,
            keywords="reference thresholds drawer search rule",
            shortcut_hint="Ctrl+R on research pages",
        )
        self.settings_act = add(
            "action-settings", "Settings\u2026", self._open_settings, group=places, shortcut="Ctrl+,",
            keywords="preferences data folders models theme appearance",
        )
        self.settings_act.setToolTip("Settings (Ctrl+,) \u2014 data folders, Ollama models dir, theme")

        data = "Data"
        add("action-open-emr", "Open EMR folder\u2026", self._browse_emr_folder, group=data,
            shortcut="Ctrl+O", keywords="file choose source")
        add("action-open-wave", "Open Wave folder\u2026", self._browse_wave_folder, group=data,
            shortcut="Ctrl+Shift+O", keywords="file choose waveforms")
        add("action-import-articles", "Import articles\u2026", self._import_articles, group=data,
            keywords="file csv jsonl catalog")
        add("action-reload", "Reload processed data", self._autoload_processed, group=data,
            shortcut="Ctrl+R", keywords="file refresh cache")
        add("action-run-pipeline", "Run pipeline (50 cases)\u2026", self._run_pipeline_menu, group=data,
            keywords="file analyze rebuild")
        add("action-scan-emr", "Scan full EMR (all ventilated surgeries)\u2026", self._run_corpus_scan,
            group=data, keywords="file score corpus")
        add("action-setup", "Data setup\u2026", self._open_setup_wizard, group=data,
            keywords="setup file wizard first-time folders demo")

        appearance = "Appearance"
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for mode, label in (("light", "Theme: Light"), ("dark", "Theme: Dark"), ("system", "Theme: System")):
            action = add(f"action-theme-{mode}", label, partial(self._set_theme_quick, mode),
                         group=appearance, keywords="view dark mode colors")
            action.setCheckable(True)
            self._theme_group.addAction(action)
            self._theme_actions[mode] = action
        self._sync_theme_actions()

        app = "App"
        add("action-about", "About", self._about, group=app, keywords="help version")
        self.update_act = add("action-restart-update", "Restart to finish update", self._restart_for_update,
                              group=app, keywords="help upgrade version")
        self.update_act.setVisible(False)
        add("action-exit", "Exit", self._quit_app, group=app, shortcut="Ctrl+Q", keywords="file quit close")
        self._find_act = QAction("Find", self)
        self._find_act.setObjectName("action-find")
        self._find_act.setShortcut("Ctrl+K")
        self._find_act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self._find_act.triggered.connect(lambda _checked=False: self._open_palette())
        self.addAction(self._find_act)

    def _run_command(self, name: str, run) -> None:
        """Run a command, except while the setup flow fills the window: it hides the pages these commands act on."""
        if self._setup_flow is not None and name != "action-exit":
            self.statusBar().showMessage("Finish setup, or choose Not now, first.", 4000)
            return
        run()

    def _sync_theme_actions(self) -> None:
        action = self._theme_actions.get(get_theme())
        if action is not None:
            action.setChecked(True)

    def _quit_app(self) -> None:
        self.close()

    def _open_palette(self) -> None:
        if self._palette is not None or self._setup_flow is not None:
            return
        # An action hidden from view (an update that is not ready) is not offered either
        commands = [command for command in self._commands if command.action.isVisible()]
        # The installed models change with Ollama's state, so their lines are made when the palette opens.
        models = [Command(action, "Answer model", "ollama local ai choose use")
                  for action in self.research.model_menu.model_actions()]
        after_local_ai = 1 + max(i for i, command in enumerate(commands) if command.group == "Local AI")
        commands[after_local_ai:after_local_ai] = models
        palette = CommandPalette(commands, self)
        palette.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        palette.finished.connect(self._palette_closed)
        self._palette = palette
        palette.open_over(self)

    def _palette_closed(self) -> None:
        self._palette = None
        # Some platforms leave no window active after a frameless dialog closes, and shortcuts need one
        self.activateWindow()

    # ----- layout -----
    def statusBar(self) -> QStatusBar:  # noqa: N802
        """The footer. It lives in the page column, not in the window's own status bar slot.

        Qt status tips (QAction.setStatusTip) post to the window's own slot, which is empty, so they
        show nowhere; write footer text through this bar instead.
        """
        return self._footer_bar

    def _build_ui(self) -> None:
        self.research = ResearchWorkspace()
        self._article_catalog = None
        try:
            self._article_catalog = ArticleCatalog(settings_path().parent / "case_embeddings.sqlite3")
            self.research.set_article_catalog(self._article_catalog.count)
        except ArticleCatalogError as exc:
            self.research.set_article_catalog(0, str(exc))
            self.statusBar().showMessage(str(exc))
        self.research.question_submitted.connect(self._start_research_search)
        self.research.cancel_requested.connect(self._cancel_research)
        self.research.board_restore_requested.connect(self._invalidate_research_requests)
        self.research.case_requested.connect(self._load_research_case)
        self.research.setup_requested.connect(self._open_setup_wizard)
        self.research.model_start_requested.connect(self._ollama_start)
        self.research.model_refresh_requested.connect(
            lambda: self._refresh_ollama_models(start_if_needed=False)
        )
        self.research.model_changed.connect(self._on_ollama_model_changed)
        self.research.model_stop_requested.connect(self._ollama_stop)
        self.research.model_unload_requested.connect(self._ollama_unload)
        # The page's own commands follow the places they belong to, ahead of Data and Appearance.
        page_commands = [
            Command(action, group, keywords, "Ctrl+Enter" if action is self.research.trace_action else "")
            for action, group, keywords in self.research.command_entries()
        ]
        after_places = 1 + max(i for i, command in enumerate(self._commands) if command.group == "Go to")
        self._commands[after_places:after_places] = page_commands
        self.module_stack = QStackedWidget()
        self.module_stack.setObjectName("suite-modules")
        self.module_stack.setFrameShape(QFrame.Shape.NoFrame)
        self.module_stack.setContentsMargins(0, 0, 0, 0)
        self.module_stack.addWidget(self.research)
        self._suite_pages: dict[str, QWidget] = {"monitoring": self.research}
        self._build_frame()
        self._footer = FooterFields(self.statusBar())
        self.research.place_changed.connect(self._sync_frame)
        self._show_suite_module("monitoring")

    def _build_frame(self) -> None:
        """Rail on the left; running head, practice tabs and the pages on the right."""
        self.rail = NavRail()
        self.rail.item_clicked.connect(self._go)
        self.head = RunningHead()
        self.head.find_requested.connect(self._open_palette)
        self.practice_tabs = PracticeTabs(PRACTICE_TOOLS)
        self.practice_tabs.tool_selected.connect(self._go)
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.head)
        column.addWidget(self.practice_tabs)
        column.addWidget(self.module_stack, 1)
        column.addWidget(self._footer_bar)
        frame = QWidget()
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.rail)
        row.addWidget(body, 1)
        # The setup flow is a second page of this stack, so it fills the window, rail included
        self._root = QStackedWidget()
        self._root.addWidget(frame)
        self.setCentralWidget(self._root)

    # ----- paths -----
    def _sync_path_fields_from_runtime(self) -> None:
        """Refresh the footer after path changes (the folders live in the source field's tooltip and in Settings)."""
        self._refresh_footer()
        page = self._settings_page()
        if page is not None:
            page.refresh_in_use()
            if self._current_module == "settings" and not page.is_dirty():
                page.load()

    def _set_status(self, message: str, timeout: int = 0) -> None:
        """Show a temporary message. Folder paths never go here; the source field's tooltip holds them."""
        self._refresh_footer()
        self.statusBar().showMessage(message, timeout)

    def _browse_emr_folder(self) -> None:
        start = str(emr_dir())
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select SIS EMR folder (patient_information.csv)",
            start,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not chosen:
            return
        try:
            configure_emr_directory(chosen, persist=True)
        except Exception as e:
            show_failure(self, what="That folder cannot be used as the EMR folder.", why=str(e),
                         next_step="Choose another folder. The EMR folder stays as it was.",
                         title="Invalid EMR folder")
            return
        self._set_status("EMR folder set")
        self._autoload_processed()

    def _browse_wave_folder(self) -> None:
        wave = wave_dir()
        start = str(wave) if wave else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select SIS wave folder (Waveforms/ or sis_wave*.tar.gz)",
            start,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not chosen:
            return
        try:
            configure_wave_directory(chosen, persist=True)
        except Exception as e:
            show_failure(self, what="That folder cannot be used as the wave folder.", why=str(e),
                         next_step="Choose another folder. The wave folder stays as it was.",
                         title="Invalid wave folder")
            return
        # Waveforms are looked up from wave_dir() on demand, so the status bar is
        # the only thing to refresh (the old filters sidebar no longer exists).
        self._set_status("Wave folder set")

    def _browse_data_folder(self) -> None:
        """Menu shortcut: same as EMR browse (legacy)."""
        self._browse_emr_folder()

    def _apply_data_folder(self, path: Path) -> None:
        """Compatibility helper used by tests / menu."""
        try:
            configure_from_user_directory(path, persist=True)
        except Exception as e:
            show_failure(self, what="That folder cannot be used as the data folder.", why=str(e),
                         next_step="Choose another folder. The folders in use stay as they were.",
                         title="Invalid data folder")
            return
        self._set_status("EMR folder set")
        self._autoload_processed()

    # ----- workers -----
    def _bind_data_context(self) -> None:
        """Copy the live folders, preset, and local models into the shared context."""
        context = current_context()
        context.note_paths(emr_dir(), processed_dir())
        context.note_preset(
            self._active_preset,
            recorded=self._preset_recorded,
            thresholds_sha256=self._thresholds_sha256,
        )
        settings = load_settings()
        embed_changed = context.note_models(
            chat_model=str(settings.get("ollama_model") or "") or None,
            embed_model=str(settings.get("ollama_embed_model") or "nomic-embed-text"),
        )
        if embed_changed and getattr(self, "_research_engine", None) is not None:
            self._research_engine = None

    def _autoload_processed(self) -> None:
        # Even when a job is running and the reload has to wait, an old scan must go now
        self._bind_data_context()
        self._drop_stale_scan()
        self._clear_loaded_sample()
        proc = processed_dir()
        worker = self._worker
        running = worker is not None and bool(getattr(worker, "isRunning", lambda: False)())
        if running:
            # The job may be writing the first cache. Reload when it finishes
            # instead of dropping its result with no follow-up load.
            self._reload_after_scan = True
            if not (proc / "cases.parquet").exists():
                self._set_status(
                    "No processed cache — run the pipeline or pick another folder"
                )
                self.research.set_status(
                    "No analyzed case cache. Indexed surgery search is available after setup."
                )
            return
        if not (proc / "cases.parquet").exists():
            self._set_status(
                "No processed cache — run the pipeline or pick another folder"
            )
            self.research.set_status("No analyzed case cache. Indexed surgery search is available after setup.")
            self._load_corpus_scan()
            self._push_cases_overview()
            if not needs_first_run_setup():
                self._on_load_fail("Missing processed files: cases.parquet")
            return
        self._set_status("Loading data…")
        self._worker = DataLoadWorker(processed=proc, parent=self)
        self._worker.finished_ok.connect(self._on_pipeline_ok)
        self._worker.failed.connect(self._on_load_fail)
        self._worker.finished.connect(self._after_data_job)
        self._worker.start()

    def _clear_loaded_sample(self) -> None:
        # From here on whatever is shown comes from the folders in use now.
        self._shown_data_is_demo = demo_data_in_use()
        # The same case number in another folder is another record, so the Medication page must read it again.
        self._medication_shown = False
        self.cases = self.ts = self.flags = self.episodes = self.events = None
        self._analysis_facts = {}
        self._thresholds_sha256 = None
        self.research.invalidate_case_data()
        self._set_loaded_preset(self._next_run_preset(), recorded=True)
        self._data_changed()

    def _after_data_job(self) -> None:
        if self.sender() is not None and self.sender() is not self._worker:
            return
        if self._reload_after_scan:
            self._reload_after_scan = False
            self._autoload_processed()

    def _on_load_fail(self, msg: str) -> None:
        if not self._data_job_is_current():
            return
        self._clear_loaded_sample()
        self._set_status("Could not load processed data")
        self.research.set_status("Processed data could not load. Indexed surgery search remains available.")
        self._warn(
            "Load error", msg, what="The analyzed cases could not be loaded.",
            next_step="Press Ctrl+K and choose Run pipeline to rebuild the analyzed sample for this EMR.",
        )

    def _warn(self, title: str, text: str, *, what: str | None = None, next_step: str = "") -> None:
        # open() returns immediately. exec() would hold startup until the box closed.
        box = ErrorCard(self, what=what or title, why=text, next_step=next_step, title=title)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.destroyed.connect(lambda *_args: QTimer.singleShot(0, self._focus_research_question))
        box.open()

    def _focus_research_question(self) -> None:
        edit = self.research.question_edit
        if not edit.isEnabled() or not edit.isVisible():
            return
        self.activateWindow()
        edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()
        if not self._first_focus_done:
            self._first_focus_done = True
            self._focus_research_question()

    @staticmethod
    def _is_ctrl_r(event: QKeyEvent) -> bool:
        return event.key() == Qt.Key.Key_R and event.modifiers() == Qt.KeyboardModifier.ControlModifier

    def event(self, event: QEvent) -> bool:  # noqa: N802
        # Ctrl+R opens the rules on every research page, whichever frame widget (rail, Find) has focus,
        # so the Reload action must not take the key there.
        if (event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent)
                and self._is_ctrl_r(event) and self._current_module == "monitoring"):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # Keys that reach the window come from the frame (rail, Find, head); the workspace handles its own.
        if self._current_module == "monitoring":
            if self._is_ctrl_r(event):
                event.accept()
                self.research.open_rules(QApplication.focusWidget())
                return
            if (event.key() == Qt.Key.Key_Escape and self.research.stack.currentIndex() == 1
                    and not self.research.rules_drawer.isVisible()):
                event.accept()
                self.research.return_to_canvas()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._closing and not self._confirm_leave_settings():
            event.ignore()
            return
        self._updates.close()
        if self._setup_flow is not None:
            self._setup_flow.shutdown()
        running = [w for w in (
            self._worker, self._research_worker,
            self._case_worker, self._ollama_worker, *self._research_workers.values(),
            *self.research.follow_up_workers(),
        ) if w is not None and w.isRunning()]
        if running:
            self._closing = True
            self.hide()
            for worker in running:
                worker.requestInterruption()
            event.ignore()
            QTimer.singleShot(50, self.close)
            return
        self._closing = False
        super().closeEvent(event)
        if not event.isAccepted():
            self._closing = False

    def _on_update_found(self, version: str) -> None:
        self.statusBar().showMessage(f"Downloading MOVER Suite v{version} update…")

    def _on_update_ready(self, version: str, _staged: Path) -> None:
        self._update_button.setText(f"Restart to update to v{version}")
        self._update_button.show()
        self.update_act.setVisible(True)
        self.statusBar().showMessage(f"Version {version} is ready. Restart when convenient.")

    def _on_update_failed(self, detail: str) -> None:
        self.statusBar().showMessage(f"Update could not be prepared: {detail}", 15_000)

    def _update_failed_to_start(self, reason: str) -> None:
        show_failure(self, what="The update could not start.", why=reason,
                     next_step="MOVER Suite keeps running as it is. Try again later.",
                     title="Update could not start", level="warning")

    def _restart_for_update(self) -> None:
        from src.runtime_paths import app_dir
        from src.update import prepare_restart_apply, spawn_restart_apply

        staged = self._updates.staged
        if staged is None:
            return
        platform = self._updates.platform
        if platform is None:
            return
        try:
            plan = prepare_restart_apply(app_dir(), staged, platform=platform)
        except OSError as exc:
            self._update_failed_to_start(str(exc))
            return
        if not plan.ready or not plan.supported_here:
            self._update_failed_to_start(plan.detail)
            return
        try:
            result = spawn_restart_apply(plan)
        except OSError as exc:
            self._update_failed_to_start(str(exc))
            return
        if not result.spawned:
            self._update_failed_to_start(result.detail)
            return
        self.close()

    def _about(self) -> None:
        from src.desktop.about_dialog import AboutDialog

        sha = self._thresholds_sha256[:6] if self._thresholds_sha256 else ""
        rules = f"{self._active_preset}" + (f" @{sha}" if sha else "")
        AboutDialog(self._version, rules, self, demo=self._shown_data_is_demo).exec()

    def _setup_bypassed(self) -> bool:
        return os.environ.get("MOVER_SKIP_SETUP", "").strip().lower() in {
            "1",
            "true",
            "yes",
        } or os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen"

    def _startup(self) -> None:
        from src.guardrails.validate_io import validate_emr_dir

        if self._setup_bypassed():
            self.research.set_setup_required(False)
            self._autoload_processed()
            self._refresh_ollama_models(start_if_needed=False)
            return
        try:
            validate_emr_dir(emr_dir())
            path_ready = True
        except Exception:
            path_ready = False
        if needs_first_run_setup() or not path_ready:
            self._open_setup_wizard(first_run=True)
            return
        self.research.set_setup_required(False)
        self._autoload_processed()
        self._refresh_ollama_models(start_if_needed=False)

    def _open_setup_wizard(self, first_run: bool = False) -> None:
        """Show the setup flow over the whole window (first run, or Data setup from Settings and Ctrl+K)."""
        if self._setup_flow is not None or not self._confirm_leave_settings():
            return
        flow = SetupFlow(self._version, first_run=first_run)
        flow.completed.connect(self._setup_completed)
        flow.dismissed.connect(partial(self._setup_dismissed, first_run))
        flow.step_changed.connect(
            lambda step: set_page_title(self, f"{'First-time setup' if first_run else 'Data setup'}: {step}")
        )
        self._setup_flow = flow
        self._root.addWidget(flow)
        self._root.setCurrentWidget(flow)
        set_page_title(self, "First-time setup" if first_run else "Data setup")
        flow.focus_first()

    def _close_setup_flow(self) -> None:
        flow = self._setup_flow
        if flow is None:
            return
        self._setup_flow = None
        flow.shutdown()
        self._root.setCurrentIndex(0)
        self._root.removeWidget(flow)
        flow.deleteLater()
        self._sync_frame()

    def _setup_completed(self, vals: dict) -> None:
        self._close_setup_flow()
        self._on_settings_applied(vals)
        self.research.set_setup_required(False)
        self._sync_path_fields_from_runtime()
        self._set_status("Setup finished")
        self._show_suite_module("monitoring")
        self._autoload_processed()
        self._refresh_ollama_models()
        self.research.question_edit.setFocus()

    def _setup_dismissed(self, first_run: bool) -> None:
        self._close_setup_flow()
        if first_run:
            self.research.set_setup_required(True)
        self._set_status("Setup required" if first_run else "Setup cancelled")
        if self._current_module == "monitoring":
            self._focus_research_question()

    def _settings_page(self) -> SettingsPage | None:
        page = self._suite_pages.get("settings")
        return page if isinstance(page, SettingsPage) else None

    def _open_settings(self) -> None:
        """Settings is a page of the window: the rail, Ctrl+, and Ctrl+K all come here."""
        if self._current_module == "settings":
            page = self._settings_page()
            if page is not None:
                page.sections.setFocus(Qt.FocusReason.ShortcutFocusReason)
            return
        self._suite_page("settings")
        page = self._settings_page()
        if page is None:
            return
        previous = self._current_module
        page.load()
        if self._show_suite_module("settings"):
            self._settings_return = previous
            page.sections.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _confirm_leave_settings(self) -> bool:
        """True unless the Settings page has edits and the person chose to stay."""
        page = self._settings_page()
        return page is None or self._current_module != "settings" or page.confirm_leave()

    def _close_settings(self) -> None:
        """Escape on the Settings page: back to where it was opened from."""
        if self._current_module != "settings":
            return
        target = self._settings_return if self._settings_return != "settings" else "monitoring"
        if not self._show_suite_module(target):
            return
        if target == "monitoring":
            self._focus_research_question()
        else:
            self.practice_tabs.bar.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _on_settings_saved(self, vals: dict, changed: list) -> None:
        """What happens after Settings saves: apply it, then reload the data when the EMR folder is usable."""
        from src.guardrails.validate_io import validate_emr_dir

        self._on_settings_applied(vals)
        self._research_engine = None
        try:
            validate_emr_dir(emr_dir())
            ready = True
        except Exception:
            ready = False
        self.research.set_setup_required(not ready)
        self._sync_path_fields_from_runtime()
        self._set_status("Settings saved" if ready else "EMR setup required")
        saved = f"Saved: {', '.join(changed) if changed else 'nothing had changed'}."
        page = self._settings_page()
        if ready:
            self._autoload_processed()
            self._refresh_ollama_models()
            sentence = f"{saved} Reloading data from the saved folders."
        else:
            sentence = (f"{saved} The EMR folder is not usable yet, so no data was loaded. "
                        "Choose another folder or run data setup.")
        if page is not None:
            page.show_result(sentence, ok=ready)

    def _on_settings_applied(self, vals: dict) -> None:
        self._research_engine = None
        apply_persisted_settings()
        apply_ollama_env_from_settings()
        theme = vals.get("theme")
        if theme:
            from src.user_settings import update_settings

            update_settings(theme=theme)
            apply_theme(QApplication.instance(), theme)
            self._sync_theme_actions()
            self.research.refresh_charts()
        if self.cases is None:
            preset = str(vals.get("preset") or load_settings().get("preset") or "default")
            if self._set_loaded_preset(preset, recorded=True):
                self.research.invalidate_case_data()
        self._sync_path_fields_from_runtime()

    def _set_theme_quick(self, mode: str) -> None:
        from src.user_settings import update_settings

        update_settings(theme=mode)
        concrete = apply_theme(QApplication.instance(), mode)
        self._sync_theme_actions()
        page = self._settings_page()
        if page is not None:
            page.theme_saved(mode)
        self.research.refresh_charts()
        self.statusBar().showMessage(f"Theme: {mode} ({concrete})", 4000)

    def _run_pipeline_menu(self) -> None:
        """The Run pipeline command: the 50-case sample."""
        self._run_pipeline(n_cases=50, preset=self._next_run_preset())

    def _next_run_preset(self) -> str:
        preset = str(load_settings().get("preset") or "default")
        return preset if preset in ALLOWED_PRESETS else "default"

    def _run_pipeline(self, n_cases: int = 50, preset: str = "default") -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "A background job is already running.")
            return
        n_cases = max(MIN_N_CASES, min(int(n_cases), min(200, MAX_N_CASES)))
        if preset not in ALLOWED_PRESETS:
            preset = "default"
        self._bind_data_context()
        self._set_status("Running pipeline…")
        self._worker = PipelineWorker(
            n_cases=n_cases,
            preset=preset,
            force=True,
            emr=emr_dir(),
            processed=processed_dir(),
            parent=self,
        )
        self._worker.finished_ok.connect(self._on_pipeline_ok)
        self._worker.failed.connect(self._on_pipeline_fail)
        self._worker.finished.connect(self._after_data_job)
        self._worker.start()

    def _run_corpus_scan(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "A background job is already running.")
            return
        answer = QMessageBox.question(
            self,
            "Scan full EMR",
            "Score every surgery with ventilator data in the EMR folder?\n\n"
            "This reads the whole export in batches, using several CPU cores at once, "
            "and can take many minutes on a full-size export. The loaded sample and its charts are not changed; the "
            "co-pilot uses the scan for whole-dataset questions.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._bind_data_context()
        self._set_status("Scanning full EMR…")
        worker = CorpusScanWorker(emr_dir(), processed_dir(), preset=self._next_run_preset(), parent=self)
        worker.progress.connect(lambda message: self.statusBar().showMessage(message))
        worker.finished_ok.connect(
            lambda result, finished=worker: self._on_corpus_scan_ok(
                result, finished.processed, finished
            )
        )
        worker.failed.connect(self._on_corpus_scan_fail)
        worker.cancelled.connect(self._on_corpus_scan_cancelled)
        worker.finished.connect(self._scan_cancel_button.hide)
        worker.finished.connect(lambda: self._after_corpus_scan(worker))
        self._scan_cancel_button.setEnabled(True)
        self._scan_cancel_button.show()
        self._worker = worker
        worker.start()

    def _cancel_corpus_scan(self) -> None:
        if isinstance(self._worker, CorpusScanWorker) and self._worker.isRunning():
            self._worker.requestInterruption()
            self._scan_cancel_button.setEnabled(False)
            self.statusBar().showMessage("Cancelling full-EMR scan…")

    def _on_corpus_scan_ok(
        self,
        result,
        processed: Path | None = None,
        worker: QThread | None = None,
    ) -> None:
        from src.pipeline.corpus_scan import scan_matches_emr

        # A direct call has no producing worker and still applies. A queued
        # signal carries the worker that finished, not whichever job is current.
        if worker is not None:
            stamp = getattr(worker, "job_stamp", None)
            if stamp is not None and not current_context().accepts(stamp):
                self._set_status(
                    "Full-EMR scan finished for a previous data folder; it was not loaded."
                )
                return
        if (processed is not None and processed != processed_dir()) or not scan_matches_emr(result.meta, emr_dir()):
            # The EMR or processed folder changed while the scan ran
            self._set_status("Full-EMR scan finished for a previous data folder; it was not loaded.")
            self._drop_stale_scan()
            return
        self.corpus_scan = result
        self._corpus_scan_dir = processed_dir()
        self._research_engine = None  # scenario agents come from the scan
        meta = result.meta
        failures = meta.get("failed_shards", [])
        failed_count = sum(shard.get("n_pids", 0) for shard in failures)
        message = (
            f"Full-EMR scan: {meta.get('n_scanned_cases')} of "
            f"{meta.get('n_ventilated_surgeries')} surgeries scored"
        )
        if failed_count:
            message += f"; {failed_count} could not be scored (see details)"
        self.statusBar().setToolTip(str(failures[0].get("error", ""))[:300] if failures else "")
        self._set_status(message)
        self._push_cases_overview()

    def _on_corpus_scan_cancelled(self) -> None:
        self._set_status("Full-EMR scan cancelled; the previous scan was kept.")

    def _on_corpus_scan_fail(self, msg: str) -> None:
        self._set_status(msg.splitlines()[0])
        show_failure(self, what="The full-EMR scan did not finish.", why=msg[:4000],
                     next_step="The loaded sample and any earlier scan were kept.", title="Full-EMR scan")

    def _after_corpus_scan(self, worker: QThread) -> None:
        if self._reload_after_scan and self._worker is worker:
            self._reload_after_scan = False
            if self._closing:
                return
            worker.wait()
            self._autoload_processed()

    def _drop_stale_scan(self) -> None:
        """Forget a loaded scan that no longer matches the EMR or processed folder."""
        from src.pipeline.corpus_scan import scan_matches_emr

        scan = self.corpus_scan
        if scan is None or (self._corpus_scan_dir == processed_dir() and scan_matches_emr(scan.meta, emr_dir())):
            return
        self.corpus_scan = None
        self._corpus_scan_dir = None
        # A search started with the old scan must not answer after the switch
        if self.research._searching:
            self._cancel_research()
        self._data_changed()

    def _load_corpus_scan(self) -> None:
        """Pick up a previous full-EMR scan saved next to the processed sample.

        A scan recorded for a different EMR folder is set aside, not shown.
        """
        from src.pipeline.corpus_scan import load_corpus_scan

        self._research_engine = None  # scenario agents come from the scan
        try:
            self.corpus_scan = load_corpus_scan(processed_dir(), emr_dir())
            self._corpus_scan_dir = processed_dir() if self.corpus_scan is not None else None
        except Exception as exc:
            from src.desktop.errors import error_message

            self.corpus_scan = None
            self._corpus_scan_dir = None
            self._warn(
                "Saved full-EMR scan could not load",
                error_message("scan-load", exc),
            )

    def _data_job_is_current(self) -> bool:
        origin = self.sender()
        if origin is None:
            return True
        if origin is not self._worker:
            return False
        worker = origin
        stamp = getattr(worker, "job_stamp", None)
        if stamp is not None and not current_context().accepts(stamp):
            return False
        if isinstance(worker, (PipelineWorker, DataLoadWorker)):
            return (worker.emr.resolve() == emr_dir().resolve()
                    and (worker.processed is None
                         or worker.processed.resolve() == processed_dir().resolve()))
        return True

    def _on_pipeline_ok(self, data) -> None:
        if not self._data_job_is_current():
            return
        self.cases, self.ts, self.flags, self.episodes, self.events = data
        from src.services.retrieval import build_analysis_facts

        self._analysis_facts = build_analysis_facts(self.ts, self.flags)
        preset, recorded = "default", False
        recorded_hash = None
        if isinstance(self._worker, (PipelineWorker, DataLoadWorker)):
            root = self._worker.processed if self._worker.processed is not None else processed_dir()
            try:
                meta = json.loads((root / "run_meta.json").read_text())
            except (OSError, ValueError):
                meta = {}
            if isinstance(meta, dict):
                value = meta.get("thresholds_sha256")
                if isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value):
                    recorded_hash = value
        if isinstance(self._worker, PipelineWorker):
            preset, recorded = self._worker.preset, True
        elif isinstance(self._worker, DataLoadWorker):
            recorded_preset = meta.get("preset") if isinstance(meta, dict) else None
            if isinstance(recorded_preset, str) and recorded_preset in ALLOWED_PRESETS:
                preset, recorded = recorded_preset, True
        self._thresholds_sha256 = recorded_hash
        preset_changed = self._set_loaded_preset(preset, recorded)
        self._load_corpus_scan()
        self._data_changed()
        if self.research.invalidate_case_data():
            self.research.set_status(
                f"Data was reloaded with preset {self._active_preset}. Reopen the case to refresh its flags."
            )
        elif preset_changed:
            self.research.set_status(f"Data was reloaded with preset {self._active_preset}.")
        # The footer fields already show the counts; this only confirms the load, then clears
        self._set_status(f"Loaded {len(self.cases)} cases", 6000)

    def _on_pipeline_fail(self, msg: str) -> None:
        if not self._data_job_is_current():
            return
        self._set_status("Pipeline failed")
        show_failure(
            self, what="The pipeline did not finish.", why=msg[:4000],
            next_step="Make sure the EMR folder has patient_information, patient_ventilator and patient_vitals CSVs.",
            title="Pipeline / guardrail error",
        )

    def _refresh_ollama_models(self, *, start_if_needed: bool = False) -> None:
        self._start_ollama_worker("ensure" if start_if_needed else "probe")

    def _apply_ollama_status(self, status) -> None:
        from src.llm.service import ollama_status_summary

        prev = self.research.selected_model() or ""
        if not status.available:
            self.research.set_models([], placeholder="(Ollama stopped — choose Start AI)")
            self._set_ollama_status_ui(status)
            if self.statusBar().currentMessage() in ("Checking local model…", "Starting local model…", "Stopping local model…"):
                self.statusBar().showMessage(status.message)
            return

        models = status.models
        if not models:
            self.research.set_models([], placeholder="(no models — ollama pull gemma4)")
        else:
            saved = load_settings().get("ollama_model")
            preferred_list = []
            if prev:
                preferred_list.append(prev)
            if saved:
                preferred_list.append(str(saved))
            preferred_list.extend(
                [
                    "qwen3:14b",
                    "qwen2.5:14b",
                    "gemma4:latest",
                    "mistral-small3.1:24b",
                ]
            )
            self.research.set_models(
                list(models), next((name for name in preferred_list if name in models), None)
            )
        self._set_ollama_status_ui(status)
        if self.statusBar().currentMessage() in ("Checking local model…", "Starting local model…", "Stopping local model…"):
            self.statusBar().showMessage(ollama_status_summary(status))

    def _set_model_state(self, state: str) -> None:
        """One place for the local model's state, so the footer and the menu button never disagree."""
        self._model_state = state
        self.research.set_model_state(state)
        self._refresh_footer()

    def _set_ollama_status_ui(self, status) -> None:
        self._ollama_available = status.available
        self._set_model_state("ready" if status.available else "stopped")
        busy = self._ollama_worker is not None and self._ollama_worker.isRunning()
        research = self.research
        if status.available:
            n = len(status.models)
            extra = " · started by app" if status.started_by_app else ""
            research.set_model_status(f"Ollama: <b>running</b> · {n} model(s){extra}", rich=True, relevant=False)
            research.start_action.setEnabled(not busy)
            research.stop_action.setEnabled(not busy)
            research.unload_action.setEnabled(not busy)
        else:
            research.set_model_status("Ollama: <b>stopped</b> — choose Start AI in the Local AI menu", rich=True)
            research.start_action.setEnabled(not busy)
            research.stop_action.setEnabled(False)
            research.unload_action.setEnabled(False)

    def _ollama_start(self) -> None:
        self._start_ollama_worker("start")

    def _start_ollama_worker(self, action: str) -> None:
        if self._closing:
            return
        if self._ollama_worker is not None and self._ollama_worker.isRunning():
            return
        self._ollama_button_label = self.research.start_action.text()
        label = {"probe": "Checking…", "stop": "Stopping…"}.get(action, "Starting…")
        self.research.start_action.setText(label)
        for command in (self.research.start_action, self.research.stop_action, self.research.unload_action):
            command.setEnabled(False)
        message = {"probe": "Checking local model…", "stop": "Stopping local model…"}.get(action, "Starting local model…")
        self._set_model_state("busy:" + {"probe": "checking", "stop": "stopping"}.get(action, "starting"))
        self.research.set_model_status(message)
        self.research.set_status(message)
        self.statusBar().showMessage(message)
        worker = OllamaWorker(action, parent=self)
        self._ollama_worker = worker
        worker.finished_ok.connect(self._on_ollama_ready)
        worker.failed.connect(self._on_ollama_failure)
        worker.finished.connect(self._on_ollama_done)
        worker.start()

    def _on_ollama_done(self) -> None:
        if self.sender() is not self._ollama_worker:
            return
        self.research.start_action.setEnabled(True)
        self.research.stop_action.setEnabled(self._ollama_available)
        self.research.unload_action.setEnabled(self._ollama_available)
        if self._ollama_button_label is not None:
            self.research.start_action.setText(self._ollama_button_label)
            self._ollama_button_label = None

    def _on_ollama_ready(self, action: str, status) -> None:
        if self._closing:
            return
        self._apply_ollama_status(status)
        self.research.set_status("Local model ready." if status.available else status.message)
        if not status.available and action == "start":
            self._on_ollama_failure(action, status.message)

    def _on_ollama_failure(self, action: str, message: str) -> None:
        if self._closing:
            return
        self._ollama_available = False
        self._set_model_state("failed")
        self.research.set_model_status(f"Local model failed: {message}")
        self.research.set_status(f"Local model failed: {message}")
        self.statusBar().showMessage(f"Local model failed: {message}")
        if action == "start":
            show_failure(self, what="The local model service did not start.", why=message,
                         next_step="Search still works without it. Check that Ollama is installed, then try Start AI again.",
                         title="Ollama start", level="warning")

    def _ollama_stop(self) -> None:
        if self.research._searching:
            self._cancel_research()
        self._start_ollama_worker("stop")

    def _ollama_unload(self) -> None:
        from src.llm.service import unload_ollama_model

        model = self.research.selected_model() or ""
        ok, msg = unload_ollama_model(model)
        self.statusBar().showMessage(msg)
        if ok:
            QMessageBox.information(self, "Model unloaded", msg)
        else:
            show_failure(self, what="The model could not be unloaded.", why=msg, title="Unload model", level="warning")

    def _on_ollama_model_changed(self, text: str) -> None:
        text = (text or "").strip()
        if text and not text.startswith("("):
            from src.user_settings import update_settings

            update_settings(ollama_model=text)
            current_context().note_models(
                chat_model=text,
                embed_model=current_context().embed_model,
            )
            self._refresh_footer()

    def _make_session_snapshot(self):
        from src.llm.tools import SessionState

        return SessionState(
            preset=self._active_preset,
            cases=self.cases,
            timeseries=self.ts,
            flags=self.flags,
            episodes=self.episodes,
            events=self.events,
            corpus=self.corpus_scan.cases if self.corpus_scan is not None else None,
            corpus_episodes=self.corpus_scan.episodes if self.corpus_scan is not None else None,
            corpus_meta=self.corpus_scan.meta if self.corpus_scan is not None else None,
        )

    def _import_articles(self) -> None:
        if self._article_catalog is None:
            show_failure(self, what="Article import is not available.", why=self.research._article_error,
                         title="Article import unavailable", level="warning")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import articles", "", "Article files (*.csv *.jsonl)")
        if not path:
            return
        try:
            report = self._article_catalog.import_file(Path(path))
        except (OSError, ValueError) as exc:
            show_failure(self, what="The articles could not be imported.", why=str(exc),
                         title="Article import failed", level="warning")
            return
        self.research.set_article_catalog(self._article_catalog.count)
        message = f"Imported {report.imported:,} articles. {len(report.errors):,} rows skipped."
        if report.errors:
            message += "\n\n" + "\n".join(report.errors)
        QMessageBox.information(self, "Article import", message)

    def _case_agents(self) -> dict[str, str]:
        """Primary anesthetic agent per case, for scenario questions.

        A full-EMR scan supplies it for every ventilated surgery; the analyzed
        sample, scored with the loaded preset, wins where both have a case.
        """
        agents: dict[str, str] = {}
        tables = [self.corpus_scan.cases if self.corpus_scan is not None else None, self.cases]
        for cases in tables:
            if cases is None or not {"PID", "primary_agent_name"} <= set(cases.columns):
                continue
            known = cases[["PID", "primary_agent_name"]].dropna()
            agents.update(zip(known["PID"].astype(str), known["primary_agent_name"].astype(str), strict=True))
        return agents

    def _new_research_engine(self, source: Path) -> RetrievalEngine:
        from src.llm.ollama_client import OllamaEmbedder
        from src.services.retrieval import RetrievalEngine

        return RetrievalEngine(
            emr=source,
            cache_path=settings_path().parent / "case_embeddings.sqlite3",
            embedder=OllamaEmbedder(current_context().embed_model),
            preset=self._active_preset,
            analysis_facts=self._analysis_facts,
            case_agents=self._case_agents(),
            agents_from_scan=self.corpus_scan is not None,
            analyzed_pids=(self.cases["PID"].astype(str).tolist() if self.cases is not None and "PID" in self.cases else []),
        )

    def _start_research_search(self, question: str) -> None:
        from src.guardrails.validate_io import validate_emr_dir
        from src.llm.ollama_client import OllamaEmbedder

        articles = self.research.scope == "Articles"
        if articles:
            if self._article_catalog is None:
                return
            self._bind_data_context()
            engine = ArticleCatalog(
                settings_path().parent / "case_embeddings.sqlite3",
                OllamaEmbedder(current_context().embed_model),
            )
        else:
            try:
                source = validate_emr_dir(emr_dir())
            except Exception as exc:
                self.research.set_status(f"EMR setup required: {exc}")
                return
            self._bind_data_context()
            if (self._research_engine is None or self._research_engine.emr != source
                    or any(worker.isRunning() for worker in self._research_workers.values())):
                self._research_engine = self._new_research_engine(source)
            engine = self._research_engine
        if self.research._searching:
            self._cancel_research()
        self._invalidate_research_requests()
        self._research_request_id += 1
        request_id = self._research_request_id
        self._active_research_request = request_id
        self.research.begin_question(question, request_id)
        self.research.set_progress("Searching the local article catalog…" if articles else "Preparing indexed surgery search…")
        self._research_worker = ResearchWorker(
            engine,
            question,
            self.research.selected_model(),
            None if articles else self._make_session_snapshot(),
            parent=self,
        )
        self._research_workers[request_id] = self._research_worker
        self._research_worker.progress.connect(
            lambda progress: self.research.set_progress(
                progress.message, str(getattr(progress, "phase", "")), getattr(progress, "total", 0))
            if request_id == self._active_research_request else None
        )
        self._research_worker.shortlist.connect(lambda result: self._on_research_shortlist(result, request_id))
        self._research_worker.answer.connect(lambda response: self._on_research_answer(response, request_id))
        self._research_worker.failed.connect(lambda message: self._on_research_fail(message, request_id))
        self._research_worker.finished.connect(lambda: self._on_research_done(request_id))
        self._research_worker.start()

    def _set_loaded_preset(self, preset: str, recorded: bool) -> bool:
        """Make ``preset`` the one every reader uses; True when it changed."""
        changed = (preset, recorded) != (self._active_preset, self._preset_recorded)
        self._active_preset, self._preset_recorded = preset, recorded
        current_context().note_preset(
            preset,
            recorded=recorded,
            thresholds_sha256=self._thresholds_sha256,
        )
        self._refresh_rule_reference()
        self._refresh_footer()
        if changed:
            self._data_changed()
        return changed

    def _data_changed(self) -> None:
        self._research_engine = None
        self._invalidate_research_requests()
        self._push_cases_overview()

    def _push_cases_overview(self) -> None:
        if not hasattr(self, "research"):
            return
        from src.rules import load_rule_catalog

        try:
            catalog = load_rule_catalog(self._active_preset)
        except GuardrailError:
            catalog = None
        self.research.set_cases_data(self.cases, self.corpus_scan, catalog, self.flags, self.ts, self.episodes)

    def _invalidate_research_requests(self) -> None:
        self._active_research_request = None
        self._case_request_id += 1
        if self._research_worker is not None and self._research_worker.isRunning():
            self._research_worker.requestInterruption()
        if self._case_worker is not None and self._case_worker.isRunning():
            self._case_worker.requestInterruption()
        self._pending_case = None
        self._queued_case = None

    def _cancel_research(self) -> None:
        self._invalidate_research_requests()
        self.research.cancel_question()

    def _on_research_shortlist(self, result, request_id: int | None = None) -> None:
        if request_id is not None and request_id != self._active_research_request:
            return
        if isinstance(result, ArticleSearchResult):
            self.research.set_article_results(result)
            turn = self.research.conversation.turn(request_id)
            if not result.articles:
                self.research.set_answer(result.message, request_id)
            elif turn is not None and turn.model is None:
                self.research.set_answer(NO_MODEL_ANSWER, request_id, state="no_model")
            return
        self.research.set_results(result)
        self._refresh_footer()
        turn = self.research.conversation.turn(request_id)
        unanswerable = result.empty_reason or (
            "" if result.candidates else "No matching indexed surgery records were found for this question."
        )
        if turn is not None and turn.model is None:
            answer = NO_MODEL_ANSWER
            if unanswerable:
                answer += f"\n\n{unanswerable}"
            self.research.set_answer(answer, request_id, state="no_model")
        elif unanswerable:
            self.research.set_answer(unanswerable, request_id)
        preferred = self.research.preferred_pid()
        if preferred:
            self._load_research_case(preferred, False, False)
            other = next((c.pid for c in result.candidates if c.pid != preferred), None) if result.compare else None
            if other:
                self._load_research_case(other, True, False)

    def _on_research_answer(self, response, request_id: int | None = None) -> None:
        if request_id is not None and request_id != self._active_research_request:
            return
        self.research.set_answer(str(getattr(response, "answer", "") or "No answer was returned."), request_id)

    def _on_research_fail(self, message: str, request_id: int | None = None) -> None:
        if request_id is not None and request_id != self._active_research_request:
            return
        self.research.set_status(f"Research request failed: {message}")
        self.research.set_answer(f"Research request failed: {message}\n\nRetrieved sources remain available for manual inspection.", request_id, state="failed", failure=message)

    def _on_research_done(self, request_id: int) -> None:
        self._research_workers.pop(request_id, None)
        if request_id == self._active_research_request:
            self.research.set_searching(False)
            self.research.finish_progress()

    def _load_research_case(
        self, pid: str, comparison: bool, open_detail: bool, from_overview: bool | None = None,
    ) -> None:
        if from_overview is None:
            from_overview = self.research.take_case_origin(pid)
        if self._case_worker is not None and self._case_worker.isRunning():
            self._queued_case = (pid, comparison, open_detail, from_overview)
            self.research.set_status(f"Loading {pid} after the current case…")
            return
        candidate = self.research.candidate_for(pid)
        if candidate is not None and not candidate.has_vent:
            # The pipeline analyzes ventilated surgeries only; skip a doomed fetch
            self.research.show_case_unavailable(
                pid, NO_VENT_MESSAGE, comparison=comparison, from_overview=from_overview
            )
            return
        self._pending_case = (pid, comparison, open_detail, from_overview)
        if self.cases is not None and self.ts is not None and "PID" in self.cases and "PID" in self.ts:
            cases = self.cases[self.cases["PID"].astype(str) == pid]
            if not cases.empty:
                ts = self.ts[self.ts["PID"].astype(str) == pid].copy()
                flags = self.flags[self.flags["PID"].astype(str) == pid].copy() if self.flags is not None and "PID" in self.flags else pd.DataFrame()
                if not ts.empty:
                    from src.services.case_fetch import FetchResult

                    def case_frame(frame):
                        return frame[frame["PID"].astype(str) == pid].copy() if frame is not None and "PID" in frame else pd.DataFrame()

                    self._on_research_case_data(FetchResult(
                        cases=cases.copy(), timeseries=ts, flags=flags,
                        episodes=case_frame(self.episodes), events=case_frame(self.events),
                    ), thresholds_sha256=self._thresholds_sha256)
                    return
        self._case_request_id += 1
        request_id = self._case_request_id
        self.research.set_status(f"Loading this case: {pid}…")
        self._case_worker = CaseFetchWorker(pid, emr_dir(), preset=self._active_preset, parent=self)
        self._case_worker.loaded.connect(
            lambda fetched: self._on_research_case_loaded(fetched)
            if request_id == self._case_request_id else None
        )
        self._case_worker.failed.connect(
            lambda message: self._on_research_case_failed(message)
            if request_id == self._case_request_id else None
        )
        self._case_worker.finished.connect(self._on_research_case_fetch_done)
        self._case_worker.start()

    def _on_research_case_fetch_done(self) -> None:
        if self._queued_case is not None:
            request = self._queued_case
            self._queued_case = None
            self._load_research_case(*request)

    def _on_research_case_failed(self, message: str) -> None:
        if self._pending_case is None:
            return
        pid, comparison, _, from_overview = self._pending_case
        self._pending_case = None
        self.research.show_case_unavailable(
            pid, message, comparison=comparison, from_overview=from_overview
        )

    def _on_research_case_loaded(self, fetched) -> None:
        if self._pending_case is not None:
            pid = self._pending_case[0]
            self._on_research_case_data(fetched, thresholds_sha256=fetched.thresholds_sha256_by_pid.get(pid))

    def _on_research_case_data(self, fetched, *, thresholds_sha256: str | None = None) -> None:
        timeseries, flags = fetched.timeseries, fetched.flags
        if self._pending_case is None:
            return
        pid, comparison, open_detail, from_overview = self._pending_case
        self._pending_case = None
        if timeseries is None or timeseries.empty or "t_min" not in timeseries:
            self.research.show_case_unavailable(
                pid, "no signal samples were found for this surgery.",
                comparison=comparison, from_overview=from_overview
            )
            return
        self.research.set_case_data(
            pid,
            timeseries,
            flags if flags is not None else pd.DataFrame(),
            fetched=fetched,
            thresholds_sha256=thresholds_sha256,
            comparison=comparison,
            open_detail=open_detail,
            from_overview=from_overview,
        )
        self.research.set_status(f"Loaded source values for {pid}.")

    def _refresh_rule_reference(self) -> None:
        try:
            from src.config import load_thresholds
            from src.rules import load_rule_catalog

            cfg = load_thresholds(self._active_preset, validate=True)
            catalog = load_rule_catalog(self._active_preset)
        except GuardrailError as e:
            self.research.set_rule_catalog(None, recorded=self._preset_recorded, error=str(e))
            return
        self.research.set_rule_catalog(catalog, recorded=self._preset_recorded,
                                       thresholds_sha256=self._thresholds_sha256)
        self.research.rules_drawer.set_explanations(cfg)

    def _show_suite_module(self, name: str) -> bool:
        """Show a page of the window. False when Settings has edits and the person chose to stay on it."""
        stack = getattr(self, "module_stack", None)
        if stack is None:
            return False
        if name != "settings" and not self._confirm_leave_settings():
            self._sync_frame()
            return False
        page = self._suite_page(name)
        stack.setCurrentWidget(page)
        self._current_module = name
        if name in _PRACTICE_TITLES:
            self._last_practice = name
        self._sync_frame()
        return True

    def _go(self, key: str) -> None:
        """Open a rail place or a practice tool; Rules and Settings act on top of the current page."""
        if key == "settings":
            self._open_settings()
            return
        if key == "practice":
            key = self._last_practice
        if key in _PRACTICE_TITLES:
            if not self._show_suite_module(key):
                return
            self.practice_tabs.bar.setFocus(Qt.FocusReason.ShortcutFocusReason)
        elif key == "signal" and not self.research.has_open_case():
            self.statusBar().showMessage(SIGNAL_NEEDS_CASE, 6000)
            return
        elif key == "rules":
            if self._show_suite_module("monitoring"):
                self.research.open_rules(self.rail.button("rules"))
            return
        else:
            if not self._show_suite_module("monitoring"):
                return
            if key == "cases":
                self.research.show_cases()
            elif key == "signal":
                self.research.show_signal()
                self.research.back_button.setFocus(Qt.FocusReason.ShortcutFocusReason)
            else:
                self.research.show_canvas()
                self._focus_research_question()
        from src.desktop.accessibility import announce

        announce(self.head.crumbs, self.head.crumb_text())

    def _resume_research(self) -> None:
        """Ctrl+1: back to the research page that was showing (board, cases, or the open case with its minute and episode).

        The rail's Research item is different: it opens the question board.
        """
        if not self._show_suite_module("monitoring"):
            return
        page = self.research.place().page
        if page == "research":
            self._focus_research_question()
        elif page == "signal":
            self.research.back_button.setFocus(Qt.FocusReason.ShortcutFocusReason)
        from src.desktop.accessibility import announce

        announce(self.head.crumbs, self.head.crumb_text())

    def _breadcrumb(self) -> list[str]:
        if self._current_module == "settings":
            page = self._settings_page()
            return ["Settings", page.current_section()] if page is not None else ["Settings"]
        if self._current_module != "monitoring":
            return ["Practice", _PRACTICE_TITLES[self._current_module]]
        place = self.research.place()
        question = f"Question {place.question}" if place.question else None
        if place.page == "cases":
            return ["Cases"]
        if place.page == "signal":
            minute = [f"Minute {place.minute:g}"] if place.minute is not None else []
            origin = ["Cases"] if place.from_overview else ["Research", *([question] if question else [])]
            return [*origin, str(place.pid), *minute]
        return ["Research", *([f"{question} of {place.questions}"] if question else [])]

    def _sync_frame(self) -> None:
        """Bring the rail, running head, window title and footer in line with the page that is showing."""
        if not hasattr(self, "_footer"):
            return
        practice = self._current_module in _PRACTICE_TITLES
        if self._current_module == "medications":
            # The page follows the case open in Research, also when that case closes or changes while it shows.
            self._offer_open_case_to_medication()
        place = self.research.place()
        self.rail.set_current(self._current_module if self._current_module == "settings"
                              else "practice" if practice else place.page)
        self.head.set_place(self._breadcrumb(), practice=self._showing_invented_practice())
        self.practice_tabs.setVisible(practice)
        if practice:
            self.practice_tabs.select(self._current_module)
        has_case = self.research.has_open_case()
        self._nav_signal.setEnabled(has_case)
        self._nav_signal.setToolTip(SIGNAL_TIP if has_case else SIGNAL_NEEDS_CASE)
        self.rail.set_item_state("signal", enabled=has_case, tip=SIGNAL_TIP if has_case else SIGNAL_NEEDS_CASE)
        title: str | None = None
        if self._current_module == "settings":
            title = "Settings"
        elif practice:
            title = _PRACTICE_TITLES[self._current_module]
        elif place.page == "cases":
            title = "Cases"
        elif place.page == "signal":
            title = f"Case {place.pid}"
        set_page_title(self, title)
        self._refresh_footer()

    def _showing_invented_practice(self) -> bool:
        """Whether the page showing holds only invented practice material.

        Medication and vitals with a case opened in Research shows that case's data, so the running head and the
        footer must name the data in use there, not say that nothing on the page comes from a data folder.
        """
        if self._current_module not in _PRACTICE_TITLES:
            return False
        if self._current_module != "medications":
            return True
        shows_open_case = getattr(self._suite_pages.get("medications"), "shows_open_case", None)
        return not (callable(shows_open_case) and shows_open_case())

    def _indexed_count(self) -> int | None:
        result = self.research.result
        indexed = getattr(result, "indexed_count", 0)
        if isinstance(indexed, int) and indexed > 0:
            return indexed
        meta = getattr(self.corpus_scan, "meta", None)
        scan = meta.get("n_indexed_surgeries") if isinstance(meta, dict) else None
        return scan if isinstance(scan, int) and scan > 0 else None

    def _refresh_footer(self) -> None:
        """Source, counts, rules and local model for every research page; practice pages say they read no data."""
        footer = getattr(self, "_footer", None)
        if footer is None:
            return
        self.head.set_demo(self._shown_data_is_demo)
        if self._showing_invented_practice():
            footer.show_practice()
            return
        analyzed = len(self.cases) if self.cases is not None else None
        indexed = self._indexed_count()
        if analyzed is None:
            counts = ("no analyzed cases loaded", "no analyzed cases loaded")
        elif indexed is None:
            counts = (f"<b>{analyzed:,}</b> analyzed \u00b7 indexed count after the first search",
                      f"{analyzed:,} analyzed, indexed count after the first search")
        else:
            counts = (f"<b>{analyzed:,}</b> analyzed of <b>{indexed:,}</b> indexed",
                      f"{analyzed:,} analyzed of {indexed:,} indexed")
        preset = html.escape(self._active_preset)
        sha = self._thresholds_sha256[:6] if self._thresholds_sha256 else ""
        rules_plain = f"rules {self._active_preset}" + (f" at {sha}" if sha else "")
        if not self._preset_recorded:
            rules_plain += ", preset not recorded in the cache"
        rules = (
            f"rules <b>{preset}</b>" + (f" @{sha}" if sha else "")
            + ("" if self._preset_recorded else " (not recorded)"),
            rules_plain,
        )
        footer.show_research(
            source_tip=self._source_tooltip(), counts=counts, rules=rules, model=self._model_field(),
            source=DEMO_SOURCE if self._shown_data_is_demo else None,
        )

    def _source_tooltip(self) -> str:
        wave = wave_dir()
        paths = f"EMR: {emr_dir()}\nProcessed: {processed_dir()}\nWave: {wave if wave else 'not set'}"
        return f"{DEMO_SOURCE_TIP}\n{paths}" if self._shown_data_is_demo else paths

    def _model_field(self) -> tuple[str, str]:
        name = self.research.selected_model()
        state = self._model_state
        if state.startswith("busy:"):
            word = state.split(":", 1)[1]
        elif state == "failed":
            word = "failed"
        elif state == "unchecked":
            word = "not checked"
        elif state == "ready" and name:
            return (f"local model <b>{html.escape(name)}</b> ready", f"local model {name} ready")
        elif state == "ready":
            word = "ready, none installed"
        else:
            word = "stopped"
        return (f"local model <b>{word}</b>", f"local model {word}")

    def _suite_page(self, name: str) -> QWidget:
        pages = self._suite_pages
        if name in pages:
            return pages[name]
        page: QWidget
        if name == "settings":
            page = self._make_settings_page()
        elif name == "handoff":
            from src.modules.handoff_trainer.panel import HandoffPanel

            page = HandoffPanel()
        elif name == "medications":
            from src.modules.medication_vitals.panel import MedicationVitalsPanel

            page = MedicationVitalsPanel()
        elif name == "who":
            from src.modules.who_checklist.panel import WhoPanel

            page = WhoPanel()
        else:
            page = self.research
        self.module_stack.addWidget(page)
        pages[name] = page
        return page

    def _make_settings_page(self) -> QWidget:
        page = SettingsPage()
        page.settings_applied.connect(self._on_settings_saved)
        page.close_requested.connect(self._close_settings)
        page.setup_requested.connect(self._open_setup_wizard)
        page.about_requested.connect(self._about)
        page.section_changed.connect(lambda _name: self._sync_frame())
        shortcuts = [("Find", "Ctrl+K")]
        shortcuts += [(command.title, command.shortcut) for command in self._commands if command.shortcut]
        page.set_shortcuts(shortcuts)
        page.set_about_summary(f"MOVER Suite version {self._version}. Research and education only; not for patient care.")
        return page

    def _offer_open_case_to_medication(self) -> None:
        from src.modules.medication_vitals.panel import MedicationVitalsPanel

        panel = self._suite_pages.get("medications")
        if not isinstance(panel, MedicationVitalsPanel):
            return
        pid = self.research._current_pid
        view = self.research._case_views.get(pid) if pid else None
        wanted = None if pid is None or view is None else (str(pid), self._shown_data_is_demo)
        if wanted == self._medication_shown:
            return
        self._medication_shown = wanted
        if pid is None or view is None:
            panel.show_fictional()
            return
        from src.llm.case_context import load_case_medications

        origin = _case_origin(view.cases, pid)
        panel.show_case(str(pid), load_case_medications(pid), view.timeseries, origin=origin,
                        fictional_source=self._shown_data_is_demo)


def _case_origin(cases: pd.DataFrame, pid: str):
    if cases is None or cases.empty or "PID" not in cases.columns or "t_start" not in cases.columns:
        return None
    rows = cases[cases["PID"].astype(str) == str(pid)]
    if rows.empty:
        return None
    value = rows.iloc[0].get("t_start")
    if value is None or pd.isna(value):
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def main() -> int:
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get(
        "QT_QPA_PLATFORM"
    ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    apply_persisted_settings()
    apply_ollama_env_from_settings()

    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else None
    if app is None:
        try:
            QApplication.setAttribute(
                Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True
            )
        except Exception:
            pass
        app = QApplication(sys.argv)
    app.setApplicationName("MOVER Suite")
    app.setOrganizationName("MOVER-SIS")
    app.setDesktopFileName("mover-sis-monitor")
    apply_theme(app)

    win = MainWindow()
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        win.resize(min(win.width(), available.width()), min(win.height(), available.height()))
    win.show()
    win.raise_()
    win.activateWindow()
    if screen is not None:
        geo = screen.availableGeometry()
        frame = win.frameGeometry()
        if not geo.intersects(frame):
            win.move(geo.center() - frame.center())
    return app.exec()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
