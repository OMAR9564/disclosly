"""OAuth authorization-code flow with PKCE for Google and GitHub (research §R4).

`start` stores `state`, the PKCE verifier and `return_to` in Redis (`oauth:state:{state}`,
10 minutes, used once) and returns the provider's authorize URL. `finish` exchanges the code
and returns the verified identity. Only verified emails are accepted; for GitHub that is the
primary verified email from `/user/emails`.
"""

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import SecretStr

from api.config import Settings

ProviderName = Literal["google", "github"]
STATE_TTL_SECONDS = 600
TIMEOUT_SECONDS = 10.0
MAX_RETURN_TO = 512


class OAuthError(Exception):
    """The callback cannot sign the user in; `reason` goes to the sign-in page."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class StateRedis(Protocol):
    async def set(self, name: str, value: str, ex: int | None = None) -> Any: ...

    async def getdel(self, name: str) -> Any: ...


@dataclass(frozen=True)
class ProviderEndpoints:
    authorize: str
    token: str
    scope: str


ENDPOINTS: dict[ProviderName, ProviderEndpoints] = {
    "google": ProviderEndpoints(
        authorize="https://accounts.google.com/o/oauth2/v2/auth",
        token="https://oauth2.googleapis.com/token",  # noqa: S106
        scope="openid email profile",
    ),
    "github": ProviderEndpoints(
        authorize="https://github.com/login/oauth/authorize",
        token="https://github.com/login/oauth/access_token",  # noqa: S106
        scope="read:user user:email",
    ),
}
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
GITHUB_USER = "https://api.github.com/user"
GITHUB_EMAILS = "https://api.github.com/user/emails"


@dataclass(frozen=True)
class Identity:
    provider: ProviderName
    subject: str
    email: str
    display_name: str


@dataclass(frozen=True)
class PendingLogin:
    provider: ProviderName
    verifier: str
    return_to: str


def state_key(state: str) -> str:
    return f"oauth:state:{state}"


def credentials(provider: ProviderName, settings: Settings) -> tuple[str, SecretStr] | None:
    if provider == "google":
        client_id, secret = settings.google_client_id, settings.google_client_secret
    else:
        client_id, secret = settings.github_client_id, settings.github_client_secret
    return (client_id, secret) if client_id and secret else None


def redirect_uri(provider: ProviderName, settings: Settings) -> str:
    return f"{settings.api_origin}/v1/auth/{provider}/callback"


def safe_return_to(value: str | None, settings: Settings) -> str | None:
    """An absolute URL on WEB_ORIGIN, or `None` for anything else (open-redirect guard)."""
    if not value:
        return f"{settings.web_origin}/"
    if len(value) > MAX_RETURN_TO or "\\" in value or any(ord(c) < 0x20 for c in value):
        return None
    if value.startswith("/") and not value.startswith("//"):
        return f"{settings.web_origin}{value}"
    parts = urlsplit(value)
    origin = f"{parts.scheme}://{parts.netloc}"
    if parts.username is not None or parts.password is not None or origin != settings.web_origin:
        return None
    return value


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def start(
    redis: StateRedis, provider: ProviderName, client_id: str, settings: Settings, return_to: str
) -> str:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    pending = {"provider": provider, "verifier": verifier, "return_to": return_to}
    await redis.set(state_key(state), json.dumps(pending), ex=STATE_TTL_SECONDS)
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(provider, settings),
        "response_type": "code",
        "scope": ENDPOINTS[provider].scope,
        "state": state,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }
    if provider == "google":
        query["prompt"] = "select_account"
    return f"{ENDPOINTS[provider].authorize}?{urlencode(query)}"


async def take_state(redis: StateRedis, provider: ProviderName, state: str) -> PendingLogin:
    if not state or len(state) > 128:
        raise OAuthError("invalid_state")
    raw = await redis.getdel(state_key(state))
    if raw is None:
        raise OAuthError("invalid_state")
    data = json.loads(raw)
    if data.get("provider") != provider:
        raise OAuthError("invalid_state")
    return PendingLogin(provider, str(data["verifier"]), str(data["return_to"]))


async def _json(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise OAuthError("provider_error")
    try:
        return response.json()
    except ValueError:
        raise OAuthError("provider_error") from None


async def exchange(
    http: httpx.AsyncClient,
    pending: PendingLogin,
    code: str,
    client: tuple[str, SecretStr],
    settings: Settings,
) -> Identity:
    """Code → access token → verified identity. Raises `OAuthError`."""
    if not code or len(code) > 2048:
        raise OAuthError("invalid_code")
    client_id, secret = client
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(pending.provider, settings),
        "client_id": client_id,
        "client_secret": secret.get_secret_value(),
        "code_verifier": pending.verifier,
    }
    try:
        token_response = await http.post(
            ENDPOINTS[pending.provider].token,
            data=form,
            headers={"Accept": "application/json"},
            timeout=TIMEOUT_SECONDS,
        )
        token = (await _json(token_response)).get("access_token")
        if not isinstance(token, str) or not token:
            raise OAuthError("provider_error")
        auth = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if pending.provider == "google":
            return await _google_identity(http, auth)
        return await _github_identity(http, auth)
    except httpx.HTTPError:
        raise OAuthError("provider_error") from None


async def _google_identity(http: httpx.AsyncClient, auth: dict[str, str]) -> Identity:
    info = await _json(await http.get(GOOGLE_USERINFO, headers=auth, timeout=TIMEOUT_SECONDS))
    email = info.get("email")
    if not isinstance(email, str) or info.get("email_verified") is not True:
        raise OAuthError("email_unverified")
    name = info.get("name") if isinstance(info.get("name"), str) else None
    return Identity("google", str(info["sub"]), email.lower(), _display(name, email))


async def _github_identity(http: httpx.AsyncClient, auth: dict[str, str]) -> Identity:
    user = await _json(await http.get(GITHUB_USER, headers=auth, timeout=TIMEOUT_SECONDS))
    emails = await _json(await http.get(GITHUB_EMAILS, headers=auth, timeout=TIMEOUT_SECONDS))
    primary = (
        next(
            (
                entry["email"]
                for entry in emails
                if isinstance(entry, dict)
                and entry.get("primary") is True
                and entry.get("verified") is True
                and isinstance(entry.get("email"), str)
            ),
            None,
        )
        if isinstance(emails, list)
        else None
    )
    if primary is None:
        raise OAuthError("email_unverified")
    name = user.get("name") or user.get("login")
    display = _display(name if isinstance(name, str) else None, primary)
    return Identity("github", str(user["id"]), primary.lower(), display)


def _display(name: str | None, email: str) -> str:
    value = (name or "").strip() or email.split("@", 1)[0]
    return value[:200]
