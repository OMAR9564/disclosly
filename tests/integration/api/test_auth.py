"""Integration tests for the auth endpoints (T032) with mocked Google, GitHub and Turnstile."""

import json
from collections.abc import Callable
from http.cookies import SimpleCookie
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import Engine, select

from agent.loop import Budget
from agent.runs import RunService, StreamRedis
from api import schema
from api.auth import oauth
from api.auth.allowlist import cache_key, email_hash
from api.auth.session import REFRESH_COOKIE, SESSION_COOKIE, refresh_key, verify_session
from api.config import Settings
from api.main import create_app
from tests.agent_fakes import FakeTools, ScriptedRouter
from tests.api_fakes import FakeRedis, fake_guards

SETTINGS = Settings(
    app_env="test",
    database_url="postgresql://u:p@localhost:5432/db",
    redis_url="redis://localhost:6379/0",
    cors_allowed_origins=["https://disclosly.com"],
    jwt_secret="jwt-secret-value-that-is-long-enough-123",  # noqa: S106
    google_client_id="google-id",
    google_client_secret="google-secret",  # noqa: S106
    github_client_id="github-id",
    github_client_secret="github-secret",  # noqa: S106
    turnstile_secret_key="turnstile-secret",  # noqa: S106
    web_origin="https://disclosly.com",
    api_origin="https://api.disclosly.com",
)
ALLOWED = {"ada@example.com"}


class Providers:
    """Mock Google, GitHub and Cloudflare endpoints; tweak the fields per test."""

    def __init__(self) -> None:
        self.turnstile_ok = True
        self.google_info: dict[str, Any] = {
            "sub": "g-1",
            "email": "Ada@Example.com",
            "email_verified": True,
            "name": "Ada Lovelace",
        }
        self.github_emails: list[dict[str, Any]] = [
            {"email": "old@example.com", "primary": False, "verified": True},
            {"email": "ada@example.com", "primary": True, "verified": True},
        ]
        self.token_forms: list[dict[str, list[str]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://challenges.cloudflare.com"):
            return httpx.Response(200, json={"success": self.turnstile_ok})
        if url in (oauth.ENDPOINTS["google"].token, oauth.ENDPOINTS["github"].token):
            self.token_forms.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json={"access_token": "provider-token"})
        assert request.headers["authorization"] == "Bearer provider-token"
        if url == oauth.GOOGLE_USERINFO:
            return httpx.Response(200, json=self.google_info)
        if url == oauth.GITHUB_USER:
            return httpx.Response(200, json={"id": 42, "login": "ada", "name": None})
        if url == oauth.GITHUB_EMAILS:
            return httpx.Response(200, json=self.github_emails)
        return httpx.Response(404)


@pytest.fixture
def providers() -> Providers:
    return Providers()


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def client_factory(
    engine: Engine, providers: Providers, redis: FakeRedis
) -> Callable[..., httpx.AsyncClient]:
    def make(allowed: set[str] = ALLOWED) -> httpx.AsyncClient:
        guards = fake_guards(
            allowed,
            redis=redis,
            engine=engine,
            http=httpx.AsyncClient(transport=httpx.MockTransport(providers)),
        )
        service = RunService(
            engine, cast(StreamRedis, redis), ScriptedRouter([]), FakeTools({}), Budget()
        )
        app = create_app(SETTINGS, run_service=service, guards=guards)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://api.disclosly.com"
        )

    return make


def _cookies(response: httpx.Response) -> dict[str, Any]:
    jar: dict[str, Any] = {}
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        for name, morsel in parsed.items():
            jar[name] = morsel
    return jar


async def _start(client: httpx.AsyncClient, provider: str, return_to: str = "/ask") -> str:
    response = await client.post(
        f"/v1/auth/{provider}/start", json={"turnstile_token": "tok", "return_to": return_to}
    )
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["authorize_url"])
    query = parse_qs(url.query)
    assert query["redirect_uri"] == [f"https://api.disclosly.com/v1/auth/{provider}/callback"]
    assert query["code_challenge_method"] == ["S256"]
    return str(query["state"][0])


async def test_google_sign_in_sets_cookies_and_upserts_user(
    engine: Engine, client_factory: Callable[..., httpx.AsyncClient], providers: Providers
) -> None:
    async with client_factory() as client:
        state = await _start(client, "google")
        response = await client.get(
            "/v1/auth/google/callback", params={"code": "c1", "state": state}
        )
    assert response.status_code == 302
    assert response.headers["location"] == "https://disclosly.com/ask"
    cookies = _cookies(response)
    session, refresh = cookies[SESSION_COOKIE], cookies[REFRESH_COOKIE]
    for morsel in (session, refresh):
        assert morsel["httponly"] and morsel["secure"]
        assert morsel["samesite"].lower() == "lax"
        assert morsel["domain"] == ".disclosly.com"
    assert session["max-age"] == "900"
    assert refresh["max-age"] == str(7 * 24 * 3600)
    assert refresh["path"] == "/v1/auth"
    assert providers.token_forms[0]["code_verifier"]

    user = verify_session(session.value, SETTINGS)
    with engine.connect() as conn:
        row = conn.execute(select(schema.users).where(schema.users.c.id == user.id)).one()
    assert (row.email, row.display_name, row.auth_provider) == (
        "ada@example.com",
        "Ada Lovelace",
        "google",
    )


async def test_github_uses_primary_verified_email(
    engine: Engine, client_factory: Callable[..., httpx.AsyncClient]
) -> None:
    async with client_factory() as client:
        state = await _start(client, "github")
        response = await client.get(
            "/v1/auth/github/callback", params={"code": "c1", "state": state}
        )
    assert response.status_code == 302
    user = verify_session(_cookies(response)[SESSION_COOKIE].value, SETTINGS)
    with engine.connect() as conn:
        row = conn.execute(select(schema.users).where(schema.users.c.id == user.id)).one()
    assert (row.email, row.display_name, row.provider_subject) == ("ada@example.com", "ada", "42")


async def test_github_without_verified_primary_is_refused(
    client_factory: Callable[..., httpx.AsyncClient], providers: Providers
) -> None:
    providers.github_emails = [{"email": "ada@example.com", "primary": True, "verified": False}]
    async with client_factory() as client:
        state = await _start(client, "github")
        response = await client.get(
            "/v1/auth/github/callback", params={"code": "c1", "state": state}
        )
    assert response.headers["location"] == "https://disclosly.com/signin?error=email_unverified"
    assert SESSION_COOKIE not in _cookies(response)


async def test_unverified_google_email_is_refused(
    client_factory: Callable[..., httpx.AsyncClient], providers: Providers
) -> None:
    providers.google_info["email_verified"] = False
    async with client_factory() as client:
        state = await _start(client, "google")
        response = await client.get(
            "/v1/auth/google/callback", params={"code": "c1", "state": state}
        )
    assert response.status_code == 302
    assert response.headers["location"].endswith("/signin?error=email_unverified")
    assert not _cookies(response)


async def test_not_allowlisted_goes_to_beta_closed_and_is_not_stored(
    engine: Engine, client_factory: Callable[..., httpx.AsyncClient], providers: Providers
) -> None:
    providers.google_info["email"] = "eve@example.com"
    async with client_factory() as client:
        state = await _start(client, "google")
        response = await client.get(
            "/v1/auth/google/callback", params={"code": "c1", "state": state}
        )
    assert response.headers["location"] == "https://disclosly.com/beta-closed"
    assert not _cookies(response)
    with engine.connect() as conn:
        assert conn.execute(select(schema.users.c.id)).first() is None


@pytest.mark.parametrize("state", ["", "unknown-state"])
async def test_invalid_state_is_refused(
    client_factory: Callable[..., httpx.AsyncClient], state: str
) -> None:
    async with client_factory() as client:
        response = await client.get(
            "/v1/auth/google/callback", params={"code": "c1", "state": state}
        )
    assert response.headers["location"] == "https://disclosly.com/signin?error=invalid_state"


async def test_state_is_single_use_and_bound_to_provider(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    async with client_factory() as client:
        state = await _start(client, "google")
        wrong = await client.get("/v1/auth/github/callback", params={"code": "c", "state": state})
        again = await client.get("/v1/auth/google/callback", params={"code": "c", "state": state})
    assert wrong.headers["location"].endswith("error=invalid_state")
    assert again.headers["location"].endswith("error=invalid_state")


async def test_state_is_stored_for_ten_minutes(
    client_factory: Callable[..., httpx.AsyncClient], redis: FakeRedis
) -> None:
    async with client_factory() as client:
        state = await _start(client, "google")
    stored = json.loads(redis.values[oauth.state_key(state)])
    assert stored["return_to"] == "https://disclosly.com/ask"
    assert redis.ttls[oauth.state_key(state)] == 600


@pytest.mark.parametrize(
    "return_to",
    [
        "https://evil.example/ask",
        "//evil.example/ask",
        "/\\evil.example",
        "https://disclosly.com.evil.example/",
        "https://user@disclosly.com/",
        "javascript:alert(1)",
    ],
)
async def test_open_redirect_is_rejected(
    client_factory: Callable[..., httpx.AsyncClient], return_to: str
) -> None:
    async with client_factory() as client:
        response = await client.post(
            "/v1/auth/google/start", json={"turnstile_token": "tok", "return_to": return_to}
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_bad_turnstile_token_is_rejected(
    client_factory: Callable[..., httpx.AsyncClient], providers: Providers
) -> None:
    providers.turnstile_ok = False
    async with client_factory() as client:
        response = await client.post("/v1/auth/google/start", json={"turnstile_token": "bad"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bot_check_failed"


async def test_unknown_provider_is_rejected(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    async with client_factory() as client:
        response = await client.post("/v1/auth/facebook/start", json={"turnstile_token": "t"})
    assert response.status_code == 422


async def _post_refresh(client: httpx.AsyncClient, path: str, token: str) -> httpx.Response:
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE, token)
    return await client.post(path)


async def _signed_in(client: httpx.AsyncClient) -> dict[str, Any]:
    state = await _start(client, "google")
    response = await client.get("/v1/auth/google/callback", params={"code": "c", "state": state})
    return _cookies(response)


async def test_refresh_rotates_and_revokes_old_token(
    client_factory: Callable[..., httpx.AsyncClient], redis: FakeRedis
) -> None:
    async with client_factory() as client:
        old = (await _signed_in(client))[REFRESH_COOKIE].value
        response = await _post_refresh(client, "/v1/auth/refresh", old)
        assert response.status_code == 204
        new = _cookies(response)
        assert new[REFRESH_COOKIE].value != old
        assert verify_session(new[SESSION_COOKIE].value, SETTINGS)
        assert refresh_key(old) not in redis.values
        replay = await _post_refresh(client, "/v1/auth/refresh", old)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "unauthorized"


async def test_refresh_after_removal_from_allowlist_is_beta_closed(
    client_factory: Callable[..., httpx.AsyncClient], redis: FakeRedis
) -> None:
    async with client_factory() as client:
        token = (await _signed_in(client))[REFRESH_COOKIE].value
    await redis.delete(cache_key(email_hash("ada@example.com")))  # the 60 s cache ran out
    async with client_factory({"someone-else@example.com"}) as client:
        response = await _post_refresh(client, "/v1/auth/refresh", token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "beta_closed"


async def test_logout_clears_cookies_and_revokes(
    client_factory: Callable[..., httpx.AsyncClient], redis: FakeRedis
) -> None:
    async with client_factory() as client:
        token = (await _signed_in(client))[REFRESH_COOKIE].value
        response = await _post_refresh(client, "/v1/auth/logout", token)
    assert response.status_code == 204
    cleared = _cookies(response)
    assert cleared[SESSION_COOKIE]["max-age"] == "0"
    assert cleared[REFRESH_COOKIE]["max-age"] == "0"
    assert refresh_key(token) not in redis.values
