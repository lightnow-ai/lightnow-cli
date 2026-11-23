"""Local Proxy sync validates profile use before changing local configuration."""

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from lightnow_cli.main import app

API_URL = "https://registry.example.test/v0.1"
SETTINGS = {
    "settings": {
        "integrations": {
            "localProxy": {"enabled": True, "profile": "configured-profile"}
        }
    }
}


@pytest.mark.parametrize("mode", ["--local-proxy", "--from-settings"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("failure", [403, 404, 503, 401, "network", "malformed"])
def test_unavailable_profile_preserves_local_configuration(
    tmp_path: Path, mode: str, dry_run: bool, failure: int | str
) -> None:
    """Denial, failed authentication and API errors cannot replace a working setup."""
    target = tmp_path / "config.toml"
    proxy = tmp_path / "proxy.yaml"
    target.write_text('model = "existing-model"\n')
    proxy.write_text("local_proxy:\n  profile: original\n")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    def respond(method: str, url: str, **kwargs) -> httpx.Response:
        if url == f"{API_URL}/settings":
            return httpx.Response(200, json=SETTINGS)
        assert method == "GET"
        assert url == f"{API_URL}/integrations/profiles/requested-profile/servers"
        assert kwargs.get("params", {}).get("include") != "secrets"
        assert kwargs["headers"]["X-Tenant"] == "example-organization"
        if failure == "network":
            raise httpx.ConnectError("profile lookup unavailable")
        if failure == "malformed":
            return httpx.Response(200, json={"unexpected": True})
        return httpx.Response(int(failure), json={"error": {"message": "Unavailable"}})

    with (
        patch(
            "lightnow_cli.commands.integrations.require_access_token",
            return_value="test-session",
        ),
        patch("lightnow_cli.authenticated_http.httpx.request", side_effect=respond),
        patch(
            "lightnow_cli.authenticated_http.refresh_current_session",
            return_value="renewed-session",
        ),
        patch(
            "lightnow_cli.commands.integrations.config_manager.persist_current_session"
        ) as persist,
        patch(
            "lightnow_cli.commands.integrations.config_manager.get_or_create_device_installation_id",
            return_value="11111111-1111-4111-8111-111111111111",
        ) as installation,
        patch(
            "lightnow_cli.commands.integrations.register_runtime_device_client"
        ) as register,
    ):
        result = CliRunner().invoke(
            app,
            [
                "sync",
                "--client",
                "codex",
                mode,
                "--profile",
                "requested-profile",
                "--tenant",
                "example-organization",
                "--api-url",
                API_URL,
                "--config-path",
                str(target),
                "--local-proxy-config-path",
                str(proxy),
                *(["--dry-run"] if dry_run else []),
            ],
        )

    assert result.exit_code == 1
    assert "Integration sync failed" in result.stdout
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    persist.assert_not_called()
    installation.assert_not_called()
    register.assert_not_called()


@pytest.mark.parametrize("tenant", [None, "example-organization"])
@pytest.mark.parametrize("profile", ["default", "named-profile"])
def test_profile_preflight_uses_read_authorization_without_secrets(
    tmp_path: Path, tenant: str | None, profile: str
) -> None:
    """Personal and organization profiles use the runtime read endpoint, including empty profiles."""

    def respond(method: str, url: str, **kwargs) -> httpx.Response:
        assert method == "GET"
        assert url == f"{API_URL}/integrations/profiles/{profile}/servers"
        assert kwargs.get("params", {}) == {}
        assert kwargs["headers"].get("X-Tenant") == tenant
        assert kwargs["headers"]["Authorization"] == "Bearer test-session"
        return httpx.Response(200, json={"servers": []})

    with (
        patch(
            "lightnow_cli.commands.integrations.require_access_token",
            return_value="test-session",
        ),
        patch(
            "lightnow_cli.authenticated_http.httpx.request", side_effect=respond
        ) as request,
    ):
        result = CliRunner().invoke(
            app,
            [
                "sync",
                "--client",
                "codex",
                "--local-proxy",
                "--dry-run",
                "--profile",
                profile,
                "--api-url",
                API_URL,
                "--config-path",
                str(tmp_path / "config.toml"),
                "--local-proxy-config-path",
                str(tmp_path / "proxy.yaml"),
                *(["--tenant", tenant] if tenant else []),
            ],
        )

    assert result.exit_code == 0, result.stdout
    request.assert_called_once()
    assert not list(tmp_path.iterdir())
