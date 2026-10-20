"""Tests for the cassette-only eval runner (evals/run.py)."""

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from evals import run


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any socket use fails the test: the runner must never touch the network."""

    def _blocked(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def suite(tmp_path: Path) -> tuple[Path, Path]:
    datasets = tmp_path / "datasets"
    cassettes = tmp_path / "cassettes"
    datasets.mkdir()
    cassettes.mkdir()
    return datasets, cassettes


def _write_dataset(datasets: Path, name: str, *case_ids: str) -> None:
    lines = [json.dumps({"id": case_id, "question": "q"}) for case_id in case_ids]
    (datasets / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_cassette(cassettes: Path, dataset: str, case_id: str) -> None:
    folder = cassettes / dataset
    folder.mkdir(exist_ok=True)
    (folder / f"{case_id}.json").write_text("{}", encoding="utf-8")


def _main(suite: tuple[Path, Path], *extra: str) -> int:
    datasets, cassettes = suite
    return run.main(
        ["--cassettes-only", "--datasets", str(datasets), "--cassettes", str(cassettes), *extra]
    )


def test_empty_suite_passes(suite: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    assert _main(suite) == 0
    assert capsys.readouterr().out.strip() == "0 cases, passed"


def test_case_with_cassette_passes(
    suite: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    datasets, cassettes = suite
    _write_dataset(datasets, "smoke", "c1", "c2")
    _write_cassette(cassettes, "smoke", "c1")
    _write_cassette(cassettes, "smoke", "c2")

    assert _main(suite) == 0
    assert capsys.readouterr().out.strip() == "2 cases, passed"


def test_case_without_cassette_fails(
    suite: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    datasets, cassettes = suite
    _write_dataset(datasets, "smoke", "c1", "c2")
    _write_cassette(cassettes, "smoke", "c1")

    assert _main(suite) == 1
    captured = capsys.readouterr()
    assert "smoke/c2: no recorded cassette" in captured.err
    assert captured.out.strip() == "2 cases, 1 failed"


def test_invalid_case_fails(suite: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    datasets, _ = suite
    (datasets / "broken.jsonl").write_text('{"question": "no id"}\nnot json\n', encoding="utf-8")

    assert _main(suite) == 1
    err = capsys.readouterr().err
    assert "broken.jsonl:1: case has no string 'id'" in err
    assert "broken.jsonl:2: invalid JSON" in err


def test_blank_lines_are_ignored(suite: tuple[Path, Path]) -> None:
    datasets, cassettes = suite
    (datasets / "smoke.jsonl").write_text('\n{"id": "c1"}\n\n', encoding="utf-8")
    _write_cassette(cassettes, "smoke", "c1")

    assert _main(suite) == 0


def test_live_mode_is_rejected(suite: tuple[Path, Path]) -> None:
    datasets, cassettes = suite
    with pytest.raises(SystemExit) as exc:
        run.main(["--datasets", str(datasets), "--cassettes", str(cassettes)])
    assert exc.value.code == 2


def test_defaults_point_at_repository_suite() -> None:
    package = Path(run.__file__).parent
    assert package / "datasets" == run.DATASETS_DIR
    assert package / "cassettes" == run.CASSETTES_DIR
    assert run.main(["--cassettes-only"]) == 0


def test_retrieval_golden_has_its_own_runner(suite: tuple[Path, Path]) -> None:
    datasets, cassettes = suite
    _write_dataset(datasets, "retrieval_golden", "tr-1")
    report = run.run_suite(datasets, cassettes)
    assert (report.cases, report.failures) == (0, [])
