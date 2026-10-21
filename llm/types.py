"""Request/response types for the model router (contracts/model-router.md)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TaskClass = Literal["cheap", "strong"]
Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """One tool call requested by the model; `arguments` is already parsed JSON."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]
    # Provider data that must be sent back with the call, e.g. Gemini's `thought_signature`
    # under `extra_content`. Not part of the cache key.
    extra: dict[str, Any] | None = None


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class ToolSchema(BaseModel):
    """A function tool offered to the model; `parameters` is a JSON schema."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]


class ChatRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: list[ChatMessage] = Field(min_length=1)
    tools: list[ToolSchema] = Field(default_factory=list)
    tool_schema_version: str = "1"
    response_schema: dict[str, Any] | None = None
    max_tokens: int = Field(default=1024, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class ChatResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    cache_hit: bool = False
    fallback_from: list[str] = Field(default_factory=list)


class ProvidersUnavailable(Exception):
    """Every provider for the task class was cooling down, near quota or failed."""

    def __init__(self, task_class: TaskClass, attempted: list[str]) -> None:
        super().__init__(f"no provider available for {task_class}: tried {attempted}")
        self.task_class = task_class
        self.attempted = attempted
