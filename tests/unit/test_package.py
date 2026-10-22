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
    "name", ["arq", "httpx", "jwt", "pgvector", "pypdf", "pytesseract", "selectolax", "yaml"]
)
def test_runtime_dependencies_import(name: str) -> None:
    assert importlib.import_module(name) is not None


@pytest.mark.parametrize("name", ["llm", "agent", "tools", "api.auth", "api.guards", "api.routes"])
def test_phase3_packages_import(name: str) -> None:
    assert importlib.import_module(name) is not None


def test_phase4_graph_dependencies_import() -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.graph import StateGraph
    from langgraph.types import Command, interrupt

    assert all(item is not None for item in (StateGraph, interrupt, Command, AsyncPostgresSaver))


@pytest.mark.parametrize("name", ["agent.graph", "agent.graph.nodes", "sandbox", "sandbox.runner"])
def test_phase4_packages_import(name: str) -> None:
    assert importlib.import_module(name) is not None
