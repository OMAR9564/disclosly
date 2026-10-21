# Contract: Run event stream

Each event: `id: <redis stream id>`, `event: <type>`, `data: <JSON>`. Comment `: ping` every
15 s. The stream closes after `final` or `error`. Reconnecting with `Last-Event-ID` resumes
after that event; without it the full history is replayed.

| Event | Data |
|---|---|
| `run_started` | `{"run_id", "language"}` |
| `tool_called` | `{"tool": "search_documents"\|"get_financial_facts"\|"list_documents", "summary": str}` — summary is a short, user-facing description (e.g. "THYAO 2025 Q1–Q4 net kâr"), never raw args |
| `tool_result` | `{"tool", "count": int}` |
| `model_called` | `{"task_class": "cheap"\|"strong", "provider", "cache_hit": bool, "fallback_from": str\|null}` |
| `answer_delta` | `{"text": str}` — optional, only when the provider streams |
| `final` | `{"status": "completed"\|"partial"\|"refused"\|"no_evidence", "answer": Answer, "citations": Citation[]}` |
| `error` | `{"code", "message"}` |

`Answer` and `Citation`: see [agent-answer.md](agent-answer.md).
