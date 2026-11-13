"""Keyboard-operable practice for one fictional checklist card: an index card, three stages, a ruled list of lines."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QFont,
    QFontMetrics,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPen,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from src.desktop.accessibility import announce, name_combo
from src.desktop.scroll_safe_combo import ScrollSafeComboBox
from src.desktop.theme import token_color
from src.modules.who_checklist.model import (
    ITEM_CARD_FIELD,
    MARK_DONE,
    MARK_NOT_ON_CARD,
    STAGES,
    CheckItem,
    Scenario,
    apply_mark,
    card_lines,
    debrief,
    open_stage,
    save_practice,
    scenarios,
    stage_progress,
)

NOTICE = "These cards are invented. This page does not tell you what to do for a patient."
INSTRUCTION = "Read each line from the card aloud, then tick it."
KEYS = "Space ticks · arrow keys move between lines · Tab moves on"
SAVE_FAILED = "This practice could not be saved on this computer."
# Page width from which the card sits beside the lines; below it the card goes above them.
SIDE_BY_SIDE_WIDTH = 860
CARD_WIDTH = 340
# Width of the lines' column from which they run in two columns.
TWO_COLUMNS_WIDTH = 600
_GAP = 48


def _expected(item: CheckItem) -> str:
    return MARK_DONE if item.required else MARK_NOT_ON_CARD


class ItemBox(QCheckBox):
    """One line of the card as a real check box that wraps its text and says which line of the card it reads.

    A done line is a filled box with a tick, a line that is not on the card has a dashed box and slanted text, and
    a closed line is quiet: each shows by shape or style, never by colour alone. The arrow keys ask the page to
    move; Space ticks.
    """

    move_requested = Signal(int, int)  # columns right, rows down

    _BOX = 20
    _PAD_X = 8
    _PAD_Y = 14
    _GAP = 12
    _MIN_HEIGHT = 52

    def __init__(self, text: str, tag: str, not_on_card: bool) -> None:
        super().__init__(text)
        self._tag = tag
        self._not_on_card = not_on_card
        self.setAccessibleName(text)
        self.setAccessibleDescription(f"Reads the card line: {tag}")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _text_font(self) -> QFont:
        font = QFont(self.font())
        font.setItalic(self._not_on_card)
        return font

    def _tag_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(12)
        return font

    def _text_width(self, width: int) -> int:
        tag = QFontMetrics(self._tag_font()).horizontalAdvance(self._tag) + self._GAP if self._tag else 0
        return max(width - 2 * self._PAD_X - self._BOX - self._GAP - tag, 40)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        flags = int(Qt.TextFlag.TextWordWrap)
        text = QFontMetrics(self._text_font()).boundingRect(QRect(0, 0, self._text_width(width), 100000), flags,
                                                            self.text())
        return max(text.height() + 2 * self._PAD_Y, self._MIN_HEIGHT)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(300, self.heightForWidth(300))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(120, self._MIN_HEIGHT)

    def hitButton(self, pos: QPoint) -> bool:  # noqa: N802
        return self.rect().contains(pos)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        moves = {
            Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1),
        }
        move = moves.get(Qt.Key(event.key()))
        if move is None:
            super().keyPressEvent(event)
            return
        self.move_requested.emit(*move)
        event.accept()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self.updateGeometry()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled, done = self.isEnabled(), self.isChecked()
        painter.setPen(QPen(token_color("rule_faint"), 1))
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        box = QRectF(self._PAD_X, (self.height() - self._BOX) / 2, self._BOX, self._BOX)
        edge = QPen(token_color("rule_strong"), 1.5)
        if self._not_on_card:
            edge.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(edge)
        if done and not self._not_on_card:
            painter.setBrush(token_color("ink_2"))
            painter.setPen(QPen(token_color("ink_2"), 1.5))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box.adjusted(0.75, 0.75, -0.75, -0.75), 3, 3)
        if done:
            mark = token_color("paper" if not self._not_on_card else "ink_2")
            painter.setPen(QPen(mark, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            x, y = box.left(), box.top()
            painter.drawPolyline(QPolygonF([QPointF(x + 5, y + 10.5), QPointF(x + 8.5, y + 14), QPointF(x + 15, y + 6.5)]))
        text_left = self._PAD_X + self._BOX + self._GAP
        text_rect = QRect(text_left, self._PAD_Y // 2, self._text_width(self.width()), self.height() - self._PAD_Y)
        painter.setFont(self._text_font())
        if not enabled or self._not_on_card:
            painter.setPen(token_color("ink_3"))
        else:
            painter.setPen(token_color("ink_2" if done else "ink"))
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap)
        painter.drawText(text_rect, flags, self.text())
        if self._tag:
            painter.setFont(self._tag_font())
            painter.setPen(token_color("ink_3"))
            tag_rect = QRect(0, 0, self.width() - self._PAD_X, self.height())
            painter.drawText(tag_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), self._tag)
        if self.hasFocus():
            painter.setPen(QPen(token_color("accent"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -2), 3, 3)


class StagePage(QWidget):
    """The lines of one stage, in one or two columns, in the order the card gives them."""

    def __init__(self) -> None:
        super().__init__()
        self.boxes: list[ItemBox] = []
        self._columns = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(32)
        self._grid.setVerticalSpacing(0)

    def add(self, box: ItemBox) -> None:
        self.boxes.append(box)
        box.move_requested.connect(lambda dx, dy, source=box: self.move_focus(source, dx, dy))

    def set_columns(self, columns: int) -> None:
        if columns == self._columns:
            return
        self._columns = columns
        for box in self.boxes:
            self._grid.removeWidget(box)
        for index, box in enumerate(self.boxes):
            self._grid.addWidget(box, index // columns, index % columns)
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def move_focus(self, source: ItemBox, dx: int, dy: int) -> None:
        """Move focus along the grid: left and right to the neighbouring line, up and down to the line above or below."""
        columns = max(self._columns, 1)
        index = self.boxes.index(source) + dx + dy * columns
        if 0 <= index < len(self.boxes) and self.boxes[index].isEnabled():
            self.boxes[index].setFocus(Qt.FocusReason.TabFocusReason)


class WhoPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("who-checklist")
        self._scenarios = scenarios()
        self._scenario = self._scenarios[0]
        self._marks: dict[str, str] = {}
        self._boxes: dict[str, ItemBox] = {}
        self._pages: dict[str, StagePage] = {}
        self._open_stage: str | None = STAGES[0]
        self._wide: bool | None = None
        self._columns = 0

        self.scenario_box = ScrollSafeComboBox()
        name_combo(self.scenario_box, "Fictional checklist card")
        for scenario in self._scenarios:
            self.scenario_box.addItem(scenario.title, scenario.scenario_id)
        self.scenario_box.currentIndexChanged.connect(self._select_scenario)

        self.card = QFrame()
        self.card.setObjectName("indexCard")
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(20, 16, 20, 10)
        self.card_layout.setSpacing(8)
        self.card_title = QLabel()
        self.card_title.setObjectName("cardTitle")
        self.card_title.setWordWrap(True)
        self.card_grid = QGridLayout()
        self.card_grid.setHorizontalSpacing(0)
        self.card_grid.setVerticalSpacing(0)
        self.card_grid.setColumnStretch(1, 1)
        self.card_layout.addWidget(self.card_title)
        self.card_layout.addLayout(self.card_grid)
        # The authored sentence, shown only when a card's lines cannot be read out of it.
        self.briefing = QLabel()
        self.briefing.setObjectName("bodyText")
        self.briefing.setWordWrap(True)
        self.briefing.setAccessibleDescription("Checklist card")
        self.briefing.hide()
        self.card_layout.addWidget(self.briefing)
        self.notice = QLabel(NOTICE)
        self.notice.setObjectName("quietText")
        self.notice.setWordWrap(True)

        aside = QVBoxLayout()
        aside.setContentsMargins(0, 0, 0, 0)
        aside.setSpacing(14)
        aside.addWidget(self.scenario_box)
        aside.addWidget(self.card)
        aside.addWidget(self.notice)
        aside.addStretch(1)
        self.aside = QWidget()
        self.aside.setLayout(aside)

        self._narrow_tabs = False
        self._progress: tuple[tuple[str, int, int], ...] = ()
        self.stage_tabs = QTabBar()
        self.stage_tabs.setObjectName("stageTabs")
        self.stage_tabs.setAccessibleName("Checklist stages")
        self.stage_tabs.setExpanding(False)
        self.stage_tabs.setDrawBase(False)
        self.stage_tabs.setUsesScrollButtons(False)
        self.stage_tabs.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for stage in STAGES:
            self.stage_tabs.addTab(stage)
        self.stage_tabs.currentChanged.connect(self._show_stage)
        self.stage_label = QLabel()
        self.stage_label.setObjectName("quietText")
        self.stage_label.setWordWrap(True)
        self.stage_label.setAccessibleDescription("Checklist stage")
        self.instruction = QLabel(f"{INSTRUCTION} {KEYS}")
        self.instruction.setObjectName("bodyText")
        self.instruction.setWordWrap(True)
        self.stack = QStackedWidget()

        self.debrief_label = QLabel()
        self.debrief_label.setObjectName("quietText")
        self.debrief_label.setWordWrap(True)
        self.debrief_label.setAccessibleDescription("Checklist debrief")
        self.reset_button = QPushButton("Reset card")
        self.reset_button.setObjectName("textBtn")
        self.reset_button.setAutoDefault(True)
        self.reset_button.setAccessibleName("Reset checklist card")
        self.reset_button.clicked.connect(self._reset)
        self.save_button = QPushButton("Save practice")
        self.save_button.setObjectName("primaryBtn")
        self.save_button.setAutoDefault(True)
        self.save_button.setAccessibleName("Save checklist practice")
        self.save_button.clicked.connect(self._save)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._save)
        self.foot = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.foot.setSpacing(12)
        self.foot.addWidget(self.debrief_label, 1)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.reset_button)
        buttons.addWidget(self.save_button)
        self.foot.addLayout(buttons)

        work = QVBoxLayout()
        work.setContentsMargins(0, 0, 0, 0)
        work.setSpacing(12)
        work.addWidget(self.stage_tabs, 0, Qt.AlignmentFlag.AlignLeft)
        work.addWidget(self.stage_label)
        work.addWidget(self.instruction)
        work.addWidget(self.stack)
        work.addStretch(1)
        work.addLayout(self.foot)
        self.work = QWidget()
        self.work.setLayout(work)

        self.body_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.body_layout.setSpacing(_GAP)
        self.body_layout.addWidget(self.aside, 0)
        self.body_layout.addWidget(self.work, 1)
        body = QWidget()
        body.setObjectName("whoBody")
        body.setLayout(self.body_layout)
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Tab goes from control to control; an unnamed scroll area is not a stop.
        self.page_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.page_scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.page_scroll)
        self._build_form()

    # —— the card and its lines ——

    def _select_scenario(self, index: int) -> None:
        if index < 0:
            return
        self._scenario = self._scenarios[index]
        self._marks = {}
        self._build_form()

    def _build_form(self) -> None:
        while (page := self.stack.widget(0)) is not None:
            self.stack.removeWidget(page)
            # Hidden and unparented now, so nothing asks about it before the deferred delete.
            page.hide()
            page.setParent(None)
            page.deleteLater()
        self._boxes, self._pages = {}, {}
        self._show_card(self._scenario)
        for stage in STAGES:
            page = StagePage()
            for check in self._scenario.items:
                if check.stage != stage:
                    continue
                tag = ITEM_CARD_FIELD.get(check.item_id, "").lower()
                if check.required:
                    box = ItemBox(check.prompt, tag, False)
                    box.toggled.connect(lambda checked, item_id=check.item_id: self._set_required(item_id, checked))
                else:
                    box = ItemBox(f"Not on this card: {check.prompt}", tag, True)
                    box.toggled.connect(lambda checked, item_id=check.item_id: self._set_optional(item_id, checked))
                self._boxes[check.item_id] = box
                page.add(box)
            self._pages[stage] = page
            self.stack.addWidget(page)
        self._columns = 0
        self._set_columns()
        self._open_stage = open_stage(self._scenario, self._marks)
        self.stage_tabs.blockSignals(True)
        self.stage_tabs.setCurrentIndex(STAGES.index(self._open_stage) if self._open_stage else 0)
        self.stage_tabs.blockSignals(False)
        self.stack.setCurrentIndex(self.stage_tabs.currentIndex())
        self._refresh()
        self._chain_focus()

    def _show_card(self, scenario: Scenario) -> None:
        lines = card_lines(scenario)
        self.card_title.setText(f"Card {scenario.scenario_id}")
        self.card.setAccessibleName(f"Fictional card {scenario.scenario_id}")
        self.card.setAccessibleDescription(scenario.briefing)
        while self.card_grid.count():
            taken = self.card_grid.takeAt(0)
            widget = taken.widget() if taken is not None else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for row, (name, value) in enumerate(lines):
            key = QLabel(name)
            key.setObjectName("cardKey")
            said = QLabel(value)
            said.setObjectName("cardValue")
            said.setWordWrap(True)
            if row == len(lines) - 1:
                key.setProperty("last", True)
                said.setProperty("last", True)
            self.card_grid.addWidget(key, row, 0)
            self.card_grid.addWidget(said, row, 1)
        self.briefing.setText(scenario.briefing)
        self.briefing.setVisible(not lines)

    def _set_required(self, item_id: str, checked: bool) -> None:
        if checked:
            self._marks = apply_mark(self._scenario, self._marks, item_id, MARK_DONE)
        else:
            self._marks.pop(item_id, None)
        self._refresh()

    def _set_optional(self, item_id: str, checked: bool) -> None:
        if checked:
            self._marks = apply_mark(self._scenario, self._marks, item_id, MARK_NOT_ON_CARD)
        else:
            self._marks.pop(item_id, None)
        self._refresh()

    def _refresh(self) -> None:
        previous = self._open_stage
        stage = open_stage(self._scenario, self._marks)
        self._open_stage = stage
        for item in self._scenario.items:
            box = self._boxes[item.item_id]
            box.blockSignals(True)
            box.setChecked(self._marks.get(item.item_id) == _expected(item))
            box.blockSignals(False)
            box.setEnabled(stage is None or item.stage == stage)
            box.update()
        self._progress = stage_progress(self._scenario, self._marks)
        self._write_tabs()
        viewed = STAGES[self.stage_tabs.currentIndex()]
        if stage is not None and stage != previous and viewed == previous:
            # The stage being worked on is complete: carry on with the next one, and keep the keyboard moving.
            self.stage_tabs.setCurrentIndex(STAGES.index(stage))
            self._pages[stage].boxes[0].setFocus(Qt.FocusReason.OtherFocusReason)
            announce(self.stage_label, f"{previous} is complete. {stage} is open.")
        elif stage is None and previous is not None:
            self.save_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._say_stage()
        self._show_debrief()

    def _write_tabs(self) -> None:
        """Each stage with how many of its lines are marked; on a narrow page the count goes under the name."""
        separator = "\n" if self._narrow_tabs else " "
        for index, (name, done, total) in enumerate(self._progress):
            self.stage_tabs.setTabText(index, f"{name}{separator}{done} of {total}")

    def _show_stage(self, index: int) -> None:
        if 0 <= index < len(STAGES):
            self.stack.setCurrentIndex(index)
            self._say_stage()

    def _say_stage(self) -> None:
        """One quiet line for the stage being viewed: open, done, or closed and until when."""
        viewed = STAGES[self.stage_tabs.currentIndex()]
        stage = self._open_stage
        if stage is None:
            text = "All stages marked."
        elif viewed == stage:
            text = f"Current stage: {stage}"
        elif STAGES.index(viewed) < STAGES.index(stage):
            text = f"{viewed} is complete."
        else:
            text = f"{viewed} is closed until {stage} is complete."
        self.stage_label.setText(text)

    def _open_lines(self) -> list[str]:
        """The card lines the open stage still waits for, by the card line each reads."""
        stage = self._open_stage
        waiting = []
        for item in self._scenario.items:
            if item.stage != stage or self._marks.get(item.item_id) == _expected(item):
                continue
            name = ITEM_CARD_FIELD.get(item.item_id, item.prompt).lower()
            waiting.append(name if item.required else f"{name} (mark as not on this card)")
        return waiting

    def _show_debrief(self) -> None:
        result = debrief(self._scenario, self._marks)
        if result.complete:
            self.debrief_label.setText("Card complete. Save keeps this practice on this computer.")
        elif self._open_stage is not None and (waiting := self._open_lines()):
            self.debrief_label.setText(f"Still open in {self._open_stage}: {', '.join(waiting)}.")
        elif result.lines:
            self.debrief_label.setText(result.lines[0])
        else:
            self.debrief_label.setText("")

    def _reset(self) -> None:
        self._marks = {}
        self.stage_tabs.setCurrentIndex(0)
        self._refresh()
        announce(self.stage_label, "Card reset. Sign in is open.")

    def _save(self) -> None:
        try:
            save_practice(self._scenario, self._marks)
        except OSError:
            self.debrief_label.setText(SAVE_FAILED)
            announce(self.debrief_label, SAVE_FAILED)
            return
        result = debrief(self._scenario, self._marks)
        state = "complete" if result.complete else "still open"
        self.debrief_label.setText(f"Saved. This card is {state}.")
        announce(self.debrief_label, self.debrief_label.text())

    # —— keyboard order and layout ——

    def _chain_focus(self) -> None:
        """Tab follows the screen: card picker, stage tabs, the lines, Reset, Save."""
        order: list[QWidget] = [self.scenario_box, self.stage_tabs]
        for stage in STAGES:
            order.extend(self._pages[stage].boxes)
        order.extend([self.reset_button, self.save_button])
        for first, second in zip(order, order[1:], strict=False):
            QWidget.setTabOrder(first, second)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.reflow()

    def reflow(self) -> None:
        """The card beside the lines when the page is wide, above them when not; one or two columns of lines."""
        width = self.width()
        wide = width >= SIDE_BY_SIDE_WIDTH
        side = 16 if width < 480 else 32
        if (width < 480) != self._narrow_tabs:
            self._narrow_tabs = width < 480
            self.stage_tabs.setProperty("narrow", self._narrow_tabs)
            self.stage_tabs.style().unpolish(self.stage_tabs)
            self.stage_tabs.style().polish(self.stage_tabs)
            self._write_tabs()
        if wide != self._wide:
            self._wide = wide
            self.body_layout.setDirection(
                QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
            self.body_layout.setAlignment(self.aside, Qt.AlignmentFlag.AlignTop)
            if wide:
                self.aside.setFixedWidth(CARD_WIDTH)
            else:
                self.aside.setMinimumWidth(0)
                self.aside.setMaximumWidth(16777215)
        self.body_layout.setContentsMargins(side, 8, side, 24)
        self.body_layout.setSpacing(_GAP if wide else 24)
        room = width - 2 * side - ((CARD_WIDTH + _GAP) if wide else 0)
        self.foot.setDirection(QBoxLayout.Direction.LeftToRight if room >= 460 else QBoxLayout.Direction.TopToBottom)
        self._set_columns(room)

    def _set_columns(self, room: int | None = None) -> None:
        if room is None:
            room = self.width() - 64 - ((CARD_WIDTH + _GAP) if self.width() >= SIDE_BY_SIDE_WIDTH else 0)
        columns = 2 if room >= TWO_COLUMNS_WIDTH else 1
        self._columns = columns
        for page in self._pages.values():
            page.set_columns(columns)
