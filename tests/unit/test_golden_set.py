"""The retrieval golden set follows contracts/golden-set.md."""

from collections import Counter

from evals.retrieval import GOLDEN_PATH, load_golden
from ingestion.models import DocType

SEC_TYPES = {DocType.TEN_K, DocType.TEN_Q}


def test_counts_and_languages() -> None:
    cases = load_golden(GOLDEN_PATH)  # validates the schema and unique IDs
    assert len(cases) >= 50
    languages = Counter(c.language for c in cases)
    assert languages["tr"] >= 20 and languages["en"] >= 20


def test_both_sources_covered() -> None:
    types = {e.doc_type for c in load_golden(GOLDEN_PATH) for e in c.expected}
    assert types & SEC_TYPES
    assert types - SEC_TYPES  # at least one KAP type


def test_ids_carry_the_language_and_quotes_are_phrases() -> None:
    for case in load_golden(GOLDEN_PATH):
        assert case.id.startswith(f"{case.language}-"), case.id
        for expected in case.expected:
            assert len(expected.quote.split()) >= 2, case.id
