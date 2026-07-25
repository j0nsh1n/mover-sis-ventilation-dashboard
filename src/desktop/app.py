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
from PySide6.QtGui import QAction, QFont, QGuiApplication
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
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
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
from src.guardrails.exceptions import GuardrailError
from src.guardrails.limits import ALLOWED_PRESETS, MAX_N_CASES, MIN_N_CASES
from src.__version__ import get_version
from src.runtime_paths import (
    apply_persisted_settings,
    configure_from_user_directory,
    emr_dir,
    processed_dir,
)

# Shared light theme for clearer visual hierarchy
APP_STYLESHEET = """
QMainWindow, QWidget {
    background: #f4f6f8;
    color: #1f2933;
    font-size: 13px;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #d9e2ec;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #334e68;
}
QLineEdit, QSpinBox, QComboBox {
    background: #ffffff;
    border: 1px solid #bcccdc;
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 24px;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #486581;
}
QPushButton {
    background: #486581;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 7px 12px;
    font-weight: 600;
}
QPushButton:hover { background: #334e68; }
QPushButton:disabled { background: #9fb3c8; }
QPushButton#secondaryBtn {
    background: #e2e8f0;
    color: #243b53;
}
QPushButton#secondaryBtn:hover { background: #cbd5e1; }
QPushButton#primaryBtn {
    background: #0f766e;
}
QPushButton#primaryBtn:hover { background: #0d9488; }
QTabWidget::pane {
    border: 1px solid #d9e2ec;
    border-radius: 8px;
    background: #ffffff;
    top: -1px;
}
QTabBar::tab {
    background: #e2e8f0;
    border: 1px solid #d9e2ec;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 14px;
    margin-right: 3px;
    color: #486581;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #102a43;
    font-weight: 600;
}
QTableWidget {
    background: #ffffff;
    gridline-color: #e2e8f0;
    border: none;
    alternate-background-color: #f8fafc;
}
QHeaderView::section {
    background: #f0f4f8;
    padding: 6px;
    border: none;
    border-right: 1px solid #d9e2ec;
    border-bottom: 1px solid #d9e2ec;
    font-weight: 600;
}
QStatusBar {
    background: #e2e8f0;
    color: #334e68;
}
QLabel#heroTitle {
    font-size: 18px;
    font-weight: 700;
    color: #102a43;
}
QLabel#heroSub {
    color: #627d98;
}
QLabel#pathHint {
    color: #486581;
    font-size: 11px;
}
QFrame#metricCard {
    background: #ffffff;
    border: 1px solid #d9e2ec;
    border-radius: 8px;
    padding: 8px;
}
QTextEdit {
    background: #ffffff;
    border: 1px solid #d9e2ec;
    border-radius: 6px;
}
"""


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
    lay.setContentsMargins(10, 8, 10, 8)
    t = QLabel(title)
    t.setStyleSheet("color:#627d98; font-size:11px; font-weight:600;")
    value_label.setStyleSheet("font-size:16px; font-weight:700; color:#102a43;")
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
        self.setStyleSheet(APP_STYLESHEET)

        apply_persisted_settings()

        self.cases: pd.DataFrame | None = None
        self.ts: pd.DataFrame | None = None
        self.flags: pd.DataFrame | None = None
        self.episodes: pd.DataFrame | None = None
        self.events: pd.DataFrame | None = None
        self._worker: QThread | None = None
        self._charts_pending = False

        self._build_menu()
        self._build_ui()
        self._sync_path_fields_from_runtime()
        self.setStatusBar(QStatusBar())
        self._update_status_paths("Ready")

        QTimer.singleShot(0, self._autoload_processed)

    # ----- menu -----
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_data = QAction("Open data folder…", self)
        open_data.setShortcut("Ctrl+O")
        open_data.triggered.connect(self._browse_data_folder)
        file_menu.addAction(open_data)
        reload_act = QAction("Reload processed data", self)
        reload_act.setShortcut("Ctrl+R")
        reload_act.triggered.connect(self._autoload_processed)
        file_menu.addAction(reload_act)
        file_menu.addSeparator()
        quit_act = QAction("E&xit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    # ----- layout -----
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 10, 12, 8)
        outer.setSpacing(10)

        # Header
        header = QHBoxLayout()
        hero = QVBoxLayout()
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
        metrics.setSpacing(8)
        metrics.addWidget(_metric_card("Cases loaded", self.metric_cases))
        metrics.addWidget(_metric_card("With flags", self.metric_flags))
        metrics.addWidget(_metric_card("Max score", self.metric_score))
        header.addLayout(metrics)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(12)

        # ---- Sidebar ----
        side = QWidget()
        side.setMinimumWidth(300)
        side.setMaximumWidth(360)
        side_l = QVBoxLayout(side)
        side_l.setContentsMargins(0, 0, 0, 0)
        side_l.setSpacing(8)

        # Data locations
        data_box = QGroupBox("Data locations")
        dl = QVBoxLayout(data_box)
        hint = QLabel(
            "Choose the SIS EMR folder (contains patient_information.csv) "
            "or a parent folder with EMR/ / raw/EMR/."
        )
        hint.setObjectName("pathHint")
        hint.setWordWrap(True)
        dl.addWidget(hint)

        self.edit_data_root = QLineEdit()
        self.edit_data_root.setPlaceholderText("Data root or EMR folder…")
        self.edit_data_root.setClearButtonEnabled(True)
        row1 = QHBoxLayout()
        row1.addWidget(self.edit_data_root, stretch=1)
        btn_browse = QPushButton("Browse…")
        btn_browse.setObjectName("secondaryBtn")
        btn_browse.clicked.connect(self._browse_data_folder)
        row1.addWidget(btn_browse)
        dl.addLayout(row1)

        self.lbl_emr_path = QLabel("EMR: —")
        self.lbl_emr_path.setObjectName("pathHint")
        self.lbl_emr_path.setWordWrap(True)
        self.lbl_emr_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.lbl_proc_path = QLabel("Processed: —")
        self.lbl_proc_path.setObjectName("pathHint")
        self.lbl_proc_path.setWordWrap(True)
        self.lbl_proc_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        dl.addWidget(self.lbl_emr_path)
        dl.addWidget(self.lbl_proc_path)

        btn_apply = QPushButton("Use this folder")
        btn_apply.setObjectName("primaryBtn")
        btn_apply.clicked.connect(self._apply_data_folder_from_field)
        btn_reload = QPushButton("Reload cache")
        btn_reload.setObjectName("secondaryBtn")
        btn_reload.clicked.connect(self._autoload_processed)
        row_actions = QHBoxLayout()
        row_actions.addWidget(btn_apply)
        row_actions.addWidget(btn_reload)
        dl.addLayout(row_actions)
        side_l.addWidget(data_box)

        # Pipeline
        ctrl = QGroupBox("Pipeline")
        fl = QFormLayout(ctrl)
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.spin_cases = QSpinBox()
        self.spin_cases.setRange(max(1, MIN_N_CASES), min(200, MAX_N_CASES))
        self.spin_cases.setSingleStep(10)
        self.spin_cases.setValue(50)
        fl.addRow("Sample size", self.spin_cases)

        self.combo_preset = QComboBox()
        self.combo_preset.addItems(sorted(ALLOWED_PRESETS))
        self.combo_preset.setCurrentText("default")
        fl.addRow("Threshold preset", self.combo_preset)

        self.btn_run = QPushButton("Run / reload pipeline")
        self.btn_run.setObjectName("primaryBtn")
        self.btn_run.clicked.connect(self._run_pipeline)
        fl.addRow(self.btn_run)
        side_l.addWidget(ctrl)

        # Filters
        filt = QGroupBox("Filters & case")
        ff = QFormLayout(filt)
        self.combo_agent = QComboBox()
        self.combo_agent.addItem("(all)")
        self.combo_agent.currentTextChanged.connect(self._on_filters_changed)
        ff.addRow("Primary agent", self.combo_agent)

        self.spin_min_score = QSpinBox()
        self.spin_min_score.setRange(0, 100_000)
        self.spin_min_score.setValue(0)
        self.spin_min_score.valueChanged.connect(self._on_filters_changed)
        ff.addRow("Min anomaly score", self.spin_min_score)

        self.chk_vitals = QCheckBox("Show vitals on timeline")
        self.chk_vitals.setChecked(True)
        self.chk_vitals.toggled.connect(self._refresh_case_timeline)
        ff.addRow(self.chk_vitals)

        self.combo_pid = QComboBox()
        self.combo_pid.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.combo_pid.currentIndexChanged.connect(self._refresh_case_timeline)
        ff.addRow("Surgery (PID)", self.combo_pid)
        side_l.addWidget(filt)

        self.lbl_case_meta = QLabel("Select a case to inspect the timeline.")
        self.lbl_case_meta.setWordWrap(True)
        self.lbl_case_meta.setObjectName("pathHint")
        side_l.addWidget(self.lbl_case_meta)
        side_l.addStretch(1)

        # ---- Tabs ----
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed)

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
        sum_l.setContentsMargins(8, 8, 8, 8)
        charts = QSplitter(Qt.Orientation.Horizontal)
        charts.addWidget(self.plot_summary_top)
        charts.addWidget(self.plot_summary_rules)
        charts.setSizes([500, 500])
        sum_l.addWidget(charts, stretch=3)
        table_label = QLabel("Cases (double-click a row to open timeline)")
        table_label.setStyleSheet("font-weight:600; color:#334e68; margin-top:4px;")
        sum_l.addWidget(table_label)
        sum_l.addWidget(self.table_cases, stretch=2)
        self.tabs.addTab(summary, "1 · Summary")

        case_tab = QWidget()
        case_l = QVBoxLayout(case_tab)
        case_l.setContentsMargins(8, 8, 8, 8)
        self.plot_timeline = ChartView()
        self.table_episodes = QTableWidget()
        self.table_episodes.setAlternatingRowColors(True)
        self.table_episodes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        case_l.addWidget(self.plot_timeline, stretch=4)
        ep_label = QLabel("Flag episodes / minute flags")
        ep_label.setStyleSheet("font-weight:600; color:#334e68;")
        case_l.addWidget(ep_label)
        case_l.addWidget(self.table_episodes, stretch=1)
        self.tabs.addTab(case_tab, "2 · Case timeline")

        self.rules_text = QTextEdit()
        self.rules_text.setReadOnly(True)
        self.tabs.addTab(self.rules_text, "3 · Rule reference")

        body.addWidget(side)
        body.addWidget(self.tabs, stretch=1)
        outer.addLayout(body, stretch=1)

    # ----- paths -----
    def _sync_path_fields_from_runtime(self) -> None:
        emr = emr_dir()
        proc = processed_dir()
        # Prefer showing parent data root when standard layout
        root = emr
        if emr.name == "EMR" and emr.parent.name == "raw":
            root = emr.parent.parent
        self.edit_data_root.setText(str(root))
        self.lbl_emr_path.setText(f"EMR: {emr}")
        self.lbl_proc_path.setText(f"Processed: {proc}")

    def _update_status_paths(self, prefix: str = "") -> None:
        msg = f"{prefix}  ·  EMR: {emr_dir()}  ·  Processed: {processed_dir()}"
        self.statusBar().showMessage(msg)

    def _browse_data_folder(self) -> None:
        start = self.edit_data_root.text().strip() or str(emr_dir())
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select SIS data or EMR folder",
            start,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not chosen:
            return
        self.edit_data_root.setText(chosen)
        self._apply_data_folder(Path(chosen))

    def _apply_data_folder_from_field(self) -> None:
        text = self.edit_data_root.text().strip()
        if not text:
            QMessageBox.warning(self, "Data folder", "Enter or browse to a folder.")
            return
        self._apply_data_folder(Path(text))

    def _apply_data_folder(self, path: Path) -> None:
        try:
            resolved = configure_from_user_directory(path, persist=True)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Invalid data folder",
                str(e),
            )
            return
        self._sync_path_fields_from_runtime()
        self._update_status_paths(
            f"Data folder set · EMR {resolved['emr_dir'].name}"
        )
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
        if "Missing processed files" not in msg:
            QMessageBox.warning(self, "Load error", msg[:2000])

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        # Avoid Qt abort if a worker is still running when the window is destroyed
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
        super().closeEvent(event)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            f"MOVER SIS Ventilation & Anesthesia Monitor\n"
            f"Version {self._version}\n\n"
            "Native desktop client for the UC Irvine MOVER SIS research dataset.\n"
            "Choose any local EMR folder via File → Open data folder.\n"
            "Not for clinical care.",
        )

    def _run_pipeline(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "A background job is already running.")
            return
        self.btn_run.setEnabled(False)
        self._update_status_paths("Running pipeline…")
        self._worker = PipelineWorker(
            n_cases=self.spin_cases.value(),
            preset=self.combo_preset.currentText(),
            force=True,
            emr=emr_dir(),
            processed=processed_dir(),
            parent=self,
        )
        self._worker.finished_ok.connect(self._on_pipeline_ok)
        self._worker.failed.connect(self._on_pipeline_fail)
        self._worker.finished.connect(lambda: self.btn_run.setEnabled(True))
        self._worker.start()

    def _on_pipeline_ok(self, data) -> None:
        self.cases, self.ts, self.flags, self.episodes, self.events = data
        self._update_status_paths(
            f"Loaded {len(self.cases)} cases · {len(self.ts)} min rows · {len(self.flags)} flags"
        )
        self._populate_filters()
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

    def _populate_filters(self) -> None:
        agents = ["(all)"]
        if self.cases is not None and "primary_agent_name" in self.cases.columns:
            agents += sorted(
                a
                for a in self.cases["primary_agent_name"].dropna().unique().tolist()
                if a
            )
        cur = self.combo_agent.currentText()
        self.combo_agent.blockSignals(True)
        self.combo_agent.clear()
        self.combo_agent.addItems(agents)
        if cur in agents:
            self.combo_agent.setCurrentText(cur)
        self.combo_agent.blockSignals(False)

    def _filtered_cases(self) -> pd.DataFrame:
        if self.cases is None or self.cases.empty:
            return pd.DataFrame()
        df = self.cases
        agent = self.combo_agent.currentText()
        if agent != "(all)" and "primary_agent_name" in df.columns:
            df = df[df["primary_agent_name"] == agent]
        if "anomaly_score" in df.columns:
            df = df[df["anomaly_score"] >= self.spin_min_score.value()]
        return df

    def _on_filters_changed(self) -> None:
        self._refresh_tables_and_selectors()
        self._charts_pending = True
        QTimer.singleShot(0, self._refresh_charts_if_needed)

    def _on_tab_changed(self, _index: int) -> None:
        self._refresh_charts_if_needed()

    def _refresh_tables_and_selectors(self) -> None:
        filtered = self._filtered_cases()
        if filtered.empty:
            self.table_cases.setRowCount(0)
            self.combo_pid.blockSignals(True)
            self.combo_pid.clear()
            self.combo_pid.blockSignals(False)
            self.metric_cases.setText("0")
            self.metric_flags.setText("—")
            self.metric_score.setText("—")
            self.lbl_case_meta.setText("No cases match filters.")
            self.plot_summary_top.clear("No cases match filters.")
            self.plot_summary_rules.clear()
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
        # Sorting interferes with bulk fill; toggle around update
        self.table_cases.setSortingEnabled(False)
        _df_to_table(self.table_cases, filtered[show_cols])
        self.table_cases.setSortingEnabled(True)

        prev = self.combo_pid.currentData()
        self.combo_pid.blockSignals(True)
        self.combo_pid.clear()
        ranked = filtered.sort_values("anomaly_score", ascending=False)
        for _, row in ranked.iterrows():
            pid = row["PID"]
            label = f"{str(pid)[:10]}… | score={int(row.get('anomaly_score', 0))}"
            if "Procedure_short" in row and pd.notna(row["Procedure_short"]):
                label += f" | {str(row['Procedure_short'])[:40]}"
            self.combo_pid.addItem(label, pid)
        if prev is not None:
            idx = self.combo_pid.findData(prev)
            if idx >= 0:
                self.combo_pid.setCurrentIndex(idx)
        self.combo_pid.blockSignals(False)

        n_flagged = int(
            ((filtered.get("n_warn", 0) + filtered.get("n_critical", 0)) > 0).sum()
        )
        self.metric_cases.setText(str(len(filtered)))
        self.metric_flags.setText(str(n_flagged))
        self.metric_score.setText(str(int(filtered["anomaly_score"].max())))
        self.lbl_case_meta.setText(
            f"{len(filtered)} cases in view · double-click a table row for timeline"
        )

    def _refresh_charts_if_needed(self) -> None:
        filtered = self._filtered_cases()
        if filtered.empty:
            self._charts_pending = False
            return

        tab = self.tabs.currentIndex()
        if tab == 0 or self._charts_pending:
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
        if tab == 1 or self._charts_pending:
            self._refresh_case_timeline()
        self._charts_pending = False

    def _refresh_case_timeline(self) -> None:
        if self.ts is None or self.combo_pid.count() == 0:
            self.plot_timeline.clear("Select a case.")
            self.table_episodes.setRowCount(0)
            return
        pid = self.combo_pid.currentData()
        if pid is None:
            return
        cts = self.ts[self.ts["PID"] == pid].sort_values("t_min")
        cflags = (
            self.flags[self.flags["PID"] == pid]
            if self.flags is not None
            else pd.DataFrame()
        )
        if cts.empty:
            self.plot_timeline.clear("No timeseries for this case.")
            return
        self.plot_timeline.set_figure(
            case_timeline_figure(
                cts,
                cflags if not cflags.empty else None,
                show_vitals=self.chk_vitals.isChecked(),
            )
        )
        if self.episodes is not None and not self.episodes.empty:
            ep = self.episodes[self.episodes["PID"] == pid].sort_values("t_start_min")
            _df_to_table(
                self.table_episodes, ep.drop(columns=["PID"], errors="ignore")
            )
        else:
            _df_to_table(self.table_episodes, cflags)

    def _case_table_activated(self) -> None:
        row = self.table_cases.currentRow()
        if row < 0:
            return
        # PID may not be column 0 if sorted — find header
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
        idx = self.combo_pid.findData(pid)
        if idx >= 0:
            self.combo_pid.setCurrentIndex(idx)
            self.tabs.setCurrentIndex(1)

    def _refresh_rule_reference(self) -> None:
        try:
            from src.config import load_thresholds

            preset = self.combo_preset.currentText()
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

    app = QApplication.instance()
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
    app.setStyle("Fusion")

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
