"""Error envelope and exception handlers (data-model.md, ErrorEnvelope).

Phase 3 codes and localized messages: specs/003-single-agent-mvp/contracts/http-api.md.
"""

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
    "unauthorized",
    "beta_closed",
    "bot_check_failed",
    "quota_exhausted",
    "rate_limited",
    "providers_unavailable",
]

MessageLanguage = Literal["tr", "en"]
DEFAULT_LANGUAGE: MessageLanguage = "tr"

ERROR_CODES: dict[ErrorCode, int] = {
    "not_found": 404,
    "method_not_allowed": 405,
    "validation_error": 422,
    "dependency_unavailable": 503,
    "internal_error": 500,
    "unauthorized": 401,
    "beta_closed": 403,
    "bot_check_failed": 403,
    "quota_exhausted": 429,
    "rate_limited": 429,
    "providers_unavailable": 503,
}

# `{resets_at}` is filled from ApiError params; every code has both languages.
_MESSAGES: dict[MessageLanguage, dict[ErrorCode, str]] = {
    "en": {
        "not_found": "Resource not found.",
        "method_not_allowed": "Method not allowed.",
        "validation_error": "Request validation failed.",
        "dependency_unavailable": "A dependency is unavailable.",
        "internal_error": "Internal server error.",
        "unauthorized": "Please sign in.",
        "beta_closed": "Disclosly is in closed beta and your account is not on the list.",
        "bot_check_failed": "The bot check failed. Please try again.",
        "quota_exhausted": "You have used today's questions. The quota resets at {resets_at}.",
        "rate_limited": "Too many requests. Please wait and try again.",
        "providers_unavailable": "Answering is temporarily unavailable. Please try again later.",
    },
    "tr": {
        "not_found": "Kaynak bulunamadı.",
        "method_not_allowed": "Bu yönteme izin verilmiyor.",
        "validation_error": "İstek doğrulanamadı.",
        "dependency_unavailable": "Bağımlı bir servis şu anda kullanılamıyor.",
        "internal_error": "Sunucu hatası.",
        "unauthorized": "Lütfen giriş yapın.",
        "beta_closed": "Disclosly kapalı beta aşamasında ve hesabınız listede değil.",
        "bot_check_failed": "Bot doğrulaması başarısız oldu. Lütfen tekrar deneyin.",
        "quota_exhausted": "Bugünkü soru hakkınız doldu. Kota {resets_at} saatinde yenilenir.",
        "rate_limited": "Çok fazla istek gönderildi. Lütfen biraz bekleyip tekrar deneyin.",
        "providers_unavailable": "Yanıt verme geçici olarak kullanılamıyor. Lütfen sonra deneyin.",
    },
}

_CODE_BY_STATUS: dict[int, ErrorCode] = {status: code for code, status in ERROR_CODES.items()}

REQUEST_ID_HEADER = "X-Request-ID"


class ApiError(Exception):
    """Raised by routes and dependencies; rendered as the error envelope."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.code: ErrorCode = code
        self.headers = headers
        self.params = params or {}


def message_language(accept_language: str | None) -> MessageLanguage:
    """Pick `tr` or `en` from an Accept-Language header, highest q first; default `tr`."""
    if not accept_language:
        return DEFAULT_LANGUAGE
    ranked: list[tuple[float, int, str]] = []
    for index, part in enumerate(accept_language.split(",")):
        tag, _, rest = part.strip().partition(";")
        quality = 1.0
        if rest.strip().startswith("q="):
            try:
                quality = float(rest.strip()[2:])
            except ValueError:
                quality = 0.0
        ranked.append((-quality, index, tag.strip().lower().split("-")[0]))
    for negative_quality, _, primary in sorted(ranked):
        if negative_quality < 0 and primary in ("tr", "en"):
            return "tr" if primary == "tr" else "en"
    return DEFAULT_LANGUAGE


def localized_message(
    code: ErrorCode, language: MessageLanguage, params: dict[str, str] | None = None
) -> str:
    template = _MESSAGES[language][code]
    return template.format(**params) if params else template.replace(" {resets_at}", "")


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
    params: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    language = message_language(request.headers.get("accept-language"))
    message = localized_message(code, language, params)
    envelope = ErrorEnvelope(
        error=ErrorBody(code=code, message=message, request_id=request_id, details=details)
    )
    return JSONResponse(
        status_code=ERROR_CODES[code],
        content=envelope.model_dump(mode="json"),
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.code, headers=exc.headers, params=exc.params)

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
