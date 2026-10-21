"""JSON logs, redaction and request-ID middleware (data-model.md, RequestLogRecord)."""

import json
import logging
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REDACTED = "[REDACTED]"
UNMATCHED_ROUTE = "<unmatched>"
REQUEST_ID_HEADER = "X-Request-ID"

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SENSITIVE_KEY_RE = re.compile(r"key|token|secret|password|authorization", re.IGNORECASE)
# Query values that grant access, e.g. httpx's "HTTP Request: GET ...?code=…&state=…" line.
_SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:code|state|code_verifier|[A-Za-z_]*(?:key|token|secret|password))=)[^&\s\"']+",
    re.IGNORECASE,
)

# Attributes every LogRecord has; anything else on a record came from `extra=`.
# `color_message` is uvicorn's ANSI-colored copy of `msg`.
_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    | {"message", "asctime", "color_message"}
)

request_logger = logging.getLogger("api.request")


def _redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEY_RE.search(key):
        return REDACTED
    if isinstance(value, str):
        return _SENSITIVE_QUERY_RE.sub(rf"\1{REDACTED}", _EMAIL_RE.sub(REDACTED, value))
    if isinstance(value, dict):
        return {k: _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact(item) for item in value]
    return value


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _RECORD_ATTRS}


class RedactionFilter(logging.Filter):
    """Masks email-like values and sensitive field names before a record is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.getMessage())
        record.args = None
        for key, value in _extra_fields(record).items():
            setattr(record, key, _redact(value, key))
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, then any `extra` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            **_extra_fields(record),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    """Route all logs (ours and uvicorn's) through one JSON handler on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # RequestContextMiddleware writes the access line; uvicorn's own would be a duplicate.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            try:
                candidate = value.decode("ascii")
            except UnicodeDecodeError:
                return None
            return candidate if _REQUEST_ID_RE.fullmatch(candidate) else None
    return None


def route_template(scope: Scope) -> str:
    """The matched route template (e.g. `/items/{item_id}`), never the raw path."""
    path = getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE


class RequestContextMiddleware:
    """Assigns a request ID, echoes it in the response and logs one line per request."""

    def __init__(self, app: ASGIApp, version: str) -> None:
        self.app = app
        self.version = version

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        status = 500
        start = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            status = 500
            raise
        finally:
            request_logger.log(
                logging.ERROR if status >= 500 else logging.INFO,
                "request",
                extra={
                    "request_id": request_id,
                    "method": scope["method"],
                    "route": route_template(scope),
                    "status": status,
                    "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                    "version": self.version,
                },
            )


def mask_email(email: str) -> str:
    """`ayse@disclosly.com` → `a***@d***.com`; used wherever an email must be shown or logged."""
    local, at, domain = email.strip().partition("@")
    if not at or not local or not domain:
        return "***"
    host, dot, tld = domain.rpartition(".")
    if not dot or not host:
        return f"{local[0]}***@***"
    return f"{local[0]}***@{host[0]}***.{tld}"
