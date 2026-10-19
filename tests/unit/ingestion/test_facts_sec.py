import json
from decimal import Decimal
from pathlib import Path

from ingestion.facts import FactDraft, derive_ebitda, extract_sec_facts

FIXTURE = Path(__file__).parents[2] / "fixtures" / "sec" / "companyfacts_CIK0000320193.json"
ACCESSIONS = {"0000320193-25-000079": 11, "0000320193-25-000073": 12}


def _facts() -> dict[tuple[str, str], FactDraft]:
    raw = json.loads(FIXTURE.read_text())
    return {(f.metric, f.period): f for f in extract_sec_facts(raw, ACCESSIONS)}


def test_maps_concepts_to_canonical_metrics() -> None:
    facts = _facts()
    revenue = facts[("revenue", "2025-FY")]
    assert revenue.value == Decimal("416161000000")
    assert revenue.unit == "USD" and revenue.currency == "USD"
    assert revenue.source_document_id == 11
    assert revenue.source_ref == (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax 0000320193-25-000079"
    )
    assert facts[("revenue", "2025-Q2")].source_document_id == 12
    assert facts[("revenue", "2024-FY")].value == Decimal("391035000000")  # comparative
    assert facts[("net_income", "2025-FY")].value == Decimal("112010000000")
    assert facts[("cash", "2025-FY")].value == Decimal("35934000000")  # instant fact
    assert facts[("total_debt", "2025-FY")].value == Decimal("98657000000")


def test_ebitda_is_derived() -> None:
    ebitda = _facts()[("ebitda", "2025-FY")]
    assert ebitda.derived is True
    assert ebitda.value == Decimal("133050000000") + Decimal("11698000000")
    assert "OperatingIncomeLoss" in ebitda.source_ref
    assert "DepreciationDepletionAndAmortization" in ebitda.source_ref


def test_every_fact_has_a_source_and_unknown_filings_are_dropped() -> None:
    raw = json.loads(FIXTURE.read_text())
    assert extract_sec_facts(raw, {}) == []
    facts = extract_sec_facts(raw, ACCESSIONS)
    assert all(f.source_document_id in {11, 12} and f.source_ref for f in facts)
    assert len({f.key for f in facts}) == len(facts)
    assert not any(f.metric == "shares" for f in facts)


def test_year_to_date_spans_are_skipped() -> None:
    raw = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"start": "2025-01-01", "end": "2025-06-30", "val": 5, "accn": "a", "form": "10-Q"},
    ]}}}}}  # fmt: skip
    assert extract_sec_facts(raw, {"a": 1}) == []
    assert extract_sec_facts({}, {"a": 1}) == []


def test_derive_ebitda_needs_both_parts() -> None:
    op = FactDraft("operating_income", "2025-FY", Decimal(1), "USD", "USD", 1, "x")
    assert derive_ebitda([op]) == []
