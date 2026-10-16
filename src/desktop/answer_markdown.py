"""Display model Markdown without navigation, HTML, or image resources."""

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
    QTextDocumentFragment, QTextFormat,
)
from PySide6.QtWidgets import QTextBrowser


class _TextOnlyDocument(QTextDocument):
    def loadResource(self, resource_type, name):  # noqa: N802
        # Markdown images can request a resource before their objects are removed.
        return None


class MarkdownAnswer(QTextBrowser):
    """Selectable answer display with no link navigation or resource loading."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setTabChangesFocus(True)

    def loadResource(self, resource_type, name):  # noqa: N802
        return None

    def append_markdown(self, text: str) -> None:
        document = _TextOnlyDocument()
        document.setDefaultFont(self.document().defaultFont())
        document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownNoHTML)
        replacements: list[tuple[int, int, QTextCharFormat]] = []
        block = document.begin()
        while block.isValid():
            fragments = block.begin()
            while not fragments.atEnd():
                fragment = fragments.fragment()
                fmt = fragment.charFormat()
                if fmt.isImageFormat() or fmt.isAnchor():
                    replacements.append((fragment.position(), fragment.length(), fmt))
                fragments += 1
            block = block.next()

        for position, length, fmt in reversed(replacements):
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            if fmt.isImageFormat():
                alt = str(fmt.property(QTextFormat.Property.ImageAltText) or "")
                cursor.insertText(alt, QTextCharFormat())
            else:
                fmt.setAnchor(False)
                fmt.setAnchorHref("")
                fmt.setAnchorNames([])
                fmt.setFontUnderline(False)
                fmt.clearForeground()
                cursor.setCharFormat(fmt)

        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.document().isEmpty():
            cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
        cursor.insertFragment(QTextDocumentFragment(document))
        self.setTextCursor(cursor)
