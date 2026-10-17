"""Smoke test: the top-level Python packages import."""

import importlib


def test_api_package_imports() -> None:
    assert importlib.import_module("api") is not None


def test_evals_package_imports() -> None:
    assert importlib.import_module("evals") is not None
