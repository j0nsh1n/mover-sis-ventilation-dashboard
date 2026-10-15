"""Searchable side drawer that lists the loaded preset's rules inside the research workspace."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from html import escape

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from src.rules import PARAMETER_METADATA, RuleCatalog, RuleSpec

STYLE = """
QFrame#rulesDrawer { background: #fbfaf6; border: 0; border-left: 1px solid #b4afba; }
QFrame#rulesDrawer QLabel { color: #302b35; background: transparent; }
QFrame#rulesDrawer QLabel#drawerKicker { color: #5d4378; font-size: 11px; font-weight: bold; }
QFrame#rulesDrawer QLabel#drawerTitle { font-family: Georgia; font-size: 22px; color: #302637; }
QFrame#rulesDrawer QLabel#drawerMeta { color: #4b4352; }
QFrame#rulesDrawer QLabel#drawerCount { color: #4b4352; font-weight: bold; }
QFrame#rulesDrawer QLabel#drawerNotice { color: #5a3a14; background: #f2e3d3;
    border: 1px solid #ddc6af; border-radius: 5px; padding: 6px 8px; }
QFrame#rulesDrawer QLabel#ruleSummary { color: #4b4352; }
QFrame#rulesDrawer QLabel#ruleDetail { color: #302b35; }
QFrame#rulesDrawer QLineEdit#ruleSearch { background: #fffefa; color: #302b35;
    border: 1px solid #7d7287; border-radius: 5px; padding: 8px;
    placeholder-text-color: #5f566a; }
QFrame#rulesDrawer QLineEdit#ruleSearch:focus { border: 2px solid #5d4378; padding: 7px; }
QFrame#rulesDrawer QPushButton { background: #f8f7f3; color: #4b3d5d;
    border: 1px solid #7d7287; border-radius: 5px; padding: 8px 12px; }
QFrame#rulesDrawer QPushButton:focus { border: 2px solid #5d4378; padding: 7px 11px; }
QFrame#rulesDrawer QPushButton:disabled { color: #5f566a; }
QFrame#rulesDrawer QScrollArea#rulesScroll { background: #fbfaf6; border: 1px solid #fbfaf6; }
QFrame#rulesDrawer QScrollArea#rulesScroll:focus { border: 2px solid #5d4378; }
QFrame#rulesDrawer QWidget#rulesList { background: #fbfaf6; }
QFrame#rulesDrawer QFrame#ruleRow { background: #fffefa; border: 1px solid #d2cbd3; border-radius: 7px; }
QFrame#rulesDrawer QFrame#ruleRow[highlight="true"] { background: #f2e3d3;
    border: 2px solid #5d4378; }
QFrame#rulesDrawer QWidget#emptyState { background: #fbfaf6; }
"""


def _number(value: float) -> str:
    return f"{value:g}"


def _with_unit(text: str, unit: str) -> str:
    if not unit:
        return text
    return f"{text}{unit}" if unit.startswith("/") else f"{text} {unit}"


def threshold_parts(spec: RuleSpec) -> list[str]:
    """Readable `label value unit` phrases for a rule's numeric parameters."""
    parts = []
    for key, value in spec.params.items():
        metadata = PARAMETER_METADATA[key]
        label = metadata.phrase(spec.direction)
        unit = metadata.unit if metadata.unit is not None else spec.unit
        if isinstance(value, Mapping):
            shown = ", ".join(f"{name} {_with_unit(_number(item), unit)}" for name, item in value.items()
                              if not isinstance(item, Mapping))
            parts.append(f"{label} {shown}")
        else:
            parts.append(f"{label} {_with_unit(_number(value), unit)}")
    return parts


def _detail(caption: str, text: str) -> QLabel:
    label = QLabel(f"<span style='color:#4b4352'>{escape(caption)}</span>&nbsp; {escape(text)}")
    label.setObjectName("ruleDetail")
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _rule_row(spec: RuleSpec) -> QFrame:
    row = QFrame()
    row.setObjectName("ruleRow")
    row.setProperty("highlight", False)
    row.setProperty("ruleId", spec.id)
    layout = QVBoxLayout(row)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(4)
    title = QLabel(
        f"<b style='font-size:14px'>{escape(spec.label)}</b>"
        f"&nbsp; <span style='color:#4b4352;font-size:11px'>{escape(spec.id)}</span>"
    )
    title.setTextFormat(Qt.TextFormat.RichText)
    title.setWordWrap(True)
    title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(title)
    if spec.description:
        summary = QLabel(spec.description)
        summary.setObjectName("ruleSummary")
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(summary)
    layout.addWidget(_detail("Signals", ", ".join(spec.signals)))
    parts = threshold_parts(spec)
    if spec.composition:
        layout.addWidget(_detail("Combines", spec.composition))
    if parts or not spec.composition:
        layout.addWidget(_detail("Thresholds", " · ".join(parts) if parts else "none"))
    if spec.skip_phases:
        layout.addWidget(_detail("Skipped in", ", ".join(spec.skip_phases)))
    return row


class RulesDrawer(QFrame):
    closed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        fallback_focus: Callable[[], QWidget | None] | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("rulesDrawer")
        self.setStyleSheet(STYLE)
        self.setAccessibleName("Rules")
        self._fallback_focus = fallback_focus
        self._catalog: RuleCatalog | None = None
        self._recorded = True
        self._error = ""
        self._rows: dict[str, QFrame] = {}
        self._built_for: RuleCatalog | None = None
        self._highlighted: QFrame | None = None
        self._opener: QWidget | None = None
        self._escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), parent or self)
        self._escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._escape_shortcut.setEnabled(False)
        self._escape_shortcut.activated.connect(self.close_drawer)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        top = QHBoxLayout()
        heading = QVBoxLayout()
        kicker = QLabel("RULES")
        kicker.setObjectName("drawerKicker")
        heading.addWidget(kicker)
        title = QLabel("Rule reference")
        title.setObjectName("drawerTitle")
        heading.addWidget(title)
        top.addLayout(heading, 1)
        self.close_button = QPushButton("Close")
        self.close_button.setAccessibleName("Close rules drawer")
        self.close_button.clicked.connect(self.close_drawer)
        top.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        self.header_label = QLabel()
        self.header_label.setObjectName("drawerMeta")
        self.header_label.setWordWrap(True)
        self.header_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.header_label)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("ruleSearch")
        self.search_edit.setPlaceholderText("Search rules, signals, units")
        self.search_edit.setAccessibleName("Search rules")
        self.search_edit.setClearButtonEnabled(False)
        self.search_edit.setMinimumHeight(40)
        self.search_edit.textChanged.connect(self._apply_search)
        layout.addWidget(self.search_edit)

        self.count_label = QLabel()
        self.count_label.setObjectName("drawerCount")
        self.count_label.setAccessibleName("Rule count")
        layout.addWidget(self.count_label)
        self.notice_label = QLabel()
        self.notice_label.setObjectName("drawerNotice")
        self.notice_label.setWordWrap(True)
        self.notice_label.hide()
        layout.addWidget(self.notice_label)

        self.empty_state = QWidget()
        self.empty_state.setObjectName("emptyState")
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(0, 8, 0, 8)
        self.empty_label = QLabel()
        self.empty_label.setWordWrap(True)
        empty_layout.addWidget(self.empty_label)
        self.clear_button = QPushButton("Clear search")
        self.clear_button.clicked.connect(self.clear_search)
        empty_layout.addWidget(self.clear_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.empty_state.hide()
        layout.addWidget(self.empty_state)

        self.rule_scroll = QScrollArea()
        self.rule_scroll.setObjectName("rulesScroll")
        self.rule_scroll.setAccessibleName("Rule list")
        self.rule_scroll.setWidgetResizable(True)
        self.rule_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rule_scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._list = QWidget()
        self._list.setObjectName("rulesList")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch(1)
        self.rule_scroll.setWidget(self._list)
        layout.addWidget(self.rule_scroll, 1)
        # Without a stretch the hidden list's space is spread between the other widgets.
        layout.addStretch(0)

        self.setTabOrder(self.close_button, self.search_edit)
        self.setTabOrder(self.search_edit, self.clear_button)
        self.setTabOrder(self.clear_button, self.rule_scroll)
        self.hide()
        self._refresh_header()
        self._apply_search()

    @property
    def is_open(self) -> bool:
        return not self.isHidden()

    def visible_rule_ids(self) -> list[str]:
        return [rule_id for rule_id, row in self._rows.items() if not row.isHidden()]

    def set_catalog(self, catalog: RuleCatalog | None, *, recorded: bool = True, error: str = "") -> None:
        changed = catalog != self._catalog
        self._catalog, self._recorded, self._error = catalog, recorded, error
        self._refresh_header()
        if self.is_open:
            self._build_rows()
            self._apply_search()
        elif changed:
            self._built_for = None

    def search_text(self) -> str:
        return self.search_edit.text()

    def clear_search(self) -> None:
        self.search_edit.clear()
        self.search_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def open(self, opener: QWidget | None = None) -> None:
        if not self.is_open:
            focus = opener or QApplication.focusWidget()
            self._opener = None if focus is not None and self.isAncestorOf(focus) else focus
            self._build_rows()
            self._apply_search()
            self.show()
        self._escape_shortcut.setEnabled(True)
        if self.search_edit.isEnabled():
            self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.search_edit.selectAll()
        else:
            self.close_button.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def close_drawer(self) -> None:
        if not self.is_open:
            return
        self._set_highlight(None)
        self._escape_shortcut.setEnabled(False)
        self.hide()
        self.notice_label.hide()
        target = self._opener
        self._opener = None
        if target is None or not isValid(target) or not target.isVisible() or not target.isEnabled():
            target = self._fallback_focus() if self._fallback_focus else None
        if target is not None:
            target.setFocus(Qt.FocusReason.OtherFocusReason)
        self.closed.emit()

    def show_rule(self, rule_id: str, opener: QWidget | None = None) -> None:
        self.open(opener)
        self.notice_label.hide()
        row = self._rows.get(rule_id)
        if row is None:
            self.search_edit.clear()
            self._set_highlight(None)
            if self._catalog is not None:
                self.notice_label.setText(f"No rule has the id {rule_id!r}. Showing all rules.")
                self.notice_label.show()
            return
        if self.search_edit.text() and row.isHidden():
            self.search_edit.clear()
        self._set_highlight(row)
        self._scroll_to(row)
        QTimer.singleShot(0, lambda: self._scroll_to(row) if isValid(row) and not row.isHidden() else None)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.close_drawer()
            return
        super().keyPressEvent(event)

    def _refresh_header(self) -> None:
        catalog = self._catalog
        if catalog is None:
            text = ("Threshold config invalid." if self._error else "No rules loaded.")
            self.header_label.setText(text)
            return
        provenance = "" if self._recorded else ", not recorded with this data"
        self.header_label.setText(
            f"Preset: {catalog.preset} · thresholds {catalog.thresholds_sha256[:12]}{provenance}"
        )

    def _build_rows(self) -> None:
        if self._built_for is not None and self._built_for == self._catalog and self._rows:
            return
        for row in self._rows.values():
            self._list_layout.removeWidget(row)
            row.deleteLater()
        self._rows = {}
        self._highlighted = None
        if self._catalog is not None:
            for index, spec in enumerate(self._catalog.specs):
                row = _rule_row(spec)
                self._rows[spec.id] = row
                self._list_layout.insertWidget(index, row)
        self._built_for = self._catalog

    def _apply_search(self) -> None:
        self.notice_label.hide()
        self._set_highlight(None)
        catalog = self._catalog
        if catalog is None:
            self.count_label.setText("Rules unavailable")
            self.empty_label.setText(
                f"Threshold config invalid: {self._error}" if self._error
                else "No rule catalog has been loaded yet."
            )
            self.empty_state.show()
            self.clear_button.hide()
            self.search_edit.setEnabled(False)
            self.rule_scroll.hide()
            return
        self.search_edit.setEnabled(True)
        text = self.search_edit.text()
        total = len(catalog.specs)
        if text.strip():
            matches = {spec.id for spec in catalog.search(text)}
            self.count_label.setText(f"{len(matches)} of {total} rules")
        else:
            matches = {spec.id for spec in catalog.specs}
            self.count_label.setText(f"{total} rules")
        for rule_id, row in self._rows.items():
            row.setVisible(rule_id in matches)
        empty = not matches
        self.empty_state.setVisible(empty)
        self.clear_button.show()
        self.rule_scroll.setVisible(not empty)
        if empty:
            self.empty_label.setText(f"No rule matches “{text.strip()}”.")
        else:
            self.rule_scroll.verticalScrollBar().setValue(0)

    def _set_highlight(self, row: QFrame | None) -> None:
        previous = self._highlighted
        self._highlighted = row
        for frame, state in ((previous, False), (row, True)):
            if frame is not None and isValid(frame):
                frame.setProperty("highlight", state)
                frame.style().unpolish(frame)
                frame.style().polish(frame)

    def _scroll_to(self, row: QFrame) -> None:
        self._list_layout.activate()
        self._list.adjustSize()
        self.rule_scroll.verticalScrollBar().setValue(row.y() - 8)
