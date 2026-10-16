"""
MOVER SIS desktop research workspace (PySide6).

Launch:
  python -m src.desktop
  ./scripts/run_desktop.sh
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QToolButton,
)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.desktop.research_workspace import ResearchWorkspace
from src.desktop.conversation_panel import NO_MODEL_ANSWER
from src.desktop.research_workers import CaseFetchWorker, ResearchWorker
from src.services.articles import ArticleCatalog, ArticleCatalogError, ArticleSearchResult
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
    emr_dir,
    processed_dir,
    wave_dir,
)
from src.user_settings import apply_ollama_env_from_settings, load_settings, needs_first_run_setup, settings_path

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

    def run(self) -> None:
        try:
            from src.services.data import load_processed

            if self.processed is None:
                self.processed = processed_dir()
            data = load_processed(self.processed, validate=False)
            self.finished_ok.emit(data)
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


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
        self.emr = emr
        self.processed = processed

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
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


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
        self.setWindowTitle(
            f"MOVER SIS Monitor v{self._version} — Ventilation & Anesthesia"
        )
        self.resize(1380, 860)

        apply_persisted_settings()
        apply_ollama_env_from_settings()
        self._active_preset = str(load_settings().get("preset") or "default")
        self._preset_recorded = True
        self._thresholds_sha256: str | None = None

        self.cases: pd.DataFrame | None = None
        self.ts: pd.DataFrame | None = None
        self.flags: pd.DataFrame | None = None
        self._analysis_facts: dict[str, str] = {}
        self.episodes: pd.DataFrame | None = None
        self.events: pd.DataFrame | None = None
        # Full-EMR scan results (src.pipeline.corpus_scan.ScanResult), if any
        self.corpus_scan = None
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

        self._build_menu()
        self._build_ui()
        self._refresh_rule_reference()
        self.setStatusBar(QStatusBar())
        self._update_status_paths("Ready")
        self._updates = UpdateController(self._version, self)
        self._updates.found.connect(self._on_update_found)
        self._updates.ready.connect(self._on_update_ready)
        self._updates.failed.connect(self._on_update_failed)
        self._update_button = QPushButton("Restart to update", self)
        self._update_button.clicked.connect(self._restart_for_update)
        self._update_button.hide()
        self.statusBar().addPermanentWidget(self._update_button)
        self._scan_cancel_button = QPushButton("Cancel scan", self)
        self._scan_cancel_button.setStyleSheet("QPushButton:focus { border: 2px solid #8c739f; }")
        self._scan_cancel_button.clicked.connect(self._cancel_corpus_scan)
        self._scan_cancel_button.hide()
        self.statusBar().addPermanentWidget(self._scan_cancel_button)

        QTimer.singleShot(0, self._startup)

    # ----- menu -----
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_emr = QAction("Open EMR folder…", self)
        open_emr.setShortcut("Ctrl+O")
        open_emr.triggered.connect(self._browse_emr_folder)
        file_menu.addAction(open_emr)
        open_wave = QAction("Open Wave folder…", self)
        open_wave.setShortcut("Ctrl+Shift+O")
        open_wave.triggered.connect(self._browse_wave_folder)
        file_menu.addAction(open_wave)
        import_articles = QAction("Import articles…", self)
        import_articles.triggered.connect(self._import_articles)
        file_menu.addAction(import_articles)
        reload_act = QAction("Reload processed data", self)
        reload_act.setShortcut("Ctrl+R")
        reload_act.triggered.connect(self._autoload_processed)
        file_menu.addAction(reload_act)
        run_pipe = QAction("Run pipeline (50 cases)…", self)
        run_pipe.triggered.connect(self._run_pipeline_menu)
        file_menu.addAction(run_pipe)
        scan_act = QAction("Scan full EMR (all ventilated surgeries)…", self)
        scan_act.triggered.connect(self._run_corpus_scan)
        file_menu.addAction(scan_act)
        file_menu.addSeparator()
        setup_act = QAction("&Setup…", self)
        setup_act.triggered.connect(self._open_setup_wizard)
        file_menu.addAction(setup_act)
        file_menu.addSeparator()
        quit_act = QAction("E&xit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        view_menu = self.menuBar().addMenu("&View")
        theme_light = QAction("Theme: &Light", self)
        theme_light.triggered.connect(lambda: self._set_theme_quick("light"))
        view_menu.addAction(theme_light)
        theme_dark = QAction("Theme: &Dark", self)
        theme_dark.triggered.connect(lambda: self._set_theme_quick("dark"))
        view_menu.addAction(theme_dark)
        theme_sys = QAction("Theme: &System", self)
        theme_sys.triggered.connect(lambda: self._set_theme_quick("system"))
        view_menu.addAction(theme_sys)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)
        self.update_act = QAction("Restart to finish update", self)
        self.update_act.setVisible(False)
        self.update_act.triggered.connect(self._restart_for_update)
        help_menu.addAction(self.update_act)

        # Settings lives on a gear icon in the menu-bar corner, not under Edit
        self.settings_act = QAction(self._gear_icon(), "Settings…", self)
        self.settings_act.setShortcut("Ctrl+,")
        self.settings_act.setToolTip("Settings (Ctrl+,) — data folders, Ollama models dir, theme")
        self.settings_act.triggered.connect(self._open_settings)
        self.addAction(self.settings_act)  # keep the shortcut alive app-wide

        gear = QToolButton(self)
        gear.setDefaultAction(self.settings_act)
        gear.setAutoRaise(True)
        gear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        gear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menuBar().setCornerWidget(gear, Qt.Corner.TopRightCorner)

    @staticmethod
    def _gear_icon() -> QIcon:
        """Theme gear icon, with a drawn fallback for minimal icon themes."""
        icon = QIcon.fromTheme("preferences-system")
        if not icon.isNull():
            return icon
        pm = QPixmap(20, 20)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QApplication.palette().windowText().color(), 1.6)
        p.setPen(pen)
        p.drawEllipse(6, 6, 8, 8)
        for angle in range(0, 360, 45):  # eight teeth
            p.save()
            p.translate(10, 10)
            p.rotate(angle)
            p.drawLine(0, -9, 0, -6)
            p.restore()
        p.end()
        return QIcon(pm)

    # ----- layout -----
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
        self.setCentralWidget(self.research)

    # ----- paths -----
    def _sync_path_fields_from_runtime(self) -> None:
        """Refresh status bar after path changes (sidebar no longer holds path fields)."""
        self._update_status_paths()

    def _update_status_paths(self, prefix: str = "") -> None:
        wave = wave_dir()
        wave_s = str(wave) if wave else "—"
        msg = (
            f"{prefix}  ·  EMR: {emr_dir()}  ·  Wave: {wave_s}  ·  "
            f"Processed: {processed_dir()}"
        )
        self.statusBar().showMessage(msg)

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
            QMessageBox.critical(self, "Invalid EMR folder", str(e))
            return
        self._update_status_paths("EMR folder set")
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
            QMessageBox.critical(self, "Invalid wave folder", str(e))
            return
        # Waveforms are looked up from wave_dir() on demand, so the status bar is
        # the only thing to refresh (the old filters sidebar no longer exists).
        self._update_status_paths("Wave folder set")

    def _browse_data_folder(self) -> None:
        """Menu shortcut: same as EMR browse (legacy)."""
        self._browse_emr_folder()

    def _apply_data_folder(self, path: Path) -> None:
        """Compatibility helper used by tests / menu."""
        try:
            configure_from_user_directory(path, persist=True)
        except Exception as e:
            QMessageBox.critical(self, "Invalid data folder", str(e))
            return
        self._update_status_paths("EMR folder set")
        self._autoload_processed()

    # ----- workers -----
    def _autoload_processed(self) -> None:
        proc = processed_dir()
        if not (proc / "cases.parquet").exists():
            self._update_status_paths(
                "No processed cache — run the pipeline or pick another folder"
            )
            self.research.set_status("No analyzed case cache. Indexed surgery search is available after setup.")
            if not needs_first_run_setup():
                self._on_load_fail("Missing processed files: cases.parquet")
            return
        if self._worker and self._worker.isRunning():
            return
        self._update_status_paths("Loading data…")
        self._worker = DataLoadWorker(processed=proc, parent=self)
        self._worker.finished_ok.connect(self._on_pipeline_ok)
        self._worker.failed.connect(self._on_load_fail)
        self._worker.start()

    def _on_load_fail(self, msg: str) -> None:
        self._update_status_paths("Could not load processed data")
        self.research.set_status("Processed data could not load. Indexed surgery search remains available.")
        self._warn("Load error", f"Processed data: {processed_dir()}\n\n{msg[:2000]}")

    def _warn(self, title: str, text: str) -> None:
        # Not modal: this can fire during startup, where a modal box would hold the
        # window back until the user dismissed it.
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.open()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._updates.close()
        running = [w for w in (
            self._worker, self._research_worker,
            self._case_worker, self._ollama_worker, *self._research_workers.values(),
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
        self.statusBar().showMessage(f"Downloading MOVER SIS Monitor v{version} update…")

    def _on_update_ready(self, version: str, _staged: Path) -> None:
        self._update_button.setText(f"Restart to update to v{version}")
        self._update_button.show()
        self.update_act.setVisible(True)
        self.statusBar().showMessage(f"Version {version} is ready. Restart when convenient.")

    def _on_update_failed(self, detail: str) -> None:
        self.statusBar().showMessage(f"Update could not be prepared: {detail}", 15_000)

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
            QMessageBox.warning(self, "Update could not start", str(exc))
            return
        if not plan.ready or not plan.supported_here:
            QMessageBox.warning(self, "Update could not start", plan.detail)
            return
        try:
            result = spawn_restart_apply(plan)
        except OSError as exc:
            QMessageBox.warning(self, "Update could not start", str(exc))
            return
        if not result.spawned:
            QMessageBox.warning(self, "Update could not start", result.detail)
            return
        self.close()

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            f"MOVER SIS Ventilation & Anesthesia Monitor\n"
            f"Version {self._version}\n\n"
            "Native desktop client for the UC Irvine MOVER SIS research dataset.\n"
            "Configure paths via the ⚙ Settings icon (top-right) or File → Setup…\n"
            "Not for clinical care.",
        )

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
        from src.desktop.settings_ui import SetupWizard

        wiz = SetupWizard(self)
        wiz.setup_finished.connect(self._on_settings_applied)
        if first_run:
            wiz.setWindowTitle("MOVER SIS Monitor — First-time setup")
        wiz.exec()
        self._sync_path_fields_from_runtime()
        if wiz.result():
            self.research.set_setup_required(False)
        elif first_run:
            self.research.set_setup_required(True)
        self._update_status_paths("Setup finished" if wiz.result() else ("Setup required" if first_run else "Setup cancelled"))
        if wiz.result():
            self._autoload_processed()
            self._refresh_ollama_models()

    def _open_settings(self) -> None:
        from src.desktop.settings_ui import SettingsDialog

        dlg = SettingsDialog(self)
        dlg.settings_applied.connect(self._on_settings_applied)
        if dlg.exec():
            from src.guardrails.validate_io import validate_emr_dir

            self._research_engine = None
            try:
                validate_emr_dir(emr_dir())
                ready = True
            except Exception:
                ready = False
            self.research.set_setup_required(not ready)
            self._sync_path_fields_from_runtime()
            self._update_status_paths("Settings saved" if ready else "EMR setup required")
            if ready:
                self._autoload_processed()
                self._refresh_ollama_models()

    def _on_settings_applied(self, vals: dict) -> None:
        self._research_engine = None
        apply_persisted_settings()
        apply_ollama_env_from_settings()
        theme = vals.get("theme")
        if theme:
            from src.user_settings import update_settings

            update_settings(theme=theme)
            apply_theme(QApplication.instance(), theme)
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
        self.research.refresh_charts()
        self.statusBar().showMessage(f"Theme: {mode} ({concrete})", 4000)

    def _run_pipeline_menu(self) -> None:
        """File-menu pipeline run (sidebar pipeline controls removed)."""
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
        self._update_status_paths("Running pipeline…")
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
            "and can take many minutes on the full MOVER SIS data. The loaded sample and its charts are not changed; the "
            "co-pilot uses the scan for whole-dataset questions.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._update_status_paths("Scanning full EMR…")
        worker = CorpusScanWorker(emr_dir(), processed_dir(), preset=self._next_run_preset(), parent=self)
        worker.progress.connect(lambda message: self.statusBar().showMessage(message))
        worker.finished_ok.connect(self._on_corpus_scan_ok)
        worker.failed.connect(self._on_corpus_scan_fail)
        worker.cancelled.connect(self._on_corpus_scan_cancelled)
        worker.finished.connect(self._scan_cancel_button.hide)
        self._scan_cancel_button.setEnabled(True)
        self._scan_cancel_button.show()
        self._worker = worker
        worker.start()

    def _cancel_corpus_scan(self) -> None:
        if isinstance(self._worker, CorpusScanWorker) and self._worker.isRunning():
            self._worker.requestInterruption()
            self._scan_cancel_button.setEnabled(False)
            self.statusBar().showMessage("Cancelling full-EMR scan…")

    def _on_corpus_scan_ok(self, result) -> None:
        self.corpus_scan = result
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
        self._update_status_paths(message)
        self._push_cases_overview()

    def _on_corpus_scan_cancelled(self) -> None:
        self._update_status_paths("Full-EMR scan cancelled; the previous scan was kept.")

    def _on_corpus_scan_fail(self, msg: str) -> None:
        self._update_status_paths(msg.splitlines()[0])
        QMessageBox.critical(self, "Full-EMR scan", msg[:4000])

    def _load_corpus_scan(self) -> None:
        """Pick up a previous full-EMR scan saved next to the processed sample."""
        from src.pipeline.corpus_scan import load_corpus_scan

        try:
            self.corpus_scan = load_corpus_scan(processed_dir())
        except Exception as exc:
            self.corpus_scan = None
            root = processed_dir()
            self._warn(
                "Saved full-EMR scan could not load",
                f"Could not read saved scan files in {root}:\n"
                f"{root / 'corpus_cases.parquet'}\n{root / 'corpus_episodes.parquet'}\n"
                f"{root / 'corpus_meta.json'}\n\n{exc}",
            )

    def _on_pipeline_ok(self, data) -> None:
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
        self._update_status_paths(
            f"Loaded {len(self.cases)} cases · {len(self.ts)} min rows · {len(self.flags)} flags"
        )

    def _on_pipeline_fail(self, msg: str) -> None:
        self._update_status_paths("Pipeline failed")
        QMessageBox.critical(
            self,
            "Pipeline / guardrail error",
            msg[:4000]
            + (
                "\n\nEnsure the EMR folder has patient_information, "
                "patient_ventilator, and patient_vitals CSVs."
            ),
        )

    def _refresh_ollama_models(self, *, start_if_needed: bool = False) -> None:
        self._start_ollama_worker("ensure" if start_if_needed else "probe")

    def _apply_ollama_status(self, status) -> None:
        from src.llm.service import ollama_status_summary

        prev = self.research.model_combo.currentText().strip()
        self.research.model_combo.blockSignals(True)
        self.research.model_combo.clear()
        if not status.available:
            self.research.model_combo.addItem("(Ollama stopped — click Start)")
            self.research.model_combo.blockSignals(False)
            self._set_ollama_status_ui(status)
            if self.statusBar().currentMessage() in ("Checking local model…", "Starting local model…", "Stopping local model…"):
                self.statusBar().showMessage(status.message)
            return

        models = status.models
        if not models:
            self.research.model_combo.addItem("(no models — ollama pull gemma4)")
        else:
            self.research.model_combo.addItems(models)
            saved = load_settings().get("ollama_model")
            preferred_list = []
            if prev and not prev.startswith("("):
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
            for preferred in preferred_list:
                idx = self.research.model_combo.findText(preferred)
                if idx >= 0:
                    self.research.model_combo.setCurrentIndex(idx)
                    break
        self.research.model_combo.blockSignals(False)
        self._set_ollama_status_ui(status)
        if self.statusBar().currentMessage() in ("Checking local model…", "Starting local model…", "Stopping local model…"):
            self.statusBar().showMessage(ollama_status_summary(status))

    def _set_ollama_status_ui(self, status) -> None:
        self._ollama_available = status.available
        busy = self._ollama_worker is not None and self._ollama_worker.isRunning()
        if status.available:
            n = len(status.models)
            extra = " · started by app" if status.started_by_app else ""
            self.research.model_status.setText(
                f"Ollama: <b style='color:#14b8a6'>running</b> · {n} model(s){extra}"
            )
            self.research.model_status.setTextFormat(Qt.TextFormat.RichText)
            self.research.start_button.setEnabled(not busy)
            self.research.stop_button.setEnabled(not busy)
            self.research.unload_button.setEnabled(not busy)
        else:
            self.research.model_status.setText(
                "Ollama: <b style='color:#f87171'>stopped</b> — click Start to activate"
            )
            self.research.model_status.setTextFormat(Qt.TextFormat.RichText)
            self.research.start_button.setEnabled(not busy)
            self.research.stop_button.setEnabled(False)
            self.research.unload_button.setEnabled(False)

    def _ollama_start(self) -> None:
        self._start_ollama_worker("start")

    def _start_ollama_worker(self, action: str) -> None:
        if self._closing:
            return
        if self._ollama_worker is not None and self._ollama_worker.isRunning():
            return
        self._ollama_button_label = self.research.start_button.text()
        if self.research.start_button.hasFocus():
            self.research.question_edit.setFocus()
        label = {"probe": "Checking…", "stop": "Stopping…"}.get(action, "Starting…")
        self.research.start_button.setText(label)
        for button in (self.research.start_button, self.research.stop_button, self.research.unload_button):
            button.setEnabled(False)
        message = {"probe": "Checking local model…", "stop": "Stopping local model…"}.get(action, "Starting local model…")
        self.research.model_status.setText(message)
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
        self.research.start_button.setEnabled(True)
        self.research.stop_button.setEnabled(self._ollama_available)
        self.research.unload_button.setEnabled(self._ollama_available)
        if self._ollama_button_label is not None:
            self.research.start_button.setText(self._ollama_button_label)
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
        self.research.model_status.setTextFormat(Qt.TextFormat.PlainText)
        self.research.model_status.setText(f"Local model failed: {message}")
        self.research.set_status(f"Local model failed: {message}")
        self.statusBar().showMessage(f"Local model failed: {message}")
        if action == "start":
            QMessageBox.warning(self, "Ollama start", message)

    def _ollama_stop(self) -> None:
        if self.research._searching:
            self._cancel_research()
        self._start_ollama_worker("stop")

    def _ollama_unload(self) -> None:
        from src.llm.service import unload_ollama_model

        model = self.research.model_combo.currentText().strip()
        ok, msg = unload_ollama_model(model)
        self.statusBar().showMessage(msg)
        if ok:
            QMessageBox.information(self, "Model unloaded", msg)
        else:
            QMessageBox.warning(self, "Unload model", msg)

    def _on_ollama_model_changed(self, text: str) -> None:
        text = (text or "").strip()
        if text and not text.startswith("("):
            from src.user_settings import update_settings

            update_settings(ollama_model=text)

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
            QMessageBox.warning(self, "Article import unavailable", self.research._article_error)
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import articles", "", "Article files (*.csv *.jsonl)")
        if not path:
            return
        try:
            report = self._article_catalog.import_file(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Article import failed", str(exc))
            return
        self.research.set_article_catalog(self._article_catalog.count)
        message = f"Imported {report.imported:,} articles. {len(report.errors):,} rows skipped."
        if report.errors:
            message += "\n\n" + "\n".join(report.errors)
        QMessageBox.information(self, "Article import", message)

    def _start_research_search(self, question: str) -> None:
        from src.guardrails.validate_io import validate_emr_dir
        from src.llm.ollama_client import OllamaEmbedder
        from src.services.retrieval import RetrievalEngine

        articles = self.research.scope == "Articles"
        if articles:
            if self._article_catalog is None:
                return
            embed_model = str(load_settings().get("ollama_embed_model") or "nomic-embed-text")
            engine = ArticleCatalog(settings_path().parent / "case_embeddings.sqlite3", OllamaEmbedder(embed_model))
        else:
            try:
                source = validate_emr_dir(emr_dir())
            except Exception as exc:
                self.research.set_status(f"EMR setup required: {exc}")
                return
            if (self._research_engine is None or self._research_engine.emr != source
                    or any(worker.isRunning() for worker in self._research_workers.values())):
                embed_model = str(load_settings().get("ollama_embed_model") or "nomic-embed-text")
                self._research_engine = RetrievalEngine(
                    emr=source,
                    cache_path=settings_path().parent / "case_embeddings.sqlite3",
                    embedder=OllamaEmbedder(embed_model),
                    preset=self._active_preset,
                    analysis_facts=self._analysis_facts,
                    analyzed_pids=(self.cases["PID"].astype(str).tolist() if self.cases is not None and "PID" in self.cases else []),
                )
            engine = self._research_engine
        if self.research._searching:
            self._cancel_research()
        self._invalidate_research_requests()
        self._research_request_id += 1
        request_id = self._research_request_id
        self._active_research_request = request_id
        self.research.begin_question(question, request_id)
        self.research.set_status("Searching the local article catalog…" if articles else "Preparing indexed surgery search…")
        self._research_worker = ResearchWorker(
            engine,
            question,
            self.research.selected_model(),
            None if articles else self._make_session_snapshot(),
            parent=self,
        )
        self._research_workers[request_id] = self._research_worker
        self._research_worker.progress.connect(
            lambda progress: self.research.set_status(progress.message)
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
        self._refresh_rule_reference()
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
        self.research.set_cases_data(self.cases, self.corpus_scan, catalog, self.flags)

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
        turn = self.research.conversation.turn(request_id)
        if turn is not None and turn.model is None:
            answer = NO_MODEL_ANSWER
            if not result.candidates:
                answer += "\n\nNo matching indexed surgery records were found for this question."
            self.research.set_answer(answer, request_id, state="no_model")
        elif not result.candidates:
            self.research.set_answer("No matching indexed surgery records were found for this question.", request_id)
        preferred = self.research.preferred_pid()
        if preferred:
            self._load_research_case(preferred, False, False)

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
    app.setApplicationName("MOVER SIS Ventilation Monitor")
    app.setOrganizationName("MOVER-SIS")
    app.setDesktopFileName("mover-sis-monitor")
    apply_theme(app)

    win = MainWindow()
    win.show()
    win.raise_()
    win.activateWindow()
    screen = QGuiApplication.primaryScreen()
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
