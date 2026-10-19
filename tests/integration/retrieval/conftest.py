"""Fixture corpus in a migrated Postgres for retrieval tests."""

from tests.integration.ingestion.conftest import engine
from tests.integration.retrieval.corpus import corpus

__all__ = ["corpus", "engine"]
