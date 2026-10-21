"""Migrated database with the retrieval fixture corpus plus financial facts."""

from decimal import Decimal

import pytest
from sqlalchemy import Engine, insert

from ingestion import schema
from tests.integration.ingestion.conftest import engine
from tests.integration.retrieval.corpus import Corpus, load

__all__ = ["engine", "facts_corpus"]

FACTS = [
    {"id": 501, "company_id": 1, "metric": "net_income", "period": "2025-Q3",
     "value": Decimal("1234.5"), "unit": "million", "currency": "TRY",
     "source_document_id": 10, "source_ref": "table 3"},
    {"id": 502, "company_id": 1, "metric": "net_income", "period": "2024-Q3",
     "value": Decimal("1000"), "unit": "million", "currency": "TRY",
     "source_document_id": 10, "source_ref": "table 3"},
    {"id": 503, "company_id": 1, "metric": "revenue", "period": "2025-Q3",
     "value": Decimal("9000"), "unit": "million", "currency": "TRY",
     "source_document_id": 10, "source_ref": "table 1"},
    {"id": 601, "company_id": 2, "metric": "net_income", "period": "2025-Q3",
     "value": Decimal("23.4"), "unit": "billion", "currency": "USD",
     "source_document_id": 22, "source_ref": "Item 1"},
]  # fmt: skip


@pytest.fixture
def facts_corpus(engine: Engine) -> Corpus:
    corpus = load(engine)
    with engine.begin() as conn:
        conn.execute(insert(schema.financial_facts), FACTS)
    return corpus
