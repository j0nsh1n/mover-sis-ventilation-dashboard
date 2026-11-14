"""Settings as a page of the window: sections on the left, one section at a time, a save bar under it.

Nothing is written until Save. Leaving the page with edits asks whether to save them, throw them away or stay.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.desktop.accessibility import announce
from src.llm.cloud import KeyStoreError, forget_api_key, key_note, save_api_key
from src.desktop.scroll_safe_combo import ScrollSafeComboBox
from src.desktop.settings_ui import (
    FolderRow,
    SettingRow,
    apply_values,
    changed_labels,
    check_emr_folder,
    check_models_folder,
    check_processed_folder,
    check_wave_folder,
    probe_models,
    saved_values,
    scroll_panel,
)
from src.runtime_paths import (
    default_processed_for_emr,
    demo_root_in_use,
    emr_dir,
    processed_dir,
    resolve_emr_directory,
    wave_dir,
)
from src.user_settings import (
    THEME_DARK,
    THEME_LIGHT,
    THEME_SYSTEM,
    mark_setup_complete,
)

SECTIONS = (
    ("Data sources", "Where MOVER Suite reads its tables. Files are read in place and never uploaded."),
    ("Local AI", "Optional. Ollama runs models on this computer. A cloud service answers only when chosen in the research page's AI menu."),
    ("Appearance", "How the window looks. Charts follow the same choice."),
    ("Keyboard", "Every command is also in the Find box (Ctrl+K)."),
    ("About", "The version of MOVER Suite and where its parts come from."),
)
PRESETS = (("Default", "default"), ("Lenient", "lenient"), ("Strict", "strict"))
LEAVE_SAVE, LEAVE_DISCARD, LEAVE_STAY = "save", "discard", "stay"
_UNBOUNDED = 16777215


class SettingsPage(QWidget):
    """All of today's settings, with the same saved keys and defaults the Settings dialog had."""

    settings_applied = Signal(dict, list)
    close_requested = Signal()
    setup_requested = Signal()
    about_requested = Signal()
    section_changed = Signal(str)
    dirty_changed = Signal(bool)
    # The key goes straight to the password store, never through Save, so it never reaches the settings file.
    api_key_changed = Signal()

    # Below this width the section list moves above the form
    NARROW_BELOW = 760

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._baseline: dict[str, str] = {}
        self._loading = False
        self._result = ""
        self._build()
        self.load()

    # ----- build -----
    def _build(self) -> None:
        self.sections = QListWidget()
        self.sections.setObjectName("settingsSections")
        self.sections.setAccessibleName("Settings sections")
        self.sections.setFrameShape(QFrame.Shape.NoFrame)
        self.sections.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for name, _intro in SECTIONS:
            self.sections.addItem(name)
        self.sections.currentRowChanged.connect(self._section_row_changed)

        self.stack = QStackedWidget()
        self.stack.addWidget(scroll_panel(self._data_section()))
        self.stack.addWidget(scroll_panel(self._ai_section()))
        self.stack.addWidget(scroll_panel(self._appearance_section()))
        self.stack.addWidget(scroll_panel(self._keyboard_section()))
        self.stack.addWidget(scroll_panel(self._about_section()))

        self.status = QLabel()
        self.status.setObjectName("settingsStatus")
        self.status.setWordWrap(True)
        self.discard_button = QPushButton("Discard")
        self.discard_button.setObjectName("textBtn")
        self.discard_button.setAccessibleName("Discard changes")
        self.discard_button.clicked.connect(self.discard)
        self.save_button = QPushButton("Save and reload data")
        self.save_button.setObjectName("primaryBtn")
        self.save_button.setAccessibleName("Save and reload data")
        self.save_button.clicked.connect(self.save)
        self.bar = QWidget()
        self.bar.setObjectName("settingsBar")
        self._bar = QGridLayout(self.bar)
        self._bar.setHorizontalSpacing(12)
        self._bar.setVerticalSpacing(8)

        self._body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._body.addWidget(self.sections, 0, Qt.AlignmentFlag.AlignTop)
        self._body.addWidget(self.stack, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(self._body, 1)
        outer.addWidget(self.bar)
        self._narrow: bool | None = None
        self._reflow(self.NARROW_BELOW)

        chain: list[QWidget] = [self.sections, *self._tab_stops, self.discard_button, self.save_button]
        for first, second in zip(chain, chain[1:], strict=False):
            QWidget.setTabOrder(first, second)
        self.sections.setCurrentRow(0)

    def _reflow(self, width: int) -> None:
        """Wide, the section list sits left of the form; narrow, it moves above it as a wrapped strip.

        Nothing is cut and no control needs sideways scrolling at the window's least width.
        """
        narrow = width < self.NARROW_BELOW
        if narrow != self._narrow:
            self._narrow = narrow
            self.sections.setProperty("narrow", narrow)
            self.sections.style().unpolish(self.sections)
            self.sections.style().polish(self.sections)
            for widget in (self.status, self.discard_button, self.save_button):
                self._bar.removeWidget(widget)
            if narrow:
                self._body.setDirection(QBoxLayout.Direction.TopToBottom)
                self._body.setContentsMargins(16, 12, 16, 0)
                self._body.setSpacing(8)
                self.sections.setFlow(QListView.Flow.LeftToRight)
                self.sections.setWrapping(True)
                self.sections.setResizeMode(QListView.ResizeMode.Adjust)
                self.sections.setMinimumWidth(0)
                self._bar.setContentsMargins(16, 10, 16, 12)
                self._bar.addWidget(self.status, 0, 0, 1, 3)
                self._bar.addWidget(self.discard_button, 1, 1)
                self._bar.addWidget(self.save_button, 1, 2)
                self._bar.setColumnStretch(0, 1)
            else:
                self._body.setDirection(QBoxLayout.Direction.LeftToRight)
                self._body.setContentsMargins(32, 16, 0, 0)
                self._body.setSpacing(24)
                self.sections.setFlow(QListView.Flow.TopToBottom)
                self.sections.setWrapping(False)
                self.sections.setMinimumHeight(0)
                self.sections.setMaximumHeight(_UNBOUNDED)
                self.sections.setFixedWidth(180)
                self._bar.setContentsMargins(32, 12, 32, 16)
                self._bar.addWidget(self.status, 0, 0)
                self._bar.addWidget(self.discard_button, 0, 1)
                self._bar.addWidget(self.save_button, 0, 2)
                self._bar.setColumnStretch(0, 1)
        if narrow:
            self._fit_section_strip(width)

    def _fit_section_strip(self, width: int) -> None:
        """Give the wrapped strip the width the layout will give it and exactly the height its rows need."""
        self.sections.setFixedWidth(max(120, width - 32))
        self.sections.doItemsLayout()
        bottom = max((self.sections.visualItemRect(self.sections.item(row)).bottom()
                      for row in range(self.sections.count())), default=0)
        self.sections.setFixedHeight(bottom + 6)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reflow(self.width())

    def _section_page(self, index: int) -> tuple[QWidget, QVBoxLayout]:
        name, intro = SECTIONS[index]
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 32, 24)
        layout.setSpacing(28)
        head = QVBoxLayout()
        head.setSpacing(6)
        title = QLabel(name)
        title.setObjectName("pageTitle")
        title.setAccessibleName(name)
        lead = QLabel(intro)
        lead.setObjectName("quietText")
        lead.setWordWrap(True)
        head.addWidget(title)
        head.addWidget(lead)
        layout.addLayout(head)
        return page, layout

    def _combo(self, name: str) -> ScrollSafeComboBox:
        combo = ScrollSafeComboBox()
        combo.setAccessibleName(name)
        combo.setMinimumWidth(240)
        return combo

    def _data_section(self) -> QWidget:
        page, layout = self._section_page(0)
        self._tab_stops: list[QWidget] = []
        self.row_emr = FolderRow(
            "EMR folder", "required",
            "The tabular SIS export: patient_information, patient_ventilator and patient_vitals CSVs.",
            "Folder with patient_information.csv", check_emr_folder,
        )
        self.row_processed = FolderRow(
            "Processed folder", "",
            "The cache: parquet files the pipeline writes. Safe to delete; it is rebuilt.",
            "Parquet cache (often next to the EMR folder)", check_processed_folder,
        )
        self.row_wave = FolderRow(
            "Wave folder", "optional",
            "Waveform archives. Leave empty unless you study waveforms.",
            "Not set: Waveforms/ or sis_wave*.tar.gz", check_wave_folder,
        )
        self.row_emr.checked.connect(self._emr_checked)
        for row in (self.row_emr, self.row_processed, self.row_wave):
            layout.addWidget(row)
            row.changed.connect(self._edited)
            self._tab_stops += [row.edit, row.choose_button]
        preset = SettingRow(
            "Rule preset for new runs", "",
            "Loaded data keeps the preset it was computed with.",
        )
        self.combo_preset = self._combo("Rule preset for new runs")
        for label, value in PRESETS:
            self.combo_preset.addItem(label, value)
        self.combo_preset.currentIndexChanged.connect(self._edited)
        preset.add_control(self.combo_preset, left=True)
        preset.set_note("Changing this does not change loaded results until you run the pipeline again.")
        layout.addWidget(preset)
        self._tab_stops.append(self.combo_preset)
        self.setup_row = SettingRow(
            "Data setup", "",
            "Walk through choosing the EMR folder again, or try the app with fictional demo data.",
        )
        self.setup_button = QPushButton("Run data setup…")
        self.setup_button.setAccessibleName("Run data setup")
        self.setup_button.clicked.connect(self.setup_requested)
        self.setup_row.add_control(self.setup_button, left=True)
        self.demo_note = QLabel()
        self.demo_note.setObjectName("demoNote")
        self.demo_note.setWordWrap(True)
        self.demo_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.demo_note.hide()
        self.setup_row.add_control(self.demo_note)
        layout.addWidget(self.setup_row)
        self._tab_stops.append(self.setup_button)
        self.in_use = SettingRow(
            "In use now", "",
            "The saved folders, in full. They change when you save.",
        )
        self.in_use_paths = QLabel()
        self.in_use_paths.setObjectName("bodyText")
        self.in_use_paths.setWordWrap(True)
        self.in_use_paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.in_use_paths.setAccessibleName("Folders in use")
        self.in_use.add_control(self.in_use_paths)
        layout.addWidget(self.in_use)
        layout.addStretch(1)
        return page

    def _ai_section(self) -> QWidget:
        page, layout = self._section_page(1)
        self.key_row = SettingRow(
            "Claude API key", "",
            "For answers from the Claude API. Choose which AI answers in the research page's AI menu.")
        self.edit_api_key = QLineEdit()
        self.edit_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_api_key.setPlaceholderText("Paste a key that starts with sk-ant-")
        self.edit_api_key.setAccessibleName("Claude API key")
        self.edit_api_key.returnPressed.connect(self._save_key)
        self.key_row.add_control(self.edit_api_key)
        key_buttons = QWidget()
        key_buttons_layout = QHBoxLayout(key_buttons)
        key_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.save_key_button = QPushButton("Save key")
        self.save_key_button.clicked.connect(self._save_key)
        self.forget_key_button = QPushButton("Forget key")
        self.forget_key_button.clicked.connect(self._forget_key)
        key_buttons_layout.addWidget(self.save_key_button)
        key_buttons_layout.addWidget(self.forget_key_button)
        key_buttons_layout.addStretch(1)
        self.key_row.add_control(key_buttons, left=True)
        layout.addWidget(self.key_row)
        self.real_data_row = SettingRow(
            "Real MOVER cases", "",
            "Fictional demo and practice data may always use a cloud service chosen in the AI menu.")
        self.combo_real_data = self._combo("Real MOVER cases")
        self.combo_real_data.addItem("Not sent to a cloud service", "")
        self.combo_real_data.addItem("Sent trimmed: case aliases, age bands, minutes from incision", "on")
        self.combo_real_data.currentIndexChanged.connect(self._edited)
        self.real_data_row.add_control(self.combo_real_data)
        self.real_data_row.set_note("Check the MOVER data use agreement before sending real cases.")
        layout.addWidget(self.real_data_row)
        self.row_models = FolderRow(
            "Models directory", "",
            "Models stay on disk, not inside the app. The app sets OLLAMA_MODELS to this folder when it starts Ollama.",
            "OLLAMA_MODELS path", check_models_folder,
        )
        self.row_models.changed.connect(self._edited)
        layout.addWidget(self.row_models)
        preferred = SettingRow("Preferred model", "", "The answer model chosen when the app starts.")
        self.combo_model = self._combo("Preferred model")
        self.combo_model.setEditable(True)
        self.combo_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo_model.currentTextChanged.connect(self._edited)
        preferred.add_control(self.combo_model)
        layout.addWidget(preferred)
        embed = SettingRow("Embedding model", "", "Ranks cases by meaning. Without it, case search uses keywords.")
        self.edit_embed = QLineEdit()
        self.edit_embed.setPlaceholderText("nomic-embed-text")
        self.edit_embed.setAccessibleName("Embedding model")
        self.edit_embed.textChanged.connect(self._edited)
        embed.add_control(self.edit_embed)
        self.embed_ready = embed
        layout.addWidget(embed)
        layout.addStretch(1)
        self._tab_stops += [self.edit_api_key, self.save_key_button, self.forget_key_button, self.combo_real_data,
                            self.row_models.edit, self.row_models.choose_button, self.combo_model, self.edit_embed]
        self._show_key()
        return page

    def _show_key(self, problem: str = "") -> None:
        """Where the key is found, never the key; a failed save or forget says why instead."""
        self.key_row.set_note(problem or key_note(), "problem" if problem else "note")

    def _save_key(self) -> None:
        try:
            save_api_key(self.edit_api_key.text())
        except KeyStoreError as exc:
            self._show_key(str(exc))
            return
        self.edit_api_key.clear()
        self._show_key()
        announce(self.key_row.note, "Key saved.")
        self.api_key_changed.emit()

    def _forget_key(self) -> None:
        try:
            forget_api_key()
        except KeyStoreError as exc:
            self._show_key(str(exc))
            return
        self.edit_api_key.clear()
        self._show_key()
        announce(self.key_row.note, "Saved key removed.")
        self.api_key_changed.emit()

    def _appearance_section(self) -> QWidget:
        page, layout = self._section_page(2)
        row = SettingRow("Theme", "", "Light, dark, or follow the operating system.")
        self.combo_theme = self._combo("Theme")
        self.combo_theme.addItem("Light", THEME_LIGHT)
        self.combo_theme.addItem("Dark", THEME_DARK)
        self.combo_theme.addItem("System (follow OS)", THEME_SYSTEM)
        self.combo_theme.currentIndexChanged.connect(self._edited)
        row.add_control(self.combo_theme, left=True)
        layout.addWidget(row)
        layout.addStretch(1)
        self._tab_stops.append(self.combo_theme)
        return page

    def _keyboard_section(self) -> QWidget:
        page, layout = self._section_page(3)
        self.shortcut_grid = QGridLayout()
        self.shortcut_grid.setHorizontalSpacing(32)
        self.shortcut_grid.setVerticalSpacing(8)
        layout.addLayout(self.shortcut_grid)
        layout.addStretch(1)
        return page

    def _about_section(self) -> QWidget:
        page, layout = self._section_page(4)
        self.about_summary = QLabel()
        self.about_summary.setObjectName("bodyText")
        self.about_summary.setWordWrap(True)
        self.about_button = QPushButton("About MOVER Suite…")
        self.about_button.setAccessibleName("About MOVER Suite")
        self.about_button.clicked.connect(self.about_requested)
        layout.addWidget(self.about_summary)
        layout.addWidget(self.about_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        self._tab_stops.append(self.about_button)
        return page

    # ----- content from the app -----
    def set_shortcuts(self, entries: Sequence[tuple[str, str]]) -> None:
        """Fill the Keyboard section from the window's own commands, so it cannot drift from them."""
        while self.shortcut_grid.count():
            item = self.shortcut_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for row, (name, keys) in enumerate(entries):
            label = QLabel(name)
            label.setObjectName("bodyText")
            label.setWordWrap(True)
            key_label = QLabel(keys)
            key_label.setObjectName("quietText")
            key_label.setWordWrap(True)
            key_label.setAccessibleName(f"{name} shortcut")
            self.shortcut_grid.addWidget(label, row, 0)
            self.shortcut_grid.addWidget(key_label, row, 1)
        self.shortcut_grid.setColumnStretch(2, 1)

    def set_about_summary(self, text: str) -> None:
        self.about_summary.setText(text)

    def set_demo_note(self, text: str) -> None:
        self.demo_note.setText(text)
        self.demo_note.setVisible(bool(text))

    def _refresh_demo_note(self) -> None:
        root = demo_root_in_use()
        if root is not None:
            self.set_demo_note(
                "You are using fictional demo data made by MOVER Suite, not patient records. "
                f"It is stored in {root}. Choose another EMR folder above and save to switch to "
                "real data; the demo folder stays where it is."
            )
        else:
            self.set_demo_note("")

    def _emr_checked(self) -> None:
        """Leaving the demo data: its processed folder would not match another EMR, so the field follows the new one."""
        root = demo_root_in_use()
        if self._loading or root is None or self.row_processed.text() != str(root / "processed"):
            return
        try:
            emr = resolve_emr_directory(self.row_emr.text())
        except FileNotFoundError:
            return
        if not emr.is_relative_to(root):
            self.row_processed.set_text(str(default_processed_for_emr(emr)))

    def refresh_in_use(self) -> None:
        """Show the folders the app is using, in full."""
        wave = wave_dir()
        self.in_use_paths.setText(
            f"EMR: {emr_dir()}\nProcessed: {processed_dir()}\nWave: {wave if wave else 'not set'}"
        )

    # ----- values -----
    def values(self) -> dict[str, str]:
        theme = self.combo_theme.currentData()
        return {
            "emr_dir": self.row_emr.text(),
            "wave_dir": self.row_wave.text(),
            "processed_dir": self.row_processed.text(),
            "ollama_models_dir": self.row_models.text(),
            "ollama_model": self.combo_model.currentText().strip(),
            "ollama_embed_model": self.edit_embed.text().strip() or "nomic-embed-text",
            "cloud_real_data": self.combo_real_data.currentData() or "",
            "theme": theme if theme in (THEME_LIGHT, THEME_DARK, THEME_SYSTEM) else THEME_SYSTEM,
            "preset": self.combo_preset.currentData() or "default",
        }

    def load(self) -> None:
        """Show the saved values and forget any edits."""
        self._loading = True
        try:
            saved = saved_values()
            self.combo_preset.setCurrentIndex(max(0, self.combo_preset.findData(saved["preset"])))
            self.row_emr.set_text(saved["emr_dir"])
            self.row_wave.set_text(saved["wave_dir"])
            self.row_processed.set_text(saved["processed_dir"])
            self.row_models.set_text(saved["ollama_models_dir"])
            theme_index = self.combo_theme.findData(saved["theme"])
            self.combo_theme.setCurrentIndex(theme_index if theme_index >= 0 else self.combo_theme.findData(THEME_SYSTEM))
            self.edit_embed.setText(saved["ollama_embed_model"])
            self.combo_real_data.setCurrentIndex(max(0, self.combo_real_data.findData(saved["cloud_real_data"])))
            self._load_models(saved["ollama_model"], saved["ollama_embed_model"])
            self.refresh_in_use()
            self._refresh_demo_note()
        finally:
            self._loading = False
        self._baseline = self.values()
        self._result = ""
        self._refresh_bar()

    def _load_models(self, preferred: str, embedding_model: str) -> None:
        """Best effort: list what a running Ollama has. Nothing here starts it."""
        self.combo_model.clear()
        self.combo_model.addItem("")
        models, sentence = probe_models(embedding_model)
        for model in models:
            self.combo_model.addItem(model)
        self.embed_ready.set_note(sentence)
        if preferred:
            index = self.combo_model.findText(preferred)
            if index < 0:
                self.combo_model.addItem(preferred)
                index = self.combo_model.findText(preferred)
            self.combo_model.setCurrentIndex(max(0, index))

    def theme_saved(self, theme: str) -> None:
        """A theme chosen elsewhere (the Find box) is already saved: show it and count it as the saved value."""
        index = self.combo_theme.findData(theme)
        if index < 0:
            return
        was = self._loading
        self._loading = True
        try:
            self.combo_theme.setCurrentIndex(index)
        finally:
            self._loading = was
        self._baseline["theme"] = theme
        self._refresh_bar()

    def changes(self) -> list[str]:
        return changed_labels(self._baseline, self.values())

    def is_dirty(self) -> bool:
        return bool(self.changes())

    # ----- sections -----
    def current_section(self) -> str:
        return SECTIONS[max(0, self.sections.currentRow())][0]

    def show_section(self, name: str) -> None:
        for row, (title, _intro) in enumerate(SECTIONS):
            if title == name:
                self.sections.setCurrentRow(row)
                return

    def _section_row_changed(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        self.section_changed.emit(SECTIONS[row][0])

    # ----- save and discard -----
    def _edited(self, *_args) -> None:
        if self._loading:
            return
        self._result = ""
        self._refresh_bar()

    def _refresh_bar(self) -> None:
        names = self.changes()
        dirty = bool(names)
        if dirty:
            word = "change" if len(names) == 1 else "changes"
            text = f"<b>{len(names)} unsaved {word}:</b> {', '.join(names)}"
        else:
            text = self._result or "No unsaved changes."
        self.status.setText(text)
        self.status.setProperty("state", "dirty" if dirty else "clean")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        focused = self.focusWidget()
        self.save_button.setEnabled(dirty)
        self.discard_button.setEnabled(dirty)
        if not dirty and focused in (self.save_button, self.discard_button):
            # A button that disables itself would hand focus to whatever comes first on the window
            self.sections.setFocus(Qt.FocusReason.OtherFocusReason)
        if dirty != self.property("dirty"):
            self.setProperty("dirty", dirty)
            self.dirty_changed.emit(dirty)

    def show_result(self, sentence: str, *, ok: bool = True) -> None:
        """Say what Save did, in the bar, and announce it."""
        self._result = sentence
        self.status.setText(sentence)
        self.status.setProperty("state", "clean" if ok else "problem")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        announce(self.status, sentence)

    def discard(self) -> None:
        self.load()
        self.show_result("Changes discarded. Settings are as last saved.")

    def save(self) -> bool:
        """Write every value. A refusal says why, writes nothing and keeps the edits."""
        values = self.values()
        changed = changed_labels(self._baseline, values)
        try:
            apply_values(values)
        except (ValueError, OSError) as exc:
            reason = " ".join(str(exc).split("\n"))
            self.show_result(f"Not saved. {reason} Your edits are still here.", ok=False)
            return False
        mark_setup_complete()
        self._baseline = values
        self._refresh_bar()
        self.refresh_in_use()
        self._refresh_demo_note()
        self.show_result(f"Saved: {', '.join(changed) if changed else 'nothing had changed'}.")
        self.settings_applied.emit(values, changed)
        return True

    # ----- leaving -----
    def _ask_leave(self) -> str:
        box = QMessageBox(self.window())
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved settings")
        box.setText("Save your changes to Settings?")
        box.setInformativeText(
            f"You changed: {', '.join(self.changes())}. Save writes them, Discard throws them away, "
            "Stay keeps them on the page so you can keep editing."
        )
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        stay = box.addButton("Stay", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(stay)
        box.setEscapeButton(stay)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return LEAVE_SAVE
        if clicked is discard:
            return LEAVE_DISCARD
        return LEAVE_STAY

    def confirm_leave(self) -> bool:
        """True when the page may be left: nothing to save, or the person chose to save or discard."""
        if not self.is_dirty():
            return True
        choice = self._ask_leave()
        if choice == LEAVE_STAY:
            return False
        if choice == LEAVE_SAVE:
            return self.save()
        self.load()
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            event.accept()
            self.close_requested.emit()
            return
        super().keyPressEvent(event)
