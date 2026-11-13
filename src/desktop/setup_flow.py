"""First-time setup (and Data setup later): a flow that fills the window instead of opening as a dialog.

Welcome, data folders, done. The same checks and stored values as the old wizard, plus one-click
fictional demo data. Leaving ("Not now") keeps the app usable.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.desktop.accessibility import announce
from src.desktop.demo_data import STEPS as DEMO_STEPS
from src.desktop.demo_data import DemoDataWorker, use_demo_data
from src.desktop.errors import error_message
from src.desktop.scroll_safe_combo import ScrollSafeComboBox
from src.desktop.theme import active_theme
from src.desktop.settings_ui import (
    FolderRow,
    SaveRefused,
    SettingRow,
    apply_values,
    check_emr_folder,
    check_models_folder,
    check_processed_folder,
    check_wave_folder,
    probe_models,
    saved_values,
    scroll_panel,
)
from src.guardrails.validate_io import validate_emr_dir
from src.runtime_paths import default_processed_for_emr, resolve_emr_directory
from src.user_settings import THEME_DARK, THEME_LIGHT, THEME_SYSTEM, mark_setup_complete

STEPS = (("Welcome", "what this is"), ("Data folders", "where your MOVER data is"), ("Done", "check and finish"))
WELCOME, DATA, DONE, DEMO = range(4)
PROMISES = (
    ("For research and education",
     "Explore recorded anesthesia data, trace where an answer came from, and practice reading timelines."),
    ("Not for patient care",
     "It does not monitor anyone, give advice, or say what to do for a patient."),
    ("Local only",
     "Data is read from folders on this computer. Nothing is uploaded, and the optional AI runs here too."),
    ("Screening rules, not diagnoses",
     "Rule flags mark minutes worth reading. They are not alarms."),
)
NEXT_STEPS = (
    ("EMR folder", "required · the patient_*.csv tables"),
    ("Processed folder", "where analyzed cases are stored"),
    ("Advanced", "optional · wave folder, local AI models and theme"),
)
DEMO_LINK = "Try with fictional demo data"
DEMO_NOTE = ("Fictional cases made on this computer by MOVER Suite. They are not patient records, "
             "and the app says so wherever it names the data source.")


class SetupSide(QFrame):
    """The step list: the current step is marked in words and weight, done steps carry a check."""

    def __init__(self, version: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("setupSide")
        self.setProperty("mode", "light")
        self.setFixedWidth(320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 32, 32)
        layout.setSpacing(0)
        mark = QLabel("M")
        mark.setObjectName("setupMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(54, 54)
        mark.setAccessibleName("MOVER Suite")
        layout.addWidget(mark)
        layout.addSpacing(24)
        name = QLabel("MOVER Suite")
        name.setObjectName("setupName")
        layout.addWidget(name)
        sub = QLabel(f"Version {version} · first-time setup")
        sub.setObjectName("setupSub")
        sub.setWordWrap(True)
        layout.addWidget(sub)
        layout.addSpacing(36)
        self._rows: list[tuple[QLabel, QLabel]] = []
        for number, (title, _blurb) in enumerate(STEPS, start=1):
            row = QHBoxLayout()
            row.setSpacing(12)
            circle = QLabel(str(number))
            circle.setObjectName("setupStepNumber")
            circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            circle.setFixedSize(30, 30)
            label = QLabel(title)
            label.setObjectName("setupStepName")
            row.addWidget(circle)
            row.addWidget(label, 1)
            layout.addLayout(row)
            layout.addSpacing(18)
            self._rows.append((circle, label))
        layout.addStretch(1)
        note = QLabel("Everything here can be changed later in Settings. Nothing leaves this computer.")
        note.setObjectName("setupSub")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.set_step(0)

    def sync_mode(self, theme: str) -> None:
        mode = "dark" if theme == "dark" else "light"
        if self.property("mode") != mode:
            self.setProperty("mode", mode)
            self.style().unpolish(self)
            self.style().polish(self)

    def set_step(self, index: int) -> None:
        for position, (circle, label) in enumerate(self._rows):
            state = "done" if position < index else "current" if position == index else "todo"
            circle.setText("✓" if state == "done" else str(position + 1))
            for widget in (circle, label):
                widget.setProperty("state", state)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
            word = {"done": "done", "current": "current step", "todo": "not yet"}[state]
            label.setAccessibleName(f"Step {position + 1} of {len(STEPS)}: {STEPS[position][0]}, {word}")


class SetupFlow(QWidget):
    """The flow. ``completed`` carries the saved values; ``dismissed`` means the person chose Not now."""

    completed = Signal(dict)
    dismissed = Signal()
    step_changed = Signal(str)

    def __init__(self, version: str, *, first_run: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("setupFlow")
        self.first_run = first_run
        self._demo_worker: DemoDataWorker | None = None
        self._applied: dict[str, str] = {}
        self.side = SetupSide(version)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._welcome_page())
        self.pages.addWidget(self._data_page())
        self.pages.addWidget(self._done_page())
        self.pages.addWidget(self._demo_page())
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.side)
        row.addWidget(self.pages, 1)
        self.side.sync_mode(active_theme())
        self._go(WELCOME)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.StyleChange):
            self.side.sync_mode(active_theme())

    # ----- pages -----
    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(72, 56, 72, 32)
        outer.setSpacing(16)
        return page, outer

    def _title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("setupTitle")
        label.setWordWrap(True)
        label.setAccessibleName(text)
        return label

    def _kicker(self) -> QLabel:
        label = QLabel()
        label.setObjectName("setupKicker")
        return label

    def _nav(self, *widgets: QWidget | None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        for widget in widgets:
            if widget is None:
                row.addStretch(1)
            else:
                row.addWidget(widget)
        return row

    def _button(self, text: str, name: str = "", *, primary: bool = False, text_button: bool = False) -> QPushButton:
        button = QPushButton(text)
        if primary:
            button.setObjectName("primaryBtn")
        elif text_button:
            button.setObjectName("textBtn")
        button.setAccessibleName(name or text)
        return button

    def _demo_button(self) -> QPushButton:
        button = self._button(DEMO_LINK, name="Try with fictional demo data", text_button=True)
        button.setToolTip(DEMO_NOTE)
        button.setAccessibleDescription(DEMO_NOTE)
        button.clicked.connect(self.start_demo)
        return button

    def _welcome_page(self) -> QWidget:
        page, layout = self._page()
        self.welcome_kicker = self._kicker()
        layout.addWidget(self.welcome_kicker)
        layout.addWidget(self._title("A research and teaching tool for the de-identified MOVER SIS dataset."))
        box = QFrame()
        box.setObjectName("setupPromises")
        grid = QVBoxLayout(box)
        grid.setContentsMargins(22, 18, 22, 18)
        grid.setSpacing(12)
        for left, right in ((PROMISES[0], PROMISES[1]), (PROMISES[2], PROMISES[3])):
            pair = QHBoxLayout()
            pair.setSpacing(32)
            for head, body in (left, right):
                cell = QVBoxLayout()
                cell.setSpacing(2)
                title = QLabel(head)
                title.setObjectName("settingName")
                text = QLabel(body)
                text.setObjectName("bodyText")
                text.setWordWrap(True)
                cell.addWidget(title)
                cell.addWidget(text)
                cell.addStretch(1)
                pair.addLayout(cell, 1)
            pair.setAlignment(Qt.AlignmentFlag.AlignTop)
            grid.addLayout(pair)
        layout.addWidget(box)
        ask = QLabel("The next steps ask for")
        ask.setObjectName("bodyText")
        layout.addWidget(ask)
        for head, body in NEXT_STEPS:
            line = QHBoxLayout()
            line.setSpacing(24)
            title = QLabel(head)
            title.setObjectName("settingName")
            title.setFixedWidth(150)
            text = QLabel(body)
            text.setObjectName("bodyText")
            line.addWidget(title)
            line.addWidget(text, 1)
            layout.addLayout(line)
        layout.addStretch(1)
        self.not_now_welcome = self._button("Not now", name="Not now, leave setup", text_button=True)
        self.not_now_welcome.clicked.connect(self._leave)
        self.start_button = self._button("Start setup", primary=True)
        self.start_button.clicked.connect(lambda: self._go(DATA))
        self.demo_button_welcome = self._demo_button()
        layout.addLayout(self._nav(self.demo_button_welcome, None, self.not_now_welcome, self.start_button))
        return page

    def _data_page(self) -> QWidget:
        page, layout = self._page()
        self.data_kicker = self._kicker()
        layout.addWidget(self.data_kicker)
        layout.addWidget(self._title("Where is your MOVER data?"))
        intro = QLabel("Point to the folder that holds the SIS tables. We check it right away and tell you "
                       "exactly what we found.")
        intro.setObjectName("quietText")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        body = QWidget()
        form = QVBoxLayout(body)
        form.setContentsMargins(0, 8, 8, 8)
        form.setSpacing(22)
        self.row_emr = FolderRow(
            "EMR folder", "required", "The tabular SIS export: patient_*.csv tables.",
            "Folder with patient_information.csv", check_emr_folder,
        )
        self.row_processed = FolderRow(
            "Processed folder", "", "Where analyzed cases are stored. It can start empty.",
            "Parquet cache (often next to the EMR folder)", check_processed_folder,
        )
        self.row_emr.checked.connect(self._emr_checked)
        self.row_emr.edit.returnPressed.connect(self._continue_data)
        for row in (self.row_emr, self.row_processed):
            form.addWidget(row)
        preset = SettingRow("Rule preset", "", "Loaded data keeps the preset it was computed with.")
        self.combo_preset = ScrollSafeComboBox()
        self.combo_preset.setAccessibleName("Rule preset")
        for label, value in (("Default", "default"), ("Lenient", "lenient"), ("Strict", "strict")):
            self.combo_preset.addItem(label, value)
        preset.add_control(self.combo_preset, left=True)
        form.addWidget(preset)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setObjectName("setupAdvanced")
        self.advanced_toggle.setText("Advanced · wave folder, local AI and appearance (all optional)")
        self.advanced_toggle.setAccessibleName("Advanced options")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        form.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.advanced = QWidget()
        advanced = QVBoxLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setSpacing(22)
        self.row_wave = FolderRow(
            "Wave folder", "optional", "Waveform archives. Leave empty unless you study waveforms.",
            "Not set: Waveforms/ or sis_wave*.tar.gz", check_wave_folder,
        )
        self.row_models = FolderRow(
            "Models directory", "optional", "Where Ollama keeps its models. Models run on this computer.",
            "OLLAMA_MODELS path", check_models_folder,
        )
        advanced.addWidget(self.row_wave)
        advanced.addWidget(self.row_models)
        model = SettingRow("Preferred model", "optional", "The answer model chosen when the app starts.")
        self.combo_model = ScrollSafeComboBox()
        self.combo_model.setEditable(True)
        self.combo_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo_model.setAccessibleName("Preferred model")
        model.add_control(self.combo_model, left=True)
        advanced.addWidget(model)
        embed = SettingRow("Embedding model", "optional", "Ranks cases by meaning; without it, search uses keywords.")
        self.edit_embed = QLineEdit()
        self.edit_embed.setAccessibleName("Embedding model")
        self.edit_embed.setPlaceholderText("nomic-embed-text")
        embed.add_control(self.edit_embed)
        self.embed_row = embed
        advanced.addWidget(embed)
        theme = SettingRow("Theme", "optional", "Light, dark, or follow the operating system.")
        self.combo_theme = ScrollSafeComboBox()
        self.combo_theme.setAccessibleName("Theme")
        self.combo_theme.addItem("Light", THEME_LIGHT)
        self.combo_theme.addItem("Dark", THEME_DARK)
        self.combo_theme.addItem("System (follow OS)", THEME_SYSTEM)
        theme.add_control(self.combo_theme, left=True)
        advanced.addWidget(theme)
        self.advanced.hide()
        form.addWidget(self.advanced)
        form.addStretch(1)
        layout.addWidget(scroll_panel(body), 1)
        self.data_status = QLabel()
        self.data_status.setObjectName("settingsStatus")
        self.data_status.setWordWrap(True)
        layout.addWidget(self.data_status)
        self.back_button = self._button("Back", text_button=True)
        self.back_button.setShortcut("Alt+Left")
        self.back_button.clicked.connect(lambda: self._go(WELCOME))
        self.not_now_data = self._button("Not now", name="Not now, leave setup", text_button=True)
        self.not_now_data.clicked.connect(self._leave)
        self.demo_button_data = self._demo_button()
        self.continue_button = self._button("Continue", primary=True)
        self.continue_button.setShortcut("Alt+Right")
        self.continue_button.clicked.connect(self._continue_data)
        layout.addLayout(self._nav(self.back_button, self.demo_button_data, None, self.not_now_data,
                                   self.continue_button))
        self._tab_stops = [self.row_emr.edit, self.row_emr.choose_button, self.row_processed.edit,
                           self.row_processed.choose_button, self.combo_preset, self.advanced_toggle,
                           self.row_wave.edit, self.row_wave.choose_button, self.row_models.edit,
                           self.row_models.choose_button, self.combo_model, self.edit_embed, self.combo_theme,
                           self.back_button, self.demo_button_data, self.not_now_data, self.continue_button]
        for first, second in zip(self._tab_stops, self._tab_stops[1:], strict=False):
            QWidget.setTabOrder(first, second)
        return page

    def _done_page(self) -> QWidget:
        page, layout = self._page()
        self.done_kicker = self._kicker()
        layout.addWidget(self.done_kicker)
        layout.addWidget(self._title("Setup is complete."))
        self.done_summary = QLabel()
        self.done_summary.setObjectName("bodyText")
        self.done_summary.setWordWrap(True)
        layout.addWidget(self.done_summary)
        again = QLabel("You can run this setup again from Settings (Ctrl+,) or by pressing Ctrl+K and choosing "
                       "Data setup. Change individual options in Settings.")
        again.setObjectName("quietText")
        again.setWordWrap(True)
        layout.addWidget(again)
        layout.addStretch(1)
        self.back_from_done = self._button("Back", text_button=True)
        self.back_from_done.clicked.connect(lambda: self._go(DATA))
        self.finish_button = self._button("Open MOVER Suite", primary=True)
        self.finish_button.clicked.connect(lambda: self.completed.emit(dict(self._applied)))
        layout.addLayout(self._nav(self.back_from_done, None, self.finish_button))
        return page

    def _demo_page(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(self._title("Making fictional demo data"))
        note = QLabel(DEMO_NOTE)
        note.setObjectName("quietText")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.demo_steps: list[QLabel] = []
        for text in DEMO_STEPS:
            label = QLabel(text)
            label.setObjectName("bodyText")
            layout.addWidget(label)
            self.demo_steps.append(label)
        self.demo_bar = QProgressBar()
        self.demo_bar.setObjectName("turnProgress")
        self.demo_bar.setRange(0, len(DEMO_STEPS))
        self.demo_bar.setTextVisible(False)
        self.demo_bar.setAccessibleName("Demo data progress")
        layout.addWidget(self.demo_bar)
        self.demo_status = QLabel()
        self.demo_status.setObjectName("settingsStatus")
        self.demo_status.setWordWrap(True)
        self.demo_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.demo_status)
        self.demo_detail = QLabel()
        self.demo_detail.setObjectName("quietText")
        self.demo_detail.setWordWrap(True)
        self.demo_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.demo_detail.setAccessibleName("Technical detail")
        layout.addWidget(self.demo_detail)
        layout.addStretch(1)
        self.demo_back = self._button("Back", text_button=True)
        self.demo_back.clicked.connect(lambda: self._go(WELCOME))
        layout.addLayout(self._nav(self.demo_back, None))
        return page

    # ----- values -----
    def load(self) -> None:
        saved = saved_values()
        self.combo_preset.setCurrentIndex(max(0, self.combo_preset.findData(saved["preset"])))
        self.row_emr.set_text(saved["emr_dir"])
        self.row_processed.set_text(saved["processed_dir"])
        self.row_processed.touched = False
        self.row_wave.set_text(saved["wave_dir"])
        self.row_models.set_text(saved["ollama_models_dir"])
        theme_index = self.combo_theme.findData(saved["theme"])
        self.combo_theme.setCurrentIndex(theme_index if theme_index >= 0 else self.combo_theme.findData(THEME_SYSTEM))
        self.edit_embed.setText(saved["ollama_embed_model"])
        self.combo_model.clear()
        self.combo_model.addItem("")
        models, sentence = probe_models(saved["ollama_embed_model"])
        for model in models:
            self.combo_model.addItem(model)
        self.embed_row.set_note(sentence)
        if saved["ollama_model"]:
            if self.combo_model.findText(saved["ollama_model"]) < 0:
                self.combo_model.addItem(saved["ollama_model"])
            self.combo_model.setCurrentIndex(self.combo_model.findText(saved["ollama_model"]))

    def values(self) -> dict[str, str]:
        theme = self.combo_theme.currentData()
        return {
            "emr_dir": self.row_emr.text(),
            "wave_dir": self.row_wave.text(),
            "processed_dir": self.row_processed.text(),
            "ollama_models_dir": self.row_models.text(),
            "ollama_model": self.combo_model.currentText().strip(),
            "ollama_embed_model": self.edit_embed.text().strip() or "nomic-embed-text",
            "theme": theme if theme in (THEME_LIGHT, THEME_DARK, THEME_SYSTEM) else THEME_SYSTEM,
            "preset": self.combo_preset.currentData() or "default",
        }

    def _emr_checked(self) -> None:
        """The processed folder follows the EMR folder until the person sets it themselves."""
        if self.row_processed.touched:
            return
        try:
            emr = resolve_emr_directory(self.row_emr.text())
        except FileNotFoundError:
            return
        self.row_processed.set_text(str(default_processed_for_emr(emr)))
        self.row_processed.touched = False

    def _leave(self) -> None:
        """Leave the flow. Once Continue has saved the values, leaving counts as finishing, not as cancelling."""
        if self._applied:
            self.completed.emit(dict(self._applied))
        else:
            self.dismissed.emit()

    def _toggle_advanced(self, shown: bool) -> None:
        self.advanced.setVisible(shown)
        self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)

    # ----- moving between steps -----
    def _go(self, page: int) -> None:
        self.pages.setCurrentIndex(page)
        shown = min(page, DONE) if page != DEMO else WELCOME
        self.side.set_step(shown)
        kicker = f"Step {shown + 1} of {len(STEPS)}"
        for label in (self.welcome_kicker, self.data_kicker, self.done_kicker):
            label.setText(kicker)
        if page == DATA:
            self.load()
            self.data_status.setText("")
            self.row_emr.edit.setFocus(Qt.FocusReason.OtherFocusReason)
        elif page == WELCOME:
            self.start_button.setFocus(Qt.FocusReason.OtherFocusReason)
        elif page == DONE:
            self.finish_button.setFocus(Qt.FocusReason.OtherFocusReason)
        title = {WELCOME: "Welcome", DATA: "Data folders", DONE: "Done", DEMO: "Demo data"}[page]
        self.step_changed.emit(title)
        announce(self.side, f"{kicker}: {title}")

    def current_page(self) -> int:
        return self.pages.currentIndex()

    def focus_first(self) -> None:
        self.start_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _continue_data(self) -> None:
        """Check the EMR folder, save every value, and go to the last step. A refusal keeps the person here."""
        if self.current_page() != DATA:
            return
        vals = self.values()
        try:
            validate_emr_dir(vals["emr_dir"])
            apply_values(vals, require_emr=True)
        except SaveRefused as exc:
            self._refuse(exc.problems)
            return
        except Exception as exc:
            self._refuse({"emr_dir": " ".join(str(exc).split("\n"))})
            return
        mark_setup_complete()
        self._applied = vals
        self.done_summary.setText(
            f"MOVER Suite will read tables from {vals['emr_dir']} and keep analyzed cases in {vals['processed_dir']}."
        )
        self._go(DONE)

    def _refuse(self, problems: dict[str, str]) -> None:
        """Say what was refused beside the field it is about, announce it, and put the cursor there."""
        rows = {"emr_dir": self.row_emr, "processed_dir": self.row_processed, "wave_dir": self.row_wave,
                "ollama_models_dir": self.row_models}
        first: FolderRow | None = None
        for key, reason in problems.items():
            row = rows.get(key)
            if row is None:
                continue
            sentence = f"Not saved. {' '.join(reason.split(chr(10)))}"
            row.set_note(f"✕ {sentence}", "problem")
            row.note.setAccessibleName(f"{row.edit.accessibleName()} problem")
            row.note.setAccessibleDescription(sentence)
            announce(row.note, sentence)
            first = first or row
        general = problems.get("", "")
        text = f"Not saved. {general + ' ' if general else ''}Fix the folder marked above or choose another."
        self.data_status.setText(text)
        if first is None:
            announce(self.data_status, text)
            return
        if first in (self.row_wave, self.row_models):
            self.advanced_toggle.setChecked(True)
        first.edit.setFocus(Qt.FocusReason.OtherFocusReason)

    # ----- demo data -----
    def start_demo(self) -> None:
        """Make the fictional demo data and use it. Pressing again while it runs does nothing."""
        if self._demo_worker is not None and self._demo_worker.isRunning():
            return
        self._go(DEMO)
        self.demo_bar.setValue(0)
        self.demo_status.setText("")
        self.demo_detail.setText("")
        self.demo_back.setEnabled(False)
        for label in self.demo_steps:
            label.setProperty("state", "todo")
        self._mark_demo_step(-1)
        worker = DemoDataWorker(self)
        worker.step.connect(self._mark_demo_step)
        worker.finished_ok.connect(self._demo_made)
        worker.failed.connect(self._demo_failed)
        self._demo_worker = worker
        worker.start()

    def _mark_demo_step(self, current: int) -> None:
        for index, label in enumerate(self.demo_steps):
            done = index < current
            label.setText(f"{'✓ ' if done else ''}{DEMO_STEPS[index]}{' …' if index == current else ''}")
        self.demo_bar.setValue(max(0, current))
        if current >= 0:
            announce(self.demo_status, DEMO_STEPS[current])

    def _demo_made(self, root: object) -> None:
        try:
            use_demo_data(Path(str(root)))
        except Exception as exc:
            self._demo_failed("Demo data was made but the app could not switch to it.", error_message("demo-data", exc))
            return
        self.demo_bar.setValue(len(DEMO_STEPS))
        for index, label in enumerate(self.demo_steps):
            label.setText(f"✓ {DEMO_STEPS[index]}")
        self.demo_status.setText("Demo data is ready. Opening it now.")
        self.completed.emit(saved_values())

    def _demo_failed(self, message: str, detail: str) -> None:
        self.demo_back.setEnabled(True)
        self.demo_status.setText(f"{message} Nothing else was changed. You can go back and choose your own data folder.")
        self.demo_detail.setText(detail)
        announce(self.demo_status, message)
        self.demo_back.setFocus(Qt.FocusReason.OtherFocusReason)

    def shutdown(self) -> None:
        """Wait for a running demo build so closing the window never leaves a thread behind."""
        worker = self._demo_worker
        if worker is not None and worker.isRunning():
            worker.wait(60_000)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            event.accept()
            if self._demo_worker is None or not self._demo_worker.isRunning():
                self._leave()
            return
        super().keyPressEvent(event)
