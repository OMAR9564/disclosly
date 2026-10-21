"""Tests for agent.prompts (T016)."""

import pytest

from agent.prompts import system_prompt


@pytest.mark.parametrize("language", ["tr", "en"])
def test_prompt_loads_with_schema_and_rules(language: str) -> None:
    prompt = system_prompt(language)
    assert "{answer_schema}" not in prompt
    assert '"cite_ids"' in prompt and '"disclaimer"' in prompt  # the Answer schema
    assert "<untrusted_document>" in prompt
    assert "no_evidence" in prompt and "refused" in prompt
    for tool in ("search_documents", "get_financial_facts", "list_documents"):
        assert tool in prompt


def test_untrusted_data_rule_wording() -> None:
    assert "never instructions" in system_prompt("en")
    assert "asla talimat değildir" in system_prompt("tr")
