"""Cloudflare Turnstile check for sign-in (FR-022, research §R6).

The token from the widget is verified against Cloudflare's siteverify endpoint together with
the client IP. Any failure (bad token, missing secret, Cloudflare unreachable) is
`bot_check_failed`: the check fails closed.
"""

import logging

import httpx
from pydantic import SecretStr

from api.errors import ApiError

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5.0
MAX_TOKEN_LENGTH = 2048


async def verify_turnstile(
    http: httpx.AsyncClient, secret: SecretStr | None, token: str, remote_ip: str | None
) -> None:
    """Raises `ApiError("bot_check_failed")` unless Cloudflare accepts the token."""
    if secret is None or not token or len(token) > MAX_TOKEN_LENGTH:
        raise ApiError("bot_check_failed")
    form = {"secret": secret.get_secret_value(), "response": token}
    if remote_ip:
        form["remoteip"] = remote_ip
    try:
        response = await http.post(SITEVERIFY_URL, data=form, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError):
        logger.warning("turnstile siteverify failed")
        raise ApiError("bot_check_failed") from None
    if not isinstance(result, dict) or result.get("success") is not True:
        logger.info("turnstile rejected", extra={"error_codes": _codes(result)})
        raise ApiError("bot_check_failed")


def _codes(result: object) -> list[str]:
    codes = result.get("error-codes") if isinstance(result, dict) else None
    return [str(code) for code in codes] if isinstance(codes, list) else []
