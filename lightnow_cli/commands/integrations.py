"""Integration profile commands."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
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
err_console = Console(stderr=True)
app = typer.Typer(help="Integration profile commands")

BEGIN = "# >>> LightNow managed integrations >>>"
END = "# <<< LightNow managed integrations <<<"
JSON_MANIFEST_SUFFIX = ".lightnow-managed.json"


def default_vscode_mcp_path() -> Path:
    """Return VS Code's user-level MCP config path for the current platform."""
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Code"
            / "User"
            / "mcp.json"
        )
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Code" / "User" / "mcp.json"
    return Path.home() / ".config" / "Code" / "User" / "mcp.json"


CLIENT_DEFAULTS: dict[str, tuple[str, Path]] = {
    "antigravity": ("json", Path.home() / ".gemini" / "config" / "mcp_config.json"),
    "codex": ("toml", Path.home() / ".codex" / "config.toml"),
    "claude-desktop": (
        "json",
        Path.home()
        / "Library"
        / "Application Support"
        / "Claude"
        / "claude_desktop_config.json",
    ),
    "claude-code": ("json", Path.home() / ".claude.json"),
    "cursor": ("json", Path.home() / ".cursor" / "mcp.json"),
    "windsurf": ("json", Path.home() / ".codeium" / "windsurf" / "mcp_config.json"),
    "continue": ("yaml", Path.home() / ".continue" / "config.yaml"),
    "gemini-cli": ("json", Path.home() / ".gemini" / "settings.json"),
    "librechat": ("yaml", Path.cwd() / "librechat.yaml"),
    "vscode": ("json", default_vscode_mcp_path()),
    "mcp-inspector": ("shell", Path.cwd() / "lightnow-mcp-inspector.sh"),
}

CLIENTS = sorted(CLIENT_DEFAULTS)
SECRET_MODES = ["placeholder", "plaintext"]
LOCAL_PROXY_MCP_SERVERS_JSON_CLIENTS = {
    "antigravity",
    "claude-code",
    "claude-desktop",
    "cursor",
    "gemini-cli",
}
LOCAL_PROXY_VSCODE_JSON_CLIENTS = {"vscode"}
LOCAL_PROXY_JSON_CLIENTS = (
    LOCAL_PROXY_MCP_SERVERS_JSON_CLIENTS | LOCAL_PROXY_VSCODE_JSON_CLIENTS
)
DEFAULT_LOCAL_PROXY_CONFIG_DIR = Path.home() / ".lightnow" / "lightnow-proxy"
LOCAL_LIGHTNOW_CA_RELATIVE_PATH = Path(".local-runtime/certs/lightnow-local-ca.crt")
LOCAL_PROXY_EXECUTABLE = "lightnow-proxy"
LEGACY_LOCAL_PROXY_EXECUTABLE = "mcp-proxy"
LIGHTNOW_PROXY_ALIASES = {"lightnow", "LightNow"}
CLIENT_INTERNAL_MCP_SERVERS = {
    "codex": {"node_repl"},
}
VSCODE_VIRTUAL_TOOLS_THRESHOLD_SETTING = "github.copilot.chat.virtualTools.threshold"
VSCODE_VIRTUAL_TOOLS_THRESHOLD = 128
LOCAL_PROXY_STDIO_SUPPORT_MESSAGE = (
    "Local Proxy Mode stdio currently supports Codex TOML, "
    "Antigravity JSON, Claude Code JSON, Claude Desktop JSON, "
    "Cursor JSON, Gemini CLI JSON, and VS Code JSON only."
)
LOCAL_PROXY_HTTP_SUPPORT_MESSAGE = (
    "Local Proxy Mode HTTP currently supports Codex TOML only."
)
MCP_PROXY_INSTALL_HINT = (
    "Install the LightNow Proxy and make sure `lightnow-proxy` resolves on PATH, "
    "then re-run the sync so the client config can pin its full path."
)


def default_local_proxy_config_path(client: str) -> Path:
    """Return the per-client Local Proxy config path used by sync."""
    safe_client = re.sub(r"[^A-Za-z0-9_.-]+", "-", client).strip("-") or "client"
    return DEFAULT_LOCAL_PROXY_CONFIG_DIR / f"{safe_client}.yaml"


def default_local_proxy_profile_config_path(profile: str = "default") -> Path:
    """Return the profile-level Local Proxy config path used by proxy defaults."""
    safe_profile = re.sub(r"[^A-Za-z0-9_.-]+", "-", profile).strip("-") or "default"
    return DEFAULT_LOCAL_PROXY_CONFIG_DIR / f"{safe_profile}.yaml"


def local_proxy_default_config_alias(
    *,
    profile: str,
    explicit_config_path: Optional[Path],
    proxy_target: Path,
) -> Optional[Path]:
    """Return the default health-check alias path that should mirror a sync."""
    if explicit_config_path is not None or profile != "default":
        return None
    alias = default_local_proxy_profile_config_path(profile).expanduser()
    if alias == proxy_target.expanduser():
        return None
    return alias


def discover_local_lightnow_ca_file(registry_api_url: str) -> Optional[Path]:
    """Find the local LightNow CA for local development Registry API URLs."""
    parsed = urlparse(registry_api_url)
    if parsed.hostname is None or not parsed.hostname.endswith(".lightnow.local"):
        return None

    for env_name in ("LIGHTNOW_REGISTRY_CA_FILE", "NODE_EXTRA_CA_CERTS"):
        value = os.environ.get(env_name)
        if value:
            candidate = Path(value).expanduser()
            if candidate.exists():
                return candidate

    for base in [Path.cwd(), *Path.cwd().parents]:
        candidate = base / LOCAL_LIGHTNOW_CA_RELATIVE_PATH
        if candidate.exists():
            return candidate

    return None


def local_proxy_support_error(
    client: str, export_format: str, local_proxy_transport: str
) -> Optional[str]:
    """Return why a client/format/transport combination lacks Local Proxy support."""
    if local_proxy_transport == "stdio":
        if client in LOCAL_PROXY_JSON_CLIENTS and export_format == "json":
            return None
        if client == "codex" and export_format == "toml":
            return None
        return LOCAL_PROXY_STDIO_SUPPORT_MESSAGE
    if local_proxy_transport == "http":
        if client == "codex" and export_format == "toml":
            return None
        return LOCAL_PROXY_HTTP_SUPPORT_MESSAGE
    return "Local Proxy transport must be stdio or http."


def lightnow_proxy_available() -> bool:
    """Return whether the LightNow Proxy executable resolves on PATH."""
    return shutil.which(LOCAL_PROXY_EXECUTABLE) is not None


def command_available(command: str) -> bool:
    """Return whether a configured executable command can be started."""
    if command == "":
        return False
    expanded = Path(command).expanduser()
    if expanded.is_absolute() or "/" in command or "\\" in command:
        return expanded.is_file() and os.access(expanded, os.X_OK)
    return shutil.which(command) is not None


def backup_path_for(path: Path) -> Path:
    """Return the backup path secure_write_text keeps next to a client config."""
    return path.with_suffix(path.suffix + ".lightnow.bak")


def direct_server_aliases(content: str, export_format: str) -> list[str]:
    """Return non-LightNow MCP server aliases found in a client config."""
    if export_format not in {"json", "toml"}:
        return []
    try:
        entries = extract_mcp_entries(content, export_format)
    except ValueError:
        return []
    return sorted(alias for alias in entries if alias not in LIGHTNOW_PROXY_ALIASES)


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
    from_settings: Annotated[
        bool,
        typer.Option(
            "--from-settings",
            help="Use LightNow Config policy to choose direct config or Local Proxy mode.",
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
    registry_ca_file: Annotated[
        Optional[Path],
        typer.Option(
            "--registry-ca-file",
            help="CA bundle used by the Local Proxy for LightNow Registry/Auth TLS.",
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
    if from_settings and (runner or local_proxy):
        raise_bad_argument(
            "Unsupported sync mode",
            "Use --from-settings without --runner or --local-proxy.",
        )
    if local_proxy_transport not in {"stdio", "http"}:
        raise_bad_argument("Unsupported Local Proxy transport", "Use stdio or http.")

    default_format, default_path = CLIENT_DEFAULTS[client]
    export_format = format_ or default_format
    target = (config_path or default_path).expanduser()
    if local_proxy:
        support_error = local_proxy_support_error(
            client, export_format, local_proxy_transport
        )
        if support_error:
            raise_bad_argument("Unsupported Local Proxy client", support_error)
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
        local_proxy_config_path or default_local_proxy_config_path(client)
    ).expanduser()
    proxy_default_alias: Optional[Path] = None
    settings_local_proxy_summary: dict[str, Any] = {}
    removed_direct_servers: list[str] = []
    remove_unmanaged_client_servers = local_proxy
    policy_managed_sync = False

    try:
        if from_settings:
            settings_payload = fetch_integration_settings(
                api_url=registry_api_url,
                token=bearer_token,
                tenant=effective_tenant,
            )
            local_proxy_settings = settings_payload.get("localProxy")
            if not isinstance(local_proxy_settings, dict):
                local_proxy_settings = {}
            settings_local_proxy_summary = local_proxy_settings
            managed_clients = local_proxy_settings.get("managedClients")
            client_is_managed = (
                not isinstance(managed_clients, list)
                or len(managed_clients) == 0
                or client in managed_clients
            )
            if local_proxy_settings.get("enabled") is True and client_is_managed:
                local_proxy = True
                policy_managed_sync = True
                remove_unmanaged_client_servers = (
                    local_proxy_settings.get("allowUnmanagedClientServers") is False
                )
                configured_profile = local_proxy_settings.get("profile")
                if isinstance(configured_profile, str) and configured_profile:
                    profile = configured_profile
            elif isinstance(settings_payload.get("defaultProfile"), str):
                profile = str(settings_payload["defaultProfile"])

        if local_proxy:
            proxy_default_alias = local_proxy_default_config_alias(
                profile=profile,
                explicit_config_path=local_proxy_config_path,
                proxy_target=proxy_target,
            )
            generated = build_local_proxy_export(
                client=client,
                export_format=export_format,
                local_proxy_url=local_proxy_url,
                local_proxy_transport=local_proxy_transport,
                local_proxy_config_path=proxy_target,
            )
            proxy_config = build_local_proxy_config(
                local_proxy_url=local_proxy_url,
                local_proxy_transport=local_proxy_transport,
                profile=profile,
                client=client,
                registry_api_url=registry_api_url,
                tenant=effective_tenant,
                registry_ca_file=registry_ca_file,
                local_proxy_settings=(
                    settings_local_proxy_summary if from_settings else None
                ),
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
        if local_proxy and remove_unmanaged_client_servers:
            removed_direct_servers = direct_server_aliases(existing, export_format)
        if (
            local_proxy
            and remove_unmanaged_client_servers
            and client == "codex"
            and export_format == "toml"
        ):
            existing = prepare_codex_local_proxy_config(existing)
        if (
            local_proxy
            and remove_unmanaged_client_servers
            and client in LOCAL_PROXY_JSON_CLIENTS
            and export_format == "json"
        ):
            existing = prepare_json_local_proxy_config(existing)
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
    except json.JSONDecodeError as exc:
        console.print(
            f"[bold red]Integration sync failed:[/bold red] {target} is not valid JSON ({exc})."
        )
        console.print(
            "Fix the file or restore the .lightnow.bak backup next to it, then re-run the sync."
        )
        raise typer.Exit(1) from exc
    except ValueError as exc:
        console.print(f"[bold red]Integration sync failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    if dry_run:
        typer.echo(redact(patched))
        err_console.print("[cyan]Dry run: nothing was written.[/cyan]")
        err_console.print(f"[cyan]Would write client config: {target}[/cyan]")
        if local_proxy:
            err_console.print(
                f"[cyan]Would write Local Proxy config: {proxy_target}[/cyan]"
            )
            if proxy_default_alias is not None:
                err_console.print(
                    f"[cyan]Would update default Local Proxy config: {proxy_default_alias}[/cyan]"
                )
            if removed_direct_servers:
                err_console.print(
                    "[yellow]Would remove direct MCP server entries:[/yellow] "
                    f"{', '.join(removed_direct_servers)}"
                )
            if not lightnow_proxy_available():
                err_console.print(
                    f"[yellow]lightnow-proxy was not found on PATH.[/yellow] {MCP_PROXY_INSTALL_HINT}"
                )
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

    if local_proxy and removed_direct_servers and not yes and not policy_managed_sync:
        console.print(
            f"[yellow]Local Proxy Mode removes direct MCP server entries from {target}:[/yellow] "
            f"{', '.join(removed_direct_servers)}"
        )
        console.print(
            f"A backup of the current file is kept at {backup_path_for(target)}."
        )
        confirmed = typer.confirm(
            "Replace these entries with one LightNow Local Proxy entry?",
            default=True,
        )
        if not confirmed:
            console.print("[yellow]Canceled. No files were changed.[/yellow]")
            raise typer.Exit(1)

    if local_proxy:
        secure_write_text(proxy_target, proxy_config)
        console.print(f"[green]Wrote Local Proxy config to {proxy_target}[/green]")
        if proxy_default_alias is not None:
            secure_write_text(proxy_default_alias, proxy_config)
            console.print(
                f"[green]Updated default Local Proxy config at {proxy_default_alias}[/green]"
            )
        if client == "claude-code":
            warm_local_proxy_tools_cache(proxy_target)

    secure_write_text(target, patched, executable=export_format == "shell")
    if export_format == "json":
        write_json_manifest(manifest, extract_json_managed(generated))
    if local_proxy and client == "vscode":
        report_vscode_virtual_tools(target.with_name("settings.json"))

    console.print(f"[green]Synced {client} profile {profile} to {target}[/green]")
    if local_proxy:
        if removed_direct_servers:
            console.print(
                "[yellow]Removed direct MCP server entries:[/yellow] "
                f"{', '.join(removed_direct_servers)} "
                f"(backup: {backup_path_for(target)})"
            )
        status = analyze_client_config_content(
            client=client,
            export_format=export_format,
            content=patched,
            expected_proxy_config_path=proxy_target,
        )
        augment_local_proxy_posture(status)
        print_config_status(status, target)
        if from_settings:
            if settings_local_proxy_summary.get("policyMode") == "enforce":
                console.print(
                    "[cyan]LightNow policy is enforce: managed clients should keep only the Local Proxy MCP entry.[/cyan]"
                )
            if settings_local_proxy_summary.get("allowUnmanagedClientServers") is False:
                console.print(
                    "[cyan]LightNow policy blocks unmanaged MCP servers for this client config.[/cyan]"
                )
        console.print(
            f"[cyan]Next: restart {client} so it picks up the LightNow MCP entry.[/cyan]"
        )


def config_status(
    client: Annotated[str, typer.Option("--client", help="Target MCP client")],
    config_path: Annotated[
        Optional[Path],
        typer.Option("--config-path", help="Client config file to inspect"),
    ] = None,
    format_: Annotated[
        Optional[str],
        typer.Option("--format", help="Config format; defaults to the client format"),
    ] = None,
    local_proxy_config_path: Annotated[
        Optional[Path],
        typer.Option(
            "--local-proxy-config-path",
            help="Expected LightNow Local Proxy config path.",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable posture JSON"),
    ] = False,
) -> None:
    """Inspect whether a client config is managed through LightNow Local Proxy."""
    if client not in CLIENT_DEFAULTS:
        raise_bad_argument("Unsupported client", f"Use one of: {', '.join(CLIENTS)}")
    default_format, default_path = CLIENT_DEFAULTS[client]
    export_format = format_ or default_format
    target = (config_path or default_path).expanduser()
    proxy_target = (
        local_proxy_config_path or default_local_proxy_config_path(client)
    ).expanduser()

    status = analyze_client_config_file(
        client=client,
        export_format=export_format,
        path=target,
        expected_proxy_config_path=proxy_target,
    )
    augment_local_proxy_posture(status)
    if json_output:
        typer.echo(json.dumps(status, indent=2, sort_keys=True))
        return
    print_config_status(status, target)


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
    support_error = local_proxy_support_error(
        client, export_format, local_proxy_transport
    )
    if support_error:
        raise ValueError(support_error)
    if local_proxy_transport == "stdio":
        if client in LOCAL_PROXY_MCP_SERVERS_JSON_CLIENTS and export_format == "json":
            return render_local_proxy_mcp_servers_json(
                local_proxy_config_path or default_local_proxy_config_path(client)
            )
        if client in LOCAL_PROXY_VSCODE_JSON_CLIENTS and export_format == "json":
            return render_local_proxy_vscode_json(
                local_proxy_config_path or default_local_proxy_config_path(client)
            )
        return render_local_proxy_codex_stdio_toml(
            local_proxy_config_path or default_local_proxy_config_path(client)
        )
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


def local_proxy_command() -> str:
    """Return the command path most likely to work from desktop MCP clients."""
    resolved = shutil.which(LOCAL_PROXY_EXECUTABLE)
    return resolved or LOCAL_PROXY_EXECUTABLE


def warm_local_proxy_tools_cache(local_proxy_config_path: Path) -> None:
    """Warm proxy tool metadata for clients with short MCP health-check timeouts."""
    command = local_proxy_command()
    try:
        result = subprocess.run(
            [
                command,
                "--config",
                str(local_proxy_config_path.expanduser()),
                "--transport",
                "stdio",
                "--warm-tools-cache",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        console.print(
            "[yellow]Could not warm the Claude Code tools cache: lightnow-proxy is not on PATH.[/yellow] "
            f"{MCP_PROXY_INSTALL_HINT}"
        )
        return
    except subprocess.TimeoutExpired:
        console.print(
            "[yellow]Warming the Claude Code tools cache timed out after 60s.[/yellow] "
            "Claude Code will load tools on its first start instead."
        )
        return
    except Exception as exc:
        console.print(f"[yellow]Could not warm Local Proxy tools cache:[/yellow] {exc}")
        return

    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip().splitlines()
        reason = details[-1] if details else f"exit code {result.returncode}"
        console.print(
            f"[yellow]Could not warm Local Proxy tools cache:[/yellow] {reason}"
        )
        console.print(
            "Claude Code will load tools on its first start instead; "
            "check `lightnow status` if the LightNow entry stays unavailable."
        )
        return

    summary = result.stdout.strip().splitlines()
    if summary:
        console.print(f"[green]Claude Code tools cache:[/green] {summary[-1]}")


def render_local_proxy_codex_stdio_toml(local_proxy_config_path: Path) -> str:
    """Render Codex TOML that starts the local LightNow MCP proxy over stdio."""
    return (
        "# Generated by LightNow. Codex starts the local LightNow MCP proxy.\n"
        "[mcp_servers.lightnow]\n"
        f'command = "{LOCAL_PROXY_EXECUTABLE}"\n'
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


def render_local_proxy_mcp_servers_json(local_proxy_config_path: Path) -> str:
    """Render JSON mcpServers config that starts the local LightNow MCP proxy."""
    payload = {
        "mcpServers": {
            "LightNow": {
                "command": local_proxy_command(),
                "args": [
                    "--config",
                    str(local_proxy_config_path.expanduser()),
                    "--transport",
                    "stdio",
                ],
            }
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def render_local_proxy_vscode_json(local_proxy_config_path: Path) -> str:
    """Render VS Code MCP config that starts the local LightNow MCP proxy."""
    payload = {
        "servers": {
            "LightNow": {
                "type": "stdio",
                "command": local_proxy_command(),
                "args": [
                    "--config",
                    str(local_proxy_config_path.expanduser()),
                    "--transport",
                    "stdio",
                ],
            }
        }
    }
    return json.dumps(payload, indent=2) + "\n"


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
    local_proxy_transport: str = "stdio",
    client: str = "codex",
    registry_api_url: str,
    tenant: Optional[str],
    registry_ca_file: Optional[Path] = None,
    local_proxy_settings: Optional[dict[str, Any]] = None,
) -> str:
    """Render the local LightNow Proxy config for LightNow-managed profile sync."""
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
    resolved_registry_ca_file = registry_ca_file or discover_local_lightnow_ca_file(
        registry_api_url
    )
    if resolved_registry_ca_file is not None:
        registry_api["ca_file"] = str(resolved_registry_ca_file.expanduser())

    local_proxy_config: dict[str, Any] = {
        "enabled": True,
        "profile": profile,
        "path": parsed.path or "/mcp",
        "sync_from_lightnow": True,
        "client_name": client,
        "client_version": None,
        "runner_name": "lightnow-local-proxy",
        "client_transport": (
            "streamable-http" if local_proxy_transport == "http" else "stdio"
        ),
    }
    if isinstance(local_proxy_settings, dict):
        local_proxy_config["telemetry_enabled"] = (
            local_proxy_settings.get("telemetryEnabled") is not False
        )
        local_proxy_config["allow_unmanaged_client_servers"] = (
            local_proxy_settings.get("allowUnmanagedClientServers") is not False
        )
        local_proxy_config["policy_mode"] = (
            "enforce"
            if local_proxy_settings.get("policyMode") == "enforce"
            else "observe"
        )

    payload = {
        "server": {
            "host": parsed.hostname,
            "port": port,
            "public_url": f"{parsed.scheme}://{parsed.hostname}:{port}",
        },
        "local_proxy": local_proxy_config,
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
        "antigravity",
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


def fetch_integration_settings(
    *,
    api_url: str,
    token: str,
    tenant: Optional[str],
) -> dict[str, Any]:
    """Fetch LightNow integration settings for the current user or tenant."""
    level = "tenant" if tenant else "user"
    url = f"{api_url.rstrip('/')}/settings"
    try:
        response = request_with_refresh(
            "GET",
            url,
            params={"level": level},
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
    settings = payload.get("settings") if isinstance(payload, dict) else None
    integrations = settings.get("integrations") if isinstance(settings, dict) else None
    if not isinstance(integrations, dict):
        raise ValueError("Registry API response did not include integration settings.")
    return integrations


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


def analyze_client_config_file(
    *,
    client: str,
    export_format: str,
    path: Path,
    expected_proxy_config_path: Path,
) -> dict[str, Any]:
    """Inspect a client config file without exposing secrets or payload values."""
    if not path.exists():
        return {
            "client": client,
            "format": export_format,
            "status": "missing",
            "path": str(path),
            "expected_proxy_config_path": str(expected_proxy_config_path.expanduser()),
            "local_proxy_present": False,
            "unmanaged_servers": [],
            "legacy_runner_servers": [],
            "warnings": ["config_missing"],
        }
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "client": client,
            "format": export_format,
            "status": "unreadable",
            "path": str(path),
            "expected_proxy_config_path": str(expected_proxy_config_path.expanduser()),
            "local_proxy_present": False,
            "unmanaged_servers": [],
            "legacy_runner_servers": [],
            "warnings": [f"read_failed:{exc.__class__.__name__}"],
        }
    return analyze_client_config_content(
        client=client,
        export_format=export_format,
        content=content,
        expected_proxy_config_path=expected_proxy_config_path,
        path=path,
    )


def analyze_client_config_content(
    *,
    client: str,
    export_format: str,
    content: str,
    expected_proxy_config_path: Path,
    path: Optional[Path] = None,
) -> dict[str, Any]:
    """Classify LightNow Local Proxy posture for supported client config shapes."""
    try:
        entries = extract_mcp_entries(content, export_format)
    except ValueError as exc:
        return {
            "client": client,
            "format": export_format,
            "status": "invalid",
            "path": str(path) if path else None,
            "expected_proxy_config_path": str(expected_proxy_config_path.expanduser()),
            "local_proxy_present": False,
            "unmanaged_servers": [],
            "legacy_runner_servers": [],
            "warnings": [str(exc)],
        }

    local_proxy_aliases: list[str] = []
    local_proxy_commands: list[str] = []
    legacy_runner_servers: list[str] = []
    internal_servers: list[str] = []
    unmanaged_servers: list[str] = []
    proxy_config_path = str(expected_proxy_config_path.expanduser())
    warnings: list[str] = []
    client_internal_servers = CLIENT_INTERNAL_MCP_SERVERS.get(client, set())
    for alias, entry in entries.items():
        raw_command = entry.get("command")
        command: str = raw_command if isinstance(raw_command, str) else ""
        raw_args = entry.get("args")
        args: list[Any] = raw_args if isinstance(raw_args, list) else []
        raw_url = entry.get("url")
        url: str = raw_url if isinstance(raw_url, str) else ""
        is_proxy_alias = alias in LIGHTNOW_PROXY_ALIASES
        is_lightnow_proxy = (
            command_looks_like(command, LOCAL_PROXY_EXECUTABLE)
            or command_looks_like(command, LEGACY_LOCAL_PROXY_EXECUTABLE)
            or is_local_proxy_url(url)
        )
        is_lightnow_runner = command_looks_like(command, "lightnow") and "run" in [
            str(arg) for arg in args
        ]

        if is_proxy_alias and is_lightnow_proxy:
            local_proxy_aliases.append(alias)
            if command:
                local_proxy_commands.append(command)
            if command_looks_like(command, LEGACY_LOCAL_PROXY_EXECUTABLE):
                warnings.append("legacy_mcp_proxy_command")
            if proxy_config_path not in [str(arg) for arg in args] and command:
                warnings.append("proxy_config_path_mismatch")
            continue
        if is_lightnow_runner:
            legacy_runner_servers.append(alias)
            continue
        if alias in client_internal_servers:
            internal_servers.append(alias)
            continue
        unmanaged_servers.append(alias)

    if local_proxy_aliases and not unmanaged_servers and not legacy_runner_servers:
        status = "managed"
    elif local_proxy_aliases:
        status = "mixed"
    elif legacy_runner_servers and not unmanaged_servers:
        status = "legacy_runner"
    elif entries:
        status = "unmanaged"
    else:
        status = "empty"

    return {
        "client": client,
        "format": export_format,
        "status": status,
        "path": str(path) if path else None,
        "expected_proxy_config_path": proxy_config_path,
        "local_proxy_present": bool(local_proxy_aliases),
        "local_proxy_aliases": sorted(local_proxy_aliases),
        "local_proxy_commands": sorted(set(local_proxy_commands)),
        "internal_servers": sorted(internal_servers),
        "unmanaged_servers": sorted(unmanaged_servers),
        "legacy_runner_servers": sorted(legacy_runner_servers),
        "warnings": sorted(set(warnings)),
    }


def augment_local_proxy_posture(status: dict[str, Any]) -> dict[str, Any]:
    """Add Local Proxy runtime checks (config file, lightnow-proxy binary) to a posture."""
    warnings = [str(item) for item in status.get("warnings", [])]
    expected_path = status.get("expected_proxy_config_path")
    proxy_config_path = Path(str(expected_path)).expanduser() if expected_path else None
    proxy_config_exists = False
    if proxy_config_path is not None:
        proxy_config_exists = proxy_config_path.exists()
    proxy_commands = [
        str(item) for item in status.get("local_proxy_commands", []) if str(item)
    ]
    proxy_command_available = (
        any(command_available(command) for command in proxy_commands)
        if proxy_commands
        else False
    )
    status["proxy_config_exists"] = proxy_config_exists
    status["lightnow_proxy_on_path"] = lightnow_proxy_available()
    status["local_proxy_command_available"] = proxy_command_available
    if status.get("local_proxy_present"):
        if not proxy_config_exists:
            warnings.append("proxy_config_missing")
        elif proxy_config_path is not None:
            try:
                raw_proxy_config = yaml.safe_load(proxy_config_path.read_text()) or {}
            except yaml.YAMLError as exc:
                warnings.append(f"proxy_config_invalid:{exc.__class__.__name__}")
                raw_proxy_config = {}
            except OSError as exc:
                warnings.append(f"proxy_config_read_failed:{exc.__class__.__name__}")
                raw_proxy_config = {}
            if isinstance(raw_proxy_config, dict):
                local_proxy = raw_proxy_config.get("local_proxy")
                if isinstance(local_proxy, dict):
                    client_name = local_proxy.get("client_name")
                    status["local_proxy_profile"] = local_proxy.get("profile")
                    status["local_proxy_client_name"] = client_name
                    status["local_proxy_client_transport"] = local_proxy.get(
                        "client_transport"
                    )
                    status["local_proxy_telemetry_enabled"] = local_proxy.get(
                        "telemetry_enabled"
                    )
                    status["local_proxy_policy_mode"] = local_proxy.get("policy_mode")
                    status["local_proxy_allow_unmanaged_client_servers"] = (
                        local_proxy.get("allow_unmanaged_client_servers")
                    )
                    if client_name and str(client_name) != str(status.get("client")):
                        warnings.append("proxy_config_client_mismatch")
                    if (
                        status.get("status") == "mixed"
                        and local_proxy.get("policy_mode") == "enforce"
                        and local_proxy.get("allow_unmanaged_client_servers") is False
                    ):
                        warnings.append("policy_blocks_unmanaged_servers")
        if not proxy_command_available:
            if proxy_commands and all(
                "/" in command or "\\" in command for command in proxy_commands
            ):
                warnings.append("lightnow_proxy_command_missing")
            else:
                warnings.append("lightnow_proxy_not_on_path")
    status["warnings"] = sorted(set(warnings))
    return status


def extract_mcp_entries(content: str, export_format: str) -> dict[str, dict[str, Any]]:
    """Return MCP server entries from known client config formats."""
    if not content.strip():
        return {}
    if export_format == "toml":
        try:
            payload = tomllib.loads(content)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid_toml:{exc.__class__.__name__}") from exc
        servers = payload.get("mcp_servers") if isinstance(payload, dict) else None
        if servers is None:
            return {}
        if not isinstance(servers, dict):
            raise ValueError("invalid_toml:mcp_servers_not_object")
        return {
            str(alias): entry
            for alias, entry in servers.items()
            if isinstance(entry, dict)
        }
    if export_format == "json":
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid_json:{exc.__class__.__name__}") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid_json:root_not_object")
        entries: dict[str, dict[str, Any]] = {}
        for key in ("mcpServers", "servers"):
            section = payload.get(key)
            if section is None:
                continue
            if not isinstance(section, dict):
                raise ValueError(f"invalid_json:{key}_not_object")
            for alias, entry in section.items():
                if isinstance(entry, dict):
                    entries[str(alias)] = entry
        return entries
    raise ValueError(f"unsupported_format:{export_format}")


def command_looks_like(command: str, executable: str) -> bool:
    """Match a command by executable name while allowing absolute paths."""
    if command == executable:
        return True
    normalized = command.replace("\\", "/").rstrip("/")
    return normalized.endswith(f"/{executable}")


def is_local_proxy_url(value: str) -> bool:
    """Return whether a URL points to an explicitly local proxy endpoint."""
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def warning_hint(code: str, client: str) -> Optional[str]:
    """Return a short recovery hint for a posture warning code."""
    sync_command = f"lightnow sync --client {client} --local-proxy"
    if code.startswith("invalid_json") or code.startswith("invalid_toml"):
        return (
            "the config file could not be parsed; fix it or restore the "
            ".lightnow.bak backup, then re-run the sync."
        )
    if code.startswith("read_failed"):
        return "the config file could not be read; check its file permissions."
    hints = {
        "proxy_config_missing": (
            f"the Local Proxy config file is missing; re-run `{sync_command}` to recreate it."
        ),
        "lightnow_proxy_not_on_path": (
            f"lightnow-proxy is not on PATH; {client} cannot start the LightNow entry. "
            + MCP_PROXY_INSTALL_HINT
        ),
        "lightnow_proxy_command_missing": (
            "the configured lightnow-proxy executable does not exist or is not executable; "
            f"re-run `{sync_command}` after installing the LightNow Proxy."
        ),
        "legacy_mcp_proxy_command": (
            "the LightNow entry still uses the legacy `mcp-proxy` command; "
            f"re-run `{sync_command}` to switch it to `lightnow-proxy`."
        ),
        "proxy_config_path_mismatch": (
            "the LightNow entry points at a different Local Proxy config; "
            f"re-run `{sync_command}` to update it."
        ),
        "proxy_config_client_mismatch": (
            "the Local Proxy config belongs to a different client; "
            f"re-run `{sync_command}` for this client."
        ),
        "policy_blocks_unmanaged_servers": (
            "LightNow policy is set to enforce and unmanaged MCP servers are still present; "
            f"re-run `{sync_command}` or remove the unmanaged entries."
        ),
    }
    return hints.get(code)


def config_status_next_step(status: dict[str, Any]) -> Optional[str]:
    """Return the single most useful next action for a config posture."""
    client = status.get("client")
    state = str(status.get("status", "unknown"))
    sync_command = f"lightnow sync --client {client} --local-proxy"
    if state in {"missing", "empty", "unmanaged"}:
        return f"Run `{sync_command}` to manage this client through the LightNow Local Proxy."
    if state == "legacy_runner":
        return f"Run `{sync_command}` to migrate the legacy runner entries to Local Proxy Mode."
    if state == "mixed":
        return (
            "Unmanaged servers bypass LightNow. Remove them from the client config "
            f"or import them into LightNow, then run `{sync_command}`."
        )
    if state == "invalid":
        return (
            "Fix the config file (or restore the .lightnow.bak backup), "
            f"then run `{sync_command}`."
        )
    if state == "unreadable":
        return "Fix the file permissions, then re-run `lightnow config-status`."
    return None


def print_config_status(status: dict[str, Any], path: Path) -> None:
    """Print a concise, non-secret client config posture summary."""
    state = status.get("status", "unknown")
    color = {
        "managed": "green",
        "mixed": "yellow",
        "legacy_runner": "yellow",
        "unmanaged": "red",
        "invalid": "red",
        "missing": "yellow",
        "empty": "yellow",
    }.get(str(state), "yellow")
    console.print(
        f"[{color}]Client config posture: {state}[/{color}] "
        f"({status.get('client')} at {path})"
    )
    aliases = status.get("local_proxy_aliases")
    if isinstance(aliases, list) and aliases:
        console.print(f"LightNow Local Proxy entry: {', '.join(map(str, aliases))}")
    expected_path = status.get("expected_proxy_config_path")
    if expected_path:
        exists = status.get("proxy_config_exists")
        suffix = "" if exists is None else (" (present)" if exists else " (missing)")
        console.print(f"Local Proxy config: {expected_path}{suffix}")
    proxy_profile = status.get("local_proxy_profile")
    if proxy_profile:
        policy_mode = status.get("local_proxy_policy_mode") or "observe"
        allow_unmanaged = status.get("local_proxy_allow_unmanaged_client_servers")
        unmanaged_label = "allowed" if allow_unmanaged is not False else "blocked"
        console.print(
            "LightNow policy: "
            f"profile={proxy_profile}, mode={policy_mode}, unmanaged={unmanaged_label}"
        )
    unmanaged = status.get("unmanaged_servers")
    if isinstance(unmanaged, list) and unmanaged:
        console.print(
            "[yellow]Unmanaged MCP servers:[/yellow] "
            f"{', '.join(map(str, unmanaged))}"
        )
    legacy = status.get("legacy_runner_servers")
    if isinstance(legacy, list) and legacy:
        console.print(
            "[yellow]Legacy LightNow runner entries:[/yellow] "
            f"{', '.join(map(str, legacy))}"
        )
    warnings = status.get("warnings")
    client = str(status.get("client", ""))
    if isinstance(warnings, list) and warnings:
        console.print(f"[yellow]Warnings:[/yellow] {', '.join(map(str, warnings))}")
        for code in warnings:
            hint = warning_hint(str(code), client)
            if hint:
                console.print(f"  [yellow]{code}:[/yellow] {hint}")
    next_step = config_status_next_step(status)
    if next_step:
        console.print(f"[cyan]Next:[/cyan] {next_step}")


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


TOML_TABLE_RE = re.compile(r"^\s*\[+\s*([^\]]+?)\s*\]+\s*(?:#.*)?$")


def prepare_codex_local_proxy_config(existing: str) -> str:
    """Remove direct Codex MCP server entries before writing Local Proxy Mode."""
    without_managed = remove_managed_block(existing)
    return strip_codex_mcp_server_tables(without_managed)


def prepare_json_local_proxy_config(existing: str) -> str:
    """Remove direct JSON MCP server entries before writing Local Proxy Mode."""
    if not existing.strip():
        return existing
    current = json.loads(existing)
    if not isinstance(current, dict):
        raise ValueError("LightNow can only sync JSON object client configs.")
    current.pop("mcpServers", None)
    current.pop("servers", None)
    return json.dumps(current, indent=2, ensure_ascii=False) + "\n"


def configure_vscode_virtual_tools(settings_path: Path) -> str:
    """Enable VS Code's virtual tools mode for large MCP tool sets."""
    existing = settings_path.read_text() if settings_path.exists() else ""
    settings = json.loads(existing) if existing.strip() else {}
    if not isinstance(settings, dict):
        raise ValueError("VS Code settings must be a JSON object.")
    current_value = settings.get(VSCODE_VIRTUAL_TOOLS_THRESHOLD_SETTING)
    if current_value == VSCODE_VIRTUAL_TOOLS_THRESHOLD:
        return "unchanged"
    settings[VSCODE_VIRTUAL_TOOLS_THRESHOLD_SETTING] = VSCODE_VIRTUAL_TOOLS_THRESHOLD
    secure_write_text(
        settings_path,
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
    )
    return "updated"


def report_vscode_virtual_tools(settings_path: Path) -> None:
    """Apply the VS Code virtual tools setting without failing the whole sync."""
    try:
        state = configure_vscode_virtual_tools(settings_path)
    except (OSError, ValueError) as exc:
        console.print(
            f"[yellow]Could not update VS Code settings at {settings_path}:[/yellow] {exc}"
        )
        console.print(
            f'Set "{VSCODE_VIRTUAL_TOOLS_THRESHOLD_SETTING}": '
            f"{VSCODE_VIRTUAL_TOOLS_THRESHOLD} there manually so all LightNow tools stay usable."
        )
        return
    if state == "updated":
        console.print(
            "[cyan]Enabled VS Code virtual tools "
            f'("{VSCODE_VIRTUAL_TOOLS_THRESHOLD_SETTING}": {VSCODE_VIRTUAL_TOOLS_THRESHOLD}) '
            "so large LightNow tool sets stay usable.[/cyan]"
        )


def remove_managed_block(existing: str) -> str:
    """Remove an existing LightNow managed block from a text config."""
    if BEGIN not in existing or END not in existing:
        return existing
    before, rest = existing.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    return before.rstrip() + "\n\n" + after.lstrip()


def strip_codex_mcp_server_tables(existing: str) -> str:
    """Strip Codex [mcp_servers.*] TOML tables while preserving other config."""
    kept: list[str] = []
    skipping = False
    for line in existing.splitlines():
        match = TOML_TABLE_RE.match(line)
        if match:
            name = match.group(1).strip()
            skipping = name == "mcp_servers" or name.startswith("mcp_servers.")
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip() + ("\n" if kept else "")


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
    for marker in (
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "PWD",
        "KEY",
        "AUTHORIZATION",
        "BEARER",
        "CREDENTIAL",
        "COOKIE",
    ):
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
