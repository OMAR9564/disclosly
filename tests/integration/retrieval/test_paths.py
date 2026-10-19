"""Vector and full-text paths: filters are applied in SQL and never violated (FR-015)."""

import pytest
from sqlalchemy import select

from ingestion import schema
from ingestion.models import DocType, Period
from retrieval.fulltext import fulltext_search
from retrieval.types import SearchFilters
from retrieval.vector import vector_search
from tests.integration.retrieval.corpus import VERSION, Corpus, basis

pytestmark = pytest.mark.integration

FILTERS = [
    SearchFilters(),
    SearchFilters(tickers=["THYAO"]),
    SearchFilters(tickers=["AAPL"], language="en"),
    SearchFilters(language="tr"),
    SearchFilters(doc_types=[DocType.KAP_FINANCIAL_REPORT, DocType.TEN_Q]),
    SearchFilters(sections=["Item 7", "body"]),
    SearchFilters(period_from="2025-Q1"),
    SearchFilters(period_to="2024-FY"),
    SearchFilters(period_from="2025-Q3", period_to="2025-FY"),
]


def _vector(corpus: Corpus, axis: int, filters: SearchFilters) -> list[str]:
    with corpus.engine.begin() as conn:
        return vector_search(conn, basis(axis), filters, chunker_version=VERSION, limit=50)


def _text(corpus: Corpus, query: str, filters: SearchFilters | None = None) -> list[str]:
    with corpus.engine.begin() as conn:
        return fulltext_search(conn, query, filters, chunker_version=VERSION, limit=50)


def _check(corpus: Corpus, chunk_ids: list[str], f: SearchFilters) -> None:
    chunks, sections = schema.chunks, schema.sections
    query = select(chunks, sections.c.label).join(sections).where(chunks.c.id.in_(chunk_ids))
    with corpus.engine.connect() as conn:
        rows = conn.execute(query).all()
    assert len(rows) == len(chunk_ids)
    for row in rows:
        assert row.chunker_version == VERSION
        assert row.document_id != 20  # superseded
        if f.tickers is not None:
            assert row.ticker in f.tickers
        if f.language is not None:
            assert row.language == f.language
        if f.doc_types is not None:
            assert row.doc_type in f.doc_types
        if f.sections is not None:
            assert row.label in f.sections
        period = Period.parse(row.period)
        if f.period_from is not None:
            assert period >= Period.parse(f.period_from)
        if f.period_to is not None:
            assert period <= Period.parse(f.period_to)


@pytest.mark.parametrize("filters", FILTERS)
def test_filters_never_violated(corpus: Corpus, filters: SearchFilters) -> None:
    for axis in range(6):
        _check(corpus, _vector(corpus, axis, filters), filters)
    for query in ("net borç revenue", "favok uçak supply", "filo growth"):
        _check(corpus, _text(corpus, query, filters), filters)


def test_vector_nearest_first_and_filtered(corpus: Corpus) -> None:
    everything = _vector(corpus, 0, SearchFilters())
    assert set(corpus.names(everything[:2])) == {"thy-debt", "aapl-debt"}
    assert "aapl-old" not in corpus.names(everything)  # superseded document
    assert "thy-v2" not in corpus.names(everything)  # other chunker version
    assert "aapl-raw" not in corpus.names(everything)  # no embedding yet
    assert corpus.names(_vector(corpus, 0, SearchFilters(tickers=["THYAO"])))[0] == "thy-debt"


def test_unaccent_favok_matches_favok_with_umlaut(corpus: Corpus) -> None:
    assert corpus.names(_text(corpus, "favok")) == ["thy-favok"]
    assert corpus.names(_text(corpus, "FAVÖK marjı"))[0] == "thy-favok"


def test_fulltext_ranks_more_matches_first(corpus: Corpus) -> None:
    names = corpus.names(_text(corpus, "net debt revenue"))
    assert names[0] == "aapl-debt"
    assert "aapl-raw" in names  # full text does not need an embedding
    assert "aapl-old" not in names


def test_fulltext_empty_queries(corpus: Corpus) -> None:
    assert _text(corpus, "  ...  ") == []
    assert _text(corpus, "zzzqqq") == []
    assert _text(corpus, "net", SearchFilters(tickers=["NOPE"])) == []
