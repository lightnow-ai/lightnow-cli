"""Tests for the OIDC device login flow."""

from __future__ import annotations

import pytest

from lightnow_cli.commands import auth
from lightnow_cli.config import Config


class FakeResponse:
    """Small response stub for device-flow tests."""

    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


class FakeDeviceClient:
    """AsyncClient stub that completes device authorization after one poll."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        assert kwargs.get("timeout") == auth.AUTH_HTTP_TIMEOUT
        self.polls = 0

    async def __aenter__(self) -> "FakeDeviceClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str) -> FakeResponse:
        assert (
            url
            == "https://auth.lightnow.local/realms/lightnow/.well-known/openid-configuration"
        )
        return FakeResponse(
            200,
            {
                "device_authorization_endpoint": "https://auth.lightnow.local/device",
                "token_endpoint": "https://auth.lightnow.local/token",
            },
        )

    async def post(self, url: str, data: dict[str, object]) -> FakeResponse:
        if url.endswith("/device"):
            assert data["client_id"] == "lightnow-cli"
            assert data["code_challenge_method"] == "S256"
            assert isinstance(data["code_challenge"], str)
            assert data["code_challenge"] != ""
            return FakeResponse(
                200,
                {
                    "verification_uri_complete": "https://auth.lightnow.local/device?user_code=TEST",
                    "verification_uri": "https://auth.lightnow.local/device",
                    "user_code": "TEST",
                    "device_code": "device-code",
                    "interval": 0,
                    "expires_in": 10,
                },
            )

        assert url.endswith("/token")
        assert isinstance(data["code_verifier"], str)
        assert data["code_verifier"] != ""
        self.polls += 1
        if self.polls == 1:
            return FakeResponse(400, {"error": "authorization_pending"})
        return FakeResponse(
            200,
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
            },
        )


@pytest.mark.asyncio
async def test_device_code_flow_opens_browser_and_returns_token(monkeypatch) -> None:
    """Device flow opens the verification URL and returns the access token."""
    opened: list[str] = []

    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeDeviceClient)
    monkeypatch.setattr(auth, "open_authorization_url", opened.append)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(auth.asyncio, "sleep", no_sleep)

    token_data = await auth.device_code_flow(
        "https://auth.lightnow.local/realms/lightnow", "lightnow-cli"
    )

    assert token_data["access_token"] == "access-token"
    assert token_data["refresh_token"] == "refresh-token"
    assert opened == ["https://auth.lightnow.local/device?user_code=TEST"]


def test_describe_http_error_names_empty_errors() -> None:
    """HTTP errors with empty string output still produce useful messages."""
    assert auth.describe_http_error(auth.httpx.ReadTimeout("")) == "ReadTimeout"


def test_get_user_info_decodes_unverified_jwt_claims_for_refresh_hints() -> None:
    """The CLI can inspect local JWT metadata without treating it as identity."""
    token = (
        "eyJhbGciOiJub25lIn0."
        "eyJzdWIiOiIxMjMiLCJlbWFpbCI6InRlc3RAbGlnaHRub3cubG9jYWwiLCJwcmVmZXJyZWRfdXNlcm5hbWUiOiJ0ZXN0IiwiZXhwIjo5OTk5OTk5OTk5fQ."
    )

    user_info = auth.get_user_info(token)

    assert user_info is not None
    assert user_info["sub"] == "123"
    assert user_info["email"] == "test@lightnow.local"
    assert user_info["preferred_username"] == "test"


def test_token_expiration_check() -> None:
    """Expired tokens are detected from the current access token."""
    expired_token = (
        "eyJhbGciOiJub25lIn0."
        "eyJzdWIiOiIxMjMiLCJlbWFpbCI6InRlc3RAbGlnaHRub3cubG9jYWwiLCJleHAiOjF9."
    )
    no_exp_token = "not-a-jwt"

    assert auth.is_token_expired(expired_token) is True
    assert auth.is_token_expired(no_exp_token) is False


@pytest.mark.asyncio
async def test_fetch_user_info_uses_oidc_userinfo_endpoint(monkeypatch) -> None:
    """Identity is fetched from the issuer instead of trusting local JWT claims."""

    class FakeUserInfoClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            assert kwargs.get("timeout") == auth.AUTH_HTTP_TIMEOUT

        async def __aenter__(self) -> "FakeUserInfoClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
            assert url == "https://auth.lightnow.local/userinfo"
            assert headers["Authorization"] == "Bearer access-token"
            return FakeResponse(
                200,
                {
                    "sub": "123",
                    "email": "test@lightnow.local",
                    "preferred_username": "test",
                },
            )

    async def endpoints(_: str) -> dict[str, str]:
        return {
            "device_authorization_endpoint": "https://auth.lightnow.local/device",
            "token_endpoint": "https://auth.lightnow.local/token",
            "userinfo_endpoint": "https://auth.lightnow.local/userinfo",
        }

    monkeypatch.setattr(auth, "discover_oidc_endpoints", endpoints)
    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeUserInfoClient)

    user_info = await auth.fetch_user_info(
        "https://auth.lightnow.local/realms/lightnow", "access-token"
    )

    assert user_info["email"] == "test@lightnow.local"


class FakeRefreshClient:
    """AsyncClient stub for refresh token tests."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.last_data: dict[str, object] = {}

    async def __aenter__(self) -> "FakeRefreshClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, data: dict[str, object]) -> FakeResponse:
        assert url == "https://auth.lightnow.local/token"
        self.last_data = data
        return self.response


@pytest.mark.asyncio
async def test_refresh_access_token_returns_new_tokens(monkeypatch) -> None:
    """Refresh token grant returns the new token response."""
    fake = FakeRefreshClient(
        FakeResponse(
            200,
            {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
            },
        )
    )

    async def endpoints(_: str) -> dict[str, str]:
        return {
            "device_authorization_endpoint": "https://auth.lightnow.local/device",
            "token_endpoint": "https://auth.lightnow.local/token",
        }

    monkeypatch.setattr(auth, "discover_oidc_endpoints", endpoints)
    monkeypatch.setattr(
        auth.httpx,
        "AsyncClient",
        lambda *args, **kwargs: fake,
    )

    token_data = await auth.refresh_access_token(
        "https://auth.lightnow.local/realms/lightnow",
        "lightnow-cli",
        "refresh-token",
    )

    assert token_data["access_token"] == "new-access-token"
    assert fake.last_data["grant_type"] == "refresh_token"
    assert fake.last_data["refresh_token"] == "refresh-token"


@pytest.mark.asyncio
async def test_refresh_access_token_invalid_grant_is_expired(monkeypatch) -> None:
    """Invalid refresh grants use the same expired-token message."""
    fake = FakeRefreshClient(FakeResponse(400, {"error": "invalid_grant"}))

    async def endpoints(_: str) -> dict[str, str]:
        return {
            "device_authorization_endpoint": "https://auth.lightnow.local/device",
            "token_endpoint": "https://auth.lightnow.local/token",
        }

    monkeypatch.setattr(auth, "discover_oidc_endpoints", endpoints)
    monkeypatch.setattr(
        auth.httpx,
        "AsyncClient",
        lambda *args, **kwargs: fake,
    )

    with pytest.raises(auth.AccessTokenExpired, match="Access token has expired"):
        await auth.refresh_access_token(
            "https://auth.lightnow.local/realms/lightnow",
            "lightnow-cli",
            "refresh-token",
        )


def test_require_access_token_refreshes_expired_token(monkeypatch) -> None:
    """Expired access tokens are refreshed and persisted."""
    expired_token = (
        "eyJhbGciOiJub25lIn0."
        "eyJzdWIiOiIxMjMiLCJlbWFpbCI6InRlc3RAbGlnaHRub3cubG9jYWwiLCJleHAiOjF9."
    )
    refreshed_token = (
        "eyJhbGciOiJub25lIn0."
        "eyJzdWIiOiIxMjMiLCJlbWFpbCI6InRlc3RAbGlnaHRub3cubG9jYWwiLCJleHAiOjk5OTk5OTk5OTl9."
    )
    config = Config(
        access_token=expired_token,
        refresh_token="refresh-token",
        user_info={"email": "test@lightnow.local", "exp": 1},
    )
    stored: list[tuple[str, str | None]] = []

    async def refresh(_: str, __: str, refresh_token: str) -> dict[str, str]:
        assert refresh_token == "refresh-token"
        return {
            "access_token": refreshed_token,
            "refresh_token": "next-refresh-token",
        }

    monkeypatch.setattr(auth.config_manager, "load_config", lambda: config)
    monkeypatch.setattr(auth, "refresh_access_token", refresh)
    monkeypatch.setattr(
        auth.config_manager,
        "set_token",
        lambda access, refresh_token=None, user_info=None: stored.append(
            (access, refresh_token)
        ),
    )

    assert auth.require_access_token() == refreshed_token
    assert stored == [(refreshed_token, "next-refresh-token")]


def test_require_access_token_ignores_cached_user_info_for_expiration(
    monkeypatch,
) -> None:
    """A stale cached identity cannot keep an expired access token alive."""
    expired_token = (
        "eyJhbGciOiJub25lIn0."
        "eyJzdWIiOiIxMjMiLCJlbWFpbCI6InRlc3RAbGlnaHRub3cubG9jYWwiLCJleHAiOjF9."
    )
    refreshed_token = (
        "eyJhbGciOiJub25lIn0."
        "eyJzdWIiOiIxMjMiLCJlbWFpbCI6InRlc3RAbGlnaHRub3cubG9jYWwiLCJleHAiOjk5OTk5OTk5OTl9."
    )
    config = Config(
        access_token=expired_token,
        refresh_token="refresh-token",
        user_info={"email": "forged@example.com", "exp": 9999999999},
    )
    stored: list[tuple[str, str | None, dict[str, object] | None]] = []

    async def refresh(_: str, __: str, refresh_token: str) -> dict[str, str]:
        assert refresh_token == "refresh-token"
        return {
            "access_token": refreshed_token,
            "refresh_token": "next-refresh-token",
        }

    monkeypatch.setattr(auth.config_manager, "load_config", lambda: config)
    monkeypatch.setattr(auth, "refresh_access_token", refresh)
    monkeypatch.setattr(
        auth.config_manager,
        "set_token",
        lambda access, refresh_token=None, user_info=None: stored.append(
            (access, refresh_token, user_info)
        ),
    )

    assert auth.require_access_token() == refreshed_token
    assert stored == [(refreshed_token, "next-refresh-token", None)]


def test_require_access_token_without_refresh_token_reports_expired() -> None:
    """Expired access tokens without refresh tokens fail consistently."""
    original_config = auth.config_manager._config
    auth.config_manager._config = Config(
        access_token=(
            "eyJhbGciOiJub25lIn0."
            "eyJzdWIiOiIxMjMiLCJlbWFpbCI6InRlc3RAbGlnaHRub3cubG9jYWwiLCJleHAiOjF9."
        ),
        user_info={"email": "test@lightnow.local", "exp": 1},
    )
    try:
        with pytest.raises(auth.AccessTokenExpired, match="Access token has expired"):
            auth.require_access_token()
    finally:
        auth.config_manager._config = original_config


def test_current_user_info_retries_userinfo_after_refresh(monkeypatch) -> None:
    """A stale access token discovered by userinfo is refreshed and retried once."""
    config = Config(
        access_token=(
            "eyJhbGciOiJub25lIn0." "eyJzdWIiOiIxMjMiLCJleHAiOjk5OTk5OTk5OTl9."
        ),
        refresh_token="refresh-token",
    )
    calls: list[str] = []

    async def fetch(_: str, token: str) -> dict[str, str]:
        calls.append(token)
        if token == "old-access-token":
            raise auth.AccessTokenExpired(auth.ACCESS_TOKEN_EXPIRED_MESSAGE)
        return {"email": "test@lightnow.local"}

    monkeypatch.setattr(auth.config_manager, "load_config", lambda: config)
    monkeypatch.setattr(auth, "require_access_token", lambda: "old-access-token")
    monkeypatch.setattr(auth, "fetch_user_info", fetch)
    monkeypatch.setattr(auth, "refresh_current_session", lambda: "new-access-token")

    assert auth.current_user_info()["email"] == "test@lightnow.local"
    assert calls == ["old-access-token", "new-access-token"]


def test_current_user_info_falls_back_to_token_claims_when_userinfo_rejects(
    monkeypatch,
) -> None:
    """Whoami/status can still display identity when the issuer rejects userinfo."""
    token = (
        "eyJhbGciOiJub25lIn0."
        "eyJzdWIiOiIxMjMiLCJlbWFpbCI6InRlc3RAbGlnaHRub3cubG9jYWwiLCJwcmVmZXJyZWRfdXNlcm5hbWUiOiJ0ZXN0IiwiZXhwIjo5OTk5OTk5OTk5fQ."
    )
    config = Config(access_token=token)

    async def fetch(_: str, __: str) -> dict[str, str]:
        raise auth.AuthError("Failed to fetch user information: HTTP 403")

    monkeypatch.setattr(auth.config_manager, "load_config", lambda: config)
    monkeypatch.setattr(auth, "require_access_token", lambda: token)
    monkeypatch.setattr(auth, "fetch_user_info", fetch)

    user_info = auth.current_user_info()

    assert user_info["sub"] == "123"
    assert user_info["email"] == "test@lightnow.local"
