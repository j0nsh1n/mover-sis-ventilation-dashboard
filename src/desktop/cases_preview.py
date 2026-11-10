"""The Cases page's preview panel: the current row's case at a glance, drawn only from data already in memory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QFrame, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from src.desktop.case_page import EPISODE_ROW_HEIGHT, GLYPH_ROLE, NAME_ROLE, RANGE_ROLE, SEVERITY_ROLE, EpisodeDelegate
from src.desktop.charts import signal_info
from src.desktop.evidence_trail import Sparkline
from src.desktop.marks import severity_glyph

PREVIEW_WIDTH = 336
TRACE_HEIGHT = 56
EPISODES_SHOWN = 5
TRACE_SIGNALS = ("PIP", "ETCO2", "HR")
NOTHING_TO_PREVIEW = "Select a case in the list to preview it."
NO_CASES_TO_PREVIEW = "There are no cases to preview."
SIGNALS_ON_OPEN = "Signals show when the case is opened."
EPISODES_ON_OPEN = "Rule episodes show when the case is opened."
NO_EPISODES = "No rule episodes in this case."
KEYS_LINE = "Click selects · Enter or double-click opens · ↑ ↓ moves · / filters · Esc goes back to the question"
_SEVERITY_RANK = {"critical": 0, "warn": 1, "info": 2}


@dataclass(frozen=True)
class PreviewEpisode:
    label: str
    severity: str
    minutes: float


@dataclass(frozen=True)
class PreviewCase:
    """Everything the panel shows for one case. ``traces`` and ``episodes`` are None while they are not in memory."""

    pid: str
    procedure: str
    facts: str
    traces: Mapping[str, tuple[float | None, ...]] | None
    episodes: tuple[PreviewEpisode, ...] | None
    # "label: value" facts for the fields the list does not have a column for
    fields: tuple[tuple[str, str], ...] = ()


class _ByPid:
    """Row positions of each PID in a frame, found once on first use so a lookup never scans the whole frame."""

    def __init__(self, frame: pd.DataFrame | None) -> None:
        self._frame = frame
        self._rows: dict[str, np.ndarray] | None = None

    @property
    def loaded(self) -> bool:
        """True when the frame is in memory at all, whether or not it holds this case."""
        return self._frame is not None

    def get(self, pid: str) -> pd.DataFrame | None:
        if self._frame is None or self._frame.empty or "PID" not in self._frame:
            return None
        if self._rows is None:
            groups = self._frame.groupby(self._frame["PID"].astype(str), sort=False).indices
            self._rows = {str(key): rows for key, rows in groups.items()}
        rows = self._rows.get(pid)
        return None if rows is None else self._frame.iloc[rows]


class PreviewSource:
    """The minute data and rule episodes the app already holds, looked up by case; nothing here reads a file."""

    def __init__(
        self, timeseries: pd.DataFrame | None = None, episodes: pd.DataFrame | None = None,
        scan_episodes: pd.DataFrame | None = None,
    ) -> None:
        self._minutes = _ByPid(timeseries)
        self._episodes = _ByPid(episodes)
        self._scan_episodes = _ByPid(scan_episodes)

    def traces(self, pid: str) -> dict[str, tuple[float | None, ...]] | None:
        """PIP, ETCO2 and heart rate by minute (None where missing), or None when the case's minutes are not in memory."""
        rows = self._minutes.get(pid)
        if rows is None or rows.empty:
            return None
        if "t_min" in rows:
            rows = rows.sort_values("t_min", kind="stable")
        traces: dict[str, tuple[float | None, ...]] = {}
        for column in TRACE_SIGNALS:
            values = pd.to_numeric(rows[column], errors="coerce") if column in rows else pd.Series(np.nan, index=rows.index)
            traces[column] = tuple(None if pd.isna(v) else float(v) for v in values)
        return traces

    def episodes(self, pid: str, labels: Mapping[str, str], *, scan: bool) -> tuple[PreviewEpisode, ...] | None:
        """The case's rule episodes, worst first, from the scan's episode table or the loaded sample's.

        None means no table that could hold the case is in memory, which is different from a case with no episodes.
        """
        table = self._scan_episodes if scan else self._episodes
        frame = table.get(pid)
        if frame is None or frame.empty:
            return () if table.loaded else None
        found = [
            PreviewEpisode(labels.get(str(row["rule_id"]), str(row["rule_id"])), str(row["severity"]),
                           float(row["duration_min"]) if pd.notna(row.get("duration_min")) else 0.0)
            for row in frame.to_dict("records")
        ]
        found.sort(key=lambda episode: (_SEVERITY_RANK.get(episode.severity.lower(), 3), -episode.minutes))
        return tuple(found)


def _minutes_text(minutes: float) -> str:
    return f"{minutes:g} min"


def _trace_description(column: str, values: tuple[float | None, ...]) -> str:
    info = signal_info(column)
    known = [v for v in values if v is not None]
    missing = len(values) - len(known)
    unit = f" {info.unit}" if info.unit else ""
    text = f"{info.label} over {len(values)} minutes, lowest {min(known):g}, highest {max(known):g}{unit}"
    return text + (f", {missing} minutes missing." if missing else ".")


class CasePreview(QFrame):
    """The one side panel: the current row's procedure, facts, signal traces and rule episodes, and Open case."""

    open_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("casePreview")
        self.setAccessibleName("Case preview")
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 20, 24, 20)
        column.setSpacing(14)
        head = QVBoxLayout()
        head.setSpacing(2)
        self.kicker = QLabel("Preview")
        self.kicker.setObjectName("quietText")
        head.addWidget(self.kicker)
        self.title = QLabel()
        self.title.setObjectName("previewTitle")
        self.title.setWordWrap(True)
        head.addWidget(self.title)
        self.facts = QLabel()
        self.facts.setObjectName("caseFacts")
        self.facts.setWordWrap(True)
        head.addWidget(self.facts)
        column.addLayout(head)
        self.note = QLabel()
        self.note.setObjectName("bodyText")
        self.note.setWordWrap(True)
        column.addWidget(self.note)
        self.trace_box = QWidget()
        traces = QVBoxLayout(self.trace_box)
        traces.setContentsMargins(0, 0, 0, 0)
        traces.setSpacing(6)
        self.traces: dict[str, Sparkline] = {}
        for column_name in TRACE_SIGNALS:
            line = Sparkline()
            line.setFixedHeight(TRACE_HEIGHT)
            line.setMinimumWidth(0)
            line.setAccessibleName(f"{signal_info(column_name).label} trace")
            traces.addWidget(line)
            self.traces[column_name] = line
        column.addWidget(self.trace_box)
        self.trace_note = QLabel(SIGNALS_ON_OPEN)
        self.trace_note.setObjectName("quietText")
        self.trace_note.setWordWrap(True)
        column.addWidget(self.trace_note)
        self.episode_list = QListWidget()
        self.episode_list.setObjectName("previewEpisodes")
        self.episode_list.setAccessibleName("Rule episodes")
        self.episode_list.setItemDelegate(EpisodeDelegate(self.episode_list))
        self.episode_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.episode_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.episode_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.episode_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        column.addWidget(self.episode_list)
        self.field_box = QWidget()
        self._fields = QVBoxLayout(self.field_box)
        self._fields.setContentsMargins(0, 0, 0, 0)
        self._fields.setSpacing(4)
        self.field_labels: list[QLabel] = []
        column.addWidget(self.field_box)
        column.addStretch(1)
        self.open_button = QPushButton("Open case")
        self.open_button.setObjectName("primaryBtn")
        self.open_button.setAutoDefault(True)
        self.open_button.setAccessibleName("Open the previewed case")
        self.open_button.clicked.connect(self.open_requested)
        column.addWidget(self.open_button)
        self.keys = QLabel(KEYS_LINE)
        self.keys.setObjectName("quietText")
        self.keys.setWordWrap(True)
        column.addWidget(self.keys)
        self.show_message(NOTHING_TO_PREVIEW)

    def show_message(self, text: str) -> None:
        """No case to show: one sentence says what to do, and Open case waits for a case."""
        self.kicker.setText("Preview")
        self.title.hide()
        self.facts.hide()
        self.note.setText(text)
        self.note.show()
        self.trace_box.hide()
        self.trace_note.hide()
        self.episode_list.hide()
        self.field_box.hide()
        self.open_button.setEnabled(False)

    def show_case(self, case: PreviewCase) -> None:
        self.kicker.setText(f"Preview · {case.pid}")
        self.title.setText(case.procedure or "Procedure not recorded")
        self.title.show()
        self.facts.setText(case.facts)
        self.facts.setVisible(bool(case.facts))
        self.note.hide()
        self._show_traces(case.traces)
        self._show_episodes(case.episodes)
        self._show_fields(case.fields)
        self.open_button.setEnabled(True)

    def _show_fields(self, fields: tuple[tuple[str, str], ...]) -> None:
        while len(self.field_labels) < len(fields):
            label = QLabel(self.field_box)
            label.setObjectName("caseFacts")
            label.setWordWrap(True)
            self._fields.addWidget(label)
            self.field_labels.append(label)
        for label, (name, value) in zip(self.field_labels, fields, strict=False):
            label.setText(f"{name}: {value}")
            label.show()
        for label in self.field_labels[len(fields):]:
            label.hide()
        self.field_box.setVisible(bool(fields))

    def _show_traces(self, traces: Mapping[str, tuple[float | None, ...]] | None) -> None:
        self.trace_box.setVisible(traces is not None)
        self.trace_note.setVisible(traces is None)
        if traces is None:
            return
        for column, line in self.traces.items():
            values = traces.get(column, ())
            info = signal_info(column)
            if any(v is not None for v in values):
                line.set_series(values, None, _trace_description(column, values), label=info.label, color=info.token)
            else:
                line.clear(f"{info.label}: no samples recorded")

    def _show_episodes(self, episodes: tuple[PreviewEpisode, ...] | None) -> None:
        self.episode_list.clear()
        rows = 0
        if episodes is None or not episodes:
            item = QListWidgetItem(EPISODES_ON_OPEN if episodes is None else NO_EPISODES)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.episode_list.addItem(item)
            rows = 1
        else:
            for episode in episodes[:EPISODES_SHOWN]:
                glyph, minutes = severity_glyph(episode.severity), _minutes_text(episode.minutes)
                item = QListWidgetItem(f"{glyph} {episode.label} · {episode.severity} · {minutes}".strip())
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setData(GLYPH_ROLE, glyph)
                item.setData(NAME_ROLE, episode.label)
                item.setData(SEVERITY_ROLE, episode.severity)
                item.setData(RANGE_ROLE, minutes)
                self.episode_list.addItem(item)
            rows = min(len(episodes), EPISODES_SHOWN)
            if len(episodes) > EPISODES_SHOWN:
                more = QListWidgetItem(f"and {len(episodes) - EPISODES_SHOWN} more")
                more.setFlags(Qt.ItemFlag.NoItemFlags)
                self.episode_list.addItem(more)
                rows += 1
        # The app style keeps a 2 px frame so a focus ring never moves the rows; the list is never focused here.
        self.episode_list.setFixedHeight(rows * EPISODE_ROW_HEIGHT + 4)
        self.episode_list.show()
