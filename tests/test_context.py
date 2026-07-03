"""Tests for LightNow context selection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from lightnow_cli.config import Config
from lightnow_cli.main import app


class FakeResponse:
    """Small response stub for context API tests."""

    def __init__(self, status_code: int, payload: object, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        return self._payload


def test_context_show_displays_personal_context() -> None:
    """The default context is Personal."""
    runner = CliRunner()

    with patch(
        "lightnow_cli.commands.context.config_manager.load_config",
        return_value=Config(),
    ):
        result = runner.invoke(app, ["context", "--show"])

    assert result.exit_code == 0
    assert "Personal" in result.stdout


def test_context_personal_stores_personal_context() -> None:
    """Users can switch back to personal context explicitly."""
    runner = CliRunner()

    with patch(
        "lightnow_cli.commands.context.config_manager.set_personal_context"
    ) as set_personal:
        result = runner.invoke(app, ["context", "--personal"])

    assert result.exit_code == 0
    set_personal.assert_called_once_with()
    assert "Context set to Personal" in result.stdout


def test_context_tenant_resolves_subdomain_and_stores_tenant_id() -> None:
    """Tenant subdomains are resolved once and stored as tenant IDs."""
    runner = CliRunner()
    response = FakeResponse(
        200,
        [
            {
                "id": "tenant-uuid",
                "name": "Acme Inc.",
                "subdomain": "acme",
                "role": "owner",
                "plan": "enterprise",
            }
        ],
    )

    with (
        patch(
            "lightnow_cli.commands.context.require_access_token", return_value="token"
        ),
        patch(
            "lightnow_cli.commands.context.config_manager.load_config",
            return_value=Config(
                admin_api_url="https://admin-api.example.com/v0/portal"
            ),
        ),
        patch(
            "lightnow_cli.commands.context.request_with_refresh", return_value=response
        ) as request,
        patch(
            "lightnow_cli.commands.context.config_manager.set_tenant_context"
        ) as set_tenant,
    ):
        result = runner.invoke(app, ["context", "--tenant", "acme"])

    assert result.exit_code == 0
    request.assert_called_once()
    assert request.call_args.args[:2] == (
        "GET",
        "https://admin-api.example.com/v0/portal/tenants",
    )
    set_tenant.assert_called_once_with("tenant-uuid", "Acme Inc. (acme)")
    assert "Context set to organization Acme Inc. (acme)" in result.stdout


def test_context_unknown_tenant_fails_without_changing_context() -> None:
    """Unknown tenants fail explicitly."""
    runner = CliRunner()
    response = FakeResponse(
        200,
        [{"id": "tenant-uuid", "name": "Acme Inc.", "subdomain": "acme"}],
    )

    with (
        patch(
            "lightnow_cli.commands.context.require_access_token", return_value="token"
        ),
        patch(
            "lightnow_cli.commands.context.config_manager.load_config",
            return_value=Config(
                admin_api_url="https://admin-api.example.com/v0/portal"
            ),
        ),
        patch(
            "lightnow_cli.commands.context.request_with_refresh", return_value=response
        ),
        patch(
            "lightnow_cli.commands.context.config_manager.set_tenant_context"
        ) as set_tenant,
    ):
        result = runner.invoke(app, ["context", "--tenant", "missing"])

    assert result.exit_code == 1
    set_tenant.assert_not_called()
    assert "No organization matches 'missing'" in result.stdout


def test_context_interactive_choice_stores_selected_tenant() -> None:
    """Interactive context selection stores the chosen organization."""
    runner = CliRunner()
    response = FakeResponse(
        200,
        [
            {"id": "tenant-uuid", "name": "Acme Inc.", "subdomain": "acme"},
        ],
    )

    with (
        patch(
            "lightnow_cli.commands.context.require_access_token", return_value="token"
        ),
        patch(
            "lightnow_cli.commands.context.config_manager.load_config",
            return_value=Config(
                admin_api_url="https://admin-api.example.com/v0/portal"
            ),
        ),
        patch(
            "lightnow_cli.commands.context.request_with_refresh", return_value=response
        ),
        patch(
            "lightnow_cli.commands.context.config_manager.set_tenant_context"
        ) as set_tenant,
    ):
        result = runner.invoke(app, ["context"], input="2\n")

    assert result.exit_code == 0
    set_tenant.assert_called_once_with("tenant-uuid", "Acme Inc. (acme)")


def test_context_api_401_uses_authentication_error() -> None:
    """Tenant listing uses the shared auth error path."""
    runner = CliRunner()
    response = MagicMock()
    response.status_code = 401

    with (
        patch(
            "lightnow_cli.commands.context.require_access_token", return_value="token"
        ),
        patch(
            "lightnow_cli.commands.context.config_manager.load_config",
            return_value=Config(
                admin_api_url="https://admin-api.example.com/v0/portal"
            ),
        ),
        patch(
            "lightnow_cli.commands.context.request_with_refresh", return_value=response
        ),
        patch(
            "lightnow_cli.commands.context.authentication_error_from_response",
            side_effect=ValueError("unauthorized"),
        ),
    ):
        result = runner.invoke(app, ["context", "--tenant", "acme"])

    assert result.exit_code == 1
    assert "Context failed" in result.stdout
