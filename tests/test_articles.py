"""The article source is local, bounded, and isolated from surgery records."""

from __future__ import annotations

import csv
import json
import socket
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "articles_sample.jsonl"


def catalog(tmp_path, embedder=None):
    from src.services.articles import ArticleCatalog
    return ArticleCatalog(tmp_path / "case_embeddings.sqlite3", embedder)


def row(**changes):
    return {**json.loads(FIXTURE.read_text().splitlines()[0]), **changes}


@pytest.mark.parametrize("suffix", ["jsonl", "csv"])
def test_import_validation_reports_bad_rows_and_duplicates(tmp_path, suffix):
    items = [row(), row(), row(id="missing", title=""), row(id="bad-year", year="nan"),
             {"id": "missing-fields"}, row(id="valid-second")]
    path = tmp_path / f"articles.{suffix}"
    if suffix == "csv":
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row()))
            writer.writeheader()
            writer.writerows(items)
    else:
        path.write_text("\n".join(json.dumps(r) for r in items) + "\n{invalid\n[]\n")
    store = catalog(tmp_path)
    report = store.import_file(path)
    assert report.imported == 2
    assert len(report.errors) == (4 if suffix == "csv" else 6)
    assert "Row 3: duplicate" in report.errors[0] if suffix == "csv" else "Row 2: duplicate" in report.errors[0]
    assert store.count == 2
    assert store.import_file(path).imported == 0
    assert store.path == tmp_path / "articles.sqlite3"
    assert all(a.imported_at for a in store.search("pressure").articles)


def test_missing_csv_columns_are_reported(tmp_path):
    path = tmp_path / "missing.csv"
    path.write_text("id,title\nexample,title\n")
    report = catalog(tmp_path).import_file(path)
    assert report.imported == 0 and "missing fields: abstract" in report.errors[0]


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def write_csv(path, items):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row()))
        writer.writeheader()
        writer.writerows(items)


def test_long_csv_abstract_under_the_cap_imports_and_over_it_reports_and_rolls_back(tmp_path):
    from src.services.articles import CSV_FIELD_LIMIT, ArticleCatalogError
    store = catalog(tmp_path)
    path = tmp_path / "long.csv"
    write_csv(path, [row(id="long", abstract="a" * 131_073)])
    assert store.import_file(path).imported == 1 and csv.field_size_limit() == 131_072

    filler = [row(id=f"filler-{i}", title=f"Filler {i}", abstract="b" * 400) for i in range(60)]
    write_csv(path, [*filler, row(id="huge", abstract="c" * (CSV_FIELD_LIMIT + 1)), row(id="after")])
    with pytest.raises(ArticleCatalogError, match=r"Line [0-9,]+: a field is larger than 1,000,000 characters\. Nothing was imported"):
        store.import_file(path)
    assert store.count == 1 and csv.field_size_limit() == 131_072


def test_invalid_utf8_mid_file_imports_nothing(tmp_path):
    from src.services.articles import ArticleCatalogError
    store = catalog(tmp_path)
    good = "\n".join(json.dumps(row(id=f"id-{i}", abstract="d" * 400)) for i in range(60))
    path = tmp_path / "bad.jsonl"
    path.write_bytes(good.encode() + b"\n" + json.dumps(row(id="bad")).encode().replace(b"fictional", b"fict\xff\xfeonal") + b"\n")
    with pytest.raises(ArticleCatalogError, match=r"Line [0-9,]+: .*Nothing was imported"):
        store.import_file(path)
    assert store.count == 0 and not store.search("fictional").articles


def test_sql_shaped_title_round_trips_and_deletes_nothing(tmp_path):
    store = catalog(tmp_path)
    title = "x'; DELETE FROM articles; --"
    path = tmp_path / "sql.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [row(id="first"), row(id="evil", title=title), row(id="third")]))
    assert store.import_file(path).imported == 3
    assert store.count == 3
    assert title in {a.title for a in store.search("DELETE").articles}


def test_oversized_field_is_a_reported_dialog_not_a_crash(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from src.desktop.app import MainWindow
    from src.services.articles import CSV_FIELD_LIMIT
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "missing-emr"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "models"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    path = tmp_path / "huge.csv"
    write_csv(path, [row(id="ok"), row(id="huge", abstract="c" * (CSV_FIELD_LIMIT + 1))])
    shown = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: shown.append((title, text)))
    win = MainWindow()
    try:
        win._import_articles()
        assert shown and shown[0][0] == "Article import failed" and "Line" in shown[0][1]
        assert win._article_catalog.count == 0
    finally:
        win.close()


def test_fts_search_matches_words_and_handles_query_syntax(tmp_path):
    store = catalog(tmp_path)
    assert store.import_file(FIXTURE).imported == 12
    assert store.search("laparoscopy").articles[0].title == row()["title"]
    assert store.search("pressure").articles
    assert not store.search("press").articles
    assert not store.search('" OR * - () xylophone').articles
    assert not store.search(" ").articles
    assert len(store.search("fictional", limit=2).articles) == 2
    reopened = catalog(tmp_path)
    assert reopened.count == 12 and reopened.search("laparoscopy").articles


def test_article_ids_never_match_pid_tokens(tmp_path):
    from src.llm.agent import _PID_TOKEN
    path = tmp_path / "ids.jsonl"
    path.write_text("\n".join(json.dumps(row(id=pid)) for pid in
                              ["0123456789abcdef", "SYN0123456789", "A-SYN0123456789", "other"]))
    store = catalog(tmp_path)
    store.import_file(path)
    ids = [a.id for a in store.search("fictional").articles]
    assert len(set(ids)) == 4
    assert all(i.startswith("A-") and _PID_TOKEN.search(i) is None for i in ids)


def test_blended_ranking_and_shared_model_text_cache(tmp_path):
    from src.services.retrieval import EmbeddingCache, source_text_hash

    class Embedder:
        model_id = "fake-local"

        def __init__(self):
            self.calls = []

        def is_available(self):
            return True

        def embed(self, texts):
            self.calls.append(list(texts))
            return [[1., 0.] if "oxygen" in t.lower() or t == "pressure" else [0., 1.] for t in texts]

    embedder = Embedder()
    store = catalog(tmp_path, embedder)
    store.import_file(FIXTURE)
    result = store.search("pressure")
    assert result.rank_method == "blended"
    assert "oxygen" in result.articles[0].title.lower()
    first = result.articles[0]
    cache = EmbeddingCache(tmp_path / "case_embeddings.sqlite3")
    assert cache.get(source_text_hash(first.search_text), embedder.model_id) == [1., 0.]
    embedder.calls.clear()
    assert store.search("pressure").articles == result.articles
    assert embedder.calls == [["pressure"]]
    embedder.model_id = "new-model"
    store.search("pressure")
    assert any(len(texts) == 12 for texts in embedder.calls)


@pytest.mark.parametrize("available", [False, True])
def test_unavailable_or_failed_embeddings_fall_back_to_fts(tmp_path, available):
    class Embedder:
        model_id = "missing"

        def is_available(self):
            return available

        def embed(self, texts):
            raise OSError("local model unavailable")

    store = catalog(tmp_path, Embedder())
    store.import_file(FIXTURE)
    result = store.search("pressure")
    assert result.rank_method == "fts" and result.articles


def test_missing_fts5_has_a_clear_message(monkeypatch, tmp_path):
    import src.services.articles as articles

    def unavailable(*args, **kwargs):
        raise articles.sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(articles.sqlite3, "connect", unavailable)
    with pytest.raises(articles.ArticleCatalogError, match="SQLite build lacks FTS5"):
        articles.ArticleCatalog(tmp_path / "case_embeddings.sqlite3")


def test_local_import_and_search_never_open_a_socket(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("article catalog attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    store = catalog(tmp_path)
    store.import_file(FIXTURE)
    assert store.search("pressure").articles


def test_answer_has_five_abstracts_no_case_session_and_no_tools(tmp_path, monkeypatch):
    from src.llm.retrieve import answer_from_articles
    from src.llm.ollama_client import ChatResult
    from src.llm.agent import run_agent

    store = catalog(tmp_path)
    store.import_file(FIXTURE)
    articles = [replace(a, abstract=f"Unique abstract marker {i}.")
                for i, a in enumerate(store.search("fictional").articles)]
    sent = []
    sessions = []

    def checked_agent(**kwargs):
        sessions.append(kwargs["session"])
        assert kwargs["allow_tools"] is False and kwargs["skip_prefetch"] is True
        return run_agent(**kwargs)

    class Client:
        def chat_turn(self, model, messages, **kwargs):
            assert not kwargs.get("tools")
            if kwargs.get("format"):
                return ChatResult(content=json.dumps(dict.fromkeys(kwargs["format"]["required"], "description")))
            sent.extend(m["content"] for m in messages)
            return ChatResult(content="The fictional paper described historical observations [A1].")

    monkeypatch.setattr("src.llm.retrieve.run_agent", checked_agent)
    result = answer_from_articles(question="pressure", model="fake", articles=articles, client=Client())
    prompt = "\n".join(sent)
    assert "[A1]" in result.answer
    assert "Local article abstracts only" in result.answer and "SIS fields" not in result.answer
    for i in range(5):
        assert f"Unique abstract marker {i}." in prompt
    for i in range(5, len(articles)):
        assert f"Unique abstract marker {i}." not in prompt
    assert "[A6]" not in prompt and "PID=" not in prompt and "scenario description" not in prompt
    assert sessions[0].cases is None and not sessions[0].cited_pids
    assert sessions[0].timeseries is None and sessions[0].medications is None


@pytest.mark.parametrize("rewrite", ["Case SYN0123456789 still had PIP 35 [A1].", "The paper reported PIP values [A1]."],
                         ids=["rewrite_keeps_pid", "rewrite_fixes_it"])
def test_fabricated_case_pid_never_reaches_an_article_answer(tmp_path, rewrite):
    from src.llm.ollama_client import ChatResult
    from src.llm.retrieve import answer_from_articles

    store = catalog(tmp_path)
    store.import_file(FIXTURE)
    drafts = ["Case SYN0123456789 had PIP 35 [A1].", rewrite]
    seen = []

    class Client:
        def chat_turn(self, model, messages, **kwargs):
            if kwargs.get("format"):
                return ChatResult(content=json.dumps(dict.fromkeys(kwargs["format"]["required"], "description")))
            seen.append(messages[-1]["content"])
            return ChatResult(content=drafts[min(len(seen), 2) - 1])

    result = answer_from_articles(question="pressure", model="fake", articles=list(store.search("fictional").articles), client=Client())
    assert len(seen) == 2 and "case-ID-like" in seen[1]
    assert "SYN0123456789" not in result.answer and "[A1]" in result.answer
    assert not any("SYN0123456789" in step for step in result.tool_trace)


def test_article_board_has_inert_sources_citations_and_saved_scope(qapp, tmp_path):
    from src.desktop.research_workspace import ResearchWorkspace
    store = catalog(tmp_path)
    store.import_file(FIXTURE)
    view = ResearchWorkspace()
    view.set_article_catalog(store.count)
    view.scope_combo.setCurrentText("Articles")
    view.begin_question("pressure", 1)
    result = store.search("pressure")
    view.set_article_results(result)
    view.set_answer("Historical observations [A1].", 1)
    view.set_searching(False)
    assert view.scope_label.text() == "Articles: 12 in the local catalog"
    assert view.case_shortlist_card.isHidden()
    assert result.articles[0].doi_or_url in view.article_list.item(0).text()
    assert not view.article_list.itemWidget(view.article_list.item(0))
    widget = view.conversation.turn_widgets[1]
    assert "[A1]" in widget.answer_text.toPlainText()
    assert not widget.citation_buttons
    view.scope_combo.setCurrentText("Cases")
    view.show_turn(1)
    assert view.scope == "Articles" and view.article_result == result
    assert view.article_list.count() == len(result.articles)
    view.scope_combo.setCurrentText("Cases")
    asked = []
    view.question_submitted.connect(lambda q: asked.append((q, view.scope)))
    widget.ask_button.click()
    assert asked == [("pressure", "Articles")]
    view.begin_question("running article question", 2)
    view.scope_combo.setCurrentText("Cases")
    assert view.scope == "Cases" and view.scope_combo.currentText() == "Cases"
    assert view.conversation.turns[-1].state == "cancelled"
    assert view.conversation.turns[-1].board.scope == "Articles"
    view.close()


def test_articles_work_without_emr_and_show_empty_states(qapp, tmp_path, monkeypatch):
    from src.desktop.app import MainWindow
    from src.services.articles import NO_CATALOG, NO_MATCHES
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("MOVER_EMR_DIR", str(tmp_path / "missing-emr"))
    monkeypatch.setenv("MOVER_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "models"))
    monkeypatch.setattr(MainWindow, "_startup", lambda self: None)
    monkeypatch.setattr("src.llm.ollama_client.OllamaEmbedder.is_available", lambda self: False)
    win = MainWindow()
    view = win.research
    view.set_models([])
    view.set_setup_required(True)
    view.scope_combo.setCurrentText("Articles")
    assert view.ask_button.isEnabled() and view.status_label.text() == NO_CATALOG

    def ask(question):
        win._start_research_search(question)
        deadline = time.monotonic() + 5
        while view._searching and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert not view._searching

    try:
        ask("pressure")
        assert view.conversation.turns[-1].answer == NO_CATALOG
        win._article_catalog.import_file(FIXTURE)
        ask("xylophone")
        assert view.conversation.turns[-1].answer == NO_MATCHES
        assert view.article_list.item(0).text() == NO_MATCHES
        ask("pressure")
        assert view.article_result.articles and view.conversation.turns[-1].state == "no_model"
        assert win._case_worker is None and win._research_engine is None
    finally:
        win.close()


def test_article_worker_does_not_touch_supplied_case_extracts(tmp_path, monkeypatch):
    from src.desktop.research_workers import ResearchWorker
    from src.llm.tools import SessionState
    from src.llm.ollama_client import ChatResult

    class Client:
        def chat_turn(self, model, messages, **kwargs):
            assert "CASE_SECRET" not in str(messages)
            if kwargs.get("format"):
                return ChatResult(content=json.dumps(dict.fromkeys(kwargs["format"]["required"], "description")))
            return ChatResult(content="The fictional abstract described pressure [A1].")

    store = catalog(tmp_path)
    store.import_file(FIXTURE)
    monkeypatch.setattr("src.desktop.research_workers.OllamaClient", lambda **kwargs: Client())
    worker = ResearchWorker(store, "pressure", "fake", SessionState(cases=pd.DataFrame({"PID": ["CASE_SECRET"]})))
    responses, errors = [], []
    worker.answer.connect(responses.append)
    worker.failed.connect(errors.append)
    worker.run()
    assert not errors and "[A1]" in responses[0].answer


def test_driver_registers_articles_scenario(tmp_path):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("articles_desktop_drive", FIXTURE.parents[2] / "tools/desktop_drive.py")
    driver = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = driver
    spec.loader.exec_module(driver)
    scenario = driver.SCENARIOS["articles"]
    assert [step.name for step in scenario.steps] == ["import-articles", "article-answer"]
    assert scenario.llm_modes == ("scripted", "off")
    assert "articles" in driver.parse_args(["--emr", str(tmp_path), "--out", str(tmp_path),
                                          "--scenario", "articles"]).scenario


def test_a_number_not_in_the_abstracts_is_removed_from_an_article_answer(tmp_path):
    from src.llm.grounding import REMOVED_MARK
    from src.llm.ollama_client import ChatResult
    from src.llm.retrieve import answer_from_articles

    store = catalog(tmp_path)
    store.import_file(FIXTURE)
    articles = [replace(a, abstract="Peak pressure was reported without a numeric value.", title="Fictional pressure study",
                        year=2019, authors="Fictional Author")
                for a in store.search("fictional").articles]
    fabricated = "The paper reported a peak pressure of 47 cmH2O [A1]."
    seen = []

    class Client:
        def chat_turn(self, model, messages, **kwargs):
            if kwargs.get("format"):
                return ChatResult(content=json.dumps(dict.fromkeys(kwargs["format"]["required"], "description")))
            seen.append(messages[-1]["content"])
            return ChatResult(content=fabricated)

    result = answer_from_articles(question="pressure", model="fake", articles=articles, client=Client())
    assert len(seen) == 2 and "the number 47 is not in the source extracts" in seen[1]
    assert "47" not in result.answer and f"of {REMOVED_MARK} cmH2O [A1]" in result.answer
