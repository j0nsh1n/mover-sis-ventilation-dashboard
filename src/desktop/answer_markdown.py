"""Display model Markdown without navigation, HTML, or image resources."""

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
    QTextDocumentFragment, QTextFormat, QTextListFormat,
)
from PySide6.QtWidgets import QTextBrowser


class _TextOnlyDocument(QTextDocument):
    def loadResource(self, resource_type, name):  # noqa: N802
        # Markdown images can request a resource before their objects are removed.
        return None


_BULLETS = {QTextListFormat.Style.ListDisc, QTextListFormat.Style.ListCircle, QTextListFormat.Style.ListSquare}


def plain_text(markdown: str) -> str:
    """The answer as a reader sees it, without Markdown marks, for the clipboard.

    List items keep their number or a "-" bullet, which plain text would otherwise drop;
    paragraphs are separated by a blank line and items of one list by a single newline.
    """
    document = _TextOnlyDocument()
    document.setMarkdown(markdown, QTextDocument.MarkdownFeature.MarkdownNoHTML)
    out: list[str] = []
    previous_list = None
    block = document.begin()
    while block.isValid():
        # An image becomes an object character in plain text; the answer keeps only words.
        text = block.text().replace("\ufffc", "").strip()
        items = block.textList()
        if text:
            if items is not None:
                marker = "-" if items.format().style() in _BULLETS else items.itemText(block)
                text = f"{marker} {text}"
            if out:
                out.append("\n" if items is not None and items == previous_list else "\n\n")
            out.append(text)
            previous_list = items
        block = block.next()
    return "".join(out)


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
