"""
MOVER SIS desktop application (PySide6).

Launch:
  python -m src.desktop
  ./scripts/run_desktop.sh
"""

from __future__ import annotations

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
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.desktop.charts import (
    ChartView,
    case_timeline_figure,
    flag_rules_figure,
    top_cases_figure,
)
from src.desktop.research_workspace import ResearchWorkspace
from src.desktop.research_workers import CaseFetchWorker, ResearchWorker
from src.desktop.theme import apply_theme, chat_role_color
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

    def __init__(self, emr: Path, processed: Path, preset: str = "default", parent=None):
        super().__init__(parent)
        self.emr = emr
        self.processed = processed
        self.preset = preset

    def run(self) -> None:
        try:
            from src.pipeline.corpus_scan import scan_corpus

            result = scan_corpus(
                self.emr,
                self.processed,
                preset=self.preset,
                on_progress=lambda message, _done, _total: self.progress.emit(message),
                should_stop=self.isInterruptionRequested,
            )
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class LLMChatWorker(QThread):
    """Tool-using Ollama agent (native tools + extract recovery + verification)."""

    finished_ok = Signal(object)  # AgentResult
    failed = Signal(str)
    status = Signal(str)

    def __init__(
        self,
        model: str,
        question: str,
        session_snapshot: object,
        mode: str = "chat",
        base_url: str = "http://127.0.0.1:11434",
        parent=None,
    ):
        super().__init__(parent)
        self.model = model
        self.question = question
        self.session_snapshot = session_snapshot
        self.mode = mode
        self.base_url = base_url

    def run(self) -> None:
        try:
            from src.llm.agent import run_agent
            from src.llm.ollama_client import OllamaClient

            client = OllamaClient(base_url=self.base_url, timeout_s=300.0)

            def _status(msg: str) -> None:
                self.status.emit(msg)

            result = run_agent(
                model=self.model,
                question=self.question,
                session=self.session_snapshot,  # type: ignore[arg-type]
                client=client,
                mode=self.mode,
                on_status=_status,
            )
            if not (result.answer or "").strip():
                raise RuntimeError("Model returned an empty answer.")
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


def _df_to_table(table: QTableWidget, df: pd.DataFrame, max_rows: int = 500) -> None:
    view = df.head(max_rows)
    table.setUpdatesEnabled(False)
    table.blockSignals(True)
    try:
        table.clear()
        cols = [str(c) for c in view.columns]
        table.setColumnCount(len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.setRowCount(len(view))
        values = view.astype(object).where(pd.notna(view), "").values
        for r in range(len(view)):
            for c in range(len(cols)):
                text = str(values[r, c])
                if len(text) > 120:
                    text = text[:117] + "…"
                table.setItem(r, c, QTableWidgetItem(text))
        table.resizeColumnsToContents()
    finally:
        table.blockSignals(False)
        table.setUpdatesEnabled(True)


def _metric_card(title: str, value_label: QLabel) -> QFrame:
    frame = QFrame()
    frame.setObjectName("metricCard")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(14, 10, 14, 10)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("metricTitle")
    value_label.setObjectName("metricValue")
    lay.addWidget(t)
    lay.addWidget(value_label)
    return frame


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

        self.cases: pd.DataFrame | None = None
        self.ts: pd.DataFrame | None = None
        self.flags: pd.DataFrame | None = None
        self.episodes: pd.DataFrame | None = None
        self.events: pd.DataFrame | None = None
        # Full-EMR scan results (src.pipeline.corpus_scan.ScanResult), if any
        self.corpus_scan = None
        self._active_pid: str | None = None
        self._focus_pids: list[str] = []
        self._worker: QThread | None = None
        self._research_worker: ResearchWorker | None = None
        self._case_worker: CaseFetchWorker | None = None
        self._research_engine = None
        self._pending_case: tuple[str, bool, bool] | None = None
        self._queued_case: tuple[str, bool, bool] | None = None
        self._charts_pending = False
        self._show_vitals = True
        self._llm_mode: str = "analyze"  # default: management-pattern analysis

        self._build_menu()
        self._build_ui()
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
        central = QWidget()
        central.setObjectName("mainCentral")
        self.main_central = central
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        self.outer_layout = outer
        outer.setContentsMargins(16, 14, 16, 10)
        outer.setSpacing(14)

        # Header
        header = QHBoxLayout()
        header.setSpacing(16)
        hero = QVBoxLayout()
        hero.setSpacing(4)
        title = QLabel(f"MOVER SIS Monitor  ·  v{self._version}")
        title.setObjectName("heroTitle")
        sub = QLabel(
            "Research dashboard for intraoperative ventilation & anesthesia depth  ·  Not for clinical care"
        )
        sub.setObjectName("heroSub")
        sub.setWordWrap(True)
        hero.addWidget(title)
        hero.addWidget(sub)
        header.addLayout(hero, stretch=1)

        self.metric_cases = QLabel("—")
        self.metric_flags = QLabel("—")
        self.metric_score = QLabel("—")
        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        metrics.addWidget(_metric_card("Cases loaded", self.metric_cases))
        metrics.addWidget(_metric_card("With flags", self.metric_flags))
        metrics.addWidget(_metric_card("Max score", self.metric_score))
        header.addLayout(metrics)
        self.header_widget = QWidget()
        self.header_widget.setLayout(header)
        outer.addWidget(self.header_widget)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ---- Sidebar (LLM session — no pipeline/filters; tools handle those) ----
        side = QWidget()
        side.setObjectName("sidePanel")
        side.setMinimumWidth(300)
        side.setMaximumWidth(360)
        side_l = QVBoxLayout(side)
        side_l.setContentsMargins(0, 0, 0, 0)
        side_l.setSpacing(10)

        paths_hint = QLabel(
            "Data folders: <b>⚙ Settings</b> (top-right) · Pipeline: <b>File → Run pipeline</b>"
        )
        paths_hint.setObjectName("pathHint")
        paths_hint.setWordWrap(True)
        paths_hint.setTextFormat(Qt.TextFormat.RichText)
        side_l.addWidget(paths_hint)

        sess = QGroupBox("Research co-pilot")
        sf = QFormLayout(sess)
        sf.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        sf.setHorizontalSpacing(10)
        sf.setVerticalSpacing(10)
        sf.setContentsMargins(4, 10, 4, 6)

        self.lbl_ollama_status = QLabel("Ollama: checking…")
        self.lbl_ollama_status.setObjectName("pathHint")
        self.lbl_ollama_status.setWordWrap(True)
        sf.addRow(self.lbl_ollama_status)

        self.combo_llm_model = QComboBox()
        self.combo_llm_model.setMinimumWidth(180)
        # Non-editable so the drop-down arrow / popup reliably work under Fusion.
        self.combo_llm_model.setEditable(False)
        self.combo_llm_model.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        sf.addRow("Ollama model", self.combo_llm_model)

        # Lifecycle (Daily Scheduler-style): Start / Stop / Unload / Refresh
        life = QHBoxLayout()
        self.btn_ollama_start = QPushButton("Start")
        self.btn_ollama_start.setObjectName("primaryBtn")
        self.btn_ollama_start.setToolTip(
            "Start ollama serve (uses models dir from Settings). Frees nothing until Stop/Unload."
        )
        self.btn_ollama_start.clicked.connect(self._ollama_start)
        life.addWidget(self.btn_ollama_start)

        self.btn_ollama_stop = QPushButton("Stop")
        self.btn_ollama_stop.setObjectName("secondaryBtn")
        self.btn_ollama_stop.setToolTip(
            "Fully stop Ollama (server + runner) to release GPU/CPU memory."
        )
        self.btn_ollama_stop.clicked.connect(self._ollama_stop)
        life.addWidget(self.btn_ollama_stop)

        self.btn_ollama_unload = QPushButton("Unload")
        self.btn_ollama_unload.setObjectName("secondaryBtn")
        self.btn_ollama_unload.setToolTip(
            "Unload the selected model from VRAM/RAM but keep the server running."
        )
        self.btn_ollama_unload.clicked.connect(self._ollama_unload)
        life.addWidget(self.btn_ollama_unload)

        btn_refresh_models = QPushButton("Refresh")
        btn_refresh_models.setObjectName("secondaryBtn")
        btn_refresh_models.setToolTip("Re-check server status and model list (does not start).")
        btn_refresh_models.clicked.connect(
            lambda: self._refresh_ollama_models(start_if_needed=False)
        )
        life.addWidget(btn_refresh_models)
        sf.addRow(life)

        self.lbl_corpus = QLabel("Corpus: (loading…)")
        self.lbl_corpus.setObjectName("pathHint")
        self.lbl_corpus.setWordWrap(True)
        sf.addRow(self.lbl_corpus)

        self.lbl_llm_case = QLabel("Active case: (none — ask the co-pilot to find cases)")
        self.lbl_llm_case.setObjectName("sectionLabel")
        self.lbl_llm_case.setWordWrap(True)
        sf.addRow(self.lbl_llm_case)

        self.lbl_focus = QLabel("Focus list: (none)")
        self.lbl_focus.setObjectName("pathHint")
        self.lbl_focus.setWordWrap(True)
        sf.addRow(self.lbl_focus)

        btn_open_tl = QPushButton("Open active case timeline")
        btn_open_tl.setObjectName("primaryBtn")
        btn_open_tl.clicked.connect(self._open_active_timeline)
        sf.addRow(btn_open_tl)

        btn_show_ctx = QPushButton("Show grounded context")
        btn_show_ctx.setObjectName("secondaryBtn")
        btn_show_ctx.clicked.connect(self._show_case_context)
        sf.addRow(btn_show_ctx)

        self.chk_vitals = QCheckBox("Show vitals on timeline")
        self.chk_vitals.setChecked(True)
        self.chk_vitals.toggled.connect(self._on_vitals_toggled)
        sf.addRow(self.chk_vitals)

        side_l.addWidget(sess)

        help_box = QGroupBox("How to use")
        hl = QVBoxLayout(help_box)
        how = QLabel(
            "1. Chat first — find cases by procedure, agent, flags, or score.<br>"
            "2. The co-pilot <b>selects</b> a PID and loads grounded data.<br>"
            "3. Then open Summary / Timeline for charts.<br><br>"
            "<i>Research concept only — not clinical care.</i>"
        )
        how.setObjectName("pathHint")
        how.setWordWrap(True)
        how.setTextFormat(Qt.TextFormat.RichText)
        hl.addWidget(how)
        side_l.addWidget(help_box)
        side_l.addStretch(1)

        # Hidden PID selector kept for internal compatibility with chart helpers
        self.combo_pid = QComboBox()
        self.combo_pid.hide()

        # ---- Research workspace and existing views ----
        self.tabs = QTabWidget()
        self.research = ResearchWorkspace()
        self.research.question_submitted.connect(self._start_research_search)
        self.research.case_requested.connect(self._load_research_case)
        self.research.setup_requested.connect(self._open_setup_wizard)
        self.research.model_start_requested.connect(self._ollama_start)
        self.research.model_refresh_requested.connect(
            lambda: self._refresh_ollama_models(start_if_needed=False)
        )
        self.research.model_changed.connect(self._research_model_changed)
        self.tabs.addTab(self.research, "1 · Research")

        # ---- 1 · Ask (primary) ----
        chat_tab = QWidget()
        chat_l = QVBoxLayout(chat_tab)
        chat_l.setContentsMargins(10, 10, 10, 10)
        chat_l.setSpacing(10)
        chat_intro = QLabel(
            "<b>Local research co-pilot</b> (Ollama, tool-calling like Local Schedule Assistant). "
            "It finds similar SIS cases, reads documented management (agent, vent, meds, flags), "
            "and explains <i>patterns in the extract</i> — not live clinical orders. "
            "<b>Not for clinical care.</b>"
        )
        chat_intro.setWordWrap(True)
        chat_intro.setObjectName("pathHint")
        chat_intro.setTextFormat(Qt.TextFormat.RichText)
        chat_l.addWidget(chat_intro)

        # Modes: Chat / Analyze / Compare (scheduler-style)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode"))
        self._mode_buttons: dict[str, QPushButton] = {}
        for mid, label in [
            ("chat", "Chat"),
            ("analyze", "Case analysis"),
            ("compare", "Compare"),
        ]:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(mid == self._llm_mode)
            b.setObjectName("secondaryBtn" if mid != self._llm_mode else "primaryBtn")
            b.clicked.connect(lambda checked=False, m=mid: self._set_llm_mode(m))
            self._mode_buttons[mid] = b
            mode_row.addWidget(b)
        mode_row.addStretch(1)
        chat_l.addLayout(mode_row)

        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_history.setPlaceholderText(
            "Just describe the patient / case — the co-pilot runs the full workflow.\n\n"
            "Examples:\n"
            "• 55y woman, hysterectomy under sevoflurane, high PIP\n"
            "• Laparoscopic chole, desflurane, ETCO2 issues\n"
            "• Elderly man, total knee, high anomaly score\n"
        )
        chat_l.addWidget(self.chat_history, stretch=1)

        ask_row = QHBoxLayout()
        self.edit_chat = QLineEdit()
        self.edit_chat.setPlaceholderText(
            "Describe the patient only (e.g. age, surgery, agent, vent issues)…"
        )
        self.edit_chat.returnPressed.connect(self._send_chat)
        ask_row.addWidget(self.edit_chat, stretch=1)
        self.btn_send_chat = QPushButton("Ask")
        self.btn_send_chat.setObjectName("primaryBtn")
        self.btn_send_chat.clicked.connect(self._send_chat)
        ask_row.addWidget(self.btn_send_chat)
        btn_clear_chat = QPushButton("Clear")
        btn_clear_chat.setObjectName("secondaryBtn")
        btn_clear_chat.clicked.connect(self._clear_chat)
        ask_row.addWidget(btn_clear_chat)
        chat_l.addLayout(ask_row)

        self.tabs.addTab(chat_tab, "2 · Ask")
        self._chat_messages: list[dict[str, str]] = []
        self._llm_worker: QThread | None = None
        self._streaming_answer = ""

        # ---- 2 · Summary ----
        self.plot_summary_top = ChartView()
        self.plot_summary_rules = ChartView()
        self.table_cases = QTableWidget()
        self.table_cases.setAlternatingRowColors(True)
        self.table_cases.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_cases.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_cases.doubleClicked.connect(self._case_table_activated)
        self.table_cases.setSortingEnabled(True)

        summary = QWidget()
        sum_l = QVBoxLayout(summary)
        sum_l.setContentsMargins(10, 10, 10, 10)
        sum_l.setSpacing(10)
        charts = QSplitter(Qt.Orientation.Horizontal)
        charts.setHandleWidth(6)
        charts.addWidget(self.plot_summary_top)
        charts.addWidget(self.plot_summary_rules)
        charts.setSizes([500, 500])
        sum_l.addWidget(charts, stretch=3)
        table_label = QLabel(
            "Cases in focus (double-click a row to set active case + open timeline)"
        )
        table_label.setObjectName("sectionLabel")
        sum_l.addWidget(table_label)
        sum_l.addWidget(self.table_cases, stretch=2)
        self.summary_tab = summary
        self.tabs.addTab(summary, "3 · Summary")

        # ---- 3 · Case timeline ----
        case_tab = QWidget()
        case_l = QVBoxLayout(case_tab)
        case_l.setContentsMargins(10, 10, 10, 10)
        case_l.setSpacing(10)
        self.plot_timeline = ChartView()
        self.table_episodes = QTableWidget()
        self.table_episodes.setAlternatingRowColors(True)
        self.table_episodes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        case_l.addWidget(self.plot_timeline, stretch=4)
        ep_label = QLabel("Flag episodes / minute flags")
        ep_label.setObjectName("sectionLabel")
        case_l.addWidget(ep_label)
        case_l.addWidget(self.table_episodes, stretch=1)
        self.case_tab = case_tab
        self.tabs.addTab(case_tab, "4 · Case timeline")

        self.rules_text = QTextEdit()
        self.rules_text.setReadOnly(True)
        self.tabs.addTab(self.rules_text, "5 · Rule reference")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.side_panel = side
        body.addWidget(side)
        body.addWidget(self.tabs, stretch=1)
        outer.addLayout(body, stretch=1)
        self.side_panel.hide()
        self._on_tab_changed(0)

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
        self._update_status_paths("Wave folder set")
        self._on_filters_changed()

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
            self.metric_cases.setText("0")
            self.metric_flags.setText("—")
            self.metric_score.setText("—")
            self.research.set_status("No analyzed case cache. Indexed surgery search is available after setup.")
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
        if "Missing processed files" not in msg:
            QMessageBox.warning(self, "Load error", msg[:2000])

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        # Avoid Qt abort if a worker is still running when the window is destroyed
        self._updates.close()
        for w in (self._worker, getattr(self, "_llm_worker", None), self._research_worker, self._case_worker):
            if w is not None and w.isRunning():
                w.requestInterruption()
                w.wait(3000)
        super().closeEvent(event)

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
            self.tabs.setEnabled(True)
            self.research.set_setup_required(False)
        elif first_run:
            self.tabs.setEnabled(False)
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
            self.tabs.setEnabled(ready)
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
            self._charts_pending = True
            QTimer.singleShot(0, self._refresh_charts_if_needed)
        self._sync_path_fields_from_runtime()

    def _set_theme_quick(self, mode: str) -> None:
        from src.user_settings import update_settings

        update_settings(theme=mode)
        concrete = apply_theme(QApplication.instance(), mode)
        # Re-render matplotlib charts so figure colors match the new theme
        self._charts_pending = True
        QTimer.singleShot(0, self._refresh_charts_if_needed)
        self.statusBar().showMessage(f"Theme: {mode} ({concrete})", 4000)

    def _run_pipeline_menu(self) -> None:
        """File-menu pipeline run (sidebar pipeline controls removed)."""
        self._run_pipeline(n_cases=50, preset="default")

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
            "This reads the whole export in batches and can take many minutes on the "
            "full MOVER SIS data. The loaded sample and its charts are not changed; the "
            "co-pilot uses the scan for whole-dataset questions.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._update_status_paths("Scanning full EMR…")
        worker = CorpusScanWorker(emr_dir(), processed_dir(), parent=self)
        worker.progress.connect(lambda message: self.statusBar().showMessage(message))
        worker.finished_ok.connect(self._on_corpus_scan_ok)
        worker.failed.connect(self._on_corpus_scan_fail)
        self._worker = worker
        worker.start()

    def _on_corpus_scan_ok(self, result) -> None:
        self.corpus_scan = result
        meta = result.meta
        self._update_status_paths(
            f"Full-EMR scan: {meta.get('n_scanned_cases')} of "
            f"{meta.get('n_ventilated_surgeries')} ventilated surgeries scored"
        )
        self._refresh_tables_and_selectors()

    def _on_corpus_scan_fail(self, msg: str) -> None:
        self._update_status_paths("Full-EMR scan failed")
        QMessageBox.critical(self, "Full-EMR scan", msg[:4000])

    def _load_corpus_scan(self) -> None:
        """Pick up a previous full-EMR scan saved next to the processed sample."""
        from src.pipeline.corpus_scan import load_corpus_scan

        try:
            self.corpus_scan = load_corpus_scan(processed_dir())
        except Exception:
            self.corpus_scan = None

    def _on_pipeline_ok(self, data) -> None:
        self.cases, self.ts, self.flags, self.episodes, self.events = data
        self._load_corpus_scan()
        self._research_engine = None
        self._update_status_paths(
            f"Loaded {len(self.cases)} cases · {len(self.ts)} min rows · {len(self.flags)} flags"
        )
        self._focus_pids = []
        self._refresh_tables_and_selectors()
        self._charts_pending = True
        QTimer.singleShot(0, self._refresh_charts_if_needed)
        self._refresh_rule_reference()

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

    def _filtered_cases(self) -> pd.DataFrame:
        """Cases for Summary table/charts: focus list from LLM tools, else all."""
        if self.cases is None or self.cases.empty:
            return pd.DataFrame()
        if self._focus_pids:
            return self.cases[
                self.cases["PID"].astype(str).isin([str(p) for p in self._focus_pids])
            ].copy()
        return self.cases.copy()

    def _on_vitals_toggled(self, checked: bool) -> None:
        self._show_vitals = bool(checked)
        self._refresh_case_timeline()

    def _on_tab_changed(self, _index: int) -> None:
        self.side_panel.setVisible(_index != 0)
        self.header_widget.setVisible(_index != 0)
        if _index == 0:
            self.outer_layout.setContentsMargins(0, 0, 0, 0)
            self.outer_layout.setSpacing(0)
            self.main_central.setStyleSheet("""
                QWidget#mainCentral { background: #ebe9e3; }
                QTabWidget::pane { border: 0; }
                QTabBar::tab { background: #f8f7f3; color: #645b65; border: 0;
                    border-right: 1px solid #d5d0d4; padding: 11px 20px; min-height: 22px; }
                QTabBar::tab:selected { background: #332941; color: #fff9f4; }
            """)
        else:
            self.outer_layout.setContentsMargins(16, 14, 16, 10)
            self.outer_layout.setSpacing(14)
            self.main_central.setStyleSheet("")
        self._refresh_charts_if_needed()

    def _set_active_pid(self, pid: str | None) -> None:
        self._active_pid = str(pid) if pid else None
        # Keep hidden combo in sync for any legacy paths
        self.combo_pid.blockSignals(True)
        self.combo_pid.clear()
        if self._active_pid and self.cases is not None:
            self.combo_pid.addItem(self._active_pid, self._active_pid)
            self.combo_pid.setCurrentIndex(0)
        self.combo_pid.blockSignals(False)
        self._update_llm_case_label()
        self._refresh_case_timeline()

    def _open_active_timeline(self) -> None:
        if not self._active_pid:
            QMessageBox.information(
                self,
                "No active case",
                "Ask the co-pilot to find and select a case first "
                "(or double-click a row on Summary).",
            )
            return
        self.tabs.setCurrentWidget(self.case_tab)
        self._refresh_case_timeline()

    def _refresh_tables_and_selectors(self) -> None:
        filtered = self._filtered_cases()
        n_all = 0 if self.cases is None else len(self.cases)
        if hasattr(self, "lbl_corpus"):
            scan = self.corpus_scan.meta if self.corpus_scan is not None else None
            self.lbl_corpus.setText(
                f"Loaded sample: {n_all} cases"
                + (
                    f" · full-EMR scan: {scan.get('n_scanned_cases')} surgeries"
                    if scan
                    else " · no full-EMR scan"
                )
                + (
                    f" · focus {len(self._focus_pids)}"
                    if self._focus_pids
                    else ""
                )
            )
        if hasattr(self, "lbl_focus"):
            if self._focus_pids:
                preview = ", ".join(str(p)[:10] for p in self._focus_pids[:6])
                if len(self._focus_pids) > 6:
                    preview += f" +{len(self._focus_pids) - 6}"
                self.lbl_focus.setText(f"Focus list: {preview}")
            else:
                self.lbl_focus.setText("Focus list: (all corpus / none yet)")

        if filtered.empty:
            self.table_cases.setRowCount(0)
            self.metric_cases.setText("0")
            self.metric_flags.setText("—")
            self.metric_score.setText("—")
            self.plot_summary_top.clear("No cases loaded.")
            self.plot_summary_rules.clear()
            self._update_llm_case_label()
            return

        show_cols = [
            c
            for c in [
                "PID",
                "Age",
                "Gender",
                "Procedure_short",
                "primary_agent_name",
                "case_duration_min",
                "median_TV",
                "median_PIP",
                "median_ETCO2",
                "n_warn",
                "n_critical",
                "anomaly_score",
                "top_rules",
            ]
            if c in filtered.columns
        ]
        self.table_cases.setSortingEnabled(False)
        _df_to_table(self.table_cases, filtered[show_cols])
        self.table_cases.setSortingEnabled(True)

        # Hidden combo holds focus PIDs for convenience
        self.combo_pid.blockSignals(True)
        self.combo_pid.clear()
        ranked = filtered
        if "anomaly_score" in filtered.columns:
            ranked = filtered.sort_values("anomaly_score", ascending=False)
        for _, row in ranked.iterrows():
            pid = row["PID"]
            label = f"{str(pid)[:10]}… | score={int(row.get('anomaly_score', 0))}"
            self.combo_pid.addItem(label, pid)
        if self._active_pid:
            idx = self.combo_pid.findData(self._active_pid)
            if idx >= 0:
                self.combo_pid.setCurrentIndex(idx)
        self.combo_pid.blockSignals(False)

        n_flagged = int(
            ((filtered.get("n_warn", 0) + filtered.get("n_critical", 0)) > 0).sum()
        )
        self.metric_cases.setText(str(len(filtered)))
        self.metric_flags.setText(str(n_flagged))
        if "anomaly_score" in filtered.columns:
            self.metric_score.setText(str(int(filtered["anomaly_score"].max())))
        else:
            self.metric_score.setText("—")
        self._update_llm_case_label()

    def _refresh_charts_if_needed(self) -> None:
        filtered = self._filtered_cases()
        if filtered.empty:
            self._charts_pending = False
            return

        tab = self.tabs.currentIndex()
        if tab == self.tabs.indexOf(self.summary_tab) or self._charts_pending:
            self.plot_summary_top.set_figure(
                top_cases_figure(filtered, n=min(15, len(filtered)))
            )
            pid_list = filtered["PID"].tolist()
            fsub = (
                self.flags[self.flags["PID"].isin(pid_list)]
                if self.flags is not None
                else pd.DataFrame()
            )
            self.plot_summary_rules.set_figure(flag_rules_figure(fsub))
        if tab == self.tabs.indexOf(self.case_tab) or self._charts_pending:
            self._refresh_case_timeline()
        self._charts_pending = False

    def _update_llm_case_label(self) -> None:
        if not hasattr(self, "lbl_llm_case"):
            return
        pid = self._active_pid
        if not pid:
            self.lbl_llm_case.setText(
                "Active case: (none — ask the co-pilot to find cases)"
            )
            return
        proc = ""
        if self.cases is not None and not self.cases.empty:
            row = self.cases[self.cases["PID"].astype(str) == str(pid)]
            if not row.empty and "Procedure_short" in row.columns:
                proc = str(row.iloc[0].get("Procedure_short", "") or "")
        self.lbl_llm_case.setText(
            f"Active case: {pid}" + (f"  —  {proc[:80]}" if proc else "")
        )

    def _refresh_ollama_models(self, *, start_if_needed: bool = False) -> None:
        if not hasattr(self, "combo_llm_model"):
            return
        from src.llm.service import ensure_ollama, ollama_status_summary, probe_ollama
        from src.user_settings import load_settings

        if start_if_needed:
            status = ensure_ollama(start_if_needed=True, wait_s=20.0)
        else:
            status = probe_ollama()

        prev = self.combo_llm_model.currentText().strip()
        self.combo_llm_model.blockSignals(True)
        self.combo_llm_model.clear()
        if not status.available:
            self.combo_llm_model.addItem("(Ollama stopped — click Start)")
            self.combo_llm_model.blockSignals(False)
            self._set_ollama_status_ui(status)
            self.statusBar().showMessage(status.message)
            return

        models = status.models
        if not models:
            self.combo_llm_model.addItem("(no models — ollama pull gemma4)")
        else:
            self.combo_llm_model.addItems(models)
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
                idx = self.combo_llm_model.findText(preferred)
                if idx >= 0:
                    self.combo_llm_model.setCurrentIndex(idx)
                    break
        self.combo_llm_model.blockSignals(False)
        if not getattr(self, "_ollama_model_hooked", False):
            self.combo_llm_model.currentTextChanged.connect(self._on_ollama_model_changed)
            self._ollama_model_hooked = True
        self._set_ollama_status_ui(status)
        self.statusBar().showMessage(ollama_status_summary(status))

    def _set_ollama_status_ui(self, status) -> None:
        if not hasattr(self, "lbl_ollama_status"):
            return
        if status.available:
            n = len(status.models)
            extra = " · started by app" if status.started_by_app else ""
            self.lbl_ollama_status.setText(
                f"Ollama: <b style='color:#14b8a6'>running</b> · {n} model(s){extra}"
            )
            self.lbl_ollama_status.setTextFormat(Qt.TextFormat.RichText)
            if hasattr(self, "btn_ollama_start"):
                self.btn_ollama_start.setEnabled(False)
                self.btn_ollama_stop.setEnabled(True)
                self.btn_ollama_unload.setEnabled(True)
        else:
            self.lbl_ollama_status.setText(
                "Ollama: <b style='color:#f87171'>stopped</b> — click Start to activate"
            )
            self.lbl_ollama_status.setTextFormat(Qt.TextFormat.RichText)
            if hasattr(self, "btn_ollama_start"):
                self.btn_ollama_start.setEnabled(True)
                self.btn_ollama_stop.setEnabled(False)
                self.btn_ollama_unload.setEnabled(False)
        if hasattr(self, "research"):
            self.research.set_models(
                list(status.models) if status.available else [],
                self.combo_llm_model.currentText().strip(),
            )

    def _ollama_start(self) -> None:
        from src.llm.service import start_ollama

        self.statusBar().showMessage("Starting Ollama…")
        for b in (
            getattr(self, "btn_ollama_start", None),
            getattr(self, "btn_ollama_stop", None),
            getattr(self, "btn_ollama_unload", None),
        ):
            if b is not None:
                b.setEnabled(False)
        QApplication.processEvents()
        try:
            status = start_ollama(wait_s=35.0)
            # Refresh list without a second start attempt
            self._refresh_ollama_models(start_if_needed=False)
            if not status.available:
                # Force UI back to startable state even if probe raced
                self.btn_ollama_start.setEnabled(True)
                self.btn_ollama_stop.setEnabled(False)
                self.btn_ollama_unload.setEnabled(False)
                self.lbl_ollama_status.setText(
                    "Ollama: <b style='color:#f87171'>failed to start</b> — try again"
                )
                self.lbl_ollama_status.setTextFormat(Qt.TextFormat.RichText)
                QMessageBox.warning(
                    self,
                    "Ollama start",
                    status.message
                    + "\n\nIf you just clicked Stop, wait 2 seconds and try Start again.",
                )
            else:
                self.statusBar().showMessage(status.message)
        except Exception as e:
            self.btn_ollama_start.setEnabled(True)
            self.btn_ollama_stop.setEnabled(False)
            self.btn_ollama_unload.setEnabled(False)
            QMessageBox.critical(self, "Ollama start", str(e))
            self._refresh_ollama_models(start_if_needed=False)

    def _ollama_stop(self) -> None:
        from src.llm.service import stop_ollama

        w = getattr(self, "_llm_worker", None)
        if w is not None and w.isRunning():
            w.requestInterruption()
            w.wait(1500)

        self.statusBar().showMessage("Stopping Ollama…")
        for b in (
            getattr(self, "btn_ollama_start", None),
            getattr(self, "btn_ollama_stop", None),
            getattr(self, "btn_ollama_unload", None),
        ):
            if b is not None:
                b.setEnabled(False)
        QApplication.processEvents()
        try:
            ok, msg = stop_ollama()
        except Exception as e:
            ok, msg = False, str(e)
        # Always refresh + re-enable Start so user can restart without relaunching the app
        self._refresh_ollama_models(start_if_needed=False)
        self.btn_ollama_start.setEnabled(True)
        self.btn_ollama_stop.setEnabled(False)
        self.btn_ollama_unload.setEnabled(False)
        self.statusBar().showMessage(msg)
        if ok:
            QMessageBox.information(self, "Ollama stopped", msg)
        else:
            QMessageBox.warning(self, "Ollama stop", msg)

    def _ollama_unload(self) -> None:
        from src.llm.service import unload_ollama_model

        model = self.combo_llm_model.currentText().strip()
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
            if hasattr(self, "research"):
                index = self.research.model_combo.findText(text)
                if index >= 0:
                    self.research.model_combo.blockSignals(True)
                    self.research.model_combo.setCurrentIndex(index)
                    self.research.model_combo.blockSignals(False)

    def _research_model_changed(self, text: str) -> None:
        index = self.combo_llm_model.findText(text)
        if index >= 0:
            self.combo_llm_model.setCurrentIndex(index)

    def _make_session_snapshot(self):
        from src.llm.tools import SessionState

        return SessionState(
            cases=self.cases,
            timeseries=self.ts,
            flags=self.flags,
            episodes=self.episodes,
            events=self.events,
            active_pid=self._active_pid,
            focus_pids=list(self._focus_pids),
            corpus=self.corpus_scan.cases if self.corpus_scan is not None else None,
            corpus_episodes=self.corpus_scan.episodes if self.corpus_scan is not None else None,
            corpus_meta=self.corpus_scan.meta if self.corpus_scan is not None else None,
        )

    def _start_research_search(self, question: str) -> None:
        from src.guardrails.validate_io import validate_emr_dir
        from src.llm.ollama_client import OllamaEmbedder
        from src.services.retrieval import RetrievalEngine

        if self._research_worker is not None and self._research_worker.isRunning():
            return
        try:
            source = validate_emr_dir(emr_dir())
        except Exception as exc:
            self.research.set_status(f"EMR setup required: {exc}")
            return
        if self._research_engine is None or self._research_engine.emr != source:
            embed_model = str(load_settings().get("ollama_embed_model") or "nomic-embed-text")
            self._research_engine = RetrievalEngine(
                emr=source,
                cache_path=settings_path().parent / "case_embeddings.sqlite3",
                embedder=OllamaEmbedder(embed_model),
                analyzed_pids=(self.cases["PID"].astype(str).tolist() if self.cases is not None and "PID" in self.cases else []),
            )
        self.research.set_searching(True)
        self.research.set_answer("")
        self.research.set_status("Preparing indexed surgery search…")
        self._research_worker = ResearchWorker(
            self._research_engine,
            question,
            self.research.selected_model(),
            self._make_session_snapshot(),
            parent=self,
        )
        self._research_worker.progress.connect(lambda progress: self.research.set_status(progress.message))
        self._research_worker.shortlist.connect(self._on_research_shortlist)
        self._research_worker.answer.connect(self._on_research_answer)
        self._research_worker.failed.connect(self._on_research_fail)
        self._research_worker.finished.connect(self._on_research_done)
        self._research_worker.start()

    def _on_research_shortlist(self, result) -> None:
        self.research.set_results(result)
        if not result.candidates:
            self.research.set_answer("No matching indexed surgery records were found for this question.")
        elif self.research.selected_model() is None:
            self.research.set_answer("Case search is available. Start and select a local chat model to generate a source-grounded answer.")
        preferred = self.research.preferred_pid()
        if preferred:
            self._load_research_case(preferred, False, False)

    def _on_research_answer(self, response) -> None:
        self.research.set_answer(str(getattr(response, "answer", "") or "No answer was returned."))

    def _on_research_fail(self, message: str) -> None:
        self.research.set_status(f"Research request failed: {message}")
        self.research.set_answer("The local model could not complete this answer. Retrieved cases remain available for manual inspection.")

    def _on_research_done(self) -> None:
        self.research.set_searching(False)

    def _load_research_case(self, pid: str, comparison: bool, open_detail: bool) -> None:
        if self._case_worker is not None and self._case_worker.isRunning():
            self._queued_case = (pid, comparison, open_detail)
            self.research.set_status(f"Loading {pid} after the current case…")
            return
        candidate = self.research.candidate_for(pid)
        if candidate is not None and not candidate.has_vent:
            # The pipeline analyzes ventilated surgeries only; skip a doomed fetch
            self.research.show_case_unavailable(pid, NO_VENT_MESSAGE, comparison=comparison)
            return
        self._pending_case = (pid, comparison, open_detail)
        if self.cases is not None and self.ts is not None and "PID" in self.cases and "PID" in self.ts:
            cases = self.cases[self.cases["PID"].astype(str) == pid]
            if not cases.empty:
                ts = self.ts[self.ts["PID"].astype(str) == pid].copy()
                flags = self.flags[self.flags["PID"].astype(str) == pid].copy() if self.flags is not None and "PID" in self.flags else pd.DataFrame()
                if not ts.empty:
                    self._on_research_case_data(ts, flags)
                    return
        self.research.set_status(f"Loading this case: {pid}…")
        self._case_worker = CaseFetchWorker(pid, emr_dir(), parent=self)
        self._case_worker.loaded.connect(self._on_research_case_loaded)
        self._case_worker.failed.connect(
            lambda message: self.research.show_case_unavailable(pid, message, comparison=comparison)
        )
        self._case_worker.finished.connect(self._on_research_case_fetch_done)
        self._case_worker.start()

    def _on_research_case_fetch_done(self) -> None:
        if self._queued_case is not None:
            request = self._queued_case
            self._queued_case = None
            self._load_research_case(*request)

    def _on_research_case_loaded(self, fetched) -> None:
        self._on_research_case_data(fetched.timeseries, fetched.flags)

    def _on_research_case_data(self, timeseries: pd.DataFrame, flags: pd.DataFrame) -> None:
        if self._pending_case is None:
            return
        pid, comparison, open_detail = self._pending_case
        self._pending_case = None
        if timeseries is None or timeseries.empty or "t_min" not in timeseries:
            self.research.show_case_unavailable(
                pid, "no signal samples were found for this surgery.", comparison=comparison
            )
            return
        self.research.set_case_data(
            pid,
            timeseries,
            flags if flags is not None else pd.DataFrame(),
            comparison=comparison,
            open_detail=open_detail,
        )
        self.research.set_status(f"Loaded source values for {pid}.")

    def _current_case_context(self) -> str:
        pid = self._active_pid
        if not pid:
            raise RuntimeError(
                "No active case yet. Ask the co-pilot to search and select a PID first."
            )
        if self.cases is None or self.ts is None:
            raise RuntimeError("Load processed case data first (File → Reload).")
        from src.llm.case_context import build_case_context

        return build_case_context(
            str(pid),
            cases=self.cases,
            timeseries=self.ts,
            flags=self.flags,
            episodes=self.episodes,
            events=self.events,
            include_emr_extras=True,
        )

    def _show_case_context(self) -> None:
        try:
            ctx = self._current_case_context()
        except Exception as e:
            QMessageBox.warning(self, "Case context", str(e))
            return
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Grounded case context")
        dlg.setText(
            "Structured briefing available to the co-pilot for the active case "
            "(truncated if very long)."
        )
        dlg.setDetailedText(ctx[:20000])
        dlg.setIcon(QMessageBox.Icon.Information)
        dlg.exec()

    def _set_llm_mode(self, mode: str) -> None:
        self._llm_mode = mode if mode in {"chat", "analyze", "compare"} else "analyze"
        for mid, btn in getattr(self, "_mode_buttons", {}).items():
            on = mid == self._llm_mode
            btn.setChecked(on)
            btn.setObjectName("primaryBtn" if on else "secondaryBtn")
            # Force style refresh
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.statusBar().showMessage(f"Co-pilot mode: {self._llm_mode}", 3000)

    def _clear_chat(self) -> None:
        self._chat_messages = []
        self.chat_history.clear()
        self._streaming_answer = ""

    def _append_chat(self, role: str, text: str) -> None:
        who = "You" if role == "user" else "Assistant"
        color = chat_role_color(role)
        # Escape HTML-ish but keep simple newlines via pre-wrap
        safe = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        self.chat_history.append(
            f'<p style="margin:8px 0 2px 0;"><b style="color:{color};">{who}</b></p>'
            f'<p style="margin:0 0 10px 0; white-space:pre-wrap;">{safe}</p>'
        )

    def _send_chat(self) -> None:
        if self._llm_worker is not None and self._llm_worker.isRunning():
            QMessageBox.information(self, "Busy", "Still waiting on the local model.")
            return
        question = self.edit_chat.text().strip()
        if not question:
            return
        model = self.combo_llm_model.currentText().strip()
        if not model or model.startswith("("):
            # Try auto-start once (user may not have clicked Start)
            self._refresh_ollama_models(start_if_needed=True)
            model = self.combo_llm_model.currentText().strip()
        if not model or model.startswith("("):
            QMessageBox.warning(
                self,
                "Ollama model",
                "Ollama is not ready.\n\n"
                "1. Click Start in the Research co-pilot panel\n"
                "2. Pull a model if needed: ollama pull qwen3:14b\n"
                "3. Click Refresh and pick a model",
            )
            return
        if self.cases is None or self.cases.empty:
            QMessageBox.warning(
                self,
                "No data",
                "No processed cases loaded.\n\n"
                "Use File → Reload processed data, or File → Run pipeline.",
            )
            return

        self.edit_chat.clear()
        self._append_chat("user", question)
        self._chat_messages.append({"role": "user", "content": question})
        self.btn_send_chat.setEnabled(False)
        self.statusBar().showMessage(f"Co-pilot ({model}) working…")

        session = self._make_session_snapshot()
        self._llm_worker = LLMChatWorker(
            model=model,
            question=question,
            session_snapshot=session,
            mode=self._llm_mode,
            parent=self,
        )
        self._llm_worker.status.connect(
            lambda m: self.statusBar().showMessage(m)
        )
        self._llm_worker.finished_ok.connect(self._on_llm_ok)
        self._llm_worker.failed.connect(self._on_llm_fail)
        self._llm_worker.finished.connect(lambda: self.btn_send_chat.setEnabled(True))
        self._llm_worker.start()

    def _on_llm_ok(self, result) -> None:
        # AgentResult from worker
        final = getattr(result, "answer", None) or str(result)
        final = str(final).strip()
        self._chat_messages.append({"role": "assistant", "content": final})
        # Sync tool-selected case / focus back to UI
        if getattr(result, "active_pid", None):
            self._set_active_pid(result.active_pid)
        if getattr(result, "focus_pids", None) is not None:
            self._focus_pids = list(result.focus_pids or [])
        self._refresh_tables_and_selectors()
        self._charts_pending = True
        QTimer.singleShot(0, self._refresh_charts_if_needed)

        self.chat_history.clear()
        for m in self._chat_messages:
            self._append_chat(m["role"], m["content"])
        tools = getattr(result, "tool_trace", None) or []
        extra = f" · tools: {len(tools)}" if tools else ""
        self.statusBar().showMessage(f"Co-pilot answer ready{extra}")
        self._streaming_answer = ""

    def _on_llm_fail(self, msg: str) -> None:
        self._append_chat(
            "assistant",
            f"[Error] {msg}\n\n"
            "Check that Ollama is running (`ollama serve`) and a model is pulled.",
        )
        self.statusBar().showMessage("Local LLM request failed")
        self._streaming_answer = ""

    def _refresh_case_timeline(self) -> None:
        if self.ts is None:
            self.plot_timeline.clear("Load processed data first.")
            self.table_episodes.setRowCount(0)
            return
        pid = self._active_pid
        if not pid:
            self.plot_timeline.clear(
                "No active case — ask the co-pilot to select a PID, "
                "or double-click a Summary row."
            )
            self.table_episodes.setRowCount(0)
            return
        cts = self.ts[self.ts["PID"].astype(str) == str(pid)].sort_values("t_min")
        cflags = (
            self.flags[self.flags["PID"].astype(str) == str(pid)]
            if self.flags is not None
            else pd.DataFrame()
        )
        if cts.empty:
            self.plot_timeline.clear("No timeseries for this case.")
            return
        show_vitals = (
            self.chk_vitals.isChecked()
            if hasattr(self, "chk_vitals")
            else self._show_vitals
        )
        self.plot_timeline.set_figure(
            case_timeline_figure(
                cts,
                cflags if not cflags.empty else None,
                show_vitals=show_vitals,
            )
        )
        if self.episodes is not None and not self.episodes.empty:
            ep = self.episodes[
                self.episodes["PID"].astype(str) == str(pid)
            ].sort_values("t_start_min")
            _df_to_table(
                self.table_episodes, ep.drop(columns=["PID"], errors="ignore")
            )
        else:
            _df_to_table(self.table_episodes, cflags)

    def _case_table_activated(self) -> None:
        row = self.table_cases.currentRow()
        if row < 0:
            return
        pid_col = 0
        for c in range(self.table_cases.columnCount()):
            h = self.table_cases.horizontalHeaderItem(c)
            if h and h.text() == "PID":
                pid_col = c
                break
        item = self.table_cases.item(row, pid_col)
        if not item:
            return
        pid = item.text()
        self._set_active_pid(pid)
        if pid not in self._focus_pids:
            self._focus_pids = [pid] + self._focus_pids
        self.tabs.setCurrentWidget(self.case_tab)

    def _refresh_rule_reference(self) -> None:
        try:
            from src.config import load_thresholds

            preset = "default"
            cfg = load_thresholds(preset, validate=True)
        except GuardrailError as e:
            self.rules_text.setPlainText(f"Threshold config invalid: {e}")
            return
        lines = [
            f"Threshold rules (preset: {cfg.get('_preset', preset)})",
            "",
            "Research/education only — not validated clinical alarms.",
            "",
        ]
        for rid, spec in cfg.get("rules", {}).items():
            lines.append(f"• {rid}: {spec.get('description', '')}")
            details = {k: v for k, v in spec.items() if k != "description"}
            lines.append(f"  {details}")
            lines.append("")
        lines.append("Scoring:")
        lines.append(f"  {cfg.get('scoring', {})}")
        lines.append("Age-40 MAC (vol %):")
        lines.append(f"  {cfg.get('mac_age40', {})}")
        self.rules_text.setPlainText("\n".join(lines))


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
    raise SystemExit(main())
