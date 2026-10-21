"""Ingestion settings and the company list (data-model.md `companies`, research §R11)."""

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_COMPANIES_PATH = Path(__file__).with_name("companies.yaml")

Market = Literal["bist", "us"]

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")
_CIK_RE = re.compile(r"^\d{10}$")


def _check_ticker(value: str) -> str:
    upper = value.strip().upper()
    if not _TICKER_RE.match(upper):
        raise ValueError("ticker must be 1-12 letters, digits, '.' or '-'")
    return upper


Ticker = Annotated[str, AfterValidator(_check_ticker)]


class IngestionSettings(BaseSettings):
    """Worker and laptop settings, read from environment variables only."""

    model_config = SettingsConfigDict(case_sensitive=False, frozen=True, extra="ignore")

    database_url: SecretStr | None = None
    sec_user_agent: str | None = None
    ingest_data_dir: Path = Path("/tmp/disclosly-ingest")  # noqa: S108 - placeholder default
    corpus_disk_limit_gb: int = Field(default=20, gt=0)
    retrieval_chunker_version: str = Field(default="v1-600-64", min_length=1)
    retrieval_rerank: bool = True
    retrieval_candidates: int = Field(default=50, gt=0)
    retrieval_rerank_top: int = Field(default=20, gt=0)
    # Server latency (SC-005): the reranker reads only the first 64 tokens of each pair and runs
    # with int8 weights. ADR 0002 has the measurements.
    retrieval_rerank_max_length: int = Field(default=64, gt=0)
    retrieval_rerank_quantize: bool = True
    embedding_model: str = "intfloat/multilingual-e5-small"
    rerank_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

    @field_validator("sec_user_agent")
    @classmethod
    def _check_user_agent(cls, value: str | None) -> str | None:
        # SEC asks for a contact address in the User-Agent; reject values without one.
        if value is not None and "@" not in value:
            raise ValueError("SEC_USER_AGENT must contain a contact email address")
        return value

    @model_validator(mode="after")
    def _check_rerank_top(self) -> Self:
        if self.retrieval_rerank_top > self.retrieval_candidates:
            raise ValueError("RETRIEVAL_RERANK_TOP must not exceed RETRIEVAL_CANDIDATES")
        return self

    def require_database_url(self) -> str:
        if self.database_url is None:
            raise RuntimeError("DATABASE_URL is required")
        return self.database_url.get_secret_value()

    def require_sec_user_agent(self) -> str:
        """Return the SEC User-Agent; the SEC source calls this before any request."""
        if self.sec_user_agent is None:
            raise RuntimeError("SEC_USER_AGENT is required to fetch from SEC EDGAR")
        return self.sec_user_agent


class Company(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: Ticker
    name: str = Field(min_length=1)
    market: Market
    kap_member_id: str | None = None
    sec_cik: str | None = None

    @model_validator(mode="after")
    def _check_market_ids(self) -> Self:
        if self.market == "bist" and not self.kap_member_id:
            raise ValueError("kap_member_id is required when market = bist")
        if self.market == "us":
            if self.sec_cik is None:
                raise ValueError("sec_cik is required when market = us")
            if not _CIK_RE.match(self.sec_cik):
                raise ValueError("sec_cik must be exactly 10 digits")
        return self


class CompanyList(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_date: date
    companies: list[Company]

    @model_validator(mode="after")
    def _check_unique_tickers(self) -> Self:
        tickers = [c.ticker for c in self.companies]
        if len(tickers) != len(set(tickers)):
            raise ValueError("company tickers must be unique")
        return self


def load_companies(path: Path = DEFAULT_COMPANIES_PATH) -> CompanyList:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return CompanyList.model_validate(raw)
