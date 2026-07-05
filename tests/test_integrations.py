"""Tests for integration profile sync commands."""

import json
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import patch

import httpx
import yaml
from typer.testing import CliRunner

from lightnow_cli.commands.auth import (
    ACCESS_TOKEN_EXPIRED_MESSAGE,
    AccessTokenExpired,
    AuthError,
)
from lightnow_cli.commands.integrations import (
    BEGIN,
    END,
    JSON_MANIFEST_SUFFIX,
    build_local_proxy_config,
    build_local_proxy_export,
    build_runner_export,
    extract_json_managed,
    fetch_export,
    import_profile_config,
    patch_config,
    prepare_codex_local_proxy_config,
    prepare_json_local_proxy_config,
    print_import_summary,
    redact,
    render_local_proxy_codex_toml,
    render_runner_config,
    secure_write_text,
)
from lightnow_cli.commands.runner import (
    assert_runtime_context_ready,
    fetch_profile_servers,
    fetch_runtime_context,
    json_response_or_error,
    launch_config_from_context,
    resolve_profile_server,
    runner_failure_summary,
)
from lightnow_cli.main import app


def test_patches_toml_without_removing_user_config() -> None:
    """TOML sync keeps existing user config and adds a managed block."""
    existing = 'model = "gpt-5"\n'
    generated = '[mcp_servers.github]\ncommand = "docker"\n'

    result = patch_config(existing, generated, "toml")

    assert 'model = "gpt-5"' in result
    assert BEGIN in result
    assert "[mcp_servers.github]" in result
    assert END in result


def test_replaces_existing_managed_block_idempotently() -> None:
    """A second TOML sync replaces only the managed block."""
    existing = f"keep = true\n\n{BEGIN}\nold\n{END}\n"

    result = patch_config(existing, "new", "toml")

    assert "keep = true" in result
    assert "new" in result
    assert "old" not in result


def test_sync_uses_api_managed_block_without_double_wrapping() -> None:
    """API exports may already include the LightNow managed markers."""
    generated = f'{BEGIN}\n[mcp_servers.github]\ncommand = "docker"\n{END}\n'

    result = patch_config("", generated, "toml")

    assert result.count(BEGIN) == 1
    assert result.count(END) == 1
    assert "[mcp_servers.github]" in result


def test_replaces_previous_lightnow_json_aliases_from_manifest() -> None:
    """JSON sync removes aliases that the manifest marks as LightNow-owned."""
    existing = (
        '{"mcpServers": {"local": {"command": "uvx"}, "old": {"command": "docker"}}}'
    )
    generated = '{"mcpServers": {"github": {"command": "docker"}}}'

    result = patch_config(existing, generated, "json", ["old"])

    assert '"local"' in result
    assert '"github"' in result
    assert '"old"' not in result


def test_redacts_secret_like_lines() -> None:
    """Dry-run output must not expose plaintext secret-like values."""
    result = redact('TOKEN = "top-secret"\nSAFE = "value"\n')

    assert 'TOKEN = "[REDACTED]"' in result
    assert 'SAFE = "value"' in result
    assert "top-secret" not in result


def test_secure_write_text_restricts_target_and_backup_permissions() -> None:
    """Client config and backup files are written without group/world access."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        target.write_text("old")
        target.chmod(0o644)

        secure_write_text(target, "new")

        backup = target.with_suffix(target.suffix + ".lightnow.bak")
        assert target.read_text() == "new"
        assert backup.read_text() == "old"
        assert target.stat().st_mode & 0o077 == 0
        assert backup.stat().st_mode & 0o077 == 0


def test_sync_dry_run_does_not_write_target() -> None:
    """Dry-run prints the redacted patched config and does not create files."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        with patch(
            "lightnow_cli.commands.integrations.require_access_token",
            return_value="token",
        ):
            with patch(
                "lightnow_cli.commands.integrations.fetch_export",
                return_value='[mcp_servers.github]\ncommand = "docker"\n',
            ):
                result = runner.invoke(
                    app,
                    [
                        "sync",
                        "--client",
                        "codex",
                        "--config-path",
                        str(target),
                        "--dry-run",
                    ],
                )

        assert result.exit_code == 0
        assert "[mcp_servers.github]" in result.stdout
        assert not target.exists()


def test_sync_defaults_to_plaintext_export_mode() -> None:
    """Client sync defaults to ready-to-use plaintext exports."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        with patch(
            "lightnow_cli.commands.integrations.require_access_token",
            return_value="token",
        ):
            with patch(
                "lightnow_cli.commands.integrations.fetch_export",
                return_value='[mcp_servers.github]\ncommand = "docker"\n',
            ) as fetch:
                result = runner.invoke(
                    app,
                    [
                        "sync",
                        "--client",
                        "codex",
                        "--config-path",
                        str(target),
                    ],
                    input="y\n",
                )

        assert result.exit_code == 0
        assert target.exists()
        assert fetch.call_args.kwargs["secret_mode"] == "plaintext"


def test_sync_passes_tenant_to_export_request() -> None:
    """Client export sync uses the selected organization context."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        with (
            patch(
                "lightnow_cli.commands.integrations.require_access_token",
                return_value="token",
            ),
            patch(
                "lightnow_cli.commands.integrations.fetch_export",
                return_value='[mcp_servers.github]\ncommand = "docker"\n',
            ) as fetch,
        ):
            result = runner.invoke(
                app,
                [
                    "sync",
                    "--client",
                    "codex",
                    "--tenant",
                    "acme",
                    "--config-path",
                    str(target),
                ],
                input="y\n",
            )

    assert result.exit_code == 0
    assert fetch.call_args.kwargs["tenant"] == "acme"


def test_sync_uses_stored_context_when_tenant_is_omitted() -> None:
    """Client export sync uses the stored organization context by default."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        with (
            patch(
                "lightnow_cli.commands.integrations.require_access_token",
                return_value="token",
            ),
            patch(
                "lightnow_cli.commands.integrations.config_manager.effective_tenant",
                return_value="tenant-uuid",
            ),
            patch(
                "lightnow_cli.commands.integrations.fetch_export",
                return_value='[mcp_servers.github]\ncommand = "docker"\n',
            ) as fetch,
        ):
            result = runner.invoke(
                app,
                [
                    "sync",
                    "--client",
                    "codex",
                    "--config-path",
                    str(target),
                ],
                input="y\n",
            )

    assert result.exit_code == 0
    assert fetch.call_args.kwargs["tenant"] == "tenant-uuid"


def test_sync_json_writes_manifest_and_preserves_user_config() -> None:
    """JSON sync stores the LightNow-owned aliases in a sibling manifest."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "mcp.json"
        target.write_text(json.dumps({"mcpServers": {"local": {"command": "uvx"}}}))

        with patch(
            "lightnow_cli.commands.integrations.require_access_token",
            return_value="token",
        ):
            with patch(
                "lightnow_cli.commands.integrations.fetch_export",
                return_value=json.dumps(
                    {"mcpServers": {"github": {"command": "docker"}}}
                ),
            ):
                result = runner.invoke(
                    app,
                    [
                        "sync",
                        "--client",
                        "cursor",
                        "--format",
                        "json",
                        "--secret-mode",
                        "placeholder",
                        "--config-path",
                        str(target),
                    ],
                )

        manifest = target.with_name(target.name + JSON_MANIFEST_SUFFIX)
        assert result.exit_code == 0
        assert '"local"' in target.read_text()
        assert '"github"' in target.read_text()
        assert json.loads(manifest.read_text()) == {
            "aliases": ["github"],
            "input_ids": [],
        }
        assert target.with_suffix(".json.lightnow.bak").exists()


def test_sync_json_preserves_user_inputs_and_replaces_lightnow_inputs() -> None:
    """VS Code sync patches inputs without deleting user-owned prompts."""
    existing = json.dumps(
        {
            "inputs": [
                {"id": "user-token", "type": "promptString"},
                {"id": "lightnow-old-token", "type": "promptString"},
            ],
            "servers": {"local": {"type": "stdio", "command": "uvx"}},
        }
    )
    generated = json.dumps(
        {
            "inputs": [
                {
                    "id": "lightnow-sonarqube-token",
                    "type": "promptString",
                    "password": True,
                }
            ],
            "servers": {
                "sonarqube": {
                    "type": "stdio",
                    "command": "docker",
                    "env": {"SONARQUBE_TOKEN": "${input:lightnow-sonarqube-token}"},
                }
            },
        }
    )

    result = patch_config(
        existing,
        generated,
        "json",
        previous_aliases=["old-sonarqube"],
        previous_input_ids=["lightnow-old-token"],
    )
    payload = json.loads(result)

    assert payload["servers"]["local"]["command"] == "uvx"
    assert payload["servers"]["sonarqube"]["env"]["SONARQUBE_TOKEN"] == (
        "${input:lightnow-sonarqube-token}"
    )
    assert [item["id"] for item in payload["inputs"]] == [
        "user-token",
        "lightnow-sonarqube-token",
    ]


def test_runner_export_for_codex_uses_lightnow_run_without_secret_values() -> None:
    """Runner sync writes LightNow wrapper commands and no client-side secrets."""
    generated = build_runner_export(
        profile_payload={
            "servers": [
                {
                    "alias": "sonarqube",
                    "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                    "version": "1.2.3",
                    "status": "linked",
                    "client_config": {
                        "env": {"SONARQUBE_TOKEN": "top-secret"},
                    },
                }
            ]
        },
        client="codex",
        export_format="toml",
        profile="default",
        tenant=None,
    )

    assert "[mcp_servers.sonarqube]" in generated
    assert 'command = "lightnow"' in generated
    assert (
        'args = ["run", "--profile", "default", "--server", "sonarqube"]' in generated
    )
    assert "SONARQUBE_TOKEN" not in generated
    assert "top-secret" not in generated


def test_local_proxy_export_for_codex_writes_one_stdio_server() -> None:
    """Local Proxy Mode writes one Codex entry that auto-starts the proxy."""
    generated = build_local_proxy_export(
        client="codex",
        export_format="toml",
        local_proxy_url="http://127.0.0.1:8080/mcp",
        local_proxy_config_path=Path("/tmp/lightnow/mcp-proxy.yaml"),
    )
    payload = tomllib.loads(generated)

    assert payload == {
        "mcp_servers": {
            "lightnow": {
                "command": "mcp-proxy",
                "args": [
                    "--config",
                    "/tmp/lightnow/mcp-proxy.yaml",
                    "--transport",
                    "stdio",
                ],
                "default_tools_approval_mode": "approve",
            }
        }
    }
    assert "url" not in generated


def test_local_proxy_export_for_codex_can_write_http_server() -> None:
    """HTTP Local Proxy Mode remains available for daemon-style clients."""
    generated = build_local_proxy_export(
        client="codex",
        export_format="toml",
        local_proxy_url="http://127.0.0.1:8080/mcp",
        local_proxy_transport="http",
    )
    payload = tomllib.loads(generated)

    assert payload == {
        "mcp_servers": {
            "lightnow": {
                "url": "http://127.0.0.1:8080/mcp",
                "default_tools_approval_mode": "approve",
            }
        }
    }
    assert "command" not in generated


def test_local_proxy_export_for_claude_desktop_writes_one_stdio_server() -> None:
    """Local Proxy Mode writes one Claude Desktop entry that auto-starts the proxy."""
    generated = build_local_proxy_export(
        client="claude-desktop",
        export_format="json",
        local_proxy_url="http://127.0.0.1:8080/mcp",
        local_proxy_config_path=Path("/tmp/lightnow/mcp-proxy.yaml"),
    )
    payload = json.loads(generated)

    assert list(payload["mcpServers"]) == ["LightNow"]
    assert payload["mcpServers"]["LightNow"]["command"].endswith("mcp-proxy")
    assert payload["mcpServers"]["LightNow"]["args"] == [
        "--config",
        "/tmp/lightnow/mcp-proxy.yaml",
        "--transport",
        "stdio",
    ]


def test_local_proxy_export_rejects_non_local_urls() -> None:
    """Local Proxy Mode must not silently configure a hosted endpoint."""
    for url in [
        "https://proxy.lightnow.ai/mcp",
        "http://proxy.lightnow.ai/mcp",
        "https://localhost:8080/mcp",
        "http://localhost/mcp",
        "http://localhost:8080.evil.test/mcp",
    ]:
        try:
            build_local_proxy_export(
                client="codex",
                export_format="toml",
                local_proxy_url=url,
                local_proxy_transport="http",
            )
        except ValueError as exc:
            assert "localhost" in str(exc)
        else:
            raise AssertionError(f"expected ValueError for {url}")


def test_local_proxy_export_rejects_unsupported_clients() -> None:
    """Local Proxy Mode rejects clients without an explicit config writer."""
    try:
        build_local_proxy_export(
            client="cursor",
            export_format="json",
            local_proxy_url="http://127.0.0.1:8080/mcp",
        )
    except ValueError as exc:
        assert "Codex TOML and Claude Desktop JSON" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_render_local_proxy_codex_toml_uses_approval_mode() -> None:
    """Codex non-interactive tool calls require explicit approval config."""
    generated = render_local_proxy_codex_toml("http://localhost:8080/mcp")

    assert 'url = "http://localhost:8080/mcp"' in generated
    assert 'default_tools_approval_mode = "approve"' in generated


def test_runner_export_rejects_custom_servers() -> None:
    """Custom servers must be registry-linked before runner sync."""
    try:
        build_runner_export(
            profile_payload={
                "servers": [
                    {
                        "alias": "redis-test",
                        "server_name": "custom:redis-test",
                        "version": None,
                        "status": "custom",
                    }
                ]
            },
            client="codex",
            export_format="toml",
            profile="default",
            tenant=None,
        )
    except ValueError as exc:
        assert "registry-linked" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_runner_export_rejects_missing_profile_servers() -> None:
    """Runner sync requires an explicit profile server list."""
    try:
        build_runner_export(
            profile_payload={},
            client="codex",
            export_format="toml",
            profile="default",
            tenant=None,
        )
    except ValueError as exc:
        assert "profile servers" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_runner_export_rejects_missing_alias() -> None:
    """Profile server aliases are required because clients address aliases."""
    try:
        build_runner_export(
            profile_payload={
                "servers": [
                    {
                        "server_name": "io.github.test/server",
                        "version": "1.0.0",
                        "status": "linked",
                    }
                ]
            },
            client="codex",
            export_format="toml",
            profile="default",
            tenant=None,
        )
    except ValueError as exc:
        assert "missing its alias" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_runner_export_renders_supported_client_formats() -> None:
    """M2 runner mode renders the wrapper shape each client expects."""
    servers = [{"alias": "sonarqube", "status": "linked", "version": "1.2.3"}]

    codex = render_runner_config(servers, "codex", "toml", "default", "acme")
    codex_payload = tomllib.loads(codex)
    assert codex_payload["mcp_servers"]["sonarqube"] == {
        "command": "lightnow",
        "args": [
            "run",
            "--profile",
            "default",
            "--server",
            "sonarqube",
            "--tenant",
            "acme",
        ],
    }

    for client in ["claude-desktop", "claude-code", "cursor", "windsurf"]:
        payload = json.loads(
            render_runner_config(servers, client, "json", "default", None)
        )
        assert payload == {
            "mcpServers": {
                "sonarqube": {
                    "command": "lightnow",
                    "args": ["run", "--profile", "default", "--server", "sonarqube"],
                }
            }
        }

    gemini = json.loads(
        render_runner_config(servers, "gemini-cli", "json", "default", None)
    )
    assert gemini == {
        "mcpServers": {
            "sonarqube": {
                "command": "lightnow",
                "args": ["run", "--profile", "default", "--server", "sonarqube"],
            }
        }
    }

    vscode = json.loads(
        render_runner_config(servers, "vscode", "json", "default", None)
    )
    assert vscode == {
        "servers": {
            "sonarqube": {
                "type": "stdio",
                "command": "lightnow",
                "args": ["run", "--profile", "default", "--server", "sonarqube"],
            }
        }
    }

    continue_yaml = render_runner_config(servers, "continue", "yaml", "default", None)
    assert yaml.safe_load(continue_yaml) == {
        "mcpServers": [
            {
                "name": "sonarqube",
                "command": "lightnow",
                "args": ["run", "--profile", "default", "--server", "sonarqube"],
            }
        ]
    }

    librechat_yaml = render_runner_config(servers, "librechat", "yaml", "default", None)
    assert yaml.safe_load(librechat_yaml) == {
        "mcpServers": {
            "sonarqube": {
                "command": "lightnow",
                "args": ["run", "--profile", "default", "--server", "sonarqube"],
            }
        }
    }

    inspector = render_runner_config(servers, "mcp-inspector", "shell", "default", None)
    assert inspector == (
        "#!/usr/bin/env sh\n"
        "exec lightnow 'run' '--profile' 'default' '--server' 'sonarqube' \"$@\"\n"
    )


def test_runner_export_rejects_unsupported_client_format() -> None:
    """Unsupported runner export shapes fail explicitly."""
    try:
        render_runner_config(
            [{"alias": "sonarqube", "status": "linked", "version": "1.2.3"}],
            "codex",
            "json",
            "default",
            None,
        )
    except ValueError as exc:
        assert "does not support" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_sync_runner_dry_run_does_not_fetch_plaintext_export() -> None:
    """Runner dry-run gets profile servers and skips plaintext export generation."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        with (
            patch(
                "lightnow_cli.commands.integrations.require_access_token",
                return_value="token",
            ),
            patch(
                "lightnow_cli.commands.integrations.fetch_profile_servers",
                return_value={
                    "servers": [
                        {
                            "alias": "sonarqube",
                            "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                            "version": "1.2.3",
                            "status": "linked",
                        }
                    ]
                },
            ),
            patch(
                "lightnow_cli.commands.integrations.fetch_export",
                side_effect=AssertionError("runner sync must not fetch client exports"),
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "sync",
                    "--client",
                    "codex",
                    "--runner",
                    "--config-path",
                    str(target),
                    "--dry-run",
                ],
            )

    assert result.exit_code == 0
    assert 'command = "lightnow"' in result.stdout
    assert "--server" in result.stdout
    assert not target.exists()


def test_sync_local_proxy_dry_run_writes_one_codex_entry_without_fetching_exports() -> (
    None
):
    """Local Proxy dry-run writes only the proxy binding and skips server export APIs."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        with (
            patch(
                "lightnow_cli.commands.integrations.require_access_token",
                return_value="token",
            ),
            patch(
                "lightnow_cli.commands.integrations.fetch_profile_servers",
                side_effect=AssertionError(
                    "local proxy sync must not fetch profile servers"
                ),
            ),
            patch(
                "lightnow_cli.commands.integrations.fetch_export",
                side_effect=AssertionError(
                    "local proxy sync must not fetch client exports"
                ),
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "sync",
                    "--client",
                    "codex",
                    "--local-proxy",
                    "--config-path",
                    str(target),
                    "--dry-run",
                ],
            )

    assert result.exit_code == 0
    assert "[mcp_servers.lightnow]" in result.stdout
    assert 'command = "mcp-proxy"' in result.stdout
    assert "--transport" in result.stdout
    assert "stdio" in result.stdout
    assert 'default_tools_approval_mode = "approve"' in result.stdout
    assert "--server" not in result.stdout
    assert not target.exists()


def test_sync_local_proxy_rejects_runner_mode_conflict() -> None:
    """Local Proxy Mode and legacy per-server runner wrappers are mutually exclusive."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        result = runner.invoke(
            app,
            [
                "sync",
                "--client",
                "codex",
                "--runner",
                "--local-proxy",
                "--config-path",
                str(target),
            ],
        )

    assert result.exit_code == 2
    assert "either --runner or --local-proxy" in result.stdout


def test_prepare_codex_local_proxy_config_removes_direct_mcp_servers() -> None:
    """Local Proxy Mode removes direct Codex MCP servers before patching."""
    existing = """
model = "gpt-5.5"

[mcp_servers.github]
command = "docker"

[mcp_servers.github.env]
GITHUB_TOKEN = "secret"

[profiles.enterprise]
model = "gpt-5"
"""

    prepared = prepare_codex_local_proxy_config(existing)

    assert 'model = "gpt-5.5"' in prepared
    assert "[profiles.enterprise]" in prepared
    assert "[mcp_servers.github]" not in prepared
    assert "GITHUB_TOKEN" not in prepared


def test_prepare_json_local_proxy_config_removes_direct_mcp_servers() -> None:
    """Local Proxy Mode removes existing JSON MCP servers while keeping app config."""
    existing = json.dumps(
        {
            "preferences": {"theme": "dark"},
            "mcpServers": {
                "github": {
                    "command": "docker",
                    "env": {"GITHUB_TOKEN": "secret"},
                }
            },
        }
    )

    prepared = prepare_json_local_proxy_config(existing)
    payload = json.loads(prepared)

    assert payload == {"preferences": {"theme": "dark"}}
    assert "secret" not in prepared


def test_sync_local_proxy_replaces_existing_codex_mcp_servers() -> None:
    """Local Proxy sync preserves user Codex config and leaves only one MCP server."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        proxy_config = Path(tmp) / "mcp-proxy.yaml"
        target.write_text(
            'model = "gpt-5.5"\n\n'
            "[mcp_servers.github]\n"
            'command = "docker"\n\n'
            "[mcp_servers.github.env]\n"
            'GITHUB_TOKEN = "secret"\n\n'
            "[profiles.enterprise]\n"
            'model = "gpt-5"\n'
        )
        with patch(
            "lightnow_cli.commands.integrations.require_access_token",
            return_value="token",
        ):
            result = runner.invoke(
                app,
                [
                    "sync",
                    "--client",
                    "codex",
                    "--local-proxy",
                    "--local-proxy-url",
                    "http://localhost:8765/mcp",
                    "--local-proxy-config-path",
                    str(proxy_config),
                    "--config-path",
                    str(target),
                ],
            )
            patched = target.read_text()
            proxy_payload = yaml.safe_load(proxy_config.read_text())

    assert result.exit_code == 0
    assert 'model = "gpt-5.5"' in patched
    assert "[profiles.enterprise]" in patched
    assert "[mcp_servers.lightnow]" in patched
    assert "[mcp_servers.github]" not in patched
    assert "GITHUB_TOKEN" not in patched
    assert 'command = "mcp-proxy"' in patched
    assert str(proxy_config) in patched
    assert '"--transport", "stdio"' in patched
    assert 'url = "http://localhost:8765/mcp"' not in patched
    assert 'default_tools_approval_mode = "approve"' in patched
    assert proxy_payload["server"] == {
        "host": "localhost",
        "port": 8765,
        "public_url": "http://localhost:8765",
    }
    assert proxy_payload["local_proxy"] == {
        "enabled": True,
        "profile": "default",
        "path": "/mcp",
        "sync_from_lightnow": True,
        "client_name": "codex",
        "client_version": None,
        "runner_name": "lightnow-local-proxy",
        "runner_version": "0.1.0",
        "client_transport": "stdio",
    }
    assert proxy_payload["registry_api"]["use_cli_session"] is True
    assert proxy_payload["registry_api"]["include_secrets"] is True
    assert proxy_payload["profiles"] == {"default": {}}
    assert proxy_payload["upstreams"] == {}


def test_sync_local_proxy_replaces_existing_claude_desktop_mcp_servers() -> None:
    """Claude Desktop Local Proxy sync leaves one LightNow MCP server entry."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "claude_desktop_config.json"
        proxy_config = Path(tmp) / "mcp-proxy.yaml"
        target.write_text(
            json.dumps(
                {
                    "preferences": {"remoteToolsDeviceName": "mars"},
                    "mcpServers": {
                        "github": {
                            "command": "docker",
                            "env": {"GITHUB_TOKEN": "secret"},
                        }
                    },
                }
            )
        )
        with patch(
            "lightnow_cli.commands.integrations.require_access_token",
            return_value="token",
        ):
            result = runner.invoke(
                app,
                [
                    "sync",
                    "--client",
                    "claude-desktop",
                    "--local-proxy",
                    "--local-proxy-config-path",
                    str(proxy_config),
                    "--config-path",
                    str(target),
                ],
            )
            patched = json.loads(target.read_text())
            patched_text = target.read_text()
            proxy_payload = yaml.safe_load(proxy_config.read_text())

    assert result.exit_code == 0
    assert patched["preferences"] == {"remoteToolsDeviceName": "mars"}
    assert list(patched["mcpServers"]) == ["LightNow"]
    assert patched["mcpServers"]["LightNow"]["command"].endswith("mcp-proxy")
    assert patched["mcpServers"]["LightNow"]["args"] == [
        "--config",
        str(proxy_config),
        "--transport",
        "stdio",
    ]
    assert "secret" not in patched_text
    assert proxy_payload["local_proxy"]["client_name"] == "claude-desktop"
    assert proxy_payload["local_proxy"]["client_transport"] == "stdio"


def test_build_local_proxy_config_can_pin_tenant_context() -> None:
    """Local Proxy config can carry the effective tenant selected during sync."""
    generated = build_local_proxy_config(
        local_proxy_url="http://127.0.0.1:8080/mcp",
        profile="engineering",
        registry_api_url="https://registry-api.lightnow.local/v0.1",
        tenant="tenant-uuid",
    )

    payload = yaml.safe_load(generated)

    assert payload["local_proxy"]["profile"] == "engineering"
    assert payload["local_proxy"]["client_name"] == "codex"
    assert payload["local_proxy"]["runner_name"] == "lightnow-local-proxy"
    assert (
        payload["registry_api"]["base_url"]
        == "https://registry-api.lightnow.local/v0.1"
    )
    assert payload["registry_api"]["cli_tenant_id"] == "tenant-uuid"


def test_build_local_proxy_config_can_include_registry_ca_file() -> None:
    """Local Proxy config can scope a custom CA to LightNow Registry/Auth."""
    generated = build_local_proxy_config(
        local_proxy_url="http://127.0.0.1:8080/mcp",
        profile="default",
        registry_api_url="https://registry-api.lightnow.local/v0.1",
        tenant=None,
        registry_ca_file=Path("/tmp/lightnow-local-ca.crt"),
    )

    payload = yaml.safe_load(generated)

    assert payload["registry_api"]["ca_file"] == "/tmp/lightnow-local-ca.crt"


def test_sync_runner_passes_tenant_to_profile_server_lookup() -> None:
    """Runner sync builds organization-aware client commands from tenant profiles."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        with (
            patch(
                "lightnow_cli.commands.integrations.require_access_token",
                return_value="token",
            ),
            patch(
                "lightnow_cli.commands.integrations.fetch_profile_servers",
                return_value={
                    "servers": [
                        {
                            "alias": "sonarqube",
                            "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                            "version": "1.2.3",
                            "status": "linked",
                        }
                    ]
                },
            ) as fetch_profile,
        ):
            result = runner.invoke(
                app,
                [
                    "sync",
                    "--client",
                    "codex",
                    "--tenant",
                    "acme",
                    "--runner",
                    "--config-path",
                    str(target),
                    "--dry-run",
                ],
            )

    assert result.exit_code == 0
    assert fetch_profile.call_args.kwargs["tenant"] == "acme"
    assert "--tenant" in result.stdout
    assert "acme" in result.stdout


def test_sync_runner_uses_stored_context_in_generated_wrappers() -> None:
    """Runner sync embeds the stored organization context in client wrappers."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        with (
            patch(
                "lightnow_cli.commands.integrations.require_access_token",
                return_value="token",
            ),
            patch(
                "lightnow_cli.commands.integrations.config_manager.effective_tenant",
                return_value="tenant-uuid",
            ),
            patch(
                "lightnow_cli.commands.integrations.fetch_profile_servers",
                return_value={
                    "servers": [
                        {
                            "alias": "sonarqube",
                            "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                            "version": "1.2.3",
                            "status": "linked",
                        }
                    ]
                },
            ) as fetch_profile,
        ):
            result = runner.invoke(
                app,
                [
                    "sync",
                    "--client",
                    "codex",
                    "--runner",
                    "--config-path",
                    str(target),
                    "--dry-run",
                ],
            )

    assert result.exit_code == 0
    assert fetch_profile.call_args.kwargs["tenant"] == "tenant-uuid"
    assert "--tenant" in result.stdout
    assert "tenant-uuid" in result.stdout


def test_fetch_runtime_context_requests_local_runner_secret_context() -> None:
    """The runner asks for secret context with consumer=local-runner."""

    class Response:
        status_code = 200
        text = "{}"

        def json(self) -> dict[str, object]:
            return {"probe_request": {"transport": "stdio", "stdio": {"cmd": "docker"}}}

    with patch("lightnow_cli.authenticated_http.httpx.request") as mock_get:
        mock_get.return_value = Response()

        fetch_runtime_context(
            api_url="https://registry-api.lightnow.local/v0.1",
            token="token",
            tenant="acme",
            profile="default",
            server_name="io.github.sonarsource/sonarqube-mcp-server",
            version="1.2.3",
            transport="stdio",
        )

    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer token"
    assert kwargs["headers"]["X-Tenant"] == "acme"
    assert kwargs["params"] == {
        "profile": "default",
        "include": "secrets",
        "transport": "stdio",
        "consumer": "local-runner",
    }


def test_fetch_profile_servers_reports_network_errors() -> None:
    """Profile-server lookup reports network failures explicitly."""
    with patch(
        "lightnow_cli.authenticated_http.httpx.request",
        side_effect=httpx.RequestError("connection refused"),
    ):
        try:
            fetch_profile_servers(
                api_url="https://registry-api.lightnow.local/v0.1",
                token="token",
                tenant=None,
                profile="default",
            )
        except ValueError as exc:
            assert "Network error" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_fetch_runtime_context_reports_network_errors() -> None:
    """Runtime context lookup reports network failures explicitly."""
    with patch(
        "lightnow_cli.authenticated_http.httpx.request",
        side_effect=httpx.RequestError("connection refused"),
    ):
        try:
            fetch_runtime_context(
                api_url="https://registry-api.lightnow.local/v0.1",
                token="token",
                tenant=None,
                profile="default",
                server_name="io.github.test/server",
                version="1.0.0",
                transport="stdio",
            )
        except ValueError as exc:
            assert "Network error" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_runner_json_response_errors_are_redacted() -> None:
    """Runner API errors never print secret-like values."""

    class Response:
        status_code = 403
        text = '{"error":"TOKEN denied"}'

        def json(self) -> dict[str, object]:
            return {}

    try:
        json_response_or_error(Response())  # type: ignore[arg-type]
    except ValueError as exc:
        assert "HTTP 403" in str(exc)
        assert "[REDACTED]" in str(exc)
        assert "TOKEN denied" not in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_runner_json_response_reports_api_401_details() -> None:
    """Runner API 401 responses preserve explicit Registry API auth details."""

    class Response:
        status_code = 401
        text = '{"error":{"message":"Email verification status mismatch"}}'

        def json(self) -> dict[str, object]:
            return {"error": {"message": "Email verification status mismatch"}}

    try:
        json_response_or_error(Response())  # type: ignore[arg-type]
    except ValueError as exc:
        assert "Email verification status mismatch" in str(exc)
        assert ACCESS_TOKEN_EXPIRED_MESSAGE not in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_runner_json_response_rejects_non_object_payload() -> None:
    """Runner API responses must be JSON objects."""

    class Response:
        status_code = 200
        text = "[]"

        def json(self) -> list[object]:
            return []

    try:
        json_response_or_error(Response())  # type: ignore[arg-type]
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_launch_config_from_context_extracts_secret_environment() -> None:
    """Runner launch config injects secret env into the child process only."""
    command, args, env, cwd = launch_config_from_context(
        {
            "probe_request": {
                "transport": "stdio",
                "stdio": {
                    "cmd": "docker",
                    "args": ["run", "--rm", "-i", "sonarsource/sonarqube-mcp"],
                    "env": {"SONARQUBE_TOKEN": "top-secret"},
                    "cwd": "~/code",
                },
            }
        }
    )

    assert command == "docker"
    assert args == ["run", "--rm", "-i", "sonarsource/sonarqube-mcp"]
    assert env == {"SONARQUBE_TOKEN": "top-secret"}
    assert cwd == "~/code"


def test_runner_context_readiness_reports_missing_inputs() -> None:
    """The runner blocks before starting a child process when config is incomplete."""
    try:
        assert_runtime_context_ready(
            {
                "missing_inputs": [
                    {
                        "scope": "env",
                        "name": "SONARQUBE_TOKEN",
                        "is_secret": True,
                        "description": "Your SonarQube USER token",
                    }
                ]
            },
            server="sonarqube",
            profile="default",
        )
    except ValueError as exc:
        message = str(exc)
        assert "Runtime profile 'default' is missing required configuration" in message
        assert "SONARQUBE_TOKEN (env, secret)" in message
        assert "Your SonarQube USER token" in message
        assert "Open LightNow Integrations" in message
    else:
        raise AssertionError("expected ValueError")


def test_runner_failure_summary_redacts_environment_values() -> None:
    """Child-process failures explain the launch shape without secret values."""
    summary = runner_failure_summary(
        server="sonarqube",
        profile="default",
        command="docker",
        args=["run", "--rm", "-i", "sonarsource/sonarqube-mcp"],
        env={"SONARQUBE_TOKEN": "top-secret"},
        cwd=None,
        exit_code=1,
    )

    assert "docker run --rm -i sonarsource/sonarqube-mcp" in summary
    assert "SONARQUBE_TOKEN" in summary
    assert "top-secret" not in summary
    assert "exited with code 1" in summary


def test_launch_config_from_context_rejects_invalid_payloads() -> None:
    """Invalid runtime context shapes fail before a child process starts."""
    invalid_contexts = [
        {},
        {"probe_request": {"transport": "streamable-http"}},
        {"probe_request": {"transport": "stdio"}},
        {"probe_request": {"transport": "stdio", "stdio": {}}},
        {
            "probe_request": {
                "transport": "stdio",
                "stdio": {"cmd": "docker", "args": [1]},
            }
        },
        {
            "probe_request": {
                "transport": "stdio",
                "stdio": {"cmd": "docker", "env": []},
            }
        },
        {
            "probe_request": {
                "transport": "stdio",
                "stdio": {"cmd": "docker", "cwd": []},
            }
        },
    ]

    for context in invalid_contexts:
        try:
            launch_config_from_context(context)  # type: ignore[arg-type]
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")


def test_resolve_profile_server_returns_linked_server() -> None:
    """The runner resolves either alias or registry server name."""
    with patch(
        "lightnow_cli.commands.runner.fetch_profile_servers",
        return_value={
            "servers": [
                {
                    "alias": "sonarqube",
                    "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                    "version": "1.2.3",
                    "status": "linked",
                }
            ]
        },
    ):
        selected = resolve_profile_server(
            api_url="https://registry-api.lightnow.local/v0.1",
            token="token",
            tenant=None,
            profile="default",
            server="io.github.sonarsource/sonarqube-mcp-server",
        )

    assert selected["alias"] == "sonarqube"


def test_resolve_profile_server_rejects_unlinked_servers() -> None:
    """The runner refuses custom profile entries instead of inventing mappings."""
    with patch(
        "lightnow_cli.commands.runner.fetch_profile_servers",
        return_value={
            "servers": [
                {
                    "alias": "redis-test",
                    "server_name": "custom:redis-test",
                    "status": "custom",
                }
            ]
        },
    ):
        try:
            resolve_profile_server(
                api_url="https://registry-api.lightnow.local/v0.1",
                token="token",
                tenant=None,
                profile="default",
                server="redis-test",
            )
        except ValueError as exc:
            assert "not ready for the local runner" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_resolve_profile_server_reports_missing_inputs() -> None:
    """Profile servers with missing inputs fail with actionable guidance."""
    with patch(
        "lightnow_cli.commands.runner.fetch_profile_servers",
        return_value={
            "servers": [
                {
                    "alias": "sonarqube",
                    "server_name": "io.github.sonarsource/sonarqube-mcp-server",
                    "version": "1.2.3",
                    "status": "needs_configuration",
                    "missing_inputs": [{"scope": "env", "name": "SONARQUBE_TOKEN"}],
                }
            ]
        },
    ):
        try:
            resolve_profile_server(
                api_url="https://registry-api.lightnow.local/v0.1",
                token="token",
                tenant=None,
                profile="default",
                server="sonarqube",
            )
        except ValueError as exc:
            assert "SONARQUBE_TOKEN" in str(exc)
            assert "Open LightNow Integrations" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_resolve_profile_server_rejects_missing_and_malformed_servers() -> None:
    """The runner fails explicitly when the profile data cannot be resolved."""
    cases = [
        {},
        {"servers": []},
        {
            "servers": [
                {
                    "alias": "sonarqube",
                    "server_name": None,
                    "version": "1.2.3",
                    "status": "linked",
                }
            ]
        },
    ]

    for payload in cases:
        with patch(
            "lightnow_cli.commands.runner.fetch_profile_servers",
            return_value=payload,
        ):
            try:
                resolve_profile_server(
                    api_url="https://registry-api.lightnow.local/v0.1",
                    token="token",
                    tenant=None,
                    profile="default",
                    server="sonarqube",
                )
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError")


def test_extract_json_managed_tracks_aliases_and_inputs() -> None:
    """The JSON manifest captures both server aliases and VS Code inputs."""
    generated = json.dumps(
        {
            "inputs": [{"id": "lightnow-token"}],
            "servers": {"sonarqube": {"type": "stdio", "command": "docker"}},
        }
    )

    assert extract_json_managed(generated) == {
        "aliases": ["sonarqube"],
        "input_ids": ["lightnow-token"],
    }


def test_sync_requires_authentication() -> None:
    """Sync exits clearly when no token is available."""
    runner = CliRunner()

    with patch(
        "lightnow_cli.commands.integrations.require_access_token",
        side_effect=AuthError("Not authenticated. Run 'lightnow login' first."),
    ):
        result = runner.invoke(app, ["sync", "--client", "codex"])

    assert result.exit_code == 1
    assert "Not authenticated. Run 'lightnow login' first." in result.stdout


def test_sync_rejects_unknown_client() -> None:
    """Unknown clients are explicit argument errors."""
    runner = CliRunner()

    result = runner.invoke(app, ["sync", "--client", "unknown"])

    assert result.exit_code == 2
    assert "Unsupported client" in result.stdout


def test_sync_rejects_unknown_secret_mode() -> None:
    """Unknown secret modes fail before any API call."""
    runner = CliRunner()

    result = runner.invoke(
        app, ["sync", "--client", "codex", "--secret-mode", "unsafe"]
    )

    assert result.exit_code == 2
    assert "Unsupported secret mode" in result.stdout


def test_sync_reports_api_401_as_expired_token() -> None:
    """Sync maps Registry API 401 responses to the shared expired-token message."""
    runner = CliRunner()

    with patch(
        "lightnow_cli.commands.integrations.require_access_token",
        return_value="token",
    ):
        with patch(
            "lightnow_cli.commands.integrations.fetch_export",
            side_effect=AccessTokenExpired(ACCESS_TOKEN_EXPIRED_MESSAGE),
        ):
            result = runner.invoke(app, ["sync", "--client", "codex"])

    assert result.exit_code == 1
    assert ACCESS_TOKEN_EXPIRED_MESSAGE in result.stdout


def test_sync_plaintext_requires_confirmation() -> None:
    """Default plaintext writes require explicit confirmation."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        with patch(
            "lightnow_cli.commands.integrations.require_access_token",
            return_value="token",
        ):
            with patch(
                "lightnow_cli.commands.integrations.fetch_export",
                return_value='[mcp_servers.github]\ncommand = "docker"\n',
            ):
                result = runner.invoke(
                    app,
                    [
                        "sync",
                        "--client",
                        "codex",
                        "--config-path",
                        str(target),
                    ],
                    input="n\n",
                )

        assert result.exit_code == 1
        assert "Canceled" in result.stdout
        assert not target.exists()


def test_fetch_export_uses_profile_and_tenant_headers() -> None:
    """Export requests send profile, client, format, secret mode and tenant."""

    class Response:
        status_code = 200
        text = "{}"

        def json(self) -> dict[str, object]:
            return {"export": {"content": "[mcp_servers.github]\n"}}

    with patch("lightnow_cli.authenticated_http.httpx.request") as mock_get:
        mock_get.return_value = Response()

        content = fetch_export(
            api_url="https://registry-api.lightnow.local/v0.1",
            token="token",
            tenant="acme",
            profile="default",
            client="codex",
            export_format="toml",
            secret_mode="placeholder",
        )

    assert content == "[mcp_servers.github]\n"
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer token"
    assert kwargs["headers"]["X-Tenant"] == "acme"
    assert kwargs["params"]["client"] == "codex"
    assert kwargs["params"]["format"] == "toml"
    assert kwargs["params"]["secret_mode"] == "placeholder"


def test_import_profile_config_posts_content_without_printing_it() -> None:
    """Import requests send client config to the API and keep terminal output separate."""

    class Response:
        status_code = 200
        text = "{}"

        def json(self) -> dict[str, object]:
            return {
                "dry_run": True,
                "profile": {"name": "codex-current"},
                "summary": {
                    "total": 1,
                    "mapped": 1,
                    "custom": 0,
                    "importable": 1,
                    "blocked": 0,
                },
                "servers": [
                    {
                        "alias": "github",
                        "status": "mapped",
                        "server_name": "io.github.github/github-mcp-server",
                    }
                ],
            }

    with patch("lightnow_cli.authenticated_http.httpx.request") as mock_post:
        mock_post.return_value = Response()

        payload = import_profile_config(
            api_url="https://registry-api.lightnow.local/v0.1",
            token="token",
            tenant="tenant-uuid",
            source="codex",
            content='[mcp_servers.github]\ncommand = "docker"\n',
            profile="codex-current",
            dry_run=True,
            replace=False,
        )

    assert payload["summary"]["mapped"] == 1
    _, kwargs = mock_post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer token"
    assert kwargs["headers"]["X-Tenant"] == "tenant-uuid"
    assert kwargs["params"]["dry_run"] == "true"
    assert kwargs["json"]["source"] == "codex"
    assert kwargs["json"]["profile"]["name"] == "codex-current"
    assert kwargs["json"]["content"] == '[mcp_servers.github]\ncommand = "docker"\n'
    assert kwargs["json"]["replace"] is False


def test_import_config_command_prints_only_redacted_summary() -> None:
    """The CLI import command must not echo source config or secret values."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "config.toml"
        target.write_text(
            '[mcp_servers.github]\ncommand = "docker"\n\n'
            '[mcp_servers.github.env]\nGITHUB_TOKEN = "secret-value"\n'
        )
        with (
            patch(
                "lightnow_cli.commands.integrations.require_access_token",
                return_value="token",
            ),
            patch(
                "lightnow_cli.commands.integrations.import_profile_config",
                return_value={
                    "dry_run": True,
                    "profile": {"name": "default"},
                    "summary": {
                        "total": 1,
                        "mapped": 1,
                        "custom": 0,
                        "importable": 1,
                        "blocked": 0,
                    },
                    "servers": [
                        {
                            "alias": "github",
                            "status": "mapped",
                            "server_name": "io.github.github/github-mcp-server",
                        }
                    ],
                },
            ) as importer,
        ):
            result = runner.invoke(
                app,
                [
                    "import-config",
                    "--client",
                    "codex",
                    "--config-path",
                    str(target),
                    "--dry-run",
                ],
            )

    assert result.exit_code == 0
    assert "Previewed" in result.stdout
    assert "github: mapped" in result.stdout
    assert "secret-value" not in result.stdout
    assert "GITHUB_TOKEN" not in result.stdout
    importer.assert_called_once()


def test_fetch_export_refreshes_and_retries_after_unauthorized() -> None:
    """Export requests share the central refresh-and-retry path."""

    class Response:
        def __init__(self, status_code: int, content: str = "") -> None:
            self.status_code = status_code
            self.text = content

        def json(self) -> dict[str, object]:
            return {"export": {"content": "[mcp_servers.github]\n"}}

    with (
        patch(
            "lightnow_cli.authenticated_http.httpx.request",
            side_effect=[Response(401), Response(200)],
        ) as mock_request,
        patch(
            "lightnow_cli.authenticated_http.refresh_current_session",
            return_value="new-token",
        ),
    ):
        content = fetch_export(
            api_url="https://registry-api.lightnow.local/v0.1",
            token="old-token",
            tenant=None,
            profile="default",
            client="codex",
            export_format="toml",
            secret_mode="placeholder",
        )

    assert content == "[mcp_servers.github]\n"
    assert mock_request.call_count == 2
    assert mock_request.call_args_list[0].kwargs["headers"]["Authorization"] == (
        "Bearer old-token"
    )
    assert mock_request.call_args_list[1].kwargs["headers"]["Authorization"] == (
        "Bearer new-token"
    )


def test_fetch_export_rejects_invalid_payload() -> None:
    """Malformed export responses fail explicitly."""

    class Response:
        status_code = 200
        text = "{}"

        def json(self) -> dict[str, object]:
            return {"export": {}}

    with (
        patch(
            "lightnow_cli.authenticated_http.httpx.request",
            side_effect=[Response(), Response()],
        ),
        patch(
            "lightnow_cli.authenticated_http.refresh_current_session",
            return_value="new-token",
        ),
    ):
        try:
            fetch_export(
                api_url="https://registry-api.lightnow.local/v0.1",
                token="token",
                tenant=None,
                profile="default",
                client="codex",
                export_format="toml",
                secret_mode="placeholder",
            )
        except ValueError as exc:
            assert "export content" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_fetch_export_reports_http_errors() -> None:
    """Registry export failures include the redacted HTTP response."""

    class Response:
        status_code = 403
        text = '{"error":"TOKEN denied"}'

        def json(self) -> dict[str, object]:
            return {}

    with (
        patch(
            "lightnow_cli.authenticated_http.httpx.request",
            side_effect=[Response(), Response()],
        ),
        patch(
            "lightnow_cli.authenticated_http.refresh_current_session",
            return_value="new-token",
        ),
    ):
        try:
            fetch_export(
                api_url="https://registry-api.lightnow.local/v0.1",
                token="token",
                tenant=None,
                profile="default",
                client="codex",
                export_format="toml",
                secret_mode="placeholder",
            )
        except ValueError as exc:
            assert "HTTP 403" in str(exc)
            assert "[REDACTED]" in str(exc)
            assert "denied" not in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_fetch_export_reports_api_401_details() -> None:
    """Registry export 401 responses preserve explicit API auth details."""

    class Response:
        status_code = 401
        text = '{"error":{"message":"Email verification status mismatch"}}'

        def json(self) -> dict[str, object]:
            return {"error": {"message": "Email verification status mismatch"}}

    with (
        patch(
            "lightnow_cli.authenticated_http.httpx.request",
            side_effect=[Response(), Response()],
        ),
        patch(
            "lightnow_cli.authenticated_http.refresh_current_session",
            return_value="new-token",
        ),
    ):
        try:
            fetch_export(
                api_url="https://registry-api.lightnow.local/v0.1",
                token="token",
                tenant=None,
                profile="default",
                client="codex",
                export_format="toml",
                secret_mode="placeholder",
            )
        except ValueError as exc:
            assert "Email verification status mismatch" in str(exc)
            assert ACCESS_TOKEN_EXPIRED_MESSAGE not in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_fetch_export_reports_network_errors() -> None:
    """Network errors fail explicitly."""
    with patch(
        "lightnow_cli.authenticated_http.httpx.request",
        side_effect=httpx.RequestError("connection refused"),
    ):
        try:
            fetch_export(
                api_url="https://registry-api.lightnow.local/v0.1",
                token="token",
                tenant=None,
                profile="default",
                client="codex",
                export_format="toml",
                secret_mode="placeholder",
            )
        except ValueError as exc:
            assert "Network error" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_json_manifest_validation() -> None:
    """Invalid JSON manifests fail loudly instead of corrupting configs."""
    from lightnow_cli.commands.integrations import read_json_manifest

    with tempfile.TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "mcp.json.lightnow-managed.json"
        manifest.write_text('{"aliases": "github"}')

        try:
            read_json_manifest(manifest)
        except ValueError as exc:
            assert "invalid aliases" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_print_import_summary_handles_missing_server_list(capsys) -> None:
    """Import summary handles sparse API responses without leaking payload data."""
    print_import_summary(
        {
            "dry_run": False,
            "summary": {"total": 1, "mapped": 0, "blocked": 0},
            "profile": {},
            "servers": [None],
        },
        Path("codex.toml"),
    )

    output = capsys.readouterr().out
    assert "Imported codex.toml into LightNow profile default" in output
    assert "total=1" in output
