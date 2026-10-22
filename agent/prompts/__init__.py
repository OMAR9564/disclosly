"""System prompts per answer language: the Phase 3 loop's, and one per graph node (Phase 4)."""

import json
from functools import cache
from pathlib import Path

from agent.answer import Language, answer_json_schema

_DIR = Path(__file__).parent


@cache
def system_prompt(language: Language) -> str:
    template = (_DIR / f"system_{language}.md").read_text(encoding="utf-8")
    schema = json.dumps(answer_json_schema(), ensure_ascii=False, indent=2)
    return template.replace("{answer_schema}", schema)


@cache
def node_prompt(node: str, language: Language) -> str:
    """A graph node's system prompt (`<node>_<language>.md`), without its schema filled in."""
    return (_DIR / f"{node}_{language}.md").read_text(encoding="utf-8")


def with_schema(prompt: str, schema: dict[str, object]) -> str:
    return prompt.replace("{schema}", json.dumps(schema, ensure_ascii=False, indent=2))
