"""The app frame: navigation rail, running head, practice tabs and footer fields.

Everything here is chrome. The pages stay where they were; the window decides what each
control does, so this module holds no data and starts no work.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from math import cos, sin

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QStatusBar,
    QTabBar,
    QToolButton,
    QVBoxLayout,
)

from src.desktop.fonts import make_tabular
from src.desktop.theme import active_theme, token_color

RAIL_WIDTH = 64
RESEARCH_STAMP = "De-identified research data · not for patient care"
# The same line while the data in use is the fictional demo data this app made
DEMO_STAMP = "Fictional demo data · not for patient care"
FICTION_LABEL = "Fictional practice"
FICTION_NOTE = "invented data, not patient records"
# Each line from its full form to its short form; the short one still says what the full one says first.
STAMP_FORMS = (f"○ {RESEARCH_STAMP}", "○ Not for patient care")
DEMO_STAMP_FORMS = (f"○ {DEMO_STAMP}", "○ Not for patient care")
FICTION_FORMS = (f"{FICTION_LABEL} · {FICTION_NOTE}", FICTION_LABEL)
# Pixels added to a line's measured text so a rounding step can never clip its last letter.
_LINE_SLACK = 4
# Pixels the status bar's own layout leaves at its edges, beside the size grip, before the first field.
_FOOTER_EDGES = 10
# The gap the status bar's layout puts between its items when it cannot be asked.
_FOOTER_GAP = 6
# The footer's source field while the folders in use are the demo data this app made
SOURCE_NAME = ("MOVER SIS local copy", "Data source: MOVER SIS local copy")
DEMO_SOURCE = ("<b>Fictional demo data</b>", "Data source: fictional demo data, not patient records")
DEMO_SOURCE_TIP = "Fictional demo data made by MOVER Suite. These are not patient records."
PRACTICE_SOURCE = "Practice reads no data folder"
PRACTICE_NOTE = "nothing here comes from MOVER cases"

_ICON_BOX = 24.0
_ICON_PX = 22

# Rail items: key, label, icon name. The first four are places; the last two act.
PLACES = (
    ("research", "Research", "research"),
    ("cases", "Cases", "cases"),
    ("signal", "Signal", "signal"),
    ("practice", "Practice", "practice"),
)
ACTIONS = (("rules", "Rules", "rules"), ("settings", "Settings", "settings"))


def _line(path: QPainterPath, points: Sequence[tuple[float, float]]) -> None:
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)


def _icon_path(name: str) -> QPainterPath:
    """Line drawing of ``name`` on a 24 by 24 grid.

    Drawn in code because the packaged app leaves QtSvg out, so an SVG file could not be
    recolored for both themes the way the chevrons are.
    """
    path = QPainterPath()
    if name == "research":  # speech bubble with a question mark
        path.addRoundedRect(QRectF(3.5, 4, 17, 12.5), 2.5, 2.5)
        _line(path, [(8.5, 16.5), (8.5, 20.5), (13, 16.5)])
        path.moveTo(9.9, 8.6)
        path.arcTo(QRectF(9.9, 7.2, 4.2, 3.6), 180, -200)
        path.lineTo(12, 12.2)
        path.moveTo(12, 14.1)
        path.lineTo(12, 14.2)
    elif name == "cases":  # table
        path.addRoundedRect(QRectF(3.5, 5, 17, 14), 2, 2)
        _line(path, [(3.5, 10), (20.5, 10)])
        _line(path, [(3.5, 14.5), (20.5, 14.5)])
        _line(path, [(9.5, 10), (9.5, 19)])
    elif name == "signal":  # pulse trace
        _line(path, [(2.5, 12), (7, 12), (9.5, 6), (13, 18), (15.5, 12), (21.5, 12)])
    elif name == "practice":  # clipboard
        path.addRoundedRect(QRectF(5.5, 5, 13, 15.5), 2, 2)
        path.addRoundedRect(QRectF(9, 3, 6, 4), 1.2, 1.2)
        _line(path, [(9, 11.5), (15, 11.5)])
        _line(path, [(9, 15), (13.5, 15)])
    elif name == "rules":  # ruler
        path.addRoundedRect(QRectF(1.8, 8.6, 20.4, 6.8), 1.5, 1.5)
        for x in (6.3, 10.2, 14.1, 18):
            _line(path, [(x, 8.6), (x, 11.6)])
    elif name == "settings":  # gear
        path.addEllipse(QPointF(12, 12), 3, 3)
        path.addEllipse(QPointF(12, 12), 6.3, 6.3)
        for step in range(8):
            angle = step * 0.7853981633974483
            _line(path, [(12 + 6.3 * sin(angle), 12 - 6.3 * cos(angle)),
                         (12 + 9 * sin(angle), 12 - 9 * cos(angle))])
    elif name == "find":  # magnifier
        path.addEllipse(QPointF(10.5, 10.5), 6, 6)
        _line(path, [(15, 15), (20.5, 20.5)])
    return path


def _icon_pixmap(name: str, color, px: int, ratio: float) -> QPixmap:
    pixmap = QPixmap(round(px * ratio), round(px * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(px / _ICON_BOX, px / _ICON_BOX)
    pen = QPen(color, 1.7)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPath(_icon_path(name))
    painter.end()
    return pixmap


def nav_icon(name: str, px: int = _ICON_PX, theme: str | None = None) -> QIcon:
    """A line icon in the active theme: muted at rest, ink on hover, accent when its item is current."""
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    icon = QIcon()
    for mode, state, token in (
        (QIcon.Mode.Normal, QIcon.State.Off, "ink_3"),
        (QIcon.Mode.Active, QIcon.State.Off, "ink"),
        (QIcon.Mode.Normal, QIcon.State.On, "accent"),
        (QIcon.Mode.Active, QIcon.State.On, "accent"),
        (QIcon.Mode.Disabled, QIcon.State.Off, "rule_strong"),
        (QIcon.Mode.Disabled, QIcon.State.On, "rule_strong"),
    ):
        icon.addPixmap(_icon_pixmap(name, token_color(token, theme), px, ratio), mode, state)
    return icon


class RailButton(QToolButton):
    """Text under icon. Keyboard focus, hover and the current mark come from the stylesheet."""

    def __init__(self, key: str, label: str, icon_name: str, *, checkable: bool, parent=None):
        super().__init__(parent)
        self.key = key
        self._icon_name = icon_name
        self.setObjectName("railButton")
        self.setText(label)
        self.setAccessibleName(label)
        self.setCheckable(checkable)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(RAIL_WIDTH)
        self.setMinimumHeight(54)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        self.setIcon(nav_icon(self._icon_name))


class NavRail(QFrame):
    """Left rail: four places at the top, Rules and Settings at the bottom.

    Settings is a place too (its page can be open); Rules only opens the drawer.
    """

    item_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("navRail")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(RAIL_WIDTH)
        self.setAccessibleName("Main navigation")
        self._icons_theme = active_theme()
        self._buttons: dict[str, RailButton] = {}
        # Exclusive by hand: Qt takes every unchecked member of an exclusive QButtonGroup out of the Tab order
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 10)
        layout.setSpacing(2)
        for key, label, icon_name in PLACES:
            layout.addWidget(self._add(key, label, icon_name, checkable=True))
        layout.addStretch(1)
        for key, label, icon_name in ACTIONS:
            # Settings is a page, so it shows as the current place; Rules opens a drawer over the page
            layout.addWidget(self._add(key, label, icon_name, checkable=key == "settings"))

    def _add(self, key: str, label: str, icon_name: str, *, checkable: bool) -> RailButton:
        button = RailButton(key, label, icon_name, checkable=checkable, parent=self)
        if checkable:
            # a click on the current place must not switch its mark off
            button.clicked.connect(lambda _checked=False, btn=button: btn.setChecked(True))
        button.clicked.connect(lambda _checked=False, name=key: self.item_clicked.emit(name))
        self._buttons[key] = button
        return button

    def button(self, key: str) -> RailButton:
        return self._buttons[key]

    def set_current(self, key: str | None) -> None:
        """Mark the place being shown without emitting a click."""
        for name, button in self._buttons.items():
            if button.isCheckable():
                button.setChecked(name == key)

    def set_item_state(self, key: str, *, enabled: bool, tip: str) -> None:
        button = self._buttons[key]
        button.setEnabled(enabled)
        button.setToolTip(tip)
        button.setAccessibleDescription(tip)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange,
                            QEvent.Type.StyleChange) and self._icons_theme != active_theme():
            self._icons_theme = active_theme()
            for button in self._buttons.values():
                button.refresh_icon()


class BreadcrumbLabel(QLabel):
    """The path to the current place. Long paths shorten in the middle; the last part stays whole."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("crumbs")
        self._parts: tuple[str, ...] = ()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_parts(self, parts: Sequence[str]) -> None:
        self._parts = tuple(parts)
        plain = " / ".join(self._parts)
        self.setAccessibleName(f"Current place: {plain}")
        self.setToolTip(plain)
        self.update()

    def parts(self) -> tuple[str, ...]:
        return self._parts

    def sizeHint(self) -> QSize:  # noqa: N802
        bold = self.font()
        bold.setWeight(self.font().Weight.DemiBold)
        width = QFontMetrics(bold).horizontalAdvance(" / ".join(self._parts)) + 4
        return QSize(width, 20)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(40, 20)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._parts:
            return
        painter = QPainter(self)
        last_font = painter.font()
        last_font.setWeight(last_font.Weight.DemiBold)
        last = self._parts[-1]
        last_width = QFontMetrics(last_font).horizontalAdvance(last)
        muted = QFontMetrics(painter.font())
        prefix = " / ".join(self._parts[:-1]) + (" / " if len(self._parts) > 1 else "")
        room = max(0, self.width() - last_width)
        prefix = muted.elidedText(prefix, Qt.TextElideMode.ElideMiddle, room)
        rect = self.rect()
        painter.setPen(token_color("ink_3"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, prefix)
        painter.setFont(last_font)
        painter.setPen(token_color("ink"))
        painter.drawText(rect.adjusted(muted.horizontalAdvance(prefix), 0, 0, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(last_font).elidedText(last, Qt.TextElideMode.ElideRight,
                                                            max(0, rect.width() - muted.horizontalAdvance(prefix))))


class RunningHead(QFrame):
    """Breadcrumb on the left; Find and the research stamp (or the fiction label) on the right.

    The research-only line and the fiction line are never cut. As the head narrows, the breadcrumb shortens first,
    then Find drops its Ctrl+K hint and then its word, and only then does the line switch to its short form.
    """

    find_requested = Signal()

    # Find from widest to narrowest: icon, word and key hint; icon and word; icon alone.
    FIND_FORMS = ("Find  Ctrl+K", "Find", "")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("runningHead")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(40)
        self.setProperty("practice", False)
        self._icons_theme = active_theme()
        self._practice = False
        self._demo = False
        self._widths: dict[tuple[str, str], int] = {}
        layout = self._row = QHBoxLayout(self)
        layout.setContentsMargins(32, 0, 32, 0)
        layout.setSpacing(20)
        self.crumbs = BreadcrumbLabel(self)
        layout.addWidget(self.crumbs, 1)
        self.find_button = QPushButton(self.FIND_FORMS[0], self)
        self.find_button.setObjectName("findButton")
        self.find_button.setIcon(nav_icon("find", 15))
        self.find_button.setIconSize(QSize(15, 15))
        self.find_button.setAccessibleName("Find")
        self.find_button.setAccessibleDescription(
            "Search every action, screen and setting. Shortcut Control K."
        )
        self.find_button.setToolTip("Find an action, screen or setting (Ctrl+K)")
        self.find_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.find_button.clicked.connect(self.find_requested)
        layout.addWidget(self.find_button)
        self.stamp = QLabel(STAMP_FORMS[0], self)
        self.stamp.setObjectName("stamp")
        self.stamp.setAccessibleName(RESEARCH_STAMP)
        self.stamp.setToolTip(RESEARCH_STAMP)
        layout.addWidget(self.stamp)
        self.fiction = QLabel(FICTION_FORMS[0], self)
        self.fiction.setObjectName("fictionLabel")
        self.fiction.setAccessibleName(f"{FICTION_LABEL}: {FICTION_NOTE}")
        self.fiction.setToolTip(f"{FICTION_LABEL}: {FICTION_NOTE}")
        layout.addWidget(self.fiction)
        self.set_place(["Research"], practice=False)

    def set_place(self, parts: Sequence[str], *, practice: bool) -> None:
        self.crumbs.set_parts(parts)
        self._practice = practice
        self.stamp.setVisible(not practice)
        self.fiction.setVisible(practice)
        if bool(self.property("practice")) != practice:
            self.setProperty("practice", practice)
            self.style().unpolish(self)
            self.style().polish(self)
        self.updateGeometry()
        self._fit()

    def set_demo(self, demo: bool) -> None:
        """Name the data in use: the demo data's stamp never calls it research data."""
        if demo == self._demo:
            return
        self._demo = demo
        sentence = DEMO_STAMP if demo else RESEARCH_STAMP
        self.stamp.setAccessibleName(sentence)
        self.stamp.setToolTip(sentence)
        self.updateGeometry()
        self._fit()

    def crumb_text(self) -> str:
        return " / ".join(self.crumbs.parts())

    def line(self) -> QLabel:
        """The line that is showing: the research stamp, or the fiction label on practice pages."""
        return self.fiction if self._practice else self.stamp

    # ----- fitting -----
    def _forms(self) -> tuple[str, ...]:
        if self._practice:
            return FICTION_FORMS
        return DEMO_STAMP_FORMS if self._demo else STAMP_FORMS

    def _width(self, widget: QLabel | QPushButton, text: str) -> int:
        """The width ``widget`` needs to show ``text`` whole."""
        key = (widget.objectName(), text)
        if key not in self._widths:
            if isinstance(widget, QPushButton):
                shown = widget.text()
                widget.setText(text)
                widget.ensurePolished()
                self._widths[key] = widget.sizeHint().width()
                widget.setText(shown)
            else:
                widget.ensurePolished()
                self._widths[key] = QFontMetrics(widget.font()).horizontalAdvance(text) + _LINE_SLACK
        return self._widths[key]

    def _least_width(self) -> int:
        """The narrowest the head can be: shortened path, Find as its icon, and the line in its short form."""
        margins = self._row.contentsMargins()
        return (margins.left() + margins.right() + self.crumbs.minimumSizeHint().width()
                + self._width(self.find_button, self.FIND_FORMS[-1]) + self._width(self.line(), self._forms()[-1])
                + 2 * self._row.spacing())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._least_width(), self.height())

    def _fit(self) -> None:
        margins = self._row.contentsMargins()
        room = self.width() - margins.left() - margins.right() - 2 * self._row.spacing() - self.crumbs.minimumSizeHint().width()
        line = self.line()
        chosen = (self._forms()[-1], self.FIND_FORMS[-1])
        for line_text in self._forms():
            found = next((find for find in self.FIND_FORMS
                          if self._width(self.find_button, find) + self._width(line, line_text) <= room), None)
            if found is not None:
                chosen = (line_text, found)
                break
        line_text, find_text = chosen
        if line.text() != line_text:
            line.setText(line_text)
        if self.find_button.text() != find_text:
            self.find_button.setText(find_text)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange,
                            QEvent.Type.StyleChange, QEvent.Type.FontChange):
            self._widths.clear()
            self._fit()
            if self._icons_theme != active_theme():
                self._icons_theme = active_theme()
                self.find_button.setIcon(nav_icon("find", 15))


class PracticeTabs(QFrame):
    """The three practice tools as tabs under the running head; shown only on practice pages."""

    tool_selected = Signal(str)

    def __init__(self, tools: Sequence[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("practiceTabs")
        self.setFrameShape(QFrame.Shape.NoFrame)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(28, 0, 32, 0)
        self.bar = QTabBar(self)
        self.bar.setAccessibleName("Practice tools")
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.bar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._keys: list[str] = []
        for key, label in tools:
            self.bar.addTab(label)
            self._keys.append(key)
        self.bar.currentChanged.connect(self._on_changed)
        layout.addWidget(self.bar)
        layout.addStretch(1)
        self.hide()

    def _on_changed(self, index: int) -> None:
        if 0 <= index < len(self._keys):
            self.tool_selected.emit(self._keys[index])

    def select(self, key: str) -> None:
        """Show ``key`` as the current tab without announcing a change."""
        if key not in self._keys:
            return
        self.bar.blockSignals(True)
        self.bar.setCurrentIndex(self._keys.index(key))
        self.bar.blockSignals(False)


class FooterField(QLabel):
    """One footer field: shown whole, shortened to whole words with an ellipsis, or not shown at all.

    It never paints a fragment of a word. Its least width is zero, so the window can shrink to a small screen;
    ``FooterFields.fit`` decides what each field shows for the room the footer has.
    """

    def __init__(self, bar: QStatusBar):
        super().__init__(bar)
        self.setObjectName("footerField")
        self.setTextFormat(Qt.TextFormat.RichText)
        self._rich = ""
        self._visible_text = ""
        self._tip = ""
        self._wanted = True
        self._shown_width = 0
        self._padding = 0
        self._natural = 0

    def set_content(self, rich: str, plain: str, tip: str = "") -> None:
        """``rich`` is what shows when it fits; ``plain`` is the accessible name; ``tip`` the tooltip."""
        document = QTextDocument()
        document.setHtml(rich)
        self._rich, self._tip = rich, tip
        self._visible_text = " ".join(document.toPlainText().split())
        self.setAccessibleName(plain)
        self.setText(rich)
        self.setToolTip(tip)
        self._measure()
        self._shown_width = self._natural

    def _measure(self) -> None:
        """The label's padding and the width of its whole rich text, taken in the font it paints with."""
        shown = self.text()
        self.ensurePolished()
        self.setText("")
        self._padding = super().sizeHint().width()
        self.setText(self._rich)
        self._natural = super().sizeHint().width()
        self.setText(shown)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange) and self._rich:
            self._measure()

    def wanted(self) -> bool:
        return self._wanted

    def set_wanted(self, wanted: bool) -> None:
        """Whether the page asks for this field; the footer may still hide it for want of room."""
        self._wanted = wanted

    def natural_width(self) -> int:
        """The width of the whole text, bold parts included."""
        return self._natural

    def shortened(self, width: int) -> str:
        """The longest run of whole words of the visible text that, with an ellipsis, fits ``width``; else empty."""
        words = self._visible_text.split(" ")
        metrics = QFontMetrics(self.font())
        for count in range(len(words) - 1, 0, -1):
            text = " ".join(words[:count]) + "…"
            if self._padding + metrics.horizontalAdvance(text) <= width:
                return text
        first = words[0] + "…" if words and words[0] else ""
        return first if first and self._padding + metrics.horizontalAdvance(first) <= width else ""

    def least_width(self) -> int:
        """The room one whole word and an ellipsis need; below it the field is not shown."""
        words = self._visible_text.split(" ")
        first = words[0] + "…" if words and words[0] else ""
        return self._padding + QFontMetrics(self.font()).horizontalAdvance(first) + _LINE_SLACK

    def show_in(self, width: int) -> None:
        """Show the field in ``width``, which is at least its least width: whole when it fits, else shortened."""
        if width >= self.natural_width():
            self.setText(self._rich)
            self.setToolTip(self._tip)
            self._shown_width = self.natural_width()
        else:
            self.setText(self.shortened(width - _LINE_SLACK))
            self.setToolTip("\n".join(part for part in (self._visible_text, self._tip) if part))
            self._shown_width = width
        self.updateGeometry()
        self.setVisible(self._wanted)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._shown_width, super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())


class _BarResized(QObject):
    """Runs ``callback`` whenever the bar it watches changes size or is shown.

    The bar's first resize comes before it counts as visible, so showing it is a trigger of its own.
    """

    def __init__(self, bar: QStatusBar, callback: Callable[[], None]):
        super().__init__(bar)
        self._callback = callback
        bar.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._callback()
        return False


class FooterFields:
    """The footer's permanent fields. A temporary status message still shows beside them."""

    def __init__(self, bar: QStatusBar):
        self._bar = bar
        self.source = self._field(bar, "footerSource")
        self.counts = self._field(bar, "footerCounts")
        make_tabular(self.counts)
        self.rules = self._field(bar, "footerRules")
        self.model = self._field(bar, "footerModel")
        self.practice_note = self._field(bar, "footerPracticeNote")
        # The last to give up room come last: the source and the counts say what the data is.
        self._shrink_order = (self.model, self.rules, self.practice_note, self.counts, self.source)
        self._watcher = _BarResized(bar, self.fit)

    @staticmethod
    def _field(bar: QStatusBar, name: str) -> FooterField:
        label = FooterField(bar)
        label.setProperty("field", name)
        bar.addPermanentWidget(label)
        return label

    def _set(self, label: FooterField, rich: str, plain: str, tip: str = "") -> None:
        label.set_content(rich, plain, tip)

    def show_research(self, *, source_tip: str, counts: tuple[str, str], rules: tuple[str, str],
                      model: tuple[str, str], source: tuple[str, str] | None = None) -> None:
        """Each field is (rich text, plain text for screen readers). ``source`` names the data when it is not the MOVER copy."""
        self._set(self.source, *(source or SOURCE_NAME), source_tip)
        self._set(self.counts, *counts)
        self._set(self.rules, *rules)
        self._set(self.model, *model)
        for label in (self.source, self.counts, self.rules, self.model):
            label.set_wanted(True)
        self.practice_note.set_wanted(False)
        self.practice_note.hide()
        self.fit()

    def show_practice(self) -> None:
        self._set(self.source, "<b>Practice</b> reads no data folder", PRACTICE_SOURCE)
        self._set(self.practice_note, PRACTICE_NOTE, PRACTICE_NOTE)
        for label in (self.counts, self.rules, self.model):
            label.set_wanted(False)
            label.hide()
        self.source.set_wanted(True)
        self.practice_note.set_wanted(True)
        self.fit()

    def _chrome_width(self) -> int:
        """What the bar keeps for itself beside the fields: its size grip and the gaps around the message area."""
        grip = self._bar.findChild(QSizeGrip)
        return (grip.width() if grip is not None and grip.isVisible() else 0) + _FOOTER_EDGES

    def fit(self) -> None:
        """Give each wanted field the room the footer has: whole, shortened, or (last resort) hidden."""
        fields = [field for field in (self.source, self.counts, self.rules, self.model, self.practice_note)
                  if field.wanted()]
        if not self._bar.isVisible():
            for field in fields:
                field.show_in(field.natural_width())
            return
        layout = self._bar.layout()
        spacing = layout.spacing() if layout is not None else _FOOTER_GAP
        room = self._bar.width() - self._chrome_width() - spacing * len(fields)
        order = [field for field in self._shrink_order if field.wanted()]
        for hidden in range(len(order) + 1):
            active = [field for field in fields if field not in order[:hidden]]
            widths = {field: field.natural_width() for field in active}
            excess = sum(widths.values()) - room
            for field in order[hidden:]:
                give = min(max(widths[field] - field.least_width(), 0), max(excess, 0))
                widths[field] -= give
                excess -= give
            if excess <= 0 or hidden == len(order):
                break
        for field in fields:
            if field in widths:
                field.show_in(widths[field])
            else:
                field.setVisible(False)
