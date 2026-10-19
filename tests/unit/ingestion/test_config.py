from pathlib import Path

import pytest
from pydantic import ValidationError

from ingestion.config import Company, IngestionSettings, load_companies

ENV_VARS = [
    "SEC_USER_AGENT",
    "INGEST_DATA_DIR",
    "CORPUS_DISK_LIMIT_GB",
    "RETRIEVAL_CHUNKER_VERSION",
    "RETRIEVAL_RERANK",
    "RETRIEVAL_CANDIDATES",
    "RETRIEVAL_RERANK_TOP",
    "EMBEDDING_MODEL",
    "RERANK_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_settings_defaults() -> None:
    settings = IngestionSettings()
    assert settings.sec_user_agent is None
    assert settings.ingest_data_dir == Path("/tmp/disclosly-ingest")  # noqa: S108
    assert settings.corpus_disk_limit_gb == 20
    assert settings.retrieval_chunker_version == "v1-600-64"
    assert settings.retrieval_rerank is False
    assert settings.retrieval_candidates == 50
    assert settings.retrieval_rerank_top == 20
    assert settings.embedding_model == "intfloat/multilingual-e5-small"
    assert settings.rerank_model == "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


def test_settings_read_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_RERANK", "true")
    monkeypatch.setenv("RETRIEVAL_CANDIDATES", "80")
    settings = IngestionSettings()
    assert settings.retrieval_rerank is True
    assert settings.retrieval_candidates == 80


def test_rerank_top_cannot_exceed_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RETRIEVAL_CANDIDATES", "10")
    monkeypatch.setenv("RETRIEVAL_RERANK_TOP", "20")
    with pytest.raises(ValidationError, match="RETRIEVAL_RERANK_TOP"):
        IngestionSettings()


def test_sec_user_agent_required_only_for_sec(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="SEC_USER_AGENT"):
        IngestionSettings().require_sec_user_agent()
    monkeypatch.setenv("SEC_USER_AGENT", "Disclosly ops@example.com")
    assert IngestionSettings().require_sec_user_agent() == "Disclosly ops@example.com"


@pytest.mark.parametrize("value", ["", "   ", "no-contact"])
def test_sec_user_agent_needs_contact(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", value)
    with pytest.raises(ValidationError, match="SEC_USER_AGENT"):
        IngestionSettings()


def test_company_bist_and_us() -> None:
    bist = Company(ticker="thyao", name="Turk Hava Yollari", market="bist", kap_member_id="abc")
    assert bist.ticker == "THYAO"
    us = Company(ticker="AAPL", name="Apple Inc.", market="us", sec_cik="0000320193")
    assert us.sec_cik == "0000320193"


def test_company_bist_requires_kap_member_id() -> None:
    with pytest.raises(ValidationError, match="kap_member_id"):
        Company(ticker="THYAO", name="THY", market="bist")


def test_company_us_requires_sec_cik() -> None:
    with pytest.raises(ValidationError, match="sec_cik"):
        Company(ticker="AAPL", name="Apple", market="us")


@pytest.mark.parametrize("cik", ["320193", "00003201930", "000032019X"])
def test_company_sec_cik_ten_digits(cik: str) -> None:
    with pytest.raises(ValidationError, match="sec_cik"):
        Company(ticker="AAPL", name="Apple", market="us", sec_cik=cik)


def test_company_rejects_unknown_market() -> None:
    with pytest.raises(ValidationError):
        Company(ticker="X", name="X", market="lse", sec_cik="0000000001")


def test_company_ticker_format() -> None:
    assert Company(ticker="brk-b", name="B", market="us", sec_cik="0001067983").ticker == "BRK-B"
    with pytest.raises(ValidationError, match="ticker"):
        Company(ticker="BAD TICKER", name="B", market="us", sec_cik="0001067983")


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "companies.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_companies(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
snapshot_date: 2026-10-19
companies:
  - {ticker: THYAO, name: THY, market: bist, kap_member_id: abc}
  - {ticker: AAPL, name: Apple, market: us, sec_cik: "0000320193"}
""",
    )
    company_list = load_companies(path)
    assert str(company_list.snapshot_date) == "2026-10-19"
    assert [c.ticker for c in company_list.companies] == ["THYAO", "AAPL"]


def test_load_companies_rejects_duplicate_ticker(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
snapshot_date: 2026-10-19
companies:
  - {ticker: aapl, name: Apple, market: us, sec_cik: "0000320193"}
  - {ticker: AAPL, name: Apple, market: us, sec_cik: "0000320193"}
""",
    )
    with pytest.raises(ValidationError, match="unique"):
        load_companies(path)


def test_load_companies_requires_snapshot_date(tmp_path: Path) -> None:
    path = _write(tmp_path, "companies: []\n")
    with pytest.raises(ValidationError, match="snapshot_date"):
        load_companies(path)


def test_shipped_company_list() -> None:
    company_list = load_companies()
    markets = [c.market for c in company_list.companies]
    assert markets.count("bist") == 20
    assert markets.count("us") == 20
    assert {"THYAO", "ASELS", "AAPL", "BRK-B"} <= {c.ticker for c in company_list.companies}
