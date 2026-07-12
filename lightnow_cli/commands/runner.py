"""Local MCP runner commands."""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse

import httpx
import typer
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

console = Console(stderr=True)


def run(
    server: Annotated[
        str,
        typer.Option(
            "--server",
            help="MCP server alias from the selected LightNow runtime profile.",
        ),
    ],
    profile: Annotated[
        str,
        typer.Option("--profile", help="LightNow runtime profile."),
    ] = "default",
    tenant: Annotated[
        Optional[str],
        typer.Option("--tenant", help="Tenant id or slug, sent as X-Tenant."),
    ] = None,
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="Registry API base URL."),
    ] = None,
    transport: Annotated[
        str,
        typer.Option("--transport", help="Requested runtime transport."),
    ] = "stdio",
) -> None:
    """Run one MCP server through LightNow and inject secrets into the child process."""
    if transport != "stdio":
        console.print("[bold red]Runner failed:[/bold red] only stdio is supported.")
        raise typer.Exit(2)

    try:
        token = require_access_token()
        registry_api_url = api_url or config_manager.load_config().registry_api_url
        effective_tenant = config_manager.effective_tenant(tenant)
        if not registry_api_url:
            raise ValueError(
                "Registry API URL required. Configure the CLI or pass --api-url."
            )
        selected = resolve_profile_server(
            api_url=registry_api_url,
            token=token,
            tenant=effective_tenant,
            profile=profile,
            server=server,
        )
        context = fetch_runtime_context(
            api_url=registry_api_url,
            token=token,
            tenant=effective_tenant,
            profile=profile,
            server_name=str(selected["server_name"]),
            version=str(selected["version"]),
            transport=transport,
        )
        resolve_external_secret_bindings(context)
        assert_runtime_context_ready(context, server=server, profile=profile)
        command, args, env, cwd = launch_config_from_context(context)
    except AccessTokenExpired:
        console.print(f"[red]{ACCESS_TOKEN_EXPIRED_MESSAGE}[/red]")
        raise typer.Exit(1)
    except (AuthError, ValueError) as exc:
        console.print(f"[bold red]Runner failed:[/bold red] {redact(str(exc))}")
        raise typer.Exit(1) from exc

    process_env = os.environ.copy()
    process_env.update(env)
    try:
        completed = subprocess.run(
            [command, *args],
            cwd=str(Path(cwd).expanduser()) if cwd else None,
            env=process_env,
            stdin=sys.stdin.buffer,
            stdout=sys.stdout.buffer,
            stderr=sys.stderr.buffer,
            check=False,
        )
    except FileNotFoundError as exc:
        console.print(
            f"[bold red]Runner failed:[/bold red] command not found: {command}"
        )
        raise typer.Exit(127) from exc

    if completed.returncode != 0:
        console.print(
            runner_failure_summary(
                server=server,
                profile=profile,
                command=command,
                args=args,
                env=env,
                cwd=cwd,
                exit_code=completed.returncode,
            )
        )

    raise typer.Exit(completed.returncode)


def resolve_profile_server(
    *,
    api_url: str,
    token: str,
    tenant: Optional[str],
    profile: str,
    server: str,
) -> dict[str, Any]:
    """Find a registry-linked profile server by alias or registry name."""
    payload = fetch_profile_servers(
        api_url=api_url,
        token=token,
        tenant=tenant,
        profile=profile,
    )
    servers = payload.get("servers") if isinstance(payload, dict) else None
    if not isinstance(servers, list):
        raise ValueError("Registry API response did not include profile servers.")

    matches = [
        item
        for item in servers
        if isinstance(item, dict)
        and (item.get("alias") == server or item.get("server_name") == server)
    ]
    if len(matches) != 1:
        raise ValueError(f"Runtime profile does not contain server '{server}'.")

    selected = matches[0]
    if selected.get("status") != "linked":
        missing = missing_inputs_from_context(selected)
        if missing:
            raise ValueError(
                format_missing_inputs_message(
                    missing,
                    server=server,
                    profile=profile,
                )
            )
        status = selected.get("status")
        raise ValueError(
            f"Server '{server}' is not ready for the local runner: {status}"
        )
    if not isinstance(selected.get("server_name"), str) or not isinstance(
        selected.get("version"), str
    ):
        raise ValueError(
            f"Server '{server}' must be linked to a registry version before runner sync."
        )
    return selected


def assert_runtime_context_ready(
    context: dict[str, Any],
    *,
    server: str,
    profile: str,
) -> None:
    """Fail before starting a child process when LightNow knows inputs are missing."""
    missing = missing_inputs_from_context(context)
    if missing:
        raise ValueError(
            format_missing_inputs_message(missing, server=server, profile=profile)
        )


def missing_inputs_from_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize missing_inputs from Registry API payloads."""
    raw_missing = context.get("missing_inputs")
    if not isinstance(raw_missing, list):
        return []
    missing: list[dict[str, Any]] = []
    for item in raw_missing:
        if isinstance(item, dict):
            missing.append(item)
        elif isinstance(item, str) and item:
            missing.append({"name": item})
    return missing


def format_missing_inputs_message(
    missing: list[dict[str, Any]],
    *,
    server: str,
    profile: str,
) -> str:
    """Render a clear readiness error for missing runtime inputs."""
    lines = [
        f"Runtime profile '{profile}' is missing required configuration for '{server}'.",
        "Missing inputs:",
    ]
    for item in missing:
        lines.append(f"  - {format_missing_input(item)}")
    lines.extend(
        [
            "Open LightNow Integrations, select this server, set the missing values,",
            "save the profile, and run the command again.",
        ]
    )
    return "\n".join(lines)


def format_missing_input(item: dict[str, Any]) -> str:
    """Render one missing input without exposing values."""
    name = item.get("name")
    scope = item.get("scope")
    is_secret = item.get("is_secret")
    description = item.get("description")

    label = str(name) if isinstance(name, str) and name else "unnamed input"
    details: list[str] = []
    if isinstance(scope, str) and scope:
        details.append(scope.replace("_", " "))
    if is_secret is True:
        details.append("secret")
    suffix = f" ({', '.join(details)})" if details else ""
    help_text = (
        f" - {description}" if isinstance(description, str) and description else ""
    )
    return f"{label}{suffix}{help_text}"


def runner_failure_summary(
    *,
    server: str,
    profile: str,
    command: str,
    args: list[str],
    env: dict[str, str],
    cwd: Optional[str],
    exit_code: int,
) -> str:
    """Explain which LightNow-resolved process failed, without printing secrets."""
    rendered_command = " ".join([command, *args])
    env_names = ", ".join(sorted(env.keys())) if env else "none"
    cwd_line = f"\n  working directory: {cwd}" if cwd else ""
    return (
        "\n[bold red]Runner failed:[/bold red] MCP server process exited with "
        f"code {exit_code}.\n"
        f"  profile: {profile}\n"
        f"  server: {server}\n"
        f"  command: {rendered_command}\n"
        f"  environment values supplied by LightNow: {env_names}"
        f"{cwd_line}\n"
        "Check this server's configuration in LightNow Integrations and run again.\n"
        f"Use 'lightnow info {server}' to inspect the server's expected inputs."
    )


def fetch_profile_servers(
    *,
    api_url: str,
    token: str,
    tenant: Optional[str],
    profile: str,
) -> dict[str, Any]:
    """Fetch servers configured in a LightNow runtime profile."""
    url = f"{api_url.rstrip('/')}/integrations/profiles/{profile}/servers"
    try:
        response = request_with_refresh(
            "GET",
            url,
            token=token,
            tenant=tenant,
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise ValueError(f"Network error: {exc}") from exc

    return json_response_or_error(response)


def fetch_runtime_context(
    *,
    api_url: str,
    token: str,
    tenant: Optional[str],
    profile: str,
    server_name: str,
    version: str,
    transport: str,
) -> dict[str, Any]:
    """Fetch a secret-bearing runtime context for the local runner."""
    encoded_server = quote(server_name, safe="")
    encoded_version = quote(version, safe="")
    url = f"{api_url.rstrip('/')}/servers/{encoded_server}/versions/{encoded_version}/context"
    try:
        response = request_with_refresh(
            "GET",
            url,
            token=token,
            tenant=tenant,
            headers={"Accept": "application/json"},
            params={
                "profile": profile,
                "include": "secrets",
                "transport": transport,
                "consumer": "local-runner",
            },
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise ValueError(f"Network error: {exc}") from exc

    return json_response_or_error(response)


def launch_config_from_context(
    context: dict[str, Any],
) -> tuple[str, list[str], dict[str, str], Optional[str]]:
    """Extract stdio launch details from a runtime context."""
    probe_request = context.get("probe_request")
    if not isinstance(probe_request, dict):
        raise ValueError("Runtime context did not include a probe request.")
    if probe_request.get("transport") != "stdio":
        raise ValueError("Runtime context did not resolve to a stdio server.")
    stdio = probe_request.get("stdio")
    if not isinstance(stdio, dict):
        raise ValueError("Runtime context did not include stdio launch details.")

    command = stdio.get("cmd")
    if not isinstance(command, str) or command == "":
        raise ValueError("Runtime context did not include a launch command.")

    args = stdio.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("Runtime context included invalid launch arguments.")

    raw_env = stdio.get("env", {})
    if raw_env is None:
        raw_env = {}
    if not isinstance(raw_env, dict):
        raise ValueError("Runtime context included invalid environment variables.")
    env = {str(key): str(value) for key, value in raw_env.items()}

    cwd = stdio.get("cwd") if "cwd" in stdio else stdio.get("working_directory")
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("Runtime context included an invalid working directory.")

    return command, list(args), env, cwd


def resolve_external_secret_bindings(context: dict[str, Any]) -> None:
    """Resolve runtime-only Vault bindings locally without persisting plaintext."""
    bindings = context.get("external_secret_bindings")
    if bindings is None:
        return
    if not isinstance(bindings, list):
        raise ValueError("Runtime context included invalid external secret bindings.")

    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError(
                "Runtime context included an invalid external secret binding."
            )
        provider = binding.get("provider")
        locator = binding.get("locator")
        target = binding.get("target")
        probe = context.get("probe_request")
        if not all(
            isinstance(item, dict) for item in (provider, locator, target, probe)
        ):
            raise ValueError("Runtime external secret binding is incomplete.")
        assert isinstance(provider, dict)
        assert isinstance(locator, dict)
        assert isinstance(target, dict)
        assert isinstance(probe, dict)

        provider_id = provider.get("id")
        if (
            provider.get("provider_type") != "vault_kv_v2"
            or provider.get("resolution_mode") != "runtime"
        ):
            raise ValueError("Runtime external secret provider is unsupported.")
        path = locator.get("path")
        field = locator.get("field")
        name = target.get("name")
        if not all(
            isinstance(item, str) and item for item in (provider_id, path, field, name)
        ):
            raise ValueError("Runtime external secret binding is incomplete.")

        target_type = target.get("type")
        transport = probe.get("transport")
        if not (
            (target_type == "env" and transport == "stdio")
            or (target_type == "header" and transport in {"http", "sse"})
        ):
            raise ValueError(
                "Runtime external secret target does not match the selected transport."
            )

        raw_config = provider.get("config")
        config: dict[str, Any] = (
            {str(key): item for key, item in raw_config.items()}
            if isinstance(raw_config, dict)
            else {}
        )
        value = resolve_vault_runtime_secret(
            provider_id=str(provider_id),
            config=config,
            path=str(path),
            field=str(field),
        )
        if target_type == "env":
            stdio = probe.setdefault("stdio", {})
            if not isinstance(stdio, dict):
                raise ValueError(
                    "Runtime context included invalid stdio configuration."
                )
            env = stdio.setdefault("env", {})
            if not isinstance(env, dict):
                raise ValueError(
                    "Runtime context included invalid environment variables."
                )
            env[str(name)] = value
        else:
            remote = probe.setdefault(str(transport), {})
            if not isinstance(remote, dict):
                raise ValueError(
                    "Runtime context included invalid remote configuration."
                )
            headers = remote.setdefault("headers", {})
            if not isinstance(headers, dict):
                raise ValueError("Runtime context included invalid HTTP headers.")
            headers[str(name)] = value


def resolve_vault_runtime_secret(
    *, provider_id: str, config: dict[str, Any], path: str, field: str
) -> str:
    """Read one Vault KV v2 scalar through Vault Proxy or an opt-in OS keyring token."""
    auth_mode = os.environ.get("LIGHTNOW_VAULT_AUTH_MODE", "vault-proxy")
    headers: dict[str, str] = {}
    namespace = config.get("namespace")
    if isinstance(namespace, str) and namespace:
        headers["X-Vault-Namespace"] = namespace

    address: str
    if auth_mode == "vault-proxy":
        address = os.environ.get("LIGHTNOW_VAULT_PROXY_URL", "http://127.0.0.1:8200")
        parsed = urlparse(address)
        host = parsed.hostname
        is_loopback = host == "localhost"
        if host is not None:
            try:
                is_loopback = is_loopback or ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        if parsed.scheme not in {"http", "https"} or not is_loopback:
            raise ValueError(
                "LIGHTNOW_VAULT_PROXY_URL must target an HTTP(S) loopback address."
            )
    elif auth_mode == "keyring":
        configured_address = os.environ.get("LIGHTNOW_VAULT_ADDRESS") or config.get(
            "address"
        )
        if not isinstance(configured_address, str) or not configured_address:
            raise ValueError(
                f"Vault address is missing for runtime provider {provider_id}."
            )
        address = configured_address
        parsed = urlparse(address)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise ValueError("Runtime Vault address must be an HTTPS origin.")
        try:
            import keyring  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError(
                "OS-keyring support is not installed. Install lightnow-cli[keyring] or use Vault Proxy."
            ) from exc
        service = os.environ.get(
            "LIGHTNOW_VAULT_KEYRING_SERVICE", "lightnow-proxy-vault"
        )
        token = keyring.get_password(service, provider_id)
        if not isinstance(token, str) or not token:
            raise ValueError(
                f"OS keyring has no Vault token for runtime provider {provider_id}."
            )
        headers["X-Vault-Token"] = token
    else:
        raise ValueError("LIGHTNOW_VAULT_AUTH_MODE must be vault-proxy or keyring.")

    verify: str | bool = os.environ.get("LIGHTNOW_VAULT_CA_FILE") or True
    url = f"{address.rstrip('/')}/v1/{path.lstrip('/')}"
    try:
        response = httpx.get(
            url, headers=headers, timeout=10.0, verify=verify, follow_redirects=False
        )
    except httpx.RequestError as exc:
        raise ValueError(
            f"Runtime Vault provider {provider_id} is not reachable."
        ) from exc
    if response.is_redirect:
        raise ValueError(f"Runtime Vault provider {provider_id} returned a redirect.")
    if response.status_code >= 400:
        raise ValueError(
            f"Runtime Vault provider {provider_id} returned HTTP {response.status_code}."
        )
    if len(response.content) > 64 * 1024:
        raise ValueError(
            f"Runtime Vault provider {provider_id} returned an oversized response."
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(
            f"Runtime Vault provider {provider_id} returned invalid JSON."
        ) from exc
    payload_data = payload.get("data") if isinstance(payload, dict) else None
    data = payload_data if isinstance(payload_data, dict) else {}
    secret_data = data.get("data") if isinstance(data.get("data"), dict) else data
    value = secret_data.get(field) if isinstance(secret_data, dict) else None
    if not isinstance(value, str | int | float | bool):
        raise ValueError(
            f"Runtime Vault provider {provider_id} did not return the configured scalar field."
        )
    return str(value)


def json_response_or_error(response: httpx.Response) -> dict[str, Any]:
    """Return JSON response or raise a redacted explicit error."""
    if response.status_code == 401:
        raise authentication_error_from_response(response)
    if response.status_code >= 400:
        raise ValueError(f"HTTP {response.status_code}: {redact(response.text)}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Registry API response was not a JSON object.")
    return payload


def redact(value: str) -> str:
    """Redact secret-like values from error output."""
    redacted = value
    for marker in ("TOKEN", "SECRET", "PASSWORD", "PWD", "KEY"):
        lines = []
        for line in redacted.splitlines():
            if marker in line.upper() and "${" not in line:
                if "=" in line:
                    key, _ = line.split("=", 1)
                    line = key + '= "[REDACTED]"'
                elif ":" in line:
                    key, _ = line.split(":", 1)
                    line = key + ': "[REDACTED]"'
            lines.append(line)
        redacted = "\n".join(lines)
    return redacted
