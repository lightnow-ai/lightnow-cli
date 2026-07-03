"""Tests for the Registry API client."""

from __future__ import annotations

import httpx
import pytest

from lightnow_cli import __version__
from lightnow_cli.client import MCPRegistryClient
from lightnow_cli.config import Config


class FakeResponse:
    """Small response stub for client tests."""

    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


class FakeHttpClient:
    """AsyncClient stub returning canned registry responses."""

    def __init__(self) -> None:
        self.last_url = ""
        self.last_headers: dict[str, str] = {}
        self.last_params: dict[str, object] = {}
        self.last_json: dict[str, object] = {}

    async def __aenter__(self) -> "FakeHttpClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        assert url == "https://registry-api.lightnow.local/v0.1/publish"
        assert timeout == 30.0
        self.last_json = json
        self.last_headers = headers
        return FakeResponse(200, {"server_id": "server", "version": "1.0.0"})

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        if method == "POST":
            return await self.post(url, **kwargs)  # type: ignore[arg-type]
        if method == "GET":
            return await self.get(url, **kwargs)  # type: ignore[arg-type]
        raise AssertionError(f"unexpected method {method}")

    async def get(
        self,
        url: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        assert timeout == 30.0
        self.last_url = url
        self.last_params = params
        self.last_headers = headers
        if url.endswith("/servers/github/versions/1.0.0"):
            return FakeResponse(200, {"name": "github", "version": "1.0.0"})
        if url.endswith("/versions"):
            return FakeResponse(200, {"servers": [{"name": "github"}]})
        return FakeResponse(200, {"servers": [{"name": "github"}], "total": 1})


class ErrorHttpClient:
    """AsyncClient stub for error responses."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    async def __aenter__(self) -> "ErrorHttpClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, *args: object, **kwargs: object) -> FakeResponse:
        return self.response

    async def post(self, *args: object, **kwargs: object) -> FakeResponse:
        return self.response

    async def request(self, *args: object, **kwargs: object) -> FakeResponse:
        return self.response


class SequenceHttpClient:
    """AsyncClient stub returning responses in order."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.headers: list[dict[str, str]] = []

    async def __aenter__(self) -> "SequenceHttpClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def request(self, *args: object, **kwargs: object) -> FakeResponse:
        self.headers.append(kwargs["headers"])  # type: ignore[arg-type]
        return self.responses.pop(0)


class RaisingResponse(FakeResponse):
    """Response stub that raises an HTTPStatusError from raise_for_status."""

    def __init__(
        self, status_code: int, payload: dict[str, object], *, raise_status: int
    ) -> None:
        super().__init__(status_code, payload)
        self.raise_status = raise_status

    def raise_for_status(self) -> None:
        request = httpx.Request(
            "POST", "https://registry-api.lightnow.local/v0.1/publish"
        )
        response = httpx.Response(self.raise_status, request=request)
        response._content = str(self._payload).encode()
        response.json = lambda: self._payload  # type: ignore[method-assign]
        raise httpx.HTTPStatusError("error", request=request, response=response)


def test_client_user_agent_uses_package_version() -> None:
    """HTTP requests identify the installed CLI version."""
    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )

    assert client._get_headers()["User-Agent"] == f"lightnow-cli/{__version__}"


@pytest.mark.asyncio
async def test_client_publishes_with_auth_header(monkeypatch) -> None:
    """Publishing sends the token and server payload to the Registry API."""
    fake = FakeHttpClient()
    monkeypatch.setattr("lightnow_cli.client.httpx.AsyncClient", lambda: fake)

    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )
    result = await client.publish_server(
        {"name": "io.lightnow/server"}, tenant="tenant"
    )

    assert result == {"server_id": "server", "version": "1.0.0"}
    assert fake.last_headers["Authorization"] == "Bearer token"
    assert fake.last_headers["X-Tenant"] == "tenant"
    assert fake.last_json["name"] == "io.lightnow/server"


@pytest.mark.asyncio
async def test_client_publish_reports_validation_details(monkeypatch) -> None:
    """Publish validation errors include API error locations and messages."""
    fake = ErrorHttpClient(
        FakeResponse(
            422,
            {
                "detail": "The request data does not conform to the MCP server schema",
                "errors": [
                    {
                        "location": "body.name",
                        "message": "The string should match namespace/name.",
                    }
                ],
            },
        )
    )
    monkeypatch.setattr("lightnow_cli.client.httpx.AsyncClient", lambda: fake)
    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )

    with pytest.raises(ValueError, match="body.name"):
        await client.publish_server({"name": "invalid"})


@pytest.mark.asyncio
async def test_client_lists_servers(monkeypatch) -> None:
    """Server queries forward search, favorites and cursor parameters."""
    fake = FakeHttpClient()
    monkeypatch.setattr("lightnow_cli.client.httpx.AsyncClient", lambda: fake)

    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )
    result = await client.list_servers(
        search="redis",
        favorites="effective",
        limit=5,
        cursor="cursor-token",
    )

    assert result["total"] == 1
    assert fake.last_params["search"] == "redis"
    assert fake.last_params["favorites"] == "effective"
    assert fake.last_params["limit"] == 5
    assert fake.last_params["cursor"] == "cursor-token"


@pytest.mark.asyncio
async def test_client_gets_server_info(monkeypatch) -> None:
    """Server info lookup returns the decoded registry payload."""
    fake = FakeHttpClient()
    monkeypatch.setattr("lightnow_cli.client.httpx.AsyncClient", lambda: fake)

    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )
    result = await client.get_server_info("github", version="1.0.0")

    assert result == {"name": "github", "version": "1.0.0"}
    assert fake.last_url.endswith("/servers/github/versions/1.0.0")


@pytest.mark.asyncio
async def test_client_encodes_registry_server_names(monkeypatch) -> None:
    """Registry server names with slashes are encoded in path parameters."""
    fake = FakeHttpClient()
    monkeypatch.setattr("lightnow_cli.client.httpx.AsyncClient", lambda: fake)

    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )
    await client.get_server_info("io.github.SonarSource/sonarqube-mcp-server")

    assert fake.last_url.endswith(
        "/servers/io.github.SonarSource%2Fsonarqube-mcp-server/versions"
    )
    assert fake.last_params["limit"] == 1


@pytest.mark.asyncio
async def test_publish_requires_token() -> None:
    """Publishing fails explicitly without authentication."""
    from lightnow_cli.config import config_manager

    original_config = config_manager._config
    config_manager._config = Config(access_token=None)
    try:
        client = MCPRegistryClient(base_url="https://registry-api.lightnow.local/v0.1")

        with pytest.raises(ValueError, match="Not authenticated"):
            await client.publish_server({"name": "server"})
    finally:
        config_manager._config = original_config


@pytest.mark.asyncio
async def test_client_list_reports_unauthorized(monkeypatch) -> None:
    """Unauthorized registry responses become stable auth errors."""
    fake = ErrorHttpClient(FakeResponse(401, {"error": "unauthorized"}))
    monkeypatch.setattr("lightnow_cli.client.httpx.AsyncClient", lambda: fake)
    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )

    with pytest.raises(ValueError, match="Authentication failed: unauthorized"):
        await client.list_servers()


@pytest.mark.asyncio
async def test_client_refreshes_and_retries_after_unauthorized(monkeypatch) -> None:
    """Registry API 401 responses use the shared async refresh-and-retry path."""
    from lightnow_cli.config import config_manager

    fake = SequenceHttpClient(
        [
            FakeResponse(401, {"error": "expired"}),
            FakeResponse(200, {"servers": [{"name": "github"}], "total": 1}),
        ]
    )
    monkeypatch.setattr(
        "lightnow_cli.authenticated_http.httpx.AsyncClient", lambda: fake
    )

    async def refresh() -> str:
        return "new-token"

    monkeypatch.setattr(
        "lightnow_cli.authenticated_http.async_refresh_current_session", refresh
    )

    original_config = config_manager._config
    config_manager._config = Config(
        access_token=(
            "eyJhbGciOiJub25lIn0." "eyJzdWIiOiIxMjMiLCJleHAiOjk5OTk5OTk5OTl9."
        ),
        refresh_token="refresh-token",
    )
    try:
        client = MCPRegistryClient(base_url="https://registry-api.lightnow.local/v0.1")
        result = await client.list_servers()
    finally:
        config_manager._config = original_config

    assert result["total"] == 1
    assert fake.headers[0]["Authorization"].startswith("Bearer eyJ")
    assert fake.headers[1]["Authorization"] == "Bearer new-token"


@pytest.mark.asyncio
async def test_client_info_reports_not_found(monkeypatch) -> None:
    """Missing server lookups are reported as not found."""
    fake = ErrorHttpClient(FakeResponse(404, {"error": "not_found"}))
    monkeypatch.setattr("lightnow_cli.client.httpx.AsyncClient", lambda: fake)
    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )

    with pytest.raises(ValueError, match="not found"):
        await client.get_server_info("missing")


@pytest.mark.asyncio
async def test_client_publish_reports_validation_error(monkeypatch) -> None:
    """Validation errors from publish are preserved for the caller."""
    fake = ErrorHttpClient(FakeResponse(422, {"detail": "bad metadata"}))
    monkeypatch.setattr("lightnow_cli.client.httpx.AsyncClient", lambda: fake)
    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )

    with pytest.raises(ValueError, match="bad metadata"):
        await client.publish_server({"name": "server"})


@pytest.mark.asyncio
async def test_client_publish_preserves_validation_error_from_raised_status(
    monkeypatch,
) -> None:
    """Validation details are preserved even when raised by raise_for_status."""
    fake = ErrorHttpClient(
        RaisingResponse(200, {"detail": "bad metadata"}, raise_status=422)
    )
    monkeypatch.setattr("lightnow_cli.client.httpx.AsyncClient", lambda: fake)
    client = MCPRegistryClient(
        base_url="https://registry-api.lightnow.local/v0.1", token="token"
    )

    with pytest.raises(ValueError, match="bad metadata"):
        await client.publish_server({"name": "server"})
