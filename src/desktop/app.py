"""
MOVER SIS desktop application (PySide6).

Launch:
  python -m src.desktop
  ./scripts/run_desktop.sh
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
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

# Repo root on path when launched as script
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_thresholds
from src.dashboard.components import case_timeline_figure, flag_bar_by_rule, top_cases_bar
from src.desktop.plotly_view import PlotlyView
from src.guardrails.exceptions import GuardrailError
from src.guardrails.limits import ALLOWED_PRESETS, MAX_N_CASES, MIN_N_CASES
from src.services.data import ensure_data, load_processed


class PipelineWorker(QThread):
    """Run pipeline off the UI thread."""

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, n_cases: int, preset: str, force: bool, parent=None):
        super().__init__(parent)
        self.n_cases = n_cases
        self.preset = preset
        self.force = force

    def run(self) -> None:
        try:
            data = ensure_data(
                n_cases=self.n_cases,
                preset=self.preset,
                force=self.force,
            )
            self.finished_ok.emit(data)
        except Exception as e:
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


def _df_to_table(table: QTableWidget, df: pd.DataFrame, max_rows: int = 500) -> None:
    view = df.head(max_rows)
    table.clear()
    table.setRowCount(len(view))
    table.setColumnCount(len(view.columns))
    table.setHorizontalHeaderLabels([str(c) for c in view.columns])
    for r, (_, row) in enumerate(view.iterrows()):
        for c, col in enumerate(view.columns):
            val = row[col]
            text = "" if pd.isna(val) else str(val)
            if len(text) > 120:
                text = text[:117] + "…"
            table.setItem(r, c, QTableWidgetItem(text))
    table.resizeColumnsToContents()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MOVER SIS — Ventilation & Anesthesia Monitor")
        self.resize(1400, 900)

        self.cases: pd.DataFrame | None = None
        self.ts: pd.DataFrame | None = None
        self.flags: pd.DataFrame | None = None
        self.episodes: pd.DataFrame | None = None
        self.events: pd.DataFrame | None = None
        self._worker: PipelineWorker | None = None

        self._build_menu()
        self._build_ui()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready — load data or run the pipeline.")

        # Auto-load if processed data already exists
        try:
            from src.runtime_paths import processed_dir

            if (processed_dir() / "cases.parquet").exists():
                self._on_pipeline_ok(load_processed())
        except Exception as e:
            self.statusBar().showMessage(f"Could not auto-load processed data: {e}")

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        quit_act = QAction("E&xit", self)
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # ---- Left control panel ----
        side = QWidget()
        side.setMaximumWidth(320)
        side.setMinimumWidth(260)
        form_wrap = QVBoxLayout(side)

        title = QLabel("MOVER SIS Monitor")
        title.setFont(QFont("", 14, QFont.Weight.Bold))
        form_wrap.addWidget(title)
        caption = QLabel(
            "Desktop app for de-identified perioperative ventilation data.\n"
            "Flags are research aids — not clinical diagnoses."
        )
        caption.setWordWrap(True)
        caption.setStyleSheet("color: #555;")
        form_wrap.addWidget(caption)

        ctrl = QGroupBox("Pipeline")
        fl = QFormLayout(ctrl)
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
        self.btn_run.clicked.connect(self._run_pipeline)
        fl.addRow(self.btn_run)
        form_wrap.addWidget(ctrl)

        filt = QGroupBox("Filters")
        ff = QFormLayout(filt)
        self.combo_agent = QComboBox()
        self.combo_agent.addItem("(all)")
        self.combo_agent.currentTextChanged.connect(self._refresh_views)
        ff.addRow("Primary agent", self.combo_agent)

        self.spin_min_score = QSpinBox()
        self.spin_min_score.setRange(0, 100_000)
        self.spin_min_score.setValue(0)
        self.spin_min_score.valueChanged.connect(self._refresh_views)
        ff.addRow("Min anomaly score", self.spin_min_score)

        self.chk_vitals = QCheckBox("Show vitals on timeline")
        self.chk_vitals.setChecked(True)
        self.chk_vitals.toggled.connect(self._refresh_case_timeline)
        ff.addRow(self.chk_vitals)
        form_wrap.addWidget(filt)

        case_box = QGroupBox("Case")
        cf = QFormLayout(case_box)
        self.combo_pid = QComboBox()
        self.combo_pid.currentIndexChanged.connect(self._refresh_case_timeline)
        cf.addRow("Surgery (PID)", self.combo_pid)
        form_wrap.addWidget(case_box)

        self.lbl_metrics = QLabel("—")
        self.lbl_metrics.setWordWrap(True)
        self.lbl_metrics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form_wrap.addWidget(self.lbl_metrics)
        form_wrap.addStretch(1)

        # ---- Tabs / charts ----
        self.tabs = QTabWidget()
        self.plot_summary_top = PlotlyView()
        self.plot_summary_rules = PlotlyView()
        self.table_cases = QTableWidget()
        self.table_cases.setAlternatingRowColors(True)
        self.table_cases.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_cases.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_cases.doubleClicked.connect(self._case_table_activated)

        summary_split = QSplitter(Qt.Orientation.Vertical)
        charts = QSplitter(Qt.Orientation.Horizontal)
        charts.addWidget(self.plot_summary_top)
        charts.addWidget(self.plot_summary_rules)
        summary_split.addWidget(charts)
        summary_split.addWidget(self.table_cases)
        summary_split.setStretchFactor(0, 3)
        summary_split.setStretchFactor(1, 2)
        self.tabs.addTab(summary_split, "Summary")

        self.plot_timeline = PlotlyView()
        self.table_episodes = QTableWidget()
        self.table_episodes.setAlternatingRowColors(True)
        self.table_episodes.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        case_split = QSplitter(Qt.Orientation.Vertical)
        case_split.addWidget(self.plot_timeline)
        case_split.addWidget(self.table_episodes)
        case_split.setStretchFactor(0, 4)
        case_split.setStretchFactor(1, 1)
        self.tabs.addTab(case_split, "Case timeline")

        self.rules_text = QTextEdit()
        self.rules_text.setReadOnly(True)
        self.tabs.addTab(self.rules_text, "Rule reference")

        root.addWidget(side)
        root.addWidget(self.tabs, stretch=1)

    # ----- actions -----
    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About",
            "MOVER SIS Ventilation & Anesthesia Monitor\n\n"
            "Native desktop client for the UC Irvine MOVER SIS research dataset.\n"
            "Not for clinical care.",
        )

    def _run_pipeline(self) -> None:
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "Pipeline is already running.")
            return
        self.btn_run.setEnabled(False)
        self.statusBar().showMessage("Running pipeline…")
        self._worker = PipelineWorker(
            n_cases=self.spin_cases.value(),
            preset=self.combo_preset.currentText(),
            force=True,
            parent=self,
        )
        self._worker.finished_ok.connect(self._on_pipeline_ok)
        self._worker.failed.connect(self._on_pipeline_fail)
        self._worker.finished.connect(lambda: self.btn_run.setEnabled(True))
        self._worker.start()

    def _on_pipeline_ok(self, data) -> None:
        self.cases, self.ts, self.flags, self.episodes, self.events = data
        self.statusBar().showMessage(
            f"Loaded {len(self.cases)} cases · "
            f"{len(self.ts)} minute rows · {len(self.flags)} flags"
        )
        self._populate_filters()
        self._refresh_rule_reference()
        self._refresh_views()

    def _on_pipeline_fail(self, msg: str) -> None:
        self.statusBar().showMessage("Pipeline failed")
        QMessageBox.critical(
            self,
            "Pipeline / guardrail error",
            msg[:4000]
            + (
                "\n\nEnsure data/raw/EMR/ has patient_information, "
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
        df = self.cases.copy()
        agent = self.combo_agent.currentText()
        if agent != "(all)" and "primary_agent_name" in df.columns:
            df = df[df["primary_agent_name"] == agent]
        if "anomaly_score" in df.columns:
            df = df[df["anomaly_score"] >= self.spin_min_score.value()]
        return df

    def _refresh_views(self) -> None:
        filtered = self._filtered_cases()
        if filtered.empty:
            self.plot_summary_top.clear("No cases match filters.")
            self.plot_summary_rules.clear()
            self.table_cases.setRowCount(0)
            self.combo_pid.blockSignals(True)
            self.combo_pid.clear()
            self.combo_pid.blockSignals(False)
            self.lbl_metrics.setText("No data")
            return

        self.plot_summary_top.set_figure(
            top_cases_bar(filtered, n=min(15, len(filtered)))
        )
        pid_list = filtered["PID"].tolist()
        fsub = (
            self.flags[self.flags["PID"].isin(pid_list)]
            if self.flags is not None
            else pd.DataFrame()
        )
        self.plot_summary_rules.set_figure(flag_bar_by_rule(fsub))

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
        _df_to_table(self.table_cases, filtered[show_cols])

        # PID selector
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
        # restore selection if possible
        if prev is not None:
            idx = self.combo_pid.findData(prev)
            if idx >= 0:
                self.combo_pid.setCurrentIndex(idx)
        self.combo_pid.blockSignals(False)

        n_flagged = int(
            ((filtered.get("n_warn", 0) + filtered.get("n_critical", 0)) > 0).sum()
        )
        med = (
            f"{filtered['case_duration_min'].median():.0f}"
            if "case_duration_min" in filtered
            else "—"
        )
        self.lbl_metrics.setText(
            f"<b>Cases:</b> {len(filtered)}<br>"
            f"<b>With flags:</b> {n_flagged}<br>"
            f"<b>Median duration:</b> {med} min<br>"
            f"<b>Max score:</b> {int(filtered['anomaly_score'].max())}"
        )
        self._refresh_case_timeline()

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
        cevents = (
            self.events[self.events["PID"] == pid]
            if self.events is not None and not self.events.empty
            else None
        )
        if cts.empty:
            self.plot_timeline.clear("No timeseries for this case.")
            return
        fig = case_timeline_figure(
            cts,
            cflags,
            cevents,
            show_vitals=self.chk_vitals.isChecked(),
        )
        self.plot_timeline.set_figure(fig)

        if self.episodes is not None and not self.episodes.empty:
            ep = self.episodes[self.episodes["PID"] == pid].sort_values("t_start_min")
            _df_to_table(self.table_episodes, ep.drop(columns=["PID"], errors="ignore"))
        else:
            _df_to_table(self.table_episodes, cflags)

    def _case_table_activated(self) -> None:
        row = self.table_cases.currentRow()
        if row < 0:
            return
        # PID is column 0 when present
        item = self.table_cases.item(row, 0)
        if not item:
            return
        pid = item.text()
        idx = self.combo_pid.findData(pid)
        if idx >= 0:
            self.combo_pid.setCurrentIndex(idx)
            self.tabs.setCurrentIndex(1)

    def _refresh_rule_reference(self) -> None:
        try:
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
    """
    Start the desktop UI.

    Prefer ``python -m src.desktop`` so OpenGL/WebEngine attributes are set
    before QtWebEngine is imported.
    """
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
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    # When executed as a file, ensure attribute before this module's top-level
    # WebEngine import is not possible; use -m src.desktop instead.
    raise SystemExit(main())
