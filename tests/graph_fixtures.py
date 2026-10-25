"""A recorded deep run shared by the deep-flow and recorder tests: question, facts, replies.

`Recorder` answers each graph node's request with its reply from `REPLIES` (the node is found
from its Turkish system prompt) and records the request as a cassette entry.
"""

import json
from decimal import Decimal
from typing import Any

from llm.testing import interaction
from llm.types import ChatRequest, ChatResponse, TaskClass
from tools.types import SeenItem

QUESTION = "THYAO'nun net borcu 2025 çeyrekleri boyunca nasıl değişti?"
PERIODS = ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]
VALUES = ["35,1", "36,4", "38,5", "41,2"]
FACTS = [
    SeenItem(
        cite_id=f"f:{80 + i}",
        text=f"Toplam borç ({period}): {value} milyar TRY",
        value=Decimal(value.replace(",", ".")),
        scale=Decimal(10) ** 9,
        source={"kind": "fact", "ticker": "THYAO", "period": period},
    )
    for i, (period, value) in enumerate(zip(PERIODS, VALUES, strict=True))
]
REPLIES: dict[str, dict[str, Any]] = {
    "router": {"mode": "deep", "tickers": ["THYAO"], "periods": PERIODS, "metrics": ["total_debt"]},
    "planner": {
        "steps": [
            {
                "id": "s1",
                "kind": "facts",
                "goal": "THYAO toplam borcu, 2025'in dört çeyreği",
                "tickers": ["THYAO"],
                "periods": PERIODS,
                "metrics": ["total_debt"],
            }
        ]
    },
    "retriever": {"steps": [{"step_id": "s1", "sufficient": True}]},
    "analyst": {
        "findings": [
            {"id": f"F{i + 1}", "text": f"{p} toplam borç {v} milyar TL", "cite_ids": [f.cite_id]}
            for i, (p, v, f) in enumerate(zip(PERIODS, VALUES, FACTS, strict=True))
        ]
    },
    "verifier": {
        "claims": [
            {"finding_id": f"F{i + 1}", "supported": True, "reason": "kanıtta var"}
            for i in range(len(PERIODS))
        ]
    },
    "writer": {
        "language": "tr",
        "status": "completed",
        "summary": "THYAO'nun toplam borcu 2025-Q4'te 41,2 milyar TL'ye çıktı [1].",
        "points": [{"text": "2025-Q1'de 35,1 milyar TL", "cite_ids": ["f:80"]}],
        "tables": [
            {
                "title": "THYAO toplam borç",
                "columns": ["Dönem", "Toplam borç"],
                "rows": [
                    [{"text": p}, {"text": f"{v} milyar TL", "cite_id": f.cite_id}]
                    for p, v, f in zip(PERIODS, VALUES, FACTS, strict=True)
                ],
            }
        ],
        "chart": {"kind": "line", "table": 0, "x_column": "Dönem", "series": ["Toplam borç"]},
        "confidence": {"level": "high", "note": "Dört çeyreğin tamamı raporlanmış."},
        "citations": [{"marker": 1, "cite_id": "f:83"}],
    },
    # The simple and refused path answers with the Phase 3 system prompt and `Answer` JSON.
    "simple": {
        "language": "tr",
        "status": "refused",
        "summary": "Disclosly yatırım tavsiyesi vermez.",
        "points": [],
        "citations": [],
    },
}
# The start of each node's Turkish system prompt.
PROMPT_MARKERS = {
    "router": "yönlendiricisisin",
    "planner": "planlayıcısısın",
    "retriever": "Bir araştırma planının her adımı",
    "analyst": "analistisin",
    "verifier": "doğrulayıcısısın",
    "writer": "yazarısın",
    "simple": "Sen Disclosly'sin",
}


class Recorder:
    """Answers by node and records each request as a cassette entry."""

    def __init__(self) -> None:
        self.interactions: list[dict[str, Any]] = []

    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse:
        system = request.messages[0].content or ""
        node = next(n for n, marker in PROMPT_MARKERS.items() if marker in system)
        response = ChatResponse(
            content=json.dumps(REPLIES[node], ensure_ascii=False), provider="gemini", model="m"
        )
        self.interactions.append(interaction(request, task_class, response))
        return response
