import json
from datetime import UTC, datetime
from typing import Any

import pytest

from ingestion import run
from ingestion.chunk import ChunkerConfig, WhitespaceCounter
from ingestion.config import Company, IngestionSettings
from ingestion.models import DocType
from ingestion.pipeline import RechunkSummary, RunSummary

COMPANIES = [
    Company(ticker="AAPL", name="Apple", market="us", sec_cik="0000320193"),
    Company(ticker="THYAO", name="THY", market="bist", kap_member_id="m"),
]


class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def run(self, companies: list[Company], since: datetime, *, dry_run: bool) -> RunSummary:
        self.calls.append(("run", ([c.ticker for c in companies], since, dry_run)))
        return RunSummary(run_id=None if dry_run else 7, fetched=2, skipped=3, failed=1, listed=6)

    async def rechunk(
        self, config: ChunkerConfig, *, doc_types: Any = None, replace: bool = False
    ) -> RechunkSummary:
        self.calls.append(("rechunk", config.version))
        self.calls.append(("rechunk_options", (doc_types, replace)))
        return RechunkSummary(config.version, documents=2, chunks=10)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePipeline:
    pipeline = FakePipeline()

    async def with_pipeline(settings: Any, engine: Any, source: Any, action: Any) -> Any:
        pipeline.calls.append(("source", source))
        return await action(pipeline)

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setattr(run, "make_engine", lambda url: object())
    monkeypatch.setattr(run, "_with_pipeline", with_pipeline)
    monkeypatch.setattr(run, "load_companies", lambda: type("L", (), {"companies": COMPANIES}))
    monkeypatch.setattr(run, "configure_cli_logging", lambda: None)
    return pipeline


def test_ingest_prints_json_summary(fake: FakePipeline, capsys: pytest.CaptureFixture[str]) -> None:
    code = run.main(["ingest", "--tickers", "aapl", "--source", "sec", "--since", "2024-10-01"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "run_id": 7,
        "fetched": 2,
        "skipped": 3,
        "failed": 1,
        "duration_s": 0.0,
    }
    assert fake.calls == [
        ("source", "sec"),
        ("run", (["AAPL"], datetime(2024, 10, 1, tzinfo=UTC), False)),
    ]


def test_ingest_dry_run_reports_listed(
    fake: FakePipeline, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run.main(["ingest", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["listed"] == 6 and out["run_id"] is None
    assert fake.calls[1][1][0] == ["AAPL", "THYAO"]


def test_unknown_ticker_fails(fake: FakePipeline, capsys: pytest.CaptureFixture[str]) -> None:
    assert run.main(["ingest", "--tickers", "NOPE"]) == 1
    assert capsys.readouterr().out == ""


def test_rechunk(fake: FakePipeline, capsys: pytest.CaptureFixture[str]) -> None:
    assert run.main(["rechunk", "--size", "400", "--overlap", "32"]) == 0
    assert json.loads(capsys.readouterr().out)["chunker_version"] == "v1-400-32"
    assert ("rechunk", "v1-400-32") in fake.calls
    assert ("rechunk_options", (None, False)) in fake.calls


def test_rechunk_replace_selected_types(fake: FakePipeline) -> None:
    argv = ["rechunk", "--size", "600", "--doc-types", "kap_material_event", "--replace"]
    assert run.main(argv) == 0
    assert ("rechunk_options", ([DocType.KAP_MATERIAL_EVENT], True)) in fake.calls


def test_status_json(
    fake: FakePipeline, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(run, "status_report", lambda engine: {"documents": {"chunked": 3}})
    assert run.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"documents": {"chunked": 3}}


def test_missing_database_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(run, "configure_cli_logging", lambda: None)
    assert run.main(["status"]) == 1


def test_helpers() -> None:
    assert run.parse_chunker_version("v1-800-64") == ChunkerConfig(800, 64)
    with pytest.raises(ValueError, match="invalid chunker version"):
        run.parse_chunker_version("v1-x")
    assert [c.ticker for c in run.select_companies(COMPANIES, None)] == ["AAPL", "THYAO"]
    assert [c.ticker for c in run.select_companies(COMPANIES, " thyao ,")] == ["THYAO"]


def test_token_counter_falls_back_without_ml_group(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> None:
        raise ImportError(name)

    monkeypatch.setattr(run, "HuggingFaceCounter", missing)
    assert isinstance(run.token_counter(IngestionSettings()), WhitespaceCounter)


def test_configure_cli_logging_writes_json_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    run.configure_cli_logging()
    run.logger.info("hello", extra={"ticker": "AAPL"})
    err = capsys.readouterr().err.strip().splitlines()[-1]
    assert json.loads(err)["msg"] == "hello"
