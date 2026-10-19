"""Smoke test: the top-level Python packages import."""

import importlib

import pytest


def test_api_package_imports() -> None:
    assert importlib.import_module("api") is not None


def test_evals_package_imports() -> None:
    assert importlib.import_module("evals") is not None


@pytest.mark.parametrize(
    "name", ["ingestion", "ingestion.sources", "ingestion.parse", "retrieval", "workers"]
)
def test_phase2_packages_import(name: str) -> None:
    assert importlib.import_module(name) is not None


@pytest.mark.parametrize(
    "name", ["arq", "httpx", "pgvector", "pypdf", "pytesseract", "selectolax", "yaml"]
)
def test_runtime_dependencies_import(name: str) -> None:
    assert importlib.import_module(name) is not None
