"""Context selection commands."""

from __future__ import annotations

from typing import Any, Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table
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

console = Console()


def context(
    tenant: Annotated[
        Optional[str],
        typer.Option(
            "--tenant",
            help="Select an organization by exact tenant id or subdomain.",
        ),
    ] = None,
    personal: Annotated[
        bool,
        typer.Option("--personal", help="Use the personal account context."),
    ] = False,
    show: Annotated[
        bool,
        typer.Option("--show", help="Show the currently stored context."),
    ] = False,
) -> None:
    """Select the default LightNow context for context-aware commands."""
    if personal and tenant:
        console.print(
            "[bold red]Context failed:[/bold red] choose --personal or --tenant."
        )
        raise typer.Exit(2)

    if show:
        show_current_context()
        return

    if personal:
        config_manager.set_personal_context()
        console.print("[green]Context set to Personal.[/green]")
        return

    try:
        tenants = fetch_tenants()
    except AccessTokenExpired:
        console.print(f"[red]{ACCESS_TOKEN_EXPIRED_MESSAGE}[/red]")
        raise typer.Exit(1)
    except (AuthError, ValueError) as exc:
        console.print(f"[bold red]Context failed:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    if tenant:
        selected = find_tenant(tenants, tenant)
        if selected is None:
            console.print(
                "[bold red]Context failed:[/bold red] "
                f"No organization matches '{tenant}'. Use 'lightnow context' "
                "to choose from available organizations."
            )
            raise typer.Exit(1)
        store_tenant_context(selected)
        return

    choose_interactively(tenants)


def show_current_context() -> None:
    """Print the currently stored context."""
    config = config_manager.load_config()
    table = Table(title="LightNow Context")
    table.add_column("Scope", style="cyan")
    table.add_column("Details", style="white")
    if config.context_type == "tenant":
        table.add_row(
            "Organization", config.context_label or config.context_tenant or ""
        )
        if config.context_tenant:
            table.add_row("Tenant ID", config.context_tenant)
    else:
        table.add_row("Personal", "Personal LightNow account")
    console.print(table)


def fetch_tenants() -> list[dict[str, Any]]:
    """Fetch organizations available to the authenticated account."""
    token = require_access_token()
    config = config_manager.load_config()
    admin_api_url = config.admin_api_url
    if not admin_api_url:
        raise ValueError("Admin API URL required. Run 'lightnow login' again.")

    try:
        response = request_with_refresh(
            "GET",
            f"{admin_api_url.rstrip('/')}/tenants",
            token=token,
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise ValueError(f"Network error: {exc}") from exc

    if response.status_code == 401:
        raise authentication_error_from_response(response)
    if response.status_code >= 400:
        raise ValueError(f"HTTP {response.status_code}: {response.text}")

    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Admin API response did not include an organization list.")
    return [item for item in payload if isinstance(item, dict)]


def choose_interactively(tenants: list[dict[str, Any]]) -> None:
    """Render choices and store the selected context."""
    choices: list[tuple[str, Optional[dict[str, Any]]]] = [("Personal", None)]
    choices.extend((tenant_label(item), item) for item in tenants)

    console.print("[bold blue]Select LightNow context[/bold blue]")
    for index, (label, item) in enumerate(choices, start=1):
        suffix = ""
        if item is not None:
            role = text_value(item.get("role"))
            plan = text_value(item.get("plan"))
            details = " · ".join(value for value in [role, plan] if value)
            suffix = f" ({details})" if details else ""
        console.print(f"  {index}. {label}{suffix}")

    selected = typer.prompt("Context", default=1)
    try:
        selected_index = int(str(selected).strip())
    except ValueError as exc:
        raise typer.BadParameter("Context must be a number from the list.") from exc

    if selected_index < 1 or selected_index > len(choices):
        raise typer.BadParameter("Context selection is out of range.")

    _, item = choices[selected_index - 1]
    if item is None:
        config_manager.set_personal_context()
        console.print("[green]Context set to Personal.[/green]")
        return

    store_tenant_context(item)


def find_tenant(tenants: list[dict[str, Any]], value: str) -> Optional[dict[str, Any]]:
    """Find a tenant by exact id or subdomain."""
    for item in tenants:
        tenant_id = text_value(item.get("id"))
        subdomain = text_value(item.get("subdomain"))
        if value in {tenant_id, subdomain}:
            return item
    return None


def store_tenant_context(item: dict[str, Any]) -> None:
    """Persist a selected tenant context."""
    tenant_id = text_value(item.get("id"))
    if not tenant_id:
        raise ValueError("Selected organization has no tenant id.")
    label = tenant_label(item)
    config_manager.set_tenant_context(tenant_id, label)
    console.print(f"[green]Context set to organization {label}.[/green]")


def tenant_label(item: dict[str, Any]) -> str:
    """Return a display label for one tenant."""
    name = text_value(item.get("name"))
    subdomain = text_value(item.get("subdomain"))
    if name and subdomain:
        return f"{name} ({subdomain})"
    return name or subdomain or text_value(item.get("id")) or "Organization"


def text_value(value: Any) -> str:
    """Return a safe string value."""
    if isinstance(value, str):
        return value
    return ""
