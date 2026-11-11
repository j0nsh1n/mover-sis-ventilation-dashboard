"""Searchable side drawer that lists the loaded preset's rules inside the research workspace.

Every rule is one row: flag shapes with the rule file's severity words, the name, what it reads, its limits in
words, how long it must hold, and a threshold ruler where the rule has a single numeric limit. Nothing is
copied from the rule file by hand; the words come from ``src.rules`` and the ruler from ``ruler_for``, the
same one the case page uses. The drawer paints and styles from the design tokens only (see ``theme.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAccessible, QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAccessibleWidget,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from src.desktop.fonts import make_tabular
from src.desktop.marks import severity_label
from src.desktop.threshold_ruler import RulerSpec, ThresholdRuler, ruler_for
from src.rules import PARAMETER_METADATA, RuleCatalog, RuleSpec

HOLD_KEY = "min_duration_min"

# The rule file does not group its rules, so the drawer sorts them by the first signal a rule reads. This is
# navigation only; a rule whose signal is not listed here lands in the last group instead of going missing.
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Airway pressure", ("PIP", "PIP_slope", "PEEP")),
    ("Volume and rate", ("TV", "TV_mlkg", "RR")),
    ("Gas", ("ETCO2", "FIO2")),
    ("Anesthetic agent", ("MAC_total_Et", "MAC_Et", "Agent_Et_drift", "Agent_Fi_Et_gap")),
    ("Circulation", ("SPO2", "HR", "nMAP")),
)
PATTERN_GROUP = "Patterns"
OTHER_GROUP = "Other rules"
GROUP_ORDER = (*(name for name, _signals in _GROUPS), PATTERN_GROUP, OTHER_GROUP)

CURRENT_RULE_TEXT = "This is the rule of the selected episode."

_ROLES = {
    "dialog": QAccessible.Role.Dialog,
    "list": QAccessible.Role.List,
    "listitem": QAccessible.Role.ListItem,
    "graphic": QAccessible.Role.Graphic,
}
_INTERFACES: dict[int, QAccessibleWidget] = {}
_FACTORY_INSTALLED = False


def _role_factory(_classname: str, obj):
    name = obj.property("accessibleRoleName") if isinstance(obj, QWidget) else None
    role = _ROLES.get(name) if isinstance(name, str) else None
    if role is None:
        return None
    interface = QAccessibleWidget(obj, role)
    key = id(obj)
    _INTERFACES[key] = interface
    obj.destroyed.connect(lambda _o=None, k=key: _INTERFACES.pop(k, None))
    return interface


def set_accessible_role(widget: QWidget, role: str) -> None:
    """Publish ``widget`` with a role Qt's plain frame does not have (dialog, list, list item, graphic).

    Call it before anything asks for the widget's accessible interface: Qt keeps the first one it builds.
    """
    global _FACTORY_INSTALLED
    if not _FACTORY_INSTALLED:
        QAccessible.installFactory(_role_factory)
        _FACTORY_INSTALLED = True
    widget.setProperty("accessibleRoleName", role)


def _number(value: float) -> str:
    return f"{value:g}"


def _with_unit(text: str, unit: str) -> str:
    """The number, a space, then the unit: the spacing the threshold ruler's own sentence uses."""
    return f"{text} {unit}" if unit else text


def threshold_parts(spec: RuleSpec, *, skip: tuple[str, ...] = ()) -> list[str]:
    """Readable `label value unit` phrases for a rule's numeric parameters, leaving out the keys in ``skip``."""
    parts = []
    for key, value in spec.params.items():
        if key in skip:
            continue
        metadata = PARAMETER_METADATA[key]
        label = metadata.phrase(spec.direction)
        unit = metadata.unit if metadata.unit is not None else spec.unit
        if label.endswith("minute"):
            # "from minute 15" already says what the number counts.
            unit = ""
        if isinstance(value, Mapping):
            shown = ", ".join(f"{name} {_with_unit(_number(item), unit)}" for name, item in value.items()
                              if not isinstance(item, Mapping))
            parts.append(f"{label} {shown}")
        else:
            parts.append(f"{label} {_with_unit(_number(value), unit)}")
    return parts


def hold_text(spec: RuleSpec) -> str:
    """How long the condition must hold, as the rule defines it; never a number the rule does not carry."""
    minutes = spec.params.get(HOLD_KEY)
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool):
        return f"at least {_number(minutes)} min in a row"
    if spec.kind in ("threshold", "band"):
        return "none, each minute is checked on its own"
    if spec.kind == "composite":
        return "set by the rules it combines"
    return "not set in the rule file"


def group_of(spec: RuleSpec) -> str:
    if spec.kind == "composite":
        return PATTERN_GROUP
    first = spec.signals[0] if spec.signals else ""
    return next((name for name, signals in _GROUPS if first in signals), OTHER_GROUP)


def shown_description(spec: RuleSpec) -> str:
    """The rule's own description, or nothing when it only repeats the name (some rules describe themselves by it)."""
    text = spec.description.strip() if spec.description else ""
    return "" if not text or text.casefold() == spec.label.strip().casefold() else spec.description


def detail_lines(spec: RuleSpec) -> list[tuple[str, str]]:
    """The caption and text of each line under the name, in the order the row shows them."""
    lines = [("Signals", ", ".join(spec.signals))]
    if spec.composition:
        lines.append(("Combines", spec.composition))
    parts = threshold_parts(spec, skip=(HOLD_KEY,))
    if parts or not spec.composition:
        lines.append(("Thresholds", " · ".join(parts) if parts else "none"))
    lines.append(("Hold time", hold_text(spec)))
    if spec.skip_phases:
        lines.append(("Skipped in", ", ".join(spec.skip_phases)))
    return lines


def no_ruler_reason(spec: RuleSpec) -> str:
    """Why this rule has no ruler, said from what the rule itself defines and never from a list of rule ids."""
    hold = spec.params.get(HOLD_KEY)
    others = threshold_parts(spec, skip=(HOLD_KEY,))
    if spec.kind == "composite":
        return "No ruler: this pattern has no limit of its own; it fires when the rules it combines do."
    if spec.kind == "band" and "low" in spec.params and "high" in spec.params:
        edges = " or ".join(threshold_parts(replace(spec, params={k: spec.params[k] for k in ("low", "high")})))
        return f"No ruler: it flags a value {edges}, a band and not one limit."
    if isinstance(hold, (int, float)) and not isinstance(hold, bool) and others and spec.kind == "duration":
        what = "the conditions above" if len(others) > 1 else "the limit above"
        return f"No ruler: one value alone does not fire it. It needs {what} to hold {hold_text(spec)}."
    if spec.kind in ("threshold", "slope"):
        return "No ruler: the rule file gives this rule no numeric limit to draw."
    return "No ruler: this rule has no single numeric limit."


def ruler_or_reason(spec: RuleSpec) -> RulerSpec | str:
    """The ruler of ``spec``, or the reason it has none. The one place that decides who gets a ruler.

    ``ruler_for`` draws one for the rules defined by their limits (a threshold rule's warn and critical, a slope
    rule's slope). A duration rule is defined by how long its conditions last, so it gets none even when one of
    its conditions is a single limit; a band and a composite have no single limit at all.
    """
    return ruler_for(spec, None, episode=False) or no_ruler_reason(spec)


def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "?", "!")) else f"{text}."


def rule_sentence(spec: RuleSpec, *, current: bool = False) -> str:
    """Everything the row shows, in the order it shows it, as one sentence a screen reader reads."""
    pieces = [CURRENT_RULE_TEXT] if current else []
    pieces += [f"Id: {spec.id}.", _sentence(spec.label), f"Severity: {', '.join(spec.severities)}."]
    description = shown_description(spec)
    if description:
        pieces.append(_sentence(description))
    for caption, text in detail_lines(spec):
        pieces.append(_sentence(f"{caption}: {text.replace(' · ', '; ')}"))
    ruler = ruler_or_reason(spec)
    pieces.append(_sentence(ruler if isinstance(ruler, str) else ruler.description()))
    return " ".join(pieces)


def _caption_row(layout: QGridLayout, row: int, caption: str, text: str, *, tabular: bool = False) -> QLabel:
    head = QLabel(caption)
    head.setObjectName("ruleCaption")
    head.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    body = QLabel(text)
    body.setObjectName("ruleDetail")
    body.setWordWrap(True)
    body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if tabular:
        make_tabular(body)
    layout.addWidget(head, row, 0)
    layout.addWidget(body, row, 1)
    return body


class RuleRow(QFrame):
    """One rule. It takes focus, so a keyboard or screen-reader user can reach each rule, and reads as one sentence."""

    focused = Signal()
    moved = Signal(int)

    def __init__(self, spec: RuleSpec) -> None:
        super().__init__()
        self.setObjectName("ruleRow")
        self.setProperty("highlight", False)
        self.setProperty("ruleId", spec.id)
        self._spec = spec
        set_accessible_role(self, "listitem")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(rule_sentence(spec))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.current_label = QLabel(f"▸ {CURRENT_RULE_TEXT}")
        self.current_label.setObjectName("ruleCurrent")
        self.current_label.setWordWrap(True)
        self.current_label.hide()
        layout.addWidget(self.current_label)
        # The id sits above the name, as in the design, so a long id never widens the row beside the name.
        rule_id = QLabel(spec.id)
        rule_id.setObjectName("ruleId")
        layout.addWidget(rule_id)
        name = QLabel(spec.label)
        name.setObjectName("ruleName")
        name.setWordWrap(True)
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(name)
        severities = QLabel("   ".join(severity_label(word) for word in spec.severities))
        severities.setObjectName("ruleSeverity")
        layout.addWidget(severities)
        if shown_description(spec):
            summary = QLabel(spec.description)
            summary.setObjectName("ruleSummary")
            summary.setWordWrap(True)
            summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(summary)
        details = QGridLayout()
        details.setContentsMargins(0, 4, 0, 0)
        details.setHorizontalSpacing(12)
        details.setVerticalSpacing(3)
        details.setColumnMinimumWidth(0, 78)
        details.setColumnStretch(1, 1)
        for line, (caption, text) in enumerate(detail_lines(spec)):
            _caption_row(details, line, caption, text, tabular=caption in ("Thresholds", "Hold time"))
        layout.addLayout(details)
        ruler_spec = ruler_or_reason(spec)
        if isinstance(ruler_spec, RulerSpec):
            self.ruler: ThresholdRuler | None = ThresholdRuler()
            set_accessible_role(self.ruler, "graphic")
            layout.addWidget(self.ruler)
            # The ruler hides itself until it has a spec, so it is given one only once it has a parent.
            self.ruler.set_ruler(ruler_spec)
            self.ruler.setAccessibleName(f"Threshold ruler, {spec.label}")
        else:
            self.ruler = None
            note = QLabel(ruler_spec)
            note.setObjectName("ruleNoRuler")
            note.setWordWrap(True)
            layout.addWidget(note)

    def set_current(self, current: bool) -> None:
        """Mark this as the rule of the selected episode: tint and bar from the style, words and a shape here."""
        self.setProperty("highlight", current)
        self.current_label.setVisible(current)
        self.setAccessibleName(rule_sentence(self._spec, current=current))
        # The labels' colours depend on this property through their ancestor, so they are polished again too.
        for widget in (self, *self.findChildren(QLabel)):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self.focused.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            event.accept()
            self.moved.emit(-1 if event.key() == Qt.Key.Key_Up else 1)
            return
        super().keyPressEvent(event)


class RulesDrawer(QFrame):
    closed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        fallback_focus: Callable[[], QWidget | None] | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("rulesDrawer")
        set_accessible_role(self, "dialog")
        self.setAccessibleName("Rule reference")
        self._fallback_focus = fallback_focus
        self._catalog: RuleCatalog | None = None
        self._recorded = True
        self._thresholds_sha256: str | None = None
        self._error = ""
        self._rows: dict[str, RuleRow] = {}
        self._group_labels: dict[str, QLabel] = {}
        self._group_members: dict[str, list[str]] = {}
        self._built_for: RuleCatalog | None = None
        self._highlighted: RuleRow | None = None
        self._opener: QWidget | None = None
        self._escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), parent or self)
        self._escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._escape_shortcut.setEnabled(False)
        self._escape_shortcut.activated.connect(self.close_drawer)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 12, 12)
        layout.setSpacing(8)
        top = QHBoxLayout()
        title = QLabel("Rule reference")
        title.setObjectName("drawerTitle")
        top.addWidget(title, 1)
        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("linkBtn")
        self.close_button.setAutoDefault(True)
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
        make_tabular(self.count_label)
        layout.addWidget(self.count_label)
        self.keys_label = QLabel("Tab or ↑ ↓ move between rules · Esc closes")
        self.keys_label.setObjectName("quietText")
        self.keys_label.setWordWrap(True)
        layout.addWidget(self.keys_label)
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
        self.clear_button.setAutoDefault(True)
        self.clear_button.clicked.connect(self.clear_search)
        empty_layout.addWidget(self.clear_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.empty_state.hide()
        layout.addWidget(self.empty_state)

        self.rule_scroll = QScrollArea()
        self.rule_scroll.setObjectName("rulesScroll")
        self.rule_scroll.setAccessibleName("Rule list")
        self.rule_scroll.setWidgetResizable(True)
        self.rule_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Each rule is its own tab stop, so the scroll area is not another one.
        self.rule_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list = QWidget()
        self._list.setObjectName("rulesList")
        set_accessible_role(self._list, "list")
        self._list.setAccessibleName("Rules")
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 8, 0)
        self._list_layout.setSpacing(0)
        self.scoring_button, self.scoring_text = self._explanation("Scoring")
        self.mac_button, self.mac_text = self._explanation("Age-40 MAC (vol %)")
        self._list_layout.addStretch(1)
        self.rule_scroll.setWidget(self._list)
        layout.addWidget(self.rule_scroll, 1)
        # Without a stretch the hidden list's space is spread between the other widgets.
        layout.addStretch(0)

        self.setTabOrder(self.close_button, self.search_edit)
        self.setTabOrder(self.search_edit, self.clear_button)
        self._order_tab_stops()
        self.hide()
        self._refresh_header()
        self._apply_search()

    def _explanation(self, title: str) -> tuple[QPushButton, QLabel]:
        button = QPushButton(title + " ▸")
        button.setObjectName("drawerToggle")
        button.setAutoDefault(True)
        button.setCheckable(True)
        button.setAccessibleName(title)
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.hide()
        button.toggled.connect(label.setVisible)
        button.toggled.connect(lambda expanded: button.setText(title + (" ▾" if expanded else " ▸")))
        self._list_layout.addWidget(button)
        self._list_layout.addWidget(label)
        return button, label

    def set_explanations(self, thresholds: dict) -> None:
        for label, key in ((self.scoring_text, "scoring"), (self.mac_text, "mac_age40")):
            label.setText("\n".join(f"{name}: {value}" for name, value in thresholds.get(key, {}).items()))

    @property
    def is_open(self) -> bool:
        return not self.isHidden()

    def visible_rule_ids(self) -> list[str]:
        return [rule_id for rule_id, row in self._rows.items() if not row.isHidden()]

    def set_thresholds_sha256(self, value: str | None) -> None:
        self._thresholds_sha256 = value
        self._refresh_header()

    def set_catalog(self, catalog: RuleCatalog | None, *, recorded: bool = True,
                    thresholds_sha256: str | None = None, error: str = "") -> None:
        changed = catalog != self._catalog
        self._catalog, self._recorded, self._error = catalog, recorded, error
        self._thresholds_sha256 = thresholds_sha256
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
        self._refresh_header()
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
        current = catalog.refreshed()
        if current is not catalog:
            self._catalog = catalog = current
            if self.is_open:
                self._build_rows()
                self._apply_search()
        lines = list(catalog.thresholds_provenance(self._thresholds_sha256))
        if not self._recorded:
            lines.append("Preset not recorded with this data; using default.")
        self.header_label.setText("\n".join(lines))

    def _build_rows(self) -> None:
        if self._built_for is not None and self._built_for == self._catalog and self._rows:
            return
        for widget in (*self._rows.values(), *self._group_labels.values()):
            self._list_layout.removeWidget(widget)
            widget.deleteLater()
        self._rows = {}
        self._group_labels = {}
        self._group_members = {}
        self._highlighted = None
        if self._catalog is not None:
            # A stable sort: inside a group the rules keep the rule file's order.
            ordered = sorted(self._catalog.specs, key=lambda spec: GROUP_ORDER.index(group_of(spec)))
            index = 0
            for spec in ordered:
                group = group_of(spec)
                if group not in self._group_labels:
                    heading = QLabel()
                    heading.setObjectName("ruleGroup")
                    make_tabular(heading)
                    self._group_labels[group] = heading
                    self._group_members[group] = []
                    self._list_layout.insertWidget(index, heading)
                    index += 1
                row = RuleRow(spec)
                row.focused.connect(lambda row=row: self._reveal(row))
                row.moved.connect(lambda step, row=row: self._move_focus(row, step))
                self._rows[spec.id] = row
                self._group_members[group].append(spec.id)
                self._list_layout.insertWidget(index, row)
                index += 1
        self._built_for = self._catalog
        self._order_tab_stops()

    def _order_tab_stops(self) -> None:
        """Tab runs down the screen: Close, search, the rules in order, then the two reference toggles."""
        chain = [self.clear_button, *self._rows.values(), self.scoring_button, self.mac_button]
        for before, after in zip(chain, chain[1:], strict=False):
            self.setTabOrder(before, after)

    def _reveal(self, row: RuleRow) -> None:
        if isValid(row):
            self.rule_scroll.ensureWidgetVisible(row, 0, 24)

    def _move_focus(self, row: RuleRow, step: int) -> None:
        shown = [other for other in self._rows.values() if not other.isHidden()]
        if row in shown:
            target = shown[min(max(shown.index(row) + step, 0), len(shown) - 1)]
            target.setFocus(Qt.FocusReason.TabFocusReason)

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
        for group, heading in self._group_labels.items():
            members = self._group_members[group]
            shown = sum(rule_id in matches for rule_id in members)
            heading.setText(f"{group} · {shown}" if shown == len(members) else f"{group} · {shown} of {len(members)}")
            heading.setVisible(shown > 0)
        empty = not matches
        self.empty_state.setVisible(empty)
        self.clear_button.show()
        self.rule_scroll.setVisible(not empty)
        if empty:
            self.empty_label.setText(f"No rule matches “{text.strip()}”.")
        else:
            self.rule_scroll.verticalScrollBar().setValue(0)

    def _set_highlight(self, row: RuleRow | None) -> None:
        previous = self._highlighted
        self._highlighted = row
        for frame, state in ((previous, False), (row, True)):
            if frame is not None and isValid(frame):
                frame.set_current(state)

    def _scroll_to(self, row: RuleRow) -> None:
        self._list_layout.activate()
        self._list.adjustSize()
        self.rule_scroll.verticalScrollBar().setValue(row.y() - 8)
