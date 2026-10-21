"""Tool registry: model-facing schemas, dispatch and untrusted-data wrapping.

Every citable result goes to the model inside
`<untrusted_document cite_id="…">…</untrusted_document>`. Delimiters inside document text are
escaped so a chunk cannot close its own wrapper and pose as instructions.
"""

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import Engine

from llm.types import ToolCall, ToolSchema
from tools import documents, facts
from tools.documents import SearchBackend
from tools.facts import Language
from tools.types import SeenItem

TOOL_SCHEMA_VERSION = "1"

ToolName = Literal["search_documents", "get_financial_facts", "list_documents"]

_PERIOD = {"type": "string", "description": 'Period like "2025-Q3" or "2025-FY"'}

SCHEMAS: list[ToolSchema] = [
    ToolSchema(
        name="search_documents",
        description="Hybrid search over disclosure text chunks. Results are citable (c:…).",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "ticker": {"type": "string"},
                "period_from": _PERIOD,
                "period_to": _PERIOD,
                "doc_type": {"type": "string"},
                "language": {"type": "string", "enum": ["tr", "en"]},
                "k": {"type": "integer", "minimum": 1, "maximum": documents.MAX_K},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    ToolSchema(
        name="get_financial_facts",
        description="Reported financial figures for a company. Results are citable (f:…).",
        parameters={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "metrics": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(facts.KNOWN_METRICS)},
                },
                "periods": {"type": "array", "items": _PERIOD},
            },
            "required": ["ticker", "metrics", "periods"],
            "additionalProperties": False,
        },
    ),
    ToolSchema(
        name="list_documents",
        description="List a company's current documents. Metadata only, not citable.",
        parameters={
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "doc_type": {"type": "string"},
                "period_from": _PERIOD,
                "period_to": _PERIOD,
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    ),
]

_DELIMITER = re.compile(r"<(/?)(untrusted_document)", re.IGNORECASE)


def escape_delimiters(text: str) -> str:
    return _DELIMITER.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", text)


def wrap(item: SeenItem) -> str:
    # The report unit tells the model what bare table amounts are counted in.
    unit = f' amounts_in="{item.unit_note.replace(chr(34), "")}"' if item.unit_note else ""
    return (
        f'<untrusted_document cite_id="{item.cite_id}"{unit}>'
        f"{escape_delimiters(item.text)}</untrusted_document>"
    )


@dataclass(frozen=True)
class ToolResult:
    tool: str
    content: str  # the tool message sent to the model
    summary: str  # user-facing description of the call, never raw args
    items: list[SeenItem] = field(default_factory=list)
    count: int = 0
    error: str | None = None


def _summary(name: str, args: dict[str, Any]) -> str:
    ticker = str(args.get("ticker") or "").upper()
    if name == "get_financial_facts":
        periods = args.get("periods") or []
        metrics = ", ".join(str(m) for m in args.get("metrics") or [])
        span = f"{periods[0]} to {periods[-1]}" if len(periods) > 1 else "".join(periods)
        return " ".join(p for p in (ticker, span, metrics) if p)[:120]
    if name == "list_documents":
        return f"{ticker} documents".strip()
    return " ".join(p for p in (ticker, str(args.get("query") or "")) if p)[:120]


class ToolRegistry:
    def __init__(
        self,
        search: SearchBackend,
        engine: Engine,
        units: Callable[[list[SeenItem]], list[SeenItem]] | None = None,
    ) -> None:
        self._search = search
        self._engine = engine
        # Attaches report units to chunk items; tests without a database pass their own.
        self._units = units or partial(documents.with_presentation_units, engine)

    @property
    def schemas(self) -> list[ToolSchema]:
        return SCHEMAS

    async def dispatch(self, call: ToolCall, language: Language = "tr") -> ToolResult:
        args = call.arguments
        summary = _summary(call.name, args)
        try:
            if call.name == "search_documents":
                items = await documents.search_documents(self._search, **args)
                items = await asyncio.to_thread(self._units, items)
            elif call.name == "get_financial_facts":
                items = await facts.get_financial_facts(self._engine, **args, language=language)
            elif call.name == "list_documents":
                docs = await documents.list_documents(self._search, **args)
                content = json.dumps({"documents": docs}, ensure_ascii=False, default=str)
                return ToolResult(call.name, escape_delimiters(content), summary, count=len(docs))
            else:
                return self._error(call.name, summary, f"unknown tool {call.name!r}")
        except (TypeError, ValueError, ValidationError) as exc:
            return self._error(call.name, summary, f"invalid arguments: {exc}")
        if not items:
            return ToolResult(call.name, "No results.", summary)
        content = "\n".join(wrap(item) for item in items)
        return ToolResult(call.name, content, summary, items=items, count=len(items))

    @staticmethod
    def _error(name: str, summary: str, message: str) -> ToolResult:
        return ToolResult(name, f"Tool error: {message}"[:500], summary, error=message)
