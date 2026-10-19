import pytest
from pydantic import ValidationError

from retrieval.types import SearchFilters


def test_filters_normalise_tickers() -> None:
    assert SearchFilters(tickers=[" thyao "]).tickers == ["THYAO"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"period_from": "2025"},
        {"period_to": "2025-Q5"},
        {"period_from": "2025-FY", "period_to": "2025-Q4"},
        {"language": "de"},
        {"unknown": 1},
    ],
)
def test_invalid_filters(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SearchFilters.model_validate(kwargs)


def test_valid_range() -> None:
    f = SearchFilters(period_from="2025-Q4", period_to="2025-FY")
    assert (f.period_from, f.period_to) == ("2025-Q4", "2025-FY")
