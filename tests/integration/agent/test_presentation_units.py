"""Report units for chunk items, read from the KAP report's "Sunum Para Birimi" line."""

from decimal import Decimal

from sqlalchemy import update

from ingestion import schema
from tests.integration.retrieval.corpus import Corpus, corpus  # noqa: F401  (fixture)
from tools.documents import with_presentation_units
from tools.types import SeenItem


def _item(chunk: str, document_id: int | None) -> SeenItem:
    return SeenItem(cite_id=f"c:{chunk}", text="88831", source={"document_id": document_id})


def test_chunks_get_their_document_unit(corpus: Corpus) -> None:  # noqa: F811
    with corpus.engine.begin() as conn:
        conn.execute(
            update(schema.chunks)
            .where(schema.chunks.c.id == corpus.ids["thy-debt"])
            .values(text="Sunum Para Birimi: 1.000.000 TL Finansal Tablo Niteliği: Konsolide")
        )
    items = [_item("a", 10), _item("b", 21), _item("c", None)]

    got = with_presentation_units(corpus.engine, items)

    assert (got[0].scale, got[0].unit_note) == (Decimal(10) ** 6, "1.000.000 TL")
    assert got[1] == items[1]  # a 10-K states no presentation unit
    assert got[2] == items[2]
