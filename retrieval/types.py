"""Public search types (contracts/search.md)."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ingestion.models import DocType, Period


class SearchFilters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tickers: list[str] | None = None
    period_from: str | None = None  # "2024-Q1" / "2024-FY", inclusive
    period_to: str | None = None
    doc_types: list[DocType] | None = None
    sections: list[str] | None = None
    language: Literal["tr", "en"] | None = None

    @field_validator("tickers")
    @classmethod
    def _upper(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else [t.strip().upper() for t in value]

    @field_validator("period_from", "period_to")
    @classmethod
    def _period(cls, value: str | None) -> str | None:
        if value is not None:
            Period.parse(value)
        return value

    @model_validator(mode="after")
    def _range(self) -> Self:
        start, end = self.period_from, self.period_to
        if start and end and Period.parse(start) > Period.parse(end):
            raise ValueError("period_from must not be after period_to")
        return self


class SourceRef(BaseModel):
    document_id: int
    ticker: str
    doc_type: DocType
    period: str
    section: str
    page: int | None
    source_url: str


class SearchHit(BaseModel):
    chunk_id: str
    text: str  # untrusted document text
    kind: Literal["text", "table"]
    source: SourceRef
    score: float  # final score (rerank or RRF)
    vector_rank: int | None
    fulltext_rank: int | None


class DocumentMeta(BaseModel):
    document_id: int
    ticker: str
    doc_type: DocType
    period: str
    published_at: datetime
    language: Literal["tr", "en"]
    status: str
    source_url: str
