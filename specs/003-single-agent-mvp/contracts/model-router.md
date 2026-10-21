# Contract: ModelRouter

```python
class ModelRouter(Protocol):
    async def chat(self, request: ChatRequest, *, task_class: TaskClass) -> ChatResponse: ...
```

- `ChatRequest`: `messages`, `tools` (JSON schemas), `tool_schema_version: str`,
  `response_schema` (optional JSON schema for structured output), `max_tokens`,
  `temperature` (default 0).
- `ChatResponse`: `content: str | None`, `tool_calls: list[ToolCall]`, `provider`, `model`,
  `tokens_in`, `tokens_out`, `latency_ms`, `cache_hit: bool`, `fallback_from: list[str]`.
- Raises `ProvidersUnavailable` when every provider is cooling down, near quota or failed.

## providers.yaml

```yaml
classes:
  cheap: [gemini-flash-lite, groq-llama, github-mini, openrouter-free, cloudflare]
  strong: [gemini-flash, groq-large, github-large, openrouter-free]
providers:
  gemini-flash:
    base_url: https://generativelanguage.googleapis.com/v1beta/openai
    model: <model id>
    api_key_env: GEMINI_API_KEY
    daily_requests: <int>
    native_tools: true
    price_per_mtok: {input: <float>, output: <float>}   # reference only, for would-have-cost
```

A provider whose `api_key_env` is unset is skipped. Model ids appear only here.

## Cache key

`sha256(canonical_json({"messages": normalised, "model": id, "temperature", "max_tokens",
"tool_schema_version", "response_schema"}))`. A hit returns `cache_hit=true` and makes no
provider call. Cache entries are model-specific: the router checks the cache for each provider
in order before calling it.

## Failure classification

| Condition | Action |
|---|---|
| 429 | cooldown = `Retry-After` or 60 s, next provider |
| 5xx, timeout (15 s), connection error | cooldown 60 s, next provider |
| 200 with empty content and no tool calls, or unparsable tool args | cooldown 30 s, next provider |
| 400/401/403, or 404 (model id gone) | log as config error, cooldown 1 h, next provider |
| 413 (request larger than the provider's per-minute token limit) | no cooldown (smaller requests still fit), next provider |
| usage ≥ 90% of `daily_requests` | skip |
