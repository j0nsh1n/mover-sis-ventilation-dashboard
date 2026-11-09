"""Controls of the research question: the Cases | Articles switch, the Trace button and the local model menu."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleEvent,
    QAction,
    QActionGroup,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from src.desktop.theme import token_color

SCOPES = ("Cases", "Articles")
_SEMIBOLD = QFont.Weight.DemiBold
_NORMAL = QFont.Weight.Normal
# Pixels between a button's label and the second run of text after it.
_RUN_GAP = 10


@dataclass(frozen=True)
class Run:
    """One piece of a button's label: its text, its color token and its weight."""

    text: str
    token: str = "ink"
    semibold: bool = False
    pixel_size: int | None = None


class ScopeSwitch(QGroupBox):
    """Cases | Articles as two text options. Tab reaches the chosen one; the arrow keys move between them."""

    changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scopeSwitch")
        self.setAccessibleName("Question scope")
        self.setFlat(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._options: dict[str, QRadioButton] = {}
        for name in SCOPES:
            option = QRadioButton(name)
            option.setObjectName("scopeOption")
            option.setAccessibleName(name)
            option.toggled.connect(lambda checked, scope=name: self._toggled(scope, checked))
            layout.addWidget(option)
            self._options[name] = option
        self._notify = True
        self._options[SCOPES[0]].setChecked(True)

    def scope(self) -> str:
        return next(name for name, option in self._options.items() if option.isChecked())

    def option(self, scope: str) -> QRadioButton:
        return self._options[scope]

    def set_scope(self, scope: str, *, notify: bool = True) -> None:
        """Choose ``scope``; ``changed`` fires only for a real change and only when ``notify``."""
        if scope not in self._options:
            return
        self._notify = notify
        try:
            self._options[scope].setChecked(True)
        finally:
            self._notify = True

    def _toggled(self, scope: str, checked: bool) -> None:
        if checked and self._notify:
            self.changed.emit(scope)


class RunsButton(QPushButton):
    """A button whose label is several runs of text in different weights and color tokens.

    The runs are painted from tokens each time, so a theme switch needs no restyle. ``text()``
    holds the runs joined as plain text, which is also what assistive technology reads.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runs: list[Run] = []
        self._room_right = 0
        self.setAutoDefault(True)

    def set_runs(self, runs: list[Run], *, room_right: int = 0) -> None:
        self._runs = list(runs)
        self._room_right = room_right
        self.setText(" ".join(run.text for run in runs))
        self.updateGeometry()
        self.update()

    def runs(self) -> list[Run]:
        return list(self._runs)

    def _font_for(self, run: Run) -> QFont:
        font = QFont(self.font())
        font.setWeight(_SEMIBOLD if run.semibold else _NORMAL)
        if run.pixel_size is not None:
            font.setPixelSize(run.pixel_size)
        return font

    def _runs_width(self) -> int:
        widths = [QFontMetrics(self._font_for(run)).horizontalAdvance(run.text) for run in self._runs]
        return sum(widths) + _RUN_GAP * max(len(widths) - 1, 0)

    def _option(self) -> QStyleOptionButton:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        return option

    def sizeHint(self) -> QSize:  # noqa: N802
        self.ensurePolished()
        option = self._option()
        content = QSize(self._runs_width() + self._room_right, self.fontMetrics().height())
        return self.style().sizeFromContents(QStyle.ContentsType.CT_PushButton, option, content, self)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QStylePainter(self)
        option = self._option()
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        area = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
        x = float(area.left())
        for run in self._runs:
            font = self._font_for(run)
            painter.setFont(font)
            token = run.token if self.isEnabled() else "rule_strong"
            painter.setPen(token_color(token))
            width = painter.fontMetrics().horizontalAdvance(run.text)
            painter.drawText(
                int(x), area.top(), width + 1, area.height(),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), run.text,
            )
            x += width + _RUN_GAP
        self._paint_extra(painter, area)

    def _paint_extra(self, painter: QStylePainter, area) -> None:
        """Hook for marks drawn after the text, such as the model button's chevron."""


class TraceButton(RunsButton):
    """The page's one filled button: its label and, in the same button, its keyboard shortcut."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("primaryBtn")
        self.setAccessibleName("Trace the evidence")
        self.setAccessibleDescription("Shortcut Ctrl+Enter")
        self.setToolTip("Trace the evidence (Ctrl+Enter)")
        self.set_runs([
            Run("Trace the evidence", "on_accent", semibold=True),
            Run("Ctrl+Enter", "on_accent", pixel_size=12),
        ])

    def text_label(self) -> str:
        return self._runs[0].text


_STATE_WORDS = {
    "ready": "ready",
    "stopped": "stopped",
    "failed": "failed",
    "unchecked": "not checked",
    "busy:starting": "starting",
    "busy:checking": "checking",
    "busy:stopping": "stopping",
}
NO_MODEL_PLACEHOLDER = "(AI unavailable · search still works)"


class LocalModelButton(RunsButton):
    """One menu button for the local model: its name and state, with every model action inside.

    The menu holds the answer-model choices and the Start, Unload, Stop, Refresh and Data setup
    actions. They are real actions, so the command palette lists the same objects.
    """

    model_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modelMenuBtn")
        self.setAccessibleName("Local AI")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._models: list[str] = []
        self._current: str | None = None
        self._placeholder = NO_MODEL_PLACEHOLDER
        self._state = "unchecked"
        self._model_actions: list[QAction] = []
        self._model_group = QActionGroup(self)
        self._model_group.setExclusive(True)

        self.start_action = QAction("Start AI", self)
        self.refresh_action = QAction("Refresh model list", self)
        self.setup_action = QAction("Data setup", self)
        self.stop_action = QAction("Stop AI", self)
        self.unload_action = QAction("Unload model", self)
        self._describe = {
            self.start_action: "Start Ollama and look for installed models.",
            self.refresh_action: "Look for installed models again.",
            self.setup_action: "Choose the EMR folder and prepare the data.",
            self.stop_action: "Stop Ollama and release its model memory.",
            self.unload_action: "Unload the selected model and keep Ollama running.",
        }
        for action, text in self._describe.items():
            action.setToolTip(text)
            action.setStatusTip(text)
        self.stop_action.setEnabled(False)
        self.unload_action.setEnabled(False)
        for action in self._describe:
            action.enabledChanged.connect(lambda _enabled: self._set_hints())

        self.menu_widget = QMenu(self)
        self.menu_widget.setObjectName("modelMenu")
        self.menu_widget.setMinimumWidth(300)
        self.menu_widget.setAccessibleName("Local AI menu")
        self._intro = QLabel()
        self._intro.setObjectName("quietText")
        self._intro.setWordWrap(True)
        self._intro.setMaximumWidth(380)
        self._intro_action = self._label_action("Local AI", self._intro)
        self._answer_heading = self._label_action("Answer model")
        self.menu_widget.addAction(self._intro_action)
        self.menu_widget.addSeparator()
        self.menu_widget.addAction(self._answer_heading)
        self._model_anchor = self.menu_widget.addSeparator()
        for action in (self.unload_action, self.stop_action, self.start_action, self.refresh_action,
                       self.setup_action):
            self.menu_widget.addAction(action)
        self.setMenu(self.menu_widget)
        self._set_intro("")
        self._refresh()

    # ----- menu content -----
    def _label_action(self, title: str, detail: QLabel | None = None) -> QWidgetAction:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(2)
        heading = QLabel(title)
        heading.setObjectName("stepHeading")
        layout.addWidget(heading)
        if detail is not None:
            layout.addWidget(detail)
        action = QWidgetAction(self.menu_widget)
        action.setDefaultWidget(holder)
        return action

    def _set_intro(self, text: str) -> None:
        self._intro.setText(text)
        self._intro.setVisible(bool(text))

    def set_intro(self, text: str) -> None:
        """The sentence under the menu's title: what the model reads and that search works without it."""
        self._set_intro(text)

    # ----- models -----
    def set_models(self, models: list[str], current: str | None = None, placeholder: str = "") -> None:
        """Replace the installed models; the choice stays where it was unless ``current`` names another.

        This never emits ``model_chosen``: only a person's choice does.
        """
        previous = self._current
        self._models = list(models)
        self._placeholder = placeholder or NO_MODEL_PLACEHOLDER
        wanted = current if current in self._models else previous if previous in self._models else None
        self._current = wanted or (self._models[0] if self._models else None)
        for action in self._model_actions:
            self.menu_widget.removeAction(action)
            self._model_group.removeAction(action)
            action.deleteLater()
        self._model_actions = []
        if self._models:
            for name in self._models:
                action = QAction(name, self)
                action.setCheckable(True)
                action.setChecked(name == self._current)
                action.triggered.connect(lambda _checked=False, model=name: self.choose(model))
                self._model_group.addAction(action)
                self.menu_widget.insertAction(self._model_anchor, action)
                self._model_actions.append(action)
        else:
            action = QAction(self._placeholder, self)
            action.setEnabled(False)
            self.menu_widget.insertAction(self._model_anchor, action)
            self._model_actions.append(action)
        self._refresh()

    def selected_model(self) -> str | None:
        return self._current

    def models(self) -> list[str]:
        return list(self._models)

    def placeholder_text(self) -> str:
        """What the model list says when it has no models: the reason, such as Ollama stopped."""
        return "" if self._models else self._placeholder

    def model_actions(self) -> list[QAction]:
        """The answer-model choices (checkable); empty when there is no model to choose."""
        return list(self._model_actions) if self._models else []

    def choose(self, model: str) -> None:
        """Choose an installed model as a person would; ``model_chosen`` fires when it is a change."""
        if model not in self._models or model == self._current:
            return
        self._current = model
        for action in self._model_actions:
            action.setChecked(action.text() == model)
        self._refresh()
        self.model_chosen.emit(model)

    # ----- state -----
    def set_state(self, state: str) -> None:
        """One of unchecked, ready, stopped, failed or busy:<word>; shown on the button."""
        self._state = state
        self._refresh()

    def state(self) -> str:
        return self._state

    def _label_runs(self) -> list[Run]:
        word = _STATE_WORDS.get(self._state, self._state)
        if self._state == "ready":
            if self._current:
                return [Run(self._current, "ink", semibold=True), Run(word, "ink_3")]
            return [Run("No model installed", "ink", semibold=True)]
        return [Run("Local AI", "ink", semibold=True), Run(word, "ink_3")]

    def _refresh(self) -> None:
        self.set_runs(self._label_runs(), room_right=0)
        self.setAccessibleDescription(self.text())
        self.setToolTip("Local AI: model choice, start, stop, unload and data setup")
        self._set_hints()
        QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.DescriptionChanged))

    def _set_hints(self) -> None:
        """Say why an action is off, which is what the command palette shows beside a disabled command."""
        reasons = {
            self.stop_action: "Local AI is not running.",
            self.unload_action: "Local AI is not running, so no model is loaded.",
            self.start_action: "Local AI is working. Try again when it finishes.",
        }
        for action, reason in reasons.items():
            action.setToolTip(self._describe[action] if action.isEnabled() else reason)

    # ----- painting and keys -----
    def _paint_extra(self, painter: QStylePainter, area) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = token_color("ink_3" if self.isEnabled() else "rule_strong")
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        right = float(self.rect().right()) - 12.0
        middle = self.rect().center().y() + 0.5
        painter.drawPolyline([QPointF(right - 8, middle - 2), QPointF(right - 4, middle + 2),
                              QPointF(right, middle - 2)])

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # Down opens the menu, as it does on a drop-down.
        if event.key() == Qt.Key.Key_Down and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            event.accept()
            self.showMenu()
            return
        super().keyPressEvent(event)
