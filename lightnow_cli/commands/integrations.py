"""Integration profile commands."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional, cast
from urllib.parse import urlparse

import httpx
import typer
import yaml
from rich.console import Console
from typing_extensions import Annotated

from ..authenticated_http import (
    authentication_error_from_response,
    request_with_refresh,
)
from ..config import config_manager
from .auth import (
    ACCESS_TOKEN_EXPIRED_MESSAGE,
    AccessTokenExpired,
    AuthError,
    require_access_token,
)
from .runner import fetch_profile_servers

console = Console()
app = typer.Typer(help="Integration profile commands")

BEGIN = "# >>> LightNow managed integrations >>>"
END = "# <<< LightNow managed integrations <<<"
JSON_MANIFEST_SUFFIX = ".lightnow-managed.json"

CLIENT_DEFAULTS: dict[str, tuple[str, Path]] = {
    "codex": ("toml", Path.home() / ".codex" / "config.toml"),
    "claude-desktop": (
        "json",
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json",
    ),
    "claude-code": ("json", Path.home() / ".claude" / "mcp.json"),
    "cursor": ("json", Path.home() / ".cursor" / "mcp.json"),
    "windsurf": ("json", Path.home() / ".codeium" / "windsurf" / "mcp_config.json"),
    "continue": ("yaml", Path.home() / ".continue" / "config.yaml"),
    "gemini-cli": ("json", Path.home() / ".gemini" / "settings.json"),
    "librechat": ("yaml", Path.cwd() / "librechat.yaml"),
    "vscode": ("json", Path.cwd() / ".vscode" / "mcp.json"),
    "mcp-inspector": ("shell", Path.cwd() / "lightnow-mcp-inspector.sh"),
}

CLIENTS = sorted(CLIENT_DEFAULTS)
SECRET_MODES = ["placeholder", "plaintext"]
DEFAULT_LOCAL_PROXY_CONFIG_PATH = Path.home() / ".lightnow" / "mcp-proxy.yaml"


@app.command("sync")
def sync(
    client: Annotated[str, typer.Option("--client", help="Target MCP client")],
    profile: Annotated[
        str, typer.Option("--profile", help="Runtime profile")
    ] = "default",
    tenant: Annotated[
        Optional[str],
        typer.Option("--tenant", help="Tenant id or slug, sent as X-Tenant"),
    ] = None,
    format_: Annotated[
        Optional[str],
        typer.Option("--format", help="Export format; defaults to the client format"),
    ] = None,
    secret_mode: Annotated[
        str,
        typer.Option(
            "--secret-mode",
            help="plaintext writes secret values; placeholder writes ${SECRET_NAME} references",
        ),
    ] = "plaintext",
    config_path: Annotated[
        Optional[Path],
        typer.Option("--config-path", help="Target client config file"),
    ] = None,
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="Registry API base URL"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print a redacted preview without writing"),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip plaintext write confirmation"),
    ] = False,
    runner: Annotated[
        bool,
        typer.Option(
            "--runner",
            help="Write LightNow local-runner wrappers instead of client-side server configs.",
        ),
    ] = False,
    local_proxy: Annotated[
        bool,
        typer.Option(
            "--local-proxy",
            help="Write one LightNow Local Proxy entry instead of per-server config.",
        ),
    ] = False,
    local_proxy_url: Annotated[
        str,
        typer.Option(
            "--local-proxy-url",
            help="Local Proxy MCP endpoint used for HTTP mode and proxy config.",
        ),
    ] = "http://127.0.0.1:8080/mcp",
    local_proxy_transport: Annotated[
        str,
        typer.Option(
            "--local-proxy-transport",
            help="Client-facing Local Proxy transport: stdio or http.",
        ),
    ] = "stdio",
    local_proxy_config_path: Annotated[
        Optional[Path],
        typer.Option(
            "--local-proxy-config-path",
            help="Config file written for the LightNow Local Proxy.",
        ),
    ] = None,
) -> None:
    """Sync a LightNow integration runtime profile into a local MCP client config."""
    if client not in CLIENT_DEFAULTS:
        raise_bad_argument("Unsupported client", f"Use one of: {', '.join(CLIENTS)}")
    if secret_mode not in SECRET_MODES:
        raise_bad_argument("Unsupported secret mode", "Use placeholder or plaintext.")
    if runner and local_proxy:
        raise_bad_argument(
            "Unsupported sync mode", "Use either --runner or --local-proxy, not both."
        )
    if local_proxy_transport not in {"stdio", "http"}:
        raise_bad_argument("Unsupported Local Proxy transport", "Use stdio or http.")

    default_format, default_path = CLIENT_DEFAULTS[client]
    export_format = format_ or default_format
    target = (config_path or default_path).expanduser()
    try:
        bearer_token = require_access_token()
    except AccessTokenExpired:
        console.print(f"[red]{ACCESS_TOKEN_EXPIRED_MESSAGE}[/red]")
        raise typer.Exit(1)
    except AuthError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1)

    effective_tenant = config_manager.effective_tenant(tenant)
    registry_api_url = api_url or config_manager.load_config().registry_api_url
    if not registry_api_url:
        raise_bad_argument(
            "Registry API URL required", "Configure the CLI or pass --api-url."
        )
    assert registry_api_url is not None
    proxy_target = (
        local_proxy_config_path or DEFAULT_LOCAL_PROXY_CONFIG_PATH
    ).expanduser()

    try:
        if local_proxy:
            generated = build_local_proxy_export(
                client=client,
                export_format=export_format,
                local_proxy_url=local_proxy_url,
                local_proxy_transport=local_proxy_transport,
                local_proxy_config_path=proxy_target,
            )
            proxy_config = build_local_proxy_config(
                local_proxy_url=local_proxy_url,
                profile=profile,
                registry_api_url=registry_api_url,
                tenant=effective_tenant,
            )
        elif runner:
            profile_payload = fetch_profile_servers(
                api_url=registry_api_url,
                token=bearer_token,
                tenant=effective_tenant,
                profile=profile,
            )
            generated = build_runner_export(
                profile_payload=profile_payload,
                client=client,
                export_format=export_format,
                profile=profile,
                tenant=effective_tenant,
            )
        else:
            generated = fetch_export(
                api_url=registry_api_url,
                token=bearer_token,
                tenant=effective_tenant,
                profile=profile,
                client=client,
                export_format=export_format,
                secret_mode=secret_mode,
            )
        existing = target.read_text() if target.exists() else ""
        manifest = target.with_name(target.name + JSON_MANIFEST_SUFFIX)
        previous_managed = (
            read_json_manifest(manifest)
            if export_format == "json"
            else {"aliases": [], "input_ids": []}
        )
        patched = patch_config(
            existing,
            generated,
            export_format,
            previous_managed["aliases"],
            previous_managed["input_ids"],
        )
    except AccessTokenExpired:
        console.print(f"[red]{ACCESS_TOKEN_EXPIRED_MESSAGE}[/red]")
        raise typer.Exit(1)
    except ValueError as exc:
        console.print(f"[bold red]Integration sync failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    if dry_run:
        console.print(redact(patched), markup=False)
        return

    if (
        secret_mode == "plaintext"
        and not dry_run
        and not yes
        and not runner
        and not local_proxy
    ):
        confirmed = typer.confirm(
            "This writes secret values into the client config on this machine. Continue?",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Canceled.[/yellow]")
            raise typer.Exit(1)

    if local_proxy:
        secure_write_text(proxy_target, proxy_config)
        console.print(f"[green]Wrote Local Proxy config to {proxy_target}[/green]")

    secure_write_text(target, patched, executable=export_format == "shell")
    if export_format == "json":
        write_json_manifest(manifest, extract_json_managed(generated))

    console.print(f"[green]Synced {client} profile {profile} to {target}[/green]")


@app.command("import-config")
def import_config(
    client: Annotated[str, typer.Option("--client", help="Source MCP client")],
    profile: Annotated[
        str, typer.Option("--profile", help="Runtime profile to import into")
    ] = "default",
    tenant: Annotated[
        Optional[str],
        typer.Option("--tenant", help="Tenant id or slug, sent as X-Tenant"),
    ] = None,
    config_path: Annotated[
        Optional[Path],
        typer.Option("--config-path", help="Source client config file"),
    ] = None,
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="Registry API base URL"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview the import without applying it"),
    ] = False,
    replace: Annotated[
        bool,
        typer.Option("--replace", help="Replace the target profile's server list"),
    ] = False,
) -> None:
    """Import an existing MCP client configuration into a LightNow profile."""
    if client not in CLIENT_DEFAULTS:
        raise_bad_argument("Unsupported client", f"Use one of: {', '.join(CLIENTS)}")
    _, default_path = CLIENT_DEFAULTS[client]
    if client != "codex":
        raise_bad_argument(
            "Unsupported import client", "Config import currently supports Codex."
        )

    target = (config_path or default_path).expanduser()
    if not target.exists():
        raise_bad_argument("Config file not found", str(target))

    try:
        bearer_token = require_access_token()
    except AccessTokenExpired:
        console.print(f"[red]{ACCESS_TOKEN_EXPIRED_MESSAGE}[/red]")
        raise typer.Exit(1)
    except AuthError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1)

    effective_tenant = config_manager.effective_tenant(tenant)
    registry_api_url = api_url or config_manager.load_config().registry_api_url
    if not registry_api_url:
        raise_bad_argument(
            "Registry API URL required", "Configure the CLI or pass --api-url."
        )
    assert registry_api_url is not None

    try:
        content = target.read_text(encoding="utf-8")
        result = import_profile_config(
            api_url=registry_api_url,
            token=bearer_token,
            tenant=effective_tenant,
            source=client,
            content=content,
            profile=profile,
            dry_run=dry_run,
            replace=replace,
        )
    except OSError as exc:
        console.print(f"[bold red]Integration import failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc
    except AccessTokenExpired:
        console.print(f"[red]{ACCESS_TOKEN_EXPIRED_MESSAGE}[/red]")
        raise typer.Exit(1)
    except ValueError as exc:
        console.print(f"[bold red]Integration import failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    print_import_summary(result, target)


def build_local_proxy_export(
    *,
    client: str,
    export_format: str,
    local_proxy_url: str,
    local_proxy_transport: str = "stdio",
    local_proxy_config_path: Optional[Path] = None,
) -> str:
    """Build one client config entry that points at the LightNow Local Proxy."""
    if client != "codex" or export_format != "toml":
        raise ValueError("Local Proxy Mode currently supports Codex TOML only.")
    if local_proxy_transport == "stdio":
        return render_local_proxy_codex_stdio_toml(
            local_proxy_config_path or DEFAULT_LOCAL_PROXY_CONFIG_PATH
        )
    if local_proxy_transport != "http":
        raise ValueError("Local Proxy transport must be stdio or http.")
    parsed = urlparse(local_proxy_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Local Proxy URL must point to localhost.") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or port is None
    ):
        raise ValueError("Local Proxy URL must point to localhost.")
    return render_local_proxy_codex_toml(local_proxy_url)


def render_local_proxy_codex_stdio_toml(local_proxy_config_path: Path) -> str:
    """Render Codex TOML that starts the local LightNow MCP proxy over stdio."""
    return (
        "# Generated by LightNow. Codex starts the local LightNow MCP proxy.\n"
        "[mcp_servers.lightnow]\n"
        'command = "mcp-proxy"\n'
        "args = "
        + json.dumps(
            [
                "--config",
                str(local_proxy_config_path.expanduser()),
                "--transport",
                "stdio",
            ]
        )
        + "\n"
        'default_tools_approval_mode = "approve"\n'
    )


def render_local_proxy_codex_toml(local_proxy_url: str) -> str:
    """Render Codex TOML for one local LightNow MCP server."""
    return (
        "# Generated by LightNow. The MCP client talks only to the local LightNow proxy.\n"
        "[mcp_servers.lightnow]\n"
        f"url = {json.dumps(local_proxy_url)}\n"
        'default_tools_approval_mode = "approve"\n'
    )


def build_local_proxy_config(
    *,
    local_proxy_url: str,
    profile: str,
    registry_api_url: str,
    tenant: Optional[str],
) -> str:
    """Render the local mcp-proxy config for LightNow-managed profile sync."""
    parsed = urlparse(local_proxy_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Local Proxy URL must point to localhost.") from exc
    if parsed.scheme != "http" or parsed.hostname is None or port is None:
        raise ValueError("Local Proxy URL must point to localhost.")

    registry_api: dict[str, Any] = {
        "enabled": True,
        "base_url": registry_api_url,
        "include_secrets": True,
        "default_scope_type": "system",
        "timeout_seconds": 20,
        "use_cli_session": True,
        "cli_config_path": str(config_manager.config_file),
    }
    if tenant:
        registry_api["cli_tenant_id"] = tenant

    payload = {
        "server": {
            "host": parsed.hostname,
            "port": port,
            "public_url": f"{parsed.scheme}://{parsed.hostname}:{port}",
        },
        "local_proxy": {
            "enabled": True,
            "profile": profile,
            "path": parsed.path or "/mcp",
            "sync_from_lightnow": True,
        },
        "auth": {
            "enabled": False,
            "issuer": config_manager.load_config().issuer,
            "groups_claim": "groups",
            "jwks_cache_seconds": 300,
        },
        "registry_api": registry_api,
        "profiles": {profile: {}},
        "upstreams": {},
    }
    return cast(str, yaml.safe_dump(payload, sort_keys=False))


def build_runner_export(
    *,
    profile_payload: dict[str, Any],
    client: str,
    export_format: str,
    profile: str,
    tenant: Optional[str],
) -> str:
    """Build client config that delegates MCP execution to the LightNow runner."""
    servers = profile_payload.get("servers")
    if not isinstance(servers, list):
        raise ValueError("Registry API response did not include profile servers.")

    entries: list[dict[str, Any]] = []
    for item in servers:
        if not isinstance(item, dict):
            continue
        alias = item.get("alias")
        version = item.get("version")
        status = item.get("status")
        if not isinstance(alias, str) or alias == "":
            raise ValueError("A profile server is missing its alias.")
        if status != "linked" or not isinstance(version, str) or version == "":
            raise ValueError(
                f"Server '{alias}' must be registry-linked before local-runner sync."
            )
        entries.append(item)

    if entries == []:
        raise ValueError("Runtime profile does not contain runner-compatible servers.")

    return render_runner_config(entries, client, export_format, profile, tenant)


def render_runner_config(
    servers: list[dict[str, Any]],
    client: str,
    export_format: str,
    profile: str,
    tenant: Optional[str],
) -> str:
    """Render local-runner wrapper config for supported clients."""
    if export_format == "toml" and client == "codex":
        return render_runner_toml(servers, profile, tenant)

    if export_format == "json" and client in {
        "claude-desktop",
        "claude-code",
        "cursor",
        "windsurf",
        "librechat",
    }:
        return render_runner_mcp_servers_json(servers, profile, tenant)

    if export_format == "json" and client == "gemini-cli":
        return render_runner_mcp_servers_json(servers, profile, tenant)

    if export_format == "json" and client == "vscode":
        return render_runner_vscode_json(servers, profile, tenant)

    if export_format == "yaml" and client == "continue":
        return render_runner_continue_yaml(servers, profile, tenant)

    if export_format == "yaml" and client == "librechat":
        return render_runner_librechat_yaml(servers, profile, tenant)

    if export_format == "shell" and client == "mcp-inspector":
        return render_runner_inspector_shell(servers, profile, tenant)

    raise ValueError(
        f"The local runner does not support {client} with {export_format}."
    )


def render_runner_toml(
    servers: list[dict[str, Any]], profile: str, tenant: Optional[str]
) -> str:
    """Render Codex TOML for local-runner profile servers."""
    lines = [
        "# Generated by LightNow. Secrets stay in LightNow and are injected by `lightnow run`.",
        "",
    ]
    for server in servers:
        alias = str(server["alias"])
        lines.append(f"[mcp_servers.{toml_key(alias)}]")
        lines.append('command = "lightnow"')
        lines.append("args = " + json.dumps(runner_args(alias, profile, tenant)))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_runner_mcp_servers_json(
    servers: list[dict[str, Any]], profile: str, tenant: Optional[str]
) -> str:
    """Render a JSON client config using the mcpServers shape."""
    payload = {
        "mcpServers": {
            str(server["alias"]): {
                "command": "lightnow",
                "args": runner_args(str(server["alias"]), profile, tenant),
            }
            for server in servers
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def render_runner_vscode_json(
    servers: list[dict[str, Any]], profile: str, tenant: Optional[str]
) -> str:
    """Render VS Code's MCP server configuration shape."""
    payload = {
        "servers": {
            str(server["alias"]): {
                "type": "stdio",
                "command": "lightnow",
                "args": runner_args(str(server["alias"]), profile, tenant),
            }
            for server in servers
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def render_runner_continue_yaml(
    servers: list[dict[str, Any]], profile: str, tenant: Optional[str]
) -> str:
    """Render Continue YAML for local-runner profile servers."""
    lines = [
        "# Generated by LightNow. Secrets stay in LightNow and are injected by `lightnow run`.",
        "mcpServers:",
    ]
    for server in servers:
        alias = str(server["alias"])
        lines.append(f"  - name: {yaml_string(alias)}")
        lines.append('    command: "lightnow"')
        lines.append("    args:")
        for arg in runner_args(alias, profile, tenant):
            lines.append(f"      - {yaml_string(arg)}")
    return "\n".join(lines) + "\n"


def render_runner_librechat_yaml(
    servers: list[dict[str, Any]], profile: str, tenant: Optional[str]
) -> str:
    """Render LibreChat YAML for local-runner profile servers."""
    lines = [
        "# Generated by LightNow. Secrets stay in LightNow and are injected by `lightnow run`.",
        "mcpServers:",
    ]
    for server in servers:
        alias = str(server["alias"])
        lines.append(f"  {yaml_key(alias)}:")
        lines.append('    command: "lightnow"')
        lines.append("    args:")
        for arg in runner_args(alias, profile, tenant):
            lines.append(f"      - {yaml_string(arg)}")
    return "\n".join(lines) + "\n"


def render_runner_inspector_shell(
    servers: list[dict[str, Any]], profile: str, tenant: Optional[str]
) -> str:
    """Render an MCP Inspector shell wrapper for the first profile server."""
    first_alias = str(servers[0]["alias"])
    quoted = " ".join(
        shell_quote(arg) for arg in runner_args(first_alias, profile, tenant)
    )
    return "#!/usr/bin/env sh\nexec lightnow " + quoted + ' "$@"\n'


def runner_args(alias: str, profile: str, tenant: Optional[str]) -> list[str]:
    """Return command arguments for the LightNow local runner."""
    args = ["run", "--profile", profile, "--server", alias]
    if tenant:
        args.extend(["--tenant", tenant])
    return args


def toml_key(value: str) -> str:
    """Return a TOML key for a server alias."""
    if value.replace("_", "").replace("-", "").isalnum():
        return value
    return json.dumps(value)


def yaml_key(value: str) -> str:
    """Return a YAML-safe mapping key."""
    if value.replace("_", "").replace("-", "").isalnum():
        return value
    return yaml_string(value)


def yaml_string(value: str) -> str:
    """Return a YAML double-quoted string."""
    return json.dumps(value)


def shell_quote(value: str) -> str:
    """Quote a shell argument."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def fetch_export(
    *,
    api_url: str,
    token: str,
    tenant: Optional[str],
    profile: str,
    client: str,
    export_format: str,
    secret_mode: str,
) -> str:
    """Fetch rendered client config from the Registry API."""
    url = f"{api_url.rstrip('/')}/integrations/profiles/{profile}/export"
    try:
        response = request_with_refresh(
            "GET",
            url,
            params={
                "client": client,
                "format": export_format,
                "secret_mode": secret_mode,
            },
            headers={"Accept": "application/json"},
            token=token,
            tenant=tenant,
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise ValueError(f"Network error: {exc}") from exc

    if response.status_code == 401:
        raise authentication_error_from_response(response)

    if response.status_code >= 400:
        raise ValueError(f"HTTP {response.status_code}: {redact(response.text)}")

    payload = response.json()
    export = payload.get("export") if isinstance(payload, dict) else None
    content = export.get("content") if isinstance(export, dict) else None
    if not isinstance(content, str):
        raise ValueError("Registry API response did not include export content.")
    return content


def import_profile_config(
    *,
    api_url: str,
    token: str,
    tenant: Optional[str],
    source: str,
    content: str,
    profile: str,
    dry_run: bool,
    replace: bool,
) -> dict[str, Any]:
    """Import rendered client config into a LightNow runtime profile."""
    url = f"{api_url.rstrip('/')}/integrations/import"
    try:
        response = request_with_refresh(
            "POST",
            url,
            params={"dry_run": "true" if dry_run else "false"},
            json={
                "source": source,
                "content": content,
                "profile": {"name": profile},
                "replace": replace,
            },
            headers={"Accept": "application/json"},
            token=token,
            tenant=tenant,
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise ValueError(f"Network error: {exc}") from exc

    if response.status_code == 401:
        raise authentication_error_from_response(response)
    if response.status_code >= 400:
        raise ValueError(f"HTTP {response.status_code}: {redact(response.text)}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Registry API response did not include an import result.")
    return payload


def print_import_summary(result: dict[str, Any], source_path: Path) -> None:
    """Print a non-secret import summary."""
    raw_summary = result.get("summary")
    summary = cast(dict[str, Any], raw_summary) if isinstance(raw_summary, dict) else {}
    raw_profile = result.get("profile")
    profile = cast(dict[str, Any], raw_profile) if isinstance(raw_profile, dict) else {}
    profile_name = (
        profile.get("name") if isinstance(profile.get("name"), str) else "default"
    )
    mode = "Previewed" if result.get("dry_run") is True else "Imported"
    console.print(
        f"[green]{mode} {source_path} into LightNow profile {profile_name}[/green]"
    )
    console.print(
        "total={total} mapped={mapped} custom={custom} importable={importable} blocked={blocked}".format(
            total=summary.get("total", 0),
            mapped=summary.get("mapped", 0),
            custom=summary.get("custom", 0),
            importable=summary.get("importable", 0),
            blocked=summary.get("blocked", 0),
        )
    )

    servers = result.get("servers")
    if not isinstance(servers, list):
        return
    for item in servers:
        if not isinstance(item, dict):
            continue
        alias = item.get("alias") if isinstance(item.get("alias"), str) else "unknown"
        status = (
            item.get("status") if isinstance(item.get("status"), str) else "unknown"
        )
        server_name = (
            item.get("server_name") if isinstance(item.get("server_name"), str) else "-"
        )
        console.print(f"- {alias}: {status} -> {server_name}")


def patch_config(
    existing: str,
    generated: str,
    export_format: str,
    previous_aliases: Optional[list[str]] = None,
    previous_input_ids: Optional[list[str]] = None,
) -> str:
    """Patch the target config with generated LightNow content."""
    if export_format == "json":
        return patch_json_config(
            existing,
            generated,
            previous_aliases or [],
            previous_input_ids or [],
        )

    generated_block = generated.strip()
    if BEGIN in generated_block and END in generated_block:
        block = generated_block + "\n"
    else:
        block = f"{BEGIN}\n{generated.rstrip()}\n{END}\n"
    if BEGIN in existing and END in existing:
        before, rest = existing.split(BEGIN, 1)
        _, after = rest.split(END, 1)
        return before.rstrip() + "\n\n" + block + after.lstrip()
    if existing.strip() == "":
        return block
    return existing.rstrip() + "\n\n" + block


def patch_json_config(
    existing: str,
    generated: str,
    previous_aliases: list[str],
    previous_input_ids: Optional[list[str]] = None,
) -> str:
    """Patch JSON client configs while preserving user-managed entries."""
    current = json.loads(existing) if existing.strip() else {}
    incoming = json.loads(generated)
    if not isinstance(current, dict) or not isinstance(incoming, dict):
        raise ValueError("LightNow can only sync JSON object client configs.")

    old_input_ids = set(previous_input_ids or [])
    for key in ("mcpServers", "servers"):
        if key not in incoming:
            continue
        current_section = current.get(key)
        incoming_section = incoming[key]
        if current_section is None:
            current_section = {}
        if not isinstance(current_section, dict) or not isinstance(
            incoming_section, dict
        ):
            raise ValueError(f"LightNow can only sync JSON object field {key}.")
        for alias in previous_aliases:
            current_section.pop(alias, None)
        current_section.update(incoming_section)
        current[key] = current_section

    if "inputs" in incoming:
        incoming_inputs = incoming["inputs"]
        current_inputs = current.get("inputs", [])
        if not isinstance(incoming_inputs, list) or not isinstance(
            current_inputs, list
        ):
            raise ValueError("LightNow can only sync JSON array field inputs.")

        merged_inputs = [
            item
            for item in current_inputs
            if not (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"] in old_input_ids
            )
        ]
        incoming_ids: set[str] = set()
        for item in incoming_inputs:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ValueError("LightNow generated an invalid JSON input entry.")
            incoming_ids.add(item["id"])

        merged_inputs = [
            item
            for item in merged_inputs
            if not (
                isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item["id"] in incoming_ids
            )
        ]
        merged_inputs.extend(incoming_inputs)
        current["inputs"] = merged_inputs

    return json.dumps(current, indent=2, ensure_ascii=False) + "\n"


def extract_json_managed(generated: str) -> dict[str, list[str]]:
    """Return JSON aliases and input IDs owned by the latest generated export."""
    incoming = json.loads(generated)
    if not isinstance(incoming, dict):
        return {"aliases": [], "input_ids": []}

    aliases: list[str] = []
    for key in ("mcpServers", "servers"):
        section = incoming.get(key)
        if isinstance(section, dict):
            aliases.extend(str(alias) for alias in section)
    input_ids: list[str] = []
    inputs = incoming.get("inputs")
    if isinstance(inputs, list):
        for item in inputs:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                input_ids.append(item["id"])

    return {
        "aliases": sorted(set(aliases)),
        "input_ids": sorted(set(input_ids)),
    }


def extract_json_aliases(generated: str) -> list[str]:
    """Return aliases owned by the latest generated JSON export."""
    return extract_json_managed(generated)["aliases"]


def read_json_manifest(path: Path) -> dict[str, list[str]]:
    """Read the sibling JSON manifest containing LightNow-managed aliases."""
    if not path.exists():
        return {"aliases": [], "input_ids": []}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"LightNow managed JSON manifest is invalid: {path}") from exc
    aliases = data.get("aliases") if isinstance(data, dict) else None
    input_ids = data.get("input_ids") if isinstance(data, dict) else []
    if not isinstance(aliases, list) or not all(
        isinstance(alias, str) for alias in aliases
    ):
        raise ValueError(f"LightNow managed JSON manifest has invalid aliases: {path}")
    if not isinstance(input_ids, list) or not all(
        isinstance(input_id, str) for input_id in input_ids
    ):
        raise ValueError(
            f"LightNow managed JSON manifest has invalid input IDs: {path}"
        )
    return {"aliases": aliases, "input_ids": input_ids}


def write_json_manifest(path: Path, managed: dict[str, list[str]]) -> None:
    """Persist the aliases owned by the latest LightNow JSON sync."""
    secure_write_text(path, json.dumps(managed, indent=2) + "\n")


def secure_write_text(path: Path, content: str, *, executable: bool = False) -> None:
    """Atomically write secret-bearing client config with restrictive permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o700 if executable else 0o600
    if path.exists():
        backup = path.with_suffix(path.suffix + ".lightnow.bak")
        shutil.copy2(path, backup)
        backup.chmod(mode)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        path.chmod(mode)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def redact(value: str) -> str:
    """Redact secret-like lines from terminal output."""
    redacted = value
    for marker in ("TOKEN", "SECRET", "PASSWORD", "PWD", "KEY"):
        redacted = redact_marker(redacted, marker)
    return redacted


def redact_marker(value: str, marker: str) -> str:
    """Redact lines containing common secret markers unless they are placeholders."""
    lines = []
    for line in value.splitlines():
        upper = line.upper()
        if marker in upper and "${" not in line:
            if "=" in line:
                key, _ = line.split("=", 1)
                line = key + '= "[REDACTED]"'
            elif ":" in line:
                key, _ = line.split(":", 1)
                line = key + ': "[REDACTED]"'
        lines.append(line)
    return "\n".join(lines)


def raise_bad_argument(title: str, detail: str) -> None:
    """Print a consistent argument error and exit."""
    console.print(f"[bold red]{title}:[/bold red] {detail}")
    raise typer.Exit(2)
