"""Citations read again from the corpus, for the verifier's input check (research §R10).

A fact comes back with its stored value and unit scale; a chunk with its stored text and the
unit its report states. Ids not found are left out.
"""

import asyncio
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import Engine, select

from ingestion import schema
from tools.documents import with_presentation_units
from tools.facts import UNIT_SCALES
from tools.types import SeenItem


class CorpusCitations:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def reload(self, cite_ids: Iterable[str]) -> dict[str, SeenItem]:
        return await asyncio.to_thread(self._reload, list(dict.fromkeys(cite_ids)))

    def _reload(self, cite_ids: list[str]) -> dict[str, SeenItem]:
        fact_ids = [int(c[2:]) for c in cite_ids if c.startswith("f:") and c[2:].isdigit()]
        chunk_ids = [c[2:] for c in cite_ids if c.startswith("c:")]
        found: list[SeenItem] = []
        facts, chunks = schema.financial_facts, schema.chunks
        with self._engine.connect() as conn:
            if fact_ids:
                rows = conn.execute(
                    select(facts.c.id, facts.c.value, facts.c.unit).where(facts.c.id.in_(fact_ids))
                )
                found += [
                    SeenItem(
                        cite_id=f"f:{r.id}",
                        text="",
                        value=Decimal(r.value),
                        scale=UNIT_SCALES.get(r.unit, Decimal(1)),
                        source={"kind": "fact"},
                    )
                    for r in rows
                ]
            if chunk_ids:
                rows = conn.execute(
                    select(chunks.c.id, chunks.c.text, chunks.c.document_id).where(
                        chunks.c.id.in_(chunk_ids)
                    )
                )
                chunk_items = [
                    SeenItem(
                        cite_id=f"c:{r.id}",
                        text=r.text,
                        source={"kind": "chunk", "document_id": r.document_id},
                    )
                    for r in rows
                ]
                found += with_presentation_units(self._engine, chunk_items)
        return {item.cite_id: item for item in found}
