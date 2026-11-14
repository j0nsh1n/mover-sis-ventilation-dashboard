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
# GitHub's dialect, because answers use its pipe tables; CommonMark alone shows them as raw "|" text.
# Raw HTML stays off, and links and images are flattened below.
_FEATURES = QTextDocument.MarkdownFeature(
    QTextDocument.MarkdownFeature.MarkdownDialectGitHub.value | QTextDocument.MarkdownFeature.MarkdownNoHTML.value
)


def _table_text(table) -> str:
    """A table as plain rows, cells joined by " | "."""
    return "\n".join(
        " | ".join(table.cellAt(row, column).firstCursorPosition().block().text().strip()
                   for column in range(table.columns()))
        for row in range(table.rows())
    )


def plain_text(markdown: str) -> str:
    """The answer as a reader sees it, without Markdown marks, for the clipboard.

    List items keep their number or a "-" bullet, indented two spaces per nesting level, which
    plain text would otherwise drop. Code keeps its indentation. Paragraphs are separated by a
    blank line; consecutive list items and code lines by a single newline.
    """
    document = _TextOnlyDocument()
    document.setMarkdown(markdown, _FEATURES)
    out: list[str] = []
    previous = ""  # the kind of the last block written: "list", "code", "table" or "text"
    block = document.begin()
    while block.isValid():
        table = QTextCursor(block).currentTable()
        if table is not None:
            if out:
                out.append("\n\n")
            out.append(_table_text(table))
            previous = "table"
            block = document.findBlock(table.lastPosition() + 1)
            continue
        code = block.blockFormat().hasProperty(QTextFormat.Property.BlockCodeLanguage)
        # An image becomes an object character; a hard line break is a line separator.
        text = block.text().replace("\ufffc", "").replace("\u2028", "\n").replace("\xa0", " ")
        text = text.rstrip() if code else text.strip()
        items = block.textList()
        kind = "code" if code else "list" if items is not None else "text"
        if text or code:
            if items is not None:
                marker = "-" if items.format().style() in _BULLETS else items.itemText(block)
                text = "  " * max(items.format().indent() - 1, 0) + f"{marker} {text}"
            if out:
                out.append("\n" if kind == previous and kind in ("list", "code") else "\n\n")
            out.append(text)
            previous = kind
        block = block.next()
    return "".join(out).strip("\n")


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
        document.setMarkdown(text, _FEATURES)
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
