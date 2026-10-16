"""Research answers format Markdown without navigation or image resources."""

from math import ceil
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QImage, QTextDocument, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from src.desktop.answer_markdown import MarkdownAnswer, _TextOnlyDocument
from src.desktop.conversation_panel import AnswerText, Turn, TurnWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def answer_view(qapp):
    turn = Turn(1, "Synthetic question", "test-model", state="answered")
    root = TurnWidget(turn)
    browser = root.answer_text

    def render(text):
        turn.answer = text
        root.update_turn(turn)

    root.resize(900, 800)
    root.show()
    qapp.processEvents()
    yield browser, render
    root.close()
    root.deleteLater()
    qapp.processEvents()


def blocks(document):
    block = document.begin()
    while block.isValid():
        yield block
        block = block.next()


def fragments(document):
    for block in blocks(document):
        it = block.begin()
        while not it.atEnd():
            yield it.fragment()
            it += 1


def test_markdown_formats_bold_italic_lists_and_horizontal_rule(answer_view):
    browser, render = answer_view
    render("**bold** and *italic*\n\n- first item\n- second item\n\n---\n\nAfter the rule.")
    text = browser.toPlainText()
    assert "**" not in text and "*italic*" not in text and "---" not in text
    assert "- first item" not in text
    runs = list(fragments(browser.document()))
    assert any(f.text() == "bold" and f.charFormat().fontWeight() >= QFont.Weight.Bold for f in runs)
    assert any(f.text() == "italic" and f.charFormat().fontItalic() for f in runs)
    assert any(b.text() == "first item" and b.textList() is not None for b in blocks(browser.document()))
    assert any(b.blockFormat().hasProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth)
               for b in blocks(browser.document()))


def test_links_and_bare_urls_have_no_anchors_and_cannot_open(answer_view, monkeypatch, qapp):
    browser, render = answer_view
    opened = Mock(return_value=True)
    monkeypatch.setattr(QDesktopServices, "openUrl", opened)
    render("[label](https://example.com) [local](file:///missing.txt)\n\n"
           "https://example.org/bare\n\n<https://example.net/autolink>")
    qapp.processEvents()
    assert "label" in browser.toPlainText() and "local" in browser.toPlainText()
    assert "https://example.org/bare" in browser.toPlainText()
    for fragment in fragments(browser.document()):
        fmt = fragment.charFormat()
        assert not fmt.isAnchor() and not fmt.anchorHref() and not fmt.anchorNames()
    flags = browser.textInteractionFlags()
    assert not flags & Qt.TextInteractionFlag.LinksAccessibleByMouse
    assert not flags & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    assert not browser.openExternalLinks() and not browser.openLinks()
    for label in ("label", "local", "https://example.org/bare", "https://example.net/autolink"):
        cursor = browser.document().find(label)
        assert not cursor.isNull()
        cursor.setPosition(cursor.selectionStart() + 2)
        browser.setTextCursor(cursor)
        browser.ensureCursorVisible()
        QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton,
                         pos=browser.cursorRect(cursor).center())
    assert browser.source().isEmpty()
    opened.assert_not_called()


def test_raw_html_is_text_and_markdown_images_become_alt_text(answer_view, monkeypatch, tmp_path, qapp):
    browser, render = answer_view
    image_path = tmp_path / "synthetic.png"
    image = QImage(40, 40, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(image_path))
    url = QUrl.fromLocalFile(str(image_path)).toString()
    loaded = Mock(return_value=None)
    monkeypatch.setattr(MarkdownAnswer, "loadResource", loaded)
    render(f'<b>x</b>\n\n<img src="{url}">\n\n'
           f'![local alt]({url})\n\n![remote alt](https://example.com/image.png)')
    browser.document().size()
    browser.grab()
    qapp.processEvents()
    text = browser.toPlainText()
    assert "<b>x</b>" in text and f'<img src="{url}">' in text
    assert "local alt" in text and "remote alt" in text
    assert "\ufffc" not in text
    for fragment in fragments(browser.document()):
        assert not fragment.charFormat().isImageFormat()
        if "<b>x</b>" in fragment.text():
            assert fragment.charFormat().fontWeight() < QFont.Weight.Bold
    loaded.assert_not_called()


def test_parsing_document_refuses_image_resources(qapp, tmp_path):
    from PySide6.QtWidgets import QTextBrowser

    image_path = tmp_path / "image.png"
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0xFF0000)
    assert image.save(str(image_path))
    url = QUrl.fromLocalFile(str(image_path))
    kind = QTextDocument.ResourceType.ImageResource
    # Controls: Qt's default loaders read this file, so the overrides below are what stop it.
    assert QTextDocument().resource(kind, url) is not None
    assert QTextBrowser().loadResource(kind, url) is not None
    assert _TextOnlyDocument().resource(kind, url) is None
    assert MarkdownAnswer().loadResource(kind, url) is None
    assert _TextOnlyDocument().resource(kind, QUrl("https://example.com/image.png")) is None


def test_rendered_answer_remains_selectable_and_copyable(answer_view, qapp):
    browser, render = answer_view
    render("**Copy this formatted answer.**")
    browser.setFocus()
    browser.selectAll()
    browser.copy()
    assert "Copy this formatted answer." in qapp.clipboard().text()
    assert browser.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    assert browser.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard


def test_tab_leaves_answer_and_rendered_height_fits(qapp):
    root = QWidget()
    layout = QVBoxLayout(root)
    answer = AnswerText()
    next_button = QPushButton("Next")
    layout.addWidget(answer)
    layout.addWidget(next_button)
    answer.append_markdown("\n\n".join(f"**Section {i}**\n\n- Recorded sample\n- *Missing sample*\n\n---"
                                      for i in range(12)))
    root.resize(450, 800)
    root.show()
    qapp.processEvents()
    for width in (450, 700, 320):
        root.resize(width, root.height())
        qapp.processEvents()
        assert answer.height() >= ceil(answer.document().size().height()) + 2 * answer.frameWidth()
        assert answer.verticalScrollBar().maximum() == 0
        assert answer.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    answer.setFocus()
    QTest.keyClick(answer, Qt.Key.Key_Tab)
    assert qapp.focusWidget() is next_button
    root.close()
    root.deleteLater()
