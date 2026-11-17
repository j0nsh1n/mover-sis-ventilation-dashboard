"""The Cases page's preview panel: the current row's case at a glance, drawn only from data already in memory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd
from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from src.desktop.case_page import EPISODE_ROW_HEIGHT, GLYPH_ROLE, NAME_ROLE, RANGE_ROLE, SEVERITY_ROLE, EpisodeDelegate
from src.desktop.charts import signal_info
from src.desktop.evidence_trail import Sparkline
from src.desktop.fonts import tabular
from src.desktop.marks import severity_glyph
from src.desktop.theme import token_color

# Narrow enough that the list beside it keeps readable Procedure and Top rules columns in the default window.
PREVIEW_WIDTH = 270
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


_ROW_PAD_X = 8
_ROW_PAD_Y = 6
_GLYPH_COLUMN = 30
_NAME_GAP = 2
_QUIET_PIXELS = 12
_WRAP = int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignLeft) | int(Qt.AlignmentFlag.AlignTop)


class PreviewEpisodeDelegate(EpisodeDelegate):
    """A rule episode in the narrow preview: the rule's name wraps onto as many lines as it needs and is never cut,
    with its severity word and minutes on a quiet line under it."""

    def _quiet_font(self, font: QFont) -> QFont:
        quiet = QFont(font)
        quiet.setPixelSize(_QUIET_PIXELS)
        return tabular(quiet)

    def _name_width(self, row_width: int) -> int:
        return max(row_width - 2 * _ROW_PAD_X - _GLYPH_COLUMN, 24)

    def name_height(self, name: str, row_width: int, font: QFont) -> int:
        return QFontMetrics(font).boundingRect(QRect(0, 0, self._name_width(row_width), 10_000), _WRAP, name).height()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:  # noqa: N802
        name = index.data(NAME_ROLE)
        view = self.parent()
        width = view.viewport().width() if isinstance(view, QListView) else option.rect.width()
        if name is None:
            return QSize(width, EPISODE_ROW_HEIGHT)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        quiet = QFontMetrics(self._quiet_font(opt.font)).height()
        return QSize(width, 2 * _ROW_PAD_Y + self.name_height(str(name), width, opt.font) + _NAME_GAP + quiet)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        name = index.data(NAME_ROLE)
        if name is None:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect = opt.rect
        painter.save()
        painter.setFont(opt.font)
        painter.setPen(token_color("ink"))
        top = rect.top() + _ROW_PAD_Y
        line = painter.fontMetrics().height()
        painter.drawText(QRect(rect.left() + _ROW_PAD_X + 2, top, _GLYPH_COLUMN, line),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), str(index.data(GLYPH_ROLE) or ""))
        name_left = rect.left() + _ROW_PAD_X + _GLYPH_COLUMN
        name_height = self.name_height(str(name), rect.width(), opt.font)
        painter.drawText(QRect(name_left, top, self._name_width(rect.width()), name_height), _WRAP, str(name))
        painter.setFont(self._quiet_font(opt.font))
        painter.setPen(token_color("ink_3"))
        quiet = " · ".join(part for part in (str(index.data(SEVERITY_ROLE) or ""), str(index.data(RANGE_ROLE) or "")) if part)
        painter.drawText(QRect(name_left, top + name_height + _NAME_GAP, self._name_width(rect.width()),
                               painter.fontMetrics().height()),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), quiet)
        painter.restore()


class _EpisodeRows(QListWidget):
    """The preview's episodes, always shown whole: its height follows its rows, which grow when a name wraps."""

    def fit_height(self) -> None:
        self.doItemsLayout()
        rows = sum(self.sizeHintForRow(row) for row in range(self.count()))
        # The app style keeps a 2 px frame so a focus ring never moves the rows; the list is never focused here.
        self.setFixedHeight(rows + 4)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.fit_height()


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
            line = Sparkline(label_room=True)
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
        self.episode_list = _EpisodeRows()
        self.episode_list.setObjectName("previewEpisodes")
        self.episode_list.setAccessibleName("Rule episodes")
        self.episode_list.setItemDelegate(PreviewEpisodeDelegate(self.episode_list))
        self.episode_list.setResizeMode(QListView.ResizeMode.Adjust)
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
        if episodes is None or not episodes:
            item = QListWidgetItem(EPISODES_ON_OPEN if episodes is None else NO_EPISODES)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.episode_list.addItem(item)
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
            if len(episodes) > EPISODES_SHOWN:
                more = QListWidgetItem(f"and {len(episodes) - EPISODES_SHOWN} more")
                more.setFlags(Qt.ItemFlag.NoItemFlags)
                self.episode_list.addItem(more)
        self.episode_list.show()
        self.episode_list.fit_height()
