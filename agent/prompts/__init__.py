"""System prompts, one per answer language, with the Answer JSON schema filled in."""

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
