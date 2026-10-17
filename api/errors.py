"""Error envelope and exception handlers (data-model.md, ErrorEnvelope)."""

import logging
import uuid
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

ErrorCode = Literal[
    "not_found",
    "method_not_allowed",
    "validation_error",
    "dependency_unavailable",
    "internal_error",
]

ERROR_CODES: dict[ErrorCode, int] = {
    "not_found": 404,
    "method_not_allowed": 405,
    "validation_error": 422,
    "dependency_unavailable": 503,
    "internal_error": 500,
}

_MESSAGES: dict[ErrorCode, str] = {
    "not_found": "Resource not found.",
    "method_not_allowed": "Method not allowed.",
    "validation_error": "Request validation failed.",
    "dependency_unavailable": "A dependency is unavailable.",
    "internal_error": "Internal server error.",
}

_CODE_BY_STATUS: dict[int, ErrorCode] = {status: code for code, status in ERROR_CODES.items()}

REQUEST_ID_HEADER = "X-Request-ID"


class ErrorDetail(BaseModel):
    loc: list[str | int]
    msg: str


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    request_id: str
    details: list[ErrorDetail] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


def get_request_id(request: Request) -> str:
    """The request ID set by the logging middleware, or a new one if it did not run."""
    request_id = getattr(request.state, "request_id", None)
    if not isinstance(request_id, str):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
    return request_id


def error_response(
    request: Request,
    code: ErrorCode,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    envelope = ErrorEnvelope(
        error=ErrorBody(code=code, message=_MESSAGES[code], request_id=request_id, details=details)
    )
    return JSONResponse(
        status_code=ERROR_CODES[code],
        content=envelope.model_dump(mode="json"),
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODE_BY_STATUS.get(exc.status_code, "internal_error")
        allow = (exc.headers or {}).get("Allow")
        return error_response(request, code, headers={"Allow": allow} if allow else None)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            ErrorDetail(loc=list(error.get("loc", ())), msg=str(error.get("msg", "")))
            for error in exc.errors()
        ]
        return error_response(request, "validation_error", details=details)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled exception", exc_info=exc)
        return error_response(request, "internal_error")
