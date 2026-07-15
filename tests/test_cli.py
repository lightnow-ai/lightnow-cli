"""Test CLI commands."""

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from lightnow_cli import __version__
from lightnow_cli.config import (
    DEFAULT_CLIENT_ID,
    DEFAULT_ISSUER,
    LOCAL_ADMIN_API_URL,
    LOCAL_ISSUER,
    LOCAL_REGISTRY_API_URL,
)
from lightnow_cli.main import app


@pytest.fixture
def runner():
    """Create a CLI runner."""
    return CliRunner()


@pytest.fixture
def temp_dir():
    """Create a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def valid_server_json():
    """Valid server.json content."""
    return {
        "name": "io.lightnow/test-server",
        "version": "1.0.0",
        "description": "A test MCP server for CLI testing",
        "author": {"name": "Test Author", "email": "test@example.com"},
        "license": "MIT",
        "transport": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "test_server"],
        },
    }


def test_cli_version(runner):
    """Test CLI version command."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"LightNow CLI {__version__}" in result.stdout


def test_cli_help(runner):
    """Test CLI help command."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "LightNow CLI" in result.stdout
    assert "login" in result.stdout
    assert "publish" in result.stdout
    assert "search" in result.stdout
    assert "favorites" in result.stdout
    assert "info" in result.stdout
    assert "validate" in result.stdout
    assert "import-config" in result.stdout
    assert "sync" in result.stdout
    assert "status" in result.stdout
    assert "logout" in result.stdout
    assert "whoami" in result.stdout
    assert "context" in result.stdout
    assert "list" not in result.stdout
    assert "│ auth " not in result.stdout
    assert "│ integrations " not in result.stdout


def test_login_help_is_customer_friendly(runner):
    """Test login does not expose OIDC internals as required options."""
    result = runner.invoke(app, ["login", "--help"])

    assert result.exit_code == 0
    assert "Authenticate with LightNow" in result.stdout
    assert "--issuer" not in result.stdout
    assert "--client-id" not in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        "login",
        "logout",
        "status",
        "whoami",
        "context",
        "publish",
        "search",
        "favorites",
        "info",
        "validate",
        "import-config",
        "sync",
        "config-status",
        "run",
    ],
)
def test_each_public_command_has_help(command, runner):
    """Every public command has an executable help surface."""
    result = runner.invoke(app, [command, "--help"])

    assert result.exit_code == 0
    assert command in result.stdout


@patch("lightnow_cli.commands.auth.webbrowser.open")
def test_login_opens_device_authorization_url(mock_open, runner):
    """Device login opens the verification URL in the system browser."""
    from lightnow_cli.commands.auth import open_authorization_url

    mock_open.return_value = True

    assert open_authorization_url("https://auth.lightnow.local/device") is True
    mock_open.assert_called_once_with(
        "https://auth.lightnow.local/device", new=2, autoraise=True
    )


def test_run_command_starts_profile_server(runner):
    """The local runner fetches context and starts the resolved child command."""
    with (
        patch(
            "lightnow_cli.commands.runner.require_access_token",
            return_value="token",
        ),
        patch(
            "lightnow_cli.commands.runner.resolve_profile_server",
            return_value={
                "alias": "sonarqube",
                "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                "version": "1.2.3",
                "status": "linked",
            },
        ) as resolve,
        patch(
            "lightnow_cli.commands.runner.fetch_runtime_context",
            return_value={"probe_request": {"transport": "stdio", "stdio": {}}},
        ) as fetch_context,
        patch(
            "lightnow_cli.commands.runner.launch_config_from_context",
            return_value=(
                "python",
                ["-m", "server"],
                {"SONARQUBE_TOKEN": "secret"},
                None,
            ),
        ),
        patch(
            "lightnow_cli.commands.runner.subprocess.run",
            return_value=MagicMock(returncode=0),
        ) as run_process,
    ):
        result = runner.invoke(
            app,
            [
                "run",
                "--server",
                "sonarqube",
                "--profile",
                "default",
                "--api-url",
                "https://registry-api.lightnow.local/v0.1",
            ],
        )

    assert result.exit_code == 0
    resolve.assert_called_once()
    fetch_context.assert_called_once()
    assert run_process.call_args.args[0] == ["python", "-m", "server"]
    assert run_process.call_args.kwargs["env"]["SONARQUBE_TOKEN"] == "secret"


def test_run_command_passes_tenant_to_profile_and_context_requests(runner):
    """The local runner keeps organization context for lookup and secret context."""
    with (
        patch(
            "lightnow_cli.commands.runner.require_access_token",
            return_value="token",
        ),
        patch(
            "lightnow_cli.commands.runner.resolve_profile_server",
            return_value={
                "alias": "sonarqube",
                "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                "version": "1.2.3",
                "status": "linked",
            },
        ) as resolve,
        patch(
            "lightnow_cli.commands.runner.fetch_runtime_context",
            return_value={"probe_request": {"transport": "stdio", "stdio": {}}},
        ) as fetch_context,
        patch(
            "lightnow_cli.commands.runner.launch_config_from_context",
            return_value=("python", ["-m", "server"], {}, None),
        ),
        patch(
            "lightnow_cli.commands.runner.subprocess.run",
            return_value=MagicMock(returncode=0),
        ),
    ):
        result = runner.invoke(
            app,
            [
                "run",
                "--server",
                "sonarqube",
                "--profile",
                "default",
                "--tenant",
                "acme",
                "--api-url",
                "https://registry-api.lightnow.local/v0.1",
            ],
        )

    assert result.exit_code == 0
    assert resolve.call_args.kwargs["tenant"] == "acme"
    assert fetch_context.call_args.kwargs["tenant"] == "acme"


def test_run_command_uses_stored_context(runner):
    """The local runner uses the stored organization context by default."""
    with (
        patch(
            "lightnow_cli.commands.runner.require_access_token",
            return_value="token",
        ),
        patch(
            "lightnow_cli.commands.runner.config_manager.effective_tenant",
            return_value="tenant-uuid",
        ),
        patch(
            "lightnow_cli.commands.runner.resolve_profile_server",
            return_value={
                "alias": "sonarqube",
                "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                "version": "1.2.3",
                "status": "linked",
            },
        ) as resolve,
        patch(
            "lightnow_cli.commands.runner.fetch_runtime_context",
            return_value={"probe_request": {"transport": "stdio", "stdio": {}}},
        ) as fetch_context,
        patch(
            "lightnow_cli.commands.runner.launch_config_from_context",
            return_value=("python", ["-m", "server"], {}, None),
        ),
        patch(
            "lightnow_cli.commands.runner.subprocess.run",
            return_value=MagicMock(returncode=0),
        ),
    ):
        result = runner.invoke(
            app,
            [
                "run",
                "--server",
                "sonarqube",
                "--profile",
                "default",
                "--api-url",
                "https://registry-api.lightnow.local/v0.1",
            ],
        )

    assert result.exit_code == 0
    assert resolve.call_args.kwargs["tenant"] == "tenant-uuid"
    assert fetch_context.call_args.kwargs["tenant"] == "tenant-uuid"


def test_search_command_passes_tenant_context(runner):
    """Search supports organization context from the public CLI surface."""
    fake_client = MagicMock()
    fake_client.list_servers = AsyncMock(return_value={"servers": []})

    with patch("lightnow_cli.commands.query.get_client", return_value=fake_client):
        result = runner.invoke(
            app, ["search", "redis", "--tenant", "acme", "--limit", "5"]
        )

    assert result.exit_code == 0
    assert fake_client.list_servers.call_args.kwargs["tenant"] == "acme"


def test_search_command_uses_stored_context(runner):
    """Search uses the stored context when --tenant is omitted."""
    fake_client = MagicMock()
    fake_client.list_servers = AsyncMock(return_value={"servers": []})

    with (
        patch("lightnow_cli.commands.query.get_client", return_value=fake_client),
        patch(
            "lightnow_cli.commands.query.config_manager.effective_tenant",
            return_value="tenant-uuid",
        ) as effective_tenant,
    ):
        result = runner.invoke(app, ["search", "redis"])

    assert result.exit_code == 0
    effective_tenant.assert_called_once_with(None)
    assert fake_client.list_servers.call_args.kwargs["tenant"] == "tenant-uuid"


def test_favorites_command_passes_tenant_context(runner):
    """Favorites supports organization-scoped favorites from the CLI."""
    fake_client = MagicMock()
    fake_client.list_servers = AsyncMock(return_value={"servers": []})

    with patch("lightnow_cli.commands.query.get_client", return_value=fake_client):
        result = runner.invoke(
            app, ["favorites", "--scope", "tenant", "--tenant", "acme"]
        )

    assert result.exit_code == 0
    assert fake_client.list_servers.call_args.kwargs["favorites"] == "tenant"
    assert fake_client.list_servers.call_args.kwargs["tenant"] == "acme"


def test_info_command_passes_tenant_context(runner):
    """Server info supports organization context from the public CLI surface."""
    fake_client = MagicMock()
    fake_client.get_server_info = AsyncMock(
        return_value={
            "name": "io.github.test/server",
            "version": "1.0.0",
        }
    )

    with patch("lightnow_cli.commands.query.get_client", return_value=fake_client):
        result = runner.invoke(
            app, ["info", "io.github.test/server", "--tenant", "acme"]
        )

    assert result.exit_code == 0
    assert fake_client.get_server_info.call_args.kwargs["tenant"] == "acme"


def test_run_command_reports_expired_token(runner):
    """Runner commands use the shared expired-token message."""
    from lightnow_cli.commands.auth import (
        ACCESS_TOKEN_EXPIRED_MESSAGE,
        AccessTokenExpired,
    )

    with patch(
        "lightnow_cli.commands.runner.require_access_token",
        side_effect=AccessTokenExpired(ACCESS_TOKEN_EXPIRED_MESSAGE),
    ):
        result = runner.invoke(app, ["run", "--server", "sonarqube"])

    assert result.exit_code == 1
    assert ACCESS_TOKEN_EXPIRED_MESSAGE in result.stderr


def test_run_command_reports_missing_child_command(runner):
    """A missing executable fails explicitly without retrying another command."""
    with (
        patch(
            "lightnow_cli.commands.runner.require_access_token",
            return_value="token",
        ),
        patch(
            "lightnow_cli.commands.runner.resolve_profile_server",
            return_value={
                "alias": "sonarqube",
                "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                "version": "1.2.3",
                "status": "linked",
            },
        ),
        patch(
            "lightnow_cli.commands.runner.fetch_runtime_context",
            return_value={"probe_request": {"transport": "stdio", "stdio": {}}},
        ),
        patch(
            "lightnow_cli.commands.runner.launch_config_from_context",
            return_value=("missing-command", [], {}, None),
        ),
        patch(
            "lightnow_cli.commands.runner.subprocess.run",
            side_effect=FileNotFoundError(),
        ),
    ):
        result = runner.invoke(
            app,
            [
                "run",
                "--server",
                "sonarqube",
                "--api-url",
                "https://registry-api.lightnow.local/v0.1",
            ],
        )

    assert result.exit_code == 127
    assert "command not found: missing-command" in result.stderr


def test_validate_command_valid_files(runner, temp_dir, valid_server_json):
    """Test validate command with valid files."""
    # Create valid files
    server_file = temp_dir / "server.json"
    docs_file = temp_dir / "docs.md"

    with open(server_file, "w") as f:
        json.dump(valid_server_json, f)

    with open(docs_file, "w") as f:
        f.write("# Test Server\n\nThis is documentation.\n\nMore content here.")

    # Run validate command
    result = runner.invoke(
        app, ["validate", "--server", str(server_file), "--docs", str(docs_file)]
    )

    assert result.exit_code == 0
    assert "All artifacts are valid!" in result.stdout


def test_validate_command_invalid_file(runner, temp_dir):
    """Test validate command with invalid file."""
    # Create invalid server.json
    server_file = temp_dir / "server.json"
    with open(server_file, "w") as f:
        json.dump({"name": "test"}, f)  # Missing required fields

    result = runner.invoke(app, ["validate", "--server", str(server_file)])

    assert result.exit_code == 1
    assert "Validation failed" in result.stdout


def test_validate_command_no_files(runner):
    """Test validate command with no files specified."""
    result = runner.invoke(app, ["validate"])

    assert result.exit_code == 1
    assert "At least one file must be specified" in result.stdout


@patch("lightnow_cli.commands.auth.device_code_flow")
@patch("lightnow_cli.commands.auth.fetch_user_info")
@patch("lightnow_cli.config.config_manager.set_auth_config")
@patch("lightnow_cli.config.config_manager.set_token")
def test_login_command_success(
    mock_set_token, mock_set_auth_config, mock_fetch_user_info, mock_device_flow, runner
):
    """Test successful login command."""
    # Mock successful device code flow
    mock_device_flow.return_value = {
        "access_token": "mock-access-token",
        "refresh_token": "mock-refresh-token",
    }
    mock_fetch_user_info.return_value = {
        "sub": "123456",
        "email": "test@example.com",
        "preferred_username": "test",
    }

    result = runner.invoke(app, ["login"])

    assert result.exit_code == 0
    assert "Authentication successful" in result.stdout

    # Verify mocks were called
    mock_set_auth_config.assert_called_once_with(
        DEFAULT_ISSUER, DEFAULT_CLIENT_ID, None, None
    )
    mock_device_flow.assert_called_once_with(DEFAULT_ISSUER, DEFAULT_CLIENT_ID)
    mock_fetch_user_info.assert_called_once_with(DEFAULT_ISSUER, "mock-access-token")
    mock_set_token.assert_called_once()


@patch("lightnow_cli.commands.auth.device_code_flow")
@patch("lightnow_cli.commands.auth.fetch_user_info")
@patch("lightnow_cli.config.config_manager.set_auth_config")
@patch("lightnow_cli.config.config_manager.set_token")
def test_login_command_local_profile(
    mock_set_token, mock_set_auth_config, mock_fetch_user_info, mock_device_flow, runner
):
    """Local login uses local LightNow endpoints without exposing OIDC internals."""
    mock_device_flow.return_value = {
        "access_token": "mock-access-token",
        "refresh_token": "mock-refresh-token",
    }
    mock_fetch_user_info.return_value = {"email": "test@lightnow.local"}

    result = runner.invoke(app, ["login", "--local"])

    assert result.exit_code == 0
    assert "Starting local LightNow authentication" in result.stdout
    mock_set_auth_config.assert_called_once_with(
        LOCAL_ISSUER,
        DEFAULT_CLIENT_ID,
        LOCAL_REGISTRY_API_URL,
        LOCAL_ADMIN_API_URL,
    )
    mock_device_flow.assert_called_once_with(LOCAL_ISSUER, DEFAULT_CLIENT_ID)
    mock_fetch_user_info.assert_called_once_with(LOCAL_ISSUER, "mock-access-token")
    mock_set_token.assert_called_once()


@patch("lightnow_cli.config.config_manager.load_config")
def test_status_command_not_authenticated(mock_load_config, runner):
    """Test status command when not authenticated."""
    mock_config = MagicMock()
    mock_config.access_token = None
    mock_load_config.return_value = mock_config

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 1
    assert "Not authenticated" in result.stdout


@patch("lightnow_cli.config.config_manager.load_config")
@patch("lightnow_cli.commands.auth.fetch_user_info")
def test_status_command_authenticated(mock_fetch_user_info, mock_load_config, runner):
    """Test status command when authenticated."""
    mock_config = MagicMock()
    mock_config.access_token = (
        "eyJhbGciOiJub25lIn0." "eyJzdWIiOiIxMjMiLCJleHAiOjk5OTk5OTk5OTl9."
    )
    mock_config.refresh_token = None
    mock_config.issuer = DEFAULT_ISSUER
    mock_fetch_user_info.return_value = {
        "name": "Test User",
        "email": "test@example.com",
        "sub": "123456",
        "exp": 9999999999,  # Far in future
    }
    mock_load_config.return_value = mock_config

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Test User" in result.stdout
    assert "test@example.com" in result.stdout


@patch("lightnow_cli.config.config_manager.load_config")
@patch("lightnow_cli.commands.auth.fetch_user_info")
def test_whoami_command_authenticated(mock_fetch_user_info, mock_load_config, runner):
    """Whoami shows detailed identity information."""
    mock_config = MagicMock()
    mock_config.access_token = (
        "eyJhbGciOiJub25lIn0." "eyJzdWIiOiIxMjMiLCJleHAiOjk5OTk5OTk5OTl9."
    )
    mock_config.refresh_token = None
    mock_config.issuer = DEFAULT_ISSUER
    mock_fetch_user_info.return_value = {
        "name": "Test User",
        "email": "test@example.com",
        "preferred_username": "test",
        "sub": "123456",
        "exp": 9999999999,
    }
    mock_load_config.return_value = mock_config

    result = runner.invoke(app, ["whoami"])

    assert result.exit_code == 0
    assert "Current User Information" in result.stdout
    assert "Test User" in result.stdout
    assert "test@example.com" in result.stdout
    assert "test" in result.stdout
    assert "123456" in result.stdout
    assert "Scopes:" not in result.stdout
    assert "openid" not in result.stdout


@patch("lightnow_cli.config.config_manager.load_config")
@patch("lightnow_cli.commands.auth.fetch_user_info")
def test_whoami_command_json(mock_fetch_user_info, mock_load_config, runner):
    """Whoami can emit machine-readable identity details."""
    mock_config = MagicMock()
    mock_config.access_token = (
        "eyJhbGciOiJub25lIn0." "eyJzdWIiOiIxMjMiLCJleHAiOjk5OTk5OTk5OTl9."
    )
    mock_config.refresh_token = None
    mock_config.issuer = DEFAULT_ISSUER
    mock_fetch_user_info.return_value = {
        "email": "test@example.com",
        "name": "Test User",
        "preferred_username": "test",
        "sub": "123456",
    }
    mock_load_config.return_value = mock_config

    result = runner.invoke(app, ["whoami", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "email": "test@example.com",
        "name": "Test User",
        "preferred_username": "test",
        "sub": "123456",
    }
    assert "Current User Information" not in result.stdout


@patch("lightnow_cli.commands.auth.config_manager.clear_token")
def test_logout_command(mock_clear_token, runner):
    """Test logout clears the stored token."""
    result = runner.invoke(app, ["logout"])

    assert result.exit_code == 0
    assert "Logged out successfully" in result.stdout
    mock_clear_token.assert_called_once()


@patch("lightnow_cli.commands.auth.config_manager.load_config")
@patch("lightnow_cli.commands.auth.fetch_user_info")
def test_status_fetches_userinfo_when_cache_has_no_user_info(
    mock_fetch_user_info, mock_load_config, runner
):
    """Status fetches identity from OIDC userinfo instead of cached claims."""
    mock_config = MagicMock()
    mock_config.access_token = (
        "eyJhbGciOiJub25lIn0."
        "eyJzdWIiOiIxMjMiLCJlbWFpbCI6InRlc3RAbGlnaHRub3cubG9jYWwiLCJuYW1lIjoiVGVzdCBVc2VyIiwiZXhwIjo5OTk5OTk5OTk5fQ."
    )
    mock_config.refresh_token = None
    mock_config.issuer = DEFAULT_ISSUER
    mock_config.user_info = None
    mock_load_config.return_value = mock_config
    mock_fetch_user_info.return_value = {
        "name": "Test User",
        "email": "test@lightnow.local",
    }

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Test User" in result.stdout
    mock_fetch_user_info.assert_called_once()


@patch("lightnow_cli.config.config_manager.load_config")
def test_status_command_expired_token(mock_load_config, runner):
    """Status reports expired tokens clearly."""
    mock_config = MagicMock()
    mock_config.access_token = "eyJhbGciOiJub25lIn0." "eyJzdWIiOiIxMjMiLCJleHAiOjF9."
    mock_config.refresh_token = None
    mock_load_config.return_value = mock_config

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 1
    assert "expired" in result.stdout


@patch("lightnow_cli.commands.auth.device_code_flow")
@patch("lightnow_cli.commands.auth.config_manager.set_auth_config")
def test_login_command_missing_access_token(
    mock_set_auth_config, mock_device_flow, runner
):
    """Login fails clearly when the token response is malformed."""
    mock_device_flow.return_value = {"refresh_token": "refresh-token"}

    result = runner.invoke(app, ["login"])

    assert result.exit_code == 1
    assert "OIDC token response did not include an access token" in result.stdout
    mock_set_auth_config.assert_called_once()


@patch("lightnow_cli.client.MCPRegistryClient.publish_server")
def test_publish_command_success(mock_publish, runner, temp_dir, valid_server_json):
    """Test successful publish command."""
    mock_publish.return_value = {
        "server_id": "io.lightnow/test-server",
        "version": "1.0.0",
        "url": "https://registry.example.com/servers/io.lightnow/test-server",
    }

    # Create valid server file
    server_file = temp_dir / "server.json"
    with open(server_file, "w") as f:
        json.dump(valid_server_json, f)

    result = runner.invoke(app, ["publish", "--server", str(server_file)])

    assert result.exit_code == 0
    assert "Published successfully" in result.stdout
    assert "io.lightnow/test-server" in result.stdout


@patch("lightnow_cli.client.MCPRegistryClient.publish_server")
def test_publish_command_validation_only(
    mock_publish, runner, temp_dir, valid_server_json
):
    """Test publish command with --validate-only flag."""
    # Create valid server file
    server_file = temp_dir / "server.json"
    with open(server_file, "w") as f:
        json.dump(valid_server_json, f)

    result = runner.invoke(
        app, ["publish", "--server", str(server_file), "--validate-only"]
    )

    assert result.exit_code == 0
    assert "Validation complete. Skipping publish" in result.stdout

    # Verify publish was not called
    mock_publish.assert_not_called()


@patch("lightnow_cli.commands.publish.validate_artifacts")
@patch("lightnow_cli.client.MCPRegistryClient.publish_server")
def test_publish_command_shows_warnings(mock_publish, mock_validate, runner, temp_dir):
    """Publish displays validation warnings before publishing."""
    server_file = temp_dir / "server.json"
    server_file.write_text("{}")
    mock_validate.return_value = {
        "valid": True,
        "warnings": ["optional docs missing"],
        "validated": {"server": {"name": "server"}},
    }
    mock_publish.return_value = {"server_id": "server"}

    result = runner.invoke(app, ["publish", "--server", str(server_file)])

    assert result.exit_code == 0
    assert "optional docs missing" in result.stdout
    assert "Published successfully" in result.stdout


@patch("lightnow_cli.commands.publish.validate_artifacts")
def test_publish_command_validation_failure(mock_validate, runner, temp_dir):
    """Publish stops on validation errors."""
    server_file = temp_dir / "server.json"
    server_file.write_text("{}")
    mock_validate.return_value = {
        "valid": False,
        "errors": ["name is required"],
        "warnings": [],
        "validated": {},
    }

    result = runner.invoke(app, ["publish", "--server", str(server_file)])

    assert result.exit_code == 1
    assert "name is required" in result.stdout


@patch("lightnow_cli.client.MCPRegistryClient.list_servers")
def test_search_command_success(mock_list_servers, runner):
    """Search renders current Registry API server list responses."""
    mock_list_servers.return_value = {
        "servers": [
            {
                "server": {
                    "name": "server-1",
                    "version": "1.0.0",
                    "title": "Server One",
                    "description": "First server",
                }
            },
            {
                "server": {
                    "name": "server-2",
                    "version": "2.0.0",
                    "title": "Server Two",
                    "description": "Second server",
                }
            },
        ],
        "metadata": {"count": 2, "nextCursor": "opaque-next-cursor"},
    }

    result = runner.invoke(app, ["search", "server"])

    assert result.exit_code == 0
    assert "server-1" in result.stdout
    assert "server-2" in result.stdout
    assert "Showing 2 server(s)" in result.stdout
    assert "More results are available" in result.stdout
    assert "opaque-next-cursor" not in result.stdout
    mock_list_servers.assert_called_once()
    assert mock_list_servers.call_args.kwargs["search"] == "server"


@patch("lightnow_cli.client.MCPRegistryClient.list_servers")
def test_search_command_can_show_raw_cursor(mock_list_servers, runner):
    """Raw cursors are opt-in for scripted pagination."""
    mock_list_servers.return_value = {
        "servers": [
            {
                "server": {
                    "name": "server-1",
                    "version": "1.0.0",
                    "title": "Server One",
                }
            }
        ],
        "metadata": {"nextCursor": "opaque-next-cursor"},
    }

    result = runner.invoke(app, ["search", "server", "--show-cursor"])

    assert result.exit_code == 0
    assert "Next cursor: opaque-next-cursor" in result.stdout


@patch("lightnow_cli.client.MCPRegistryClient.list_servers")
def test_favorites_command_empty(mock_list_servers, runner):
    """Favorites uses the Registry favorites filter."""
    mock_list_servers.return_value = {"servers": [], "metadata": {"count": 0}}

    result = runner.invoke(app, ["favorites"])

    assert result.exit_code == 0
    assert "No favorite servers found" in result.stdout
    assert mock_list_servers.call_args.kwargs["favorites"] == "user"


@patch("lightnow_cli.client.MCPRegistryClient.list_servers")
def test_favorites_command_allows_effective_scope(mock_list_servers, runner):
    """Effective favorites remain available as an explicit combined view."""
    mock_list_servers.return_value = {"servers": [], "metadata": {"count": 0}}

    result = runner.invoke(app, ["favorites", "--scope", "effective"])

    assert result.exit_code == 0
    assert mock_list_servers.call_args.kwargs["favorites"] == "effective"


@patch("lightnow_cli.client.MCPRegistryClient.get_server_info")
def test_info_command_success(mock_get_server_info, runner):
    """Test successful info command."""
    mock_get_server_info.return_value = {
        "server": {
            "name": "test-server",
            "version": "1.0.0",
            "title": "Test Server",
            "description": "Test server description",
            "repository": {"url": "https://github.com/example/test-server"},
            "packages": [
                {
                    "registryType": "oci",
                    "identifier": "docker.io/example/test-server",
                    "environmentVariables": [
                        {
                            "name": "TEST_TOKEN",
                            "isRequired": True,
                            "isSecret": True,
                            "description": "Test access token",
                        }
                    ],
                }
            ],
        }
    }

    result = runner.invoke(app, ["info", "test-server"])

    assert result.exit_code == 0
    assert "test-server" in result.stdout
    assert "1.0.0" in result.stdout
    assert "github.com/example/test-server" in result.stdout
    assert "TEST_TOKEN" in result.stdout
    assert "required, secret" in result.stdout


@patch("lightnow_cli.client.MCPRegistryClient.get_server_info")
def test_info_command_not_found(mock_get_server_info, runner):
    """Test info command for non-existent server."""
    mock_get_server_info.side_effect = ValueError("Server 'nonexistent' not found")

    result = runner.invoke(app, ["info", "nonexistent"])

    assert result.exit_code == 1
    assert "Query failed" in result.stdout
    assert "not found" in result.stdout


@patch("lightnow_cli.commands.validate.validate_artifacts")
def test_validate_command_shows_warnings(mock_validate, runner, temp_dir):
    """Validate reports non-fatal warnings and validated files."""
    server_file = temp_dir / "server.json"
    server_file.write_text("{}")
    mock_validate.return_value = {
        "valid": True,
        "warnings": ["docs missing"],
        "validated": {"server": {"name": "server"}},
    }

    result = runner.invoke(app, ["validate", "--server", str(server_file)])

    assert result.exit_code == 0
    assert "docs missing" in result.stdout
    assert "server.json" in result.stdout


@patch("lightnow_cli.commands.validate.validate_artifacts")
def test_validate_command_reports_failure(mock_validate, runner, temp_dir):
    """Validate reports validation errors."""
    server_file = temp_dir / "server.json"
    server_file.write_text("{}")
    mock_validate.return_value = {
        "valid": False,
        "errors": ["invalid server"],
        "warnings": ["ignored"],
        "validated": {},
    }

    result = runner.invoke(app, ["validate", "--server", str(server_file)])

    assert result.exit_code == 1
    assert "invalid server" in result.stdout
    assert "ignored" in result.stdout
