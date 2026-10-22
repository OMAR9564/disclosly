"""Integration tests for tools.facts (T013)."""

from decimal import Decimal

import pytest

from llm.types import ToolCall
from tests.integration.retrieval.corpus import Corpus
from tools.facts import get_financial_facts
from tools.registry import ToolRegistry


async def test_returns_requested_facts_with_cite_ids(facts_corpus: Corpus) -> None:
    items = await get_financial_facts(
        facts_corpus.engine, ticker="thyao", metrics=["net_income"], periods=["2025-Q3", "2024-Q3"]
    )
    assert [i.cite_id for i in items] == ["f:502", "f:501"]
    latest = items[1]
    assert latest.value == Decimal("1234.5")
    assert latest.source["currency"] == "TRY"
    assert latest.source["unit"] == "million"
    assert latest.source["period"] == "2025-Q3"
    assert latest.source["source_url"] == "https://example.test/doc/10"
    assert latest.text == "Net dönem kârı (2025-Q3): 1.234,5 milyon TRY"


async def test_excerpt_follows_the_run_language(facts_corpus: Corpus) -> None:
    items = await get_financial_facts(
        facts_corpus.engine,
        ticker="AAPL",
        metrics=["net_income"],
        periods=["2025-Q3"],
        language="en",
    )
    # The fixture fact sits in a 2025-Q1 document, so the excerpt names that report.
    assert items[0].text == "Net income (2025-Q3; 2025-Q1 report): 23.4 billion USD"


async def test_filters_by_ticker(facts_corpus: Corpus) -> None:
    items = await get_financial_facts(
        facts_corpus.engine, ticker="AAPL", metrics=["net_income", "revenue"], periods=["2025-Q3"]
    )
    assert [i.cite_id for i in items] == ["f:601"]


async def test_unknown_company_returns_nothing(facts_corpus: Corpus) -> None:
    items = await get_financial_facts(
        facts_corpus.engine, ticker="NOPE", metrics=["net_income"], periods=["2025-Q3"]
    )
    assert items == []


@pytest.mark.parametrize(
    ("metrics", "periods"), [([], ["2025-Q3"]), (["net_income"], []), (["x"], ["2025"])]
)
async def test_rejects_bad_arguments(
    facts_corpus: Corpus, metrics: list[str], periods: list[str]
) -> None:
    with pytest.raises(ValueError):
        await get_financial_facts(
            facts_corpus.engine, ticker="THYAO", metrics=metrics, periods=periods
        )


async def test_registry_dispatch_wraps_facts(facts_corpus: Corpus) -> None:
    registry = ToolRegistry(search=None, engine=facts_corpus.engine)  # type: ignore[arg-type]
    result = await registry.dispatch(
        ToolCall(
            id="1",
            name="get_financial_facts",
            arguments={"ticker": "THYAO", "metrics": ["revenue"], "periods": ["2025-Q3"]},
        )
    )
    assert result.count == 1
    assert result.content.startswith('<untrusted_document cite_id="f:503">')
