"""Tests for api.guards.turnstile (T031)."""

from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from api.errors import ApiError
from api.guards.turnstile import SITEVERIFY_URL, verify_turnstile

SECRET = SecretStr("turnstile-secret")


def _http(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_accepts_success_and_sends_ip() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"success": True})

    async with _http(httpx.MockTransport(handler)) as http:
        await verify_turnstile(http, SECRET, "token-1", "203.0.113.7")

    assert str(seen[0].url) == SITEVERIFY_URL
    form = parse_qs(seen[0].content.decode())
    assert form == {
        "secret": ["turnstile-secret"],
        "response": ["token-1"],
        "remoteip": ["203.0.113.7"],
    }


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-response"]}),
        httpx.Response(200, json={"success": "true"}),
        httpx.Response(200, text="not json"),
        httpx.Response(500),
    ],
)
async def test_rejections_are_bot_check_failed(response: httpx.Response) -> None:
    async with _http(httpx.MockTransport(lambda r: response)) as http:
        with pytest.raises(ApiError) as excinfo:
            await verify_turnstile(http, SECRET, "token-1", None)
    assert excinfo.value.code == "bot_check_failed"


async def test_network_error_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with _http(httpx.MockTransport(handler)) as http:
        with pytest.raises(ApiError) as excinfo:
            await verify_turnstile(http, SECRET, "token-1", None)
    assert excinfo.value.code == "bot_check_failed"


@pytest.mark.parametrize(("secret", "token"), [(None, "t"), (SECRET, ""), (SECRET, "x" * 3000)])
async def test_missing_secret_or_bad_token_never_calls_cloudflare(
    secret: SecretStr | None, token: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    async with _http(httpx.MockTransport(handler)) as http:
        with pytest.raises(ApiError):
            await verify_turnstile(http, secret, token, None)
