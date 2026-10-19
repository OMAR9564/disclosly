"""Shared ingestion types (data-model.md): enums and the reporting period."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Self


class DocType(StrEnum):
    TEN_K = "10-K"
    TEN_Q = "10-Q"
    KAP_FINANCIAL_REPORT = "kap_financial_report"
    KAP_MATERIAL_EVENT = "kap_material_event"
    KAP_ANNUAL_REPORT = "kap_annual_report"
    KAP_PROSPECTUS = "kap_prospectus"
    KAP_FUND_REPORT = "kap_fund_report"


class Language(StrEnum):
    TR = "tr"
    EN = "en"


class DocumentStatus(StrEnum):
    FETCHED = "fetched"
    PARSED = "parsed"
    CHUNKED = "chunked"
    EMBEDDED = "embedded"
    FAILED = "failed"


_PERIOD_RE = re.compile(r"^(\d{4})-(?:Q([1-4])|FY)$")


@dataclass(frozen=True, order=True)
class Period:
    """A reporting period such as `2025-Q3` or `2025-FY`.

    Ordering: quarters of a year come first, then the full year, then the next year.
    """

    year: int
    # 1-4 for quarters; 5 sorts the full year after Q4 of the same year.
    _rank: int

    @classmethod
    def parse(cls, raw: str) -> Self:
        match = _PERIOD_RE.match(raw)
        if match is None:
            raise ValueError("period must look like 2025-Q3 or 2025-FY")
        quarter = match.group(2)
        return cls(int(match.group(1)), int(quarter) if quarter else 5)

    @property
    def quarter(self) -> int | None:
        return None if self._rank == 5 else self._rank

    def within(self, start: "Period", end: "Period") -> bool:
        return start <= self <= end

    def __str__(self) -> str:
        return f"{self.year}-FY" if self.quarter is None else f"{self.year}-Q{self.quarter}"
