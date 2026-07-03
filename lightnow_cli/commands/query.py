"""Query commands for searching and inspecting registry servers."""

import asyncio
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table
from typing_extensions import Annotated

from ..client import get_client
from ..config import config_manager

console = Console()

FAVORITE_SCOPES = {"effective", "system", "tenant", "user", "true"}


def search_servers(
    query: Annotated[str, typer.Argument(help="Search term, e.g. redis or github")],
    sort: Annotated[
        Optional[str],
        typer.Option("--sort", help="Sort order supported by the Registry API."),
    ] = None,
    tenant: Annotated[
        Optional[str], typer.Option("--tenant", help="Tenant context")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="Maximum number of servers to return")
    ] = 10,
    cursor: Annotated[
        Optional[str], typer.Option("--cursor", help="Pagination cursor")
    ] = None,
    show_cursor: Annotated[
        bool,
        typer.Option(
            "--show-cursor",
            help="Print the raw next-page cursor for scripted pagination.",
        ),
    ] = False,
) -> None:
    """Search MCP servers in the LightNow Registry."""
    console.print(f"[bold blue]Searching MCP servers for '{query}'...[/bold blue]")

    try:
        client = get_client()
        effective_tenant = config_manager.effective_tenant(tenant)
        result = asyncio.run(
            client.list_servers(
                search=query,
                sort=sort,
                tenant=effective_tenant,
                limit=limit,
                cursor=cursor,
            )
        )
        render_server_results(
            result,
            empty_message="No matching servers found.",
            show_cursor=show_cursor,
        )
    except ValueError as e:
        console.print(f"[bold red]Search failed:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Unexpected error:[/bold red] {e}")
        raise typer.Exit(1)


def favorite_servers(
    scope: Annotated[
        str,
        typer.Option(
            "--scope",
            help="Favorite scope: effective, user, tenant, system, or true.",
        ),
    ] = "user",
    sort: Annotated[
        Optional[str],
        typer.Option("--sort", help="Sort order supported by the Registry API."),
    ] = None,
    tenant: Annotated[
        Optional[str], typer.Option("--tenant", help="Tenant context")
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="Maximum number of servers to return")
    ] = 10,
    cursor: Annotated[
        Optional[str], typer.Option("--cursor", help="Pagination cursor")
    ] = None,
    show_cursor: Annotated[
        bool,
        typer.Option(
            "--show-cursor",
            help="Print the raw next-page cursor for scripted pagination.",
        ),
    ] = False,
) -> None:
    """Show MCP servers favorited for the selected context."""
    normalized_scope = scope.strip().lower()
    if normalized_scope not in FAVORITE_SCOPES:
        console.print(
            "[bold red]Favorites failed:[/bold red] "
            "scope must be effective, user, tenant, system, or true."
        )
        raise typer.Exit(2)

    console.print(
        f"[bold blue]Fetching {normalized_scope} favorite MCP servers...[/bold blue]"
    )

    try:
        client = get_client()
        effective_tenant = config_manager.effective_tenant(tenant)
        result = asyncio.run(
            client.list_servers(
                favorites=normalized_scope,
                sort=sort,
                tenant=effective_tenant,
                limit=limit,
                cursor=cursor,
            )
        )
        render_server_results(
            result,
            empty_message="No favorite servers found.",
            show_cursor=show_cursor,
        )
    except ValueError as e:
        console.print(f"[bold red]Favorites failed:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Unexpected error:[/bold red] {e}")
        raise typer.Exit(1)


def render_server_results(
    result: dict[str, Any],
    *,
    empty_message: str,
    show_cursor: bool,
) -> None:
    """Render Registry server list responses."""
    servers = result.get("servers", [])
    if not isinstance(servers, list):
        raise ValueError("Registry API response did not include a server list.")

    if not servers:
        console.print(f"[yellow]{empty_message}[/yellow]")
        return

    table = Table(title="MCP Servers")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Version", style="green", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Description", style="dim", max_width=60)

    for entry in servers:
        server = server_payload(entry)
        name = string_value(server.get("name"), "N/A")
        version = string_value(server.get("version"), "N/A")
        title = string_value(server.get("title"), "")
        description = string_value(server.get("description"), "")
        if len(description) > 60:
            description = description[:57] + "..."

        table.add_row(name, version, title, description)

    console.print(table)
    metadata = result.get("metadata", {})
    next_cursor = metadata.get("nextCursor") if isinstance(metadata, dict) else None
    console.print(f"\nShowing {len(servers)} server(s)")
    if isinstance(next_cursor, str) and next_cursor:
        if show_cursor:
            console.print(f"Next cursor: {next_cursor}")
        else:
            console.print(
                "More results are available. Use a higher --limit, or pass "
                "--show-cursor for scripted pagination."
            )


def server_payload(entry: Any) -> dict[str, Any]:
    """Return the ServerJSON payload from a list item."""
    if not isinstance(entry, dict):
        return {}
    nested = entry.get("server")
    if isinstance(nested, dict):
        return nested
    return entry


def string_value(value: Any, default: str) -> str:
    """Return a display-safe string."""
    return value if isinstance(value, str) and value else default


def server_info(
    server_id: Annotated[str, typer.Argument(help="Server ID to get information for")],
    version: Annotated[
        Optional[str], typer.Option("--version", help="Specific version to retrieve")
    ] = None,
    tenant: Annotated[
        Optional[str], typer.Option("--tenant", help="Tenant context")
    ] = None,
) -> None:
    """Get detailed information about a specific MCP server."""
    console.print(
        f"[bold blue]Fetching server information for '{server_id}'...[/bold blue]"
    )

    try:
        client = get_client()
        effective_tenant = config_manager.effective_tenant(tenant)
        result = asyncio.run(
            client.get_server_info(
                server_id=server_id,
                version=version,
                tenant=effective_tenant,
            )
        )
        payload = server_payload(result)

        console.print(f"\n[bold cyan]Server: {payload.get('name', 'N/A')}[/bold cyan]")

        if "version" in payload:
            console.print(f"Version: [green]{payload['version']}[/green]")
        if "title" in payload:
            console.print(f"Title: {payload['title']}")
        if "description" in payload:
            console.print(f"Description: {payload['description']}")

        repository = payload.get("repository")
        if isinstance(repository, dict) and repository.get("url"):
            console.print(f"Repository: [link]{repository['url']}[/link]")

        packages = payload.get("packages")
        if isinstance(packages, list) and packages:
            console.print("\n[bold]Packages:[/bold]")
            for package in packages[:5]:
                if not isinstance(package, dict):
                    continue
                identifier = package.get("identifier", "N/A")
                registry_type = package.get("registryType", "package")
                console.print(f"  • [green]{registry_type}[/green] {identifier}")

                env_vars = package.get("environmentVariables")
                if isinstance(env_vars, list) and env_vars:
                    for env_var in env_vars:
                        if not isinstance(env_var, dict):
                            continue
                        required = (
                            "required" if env_var.get("isRequired") else "optional"
                        )
                        secret = ", secret" if env_var.get("isSecret") else ""
                        name = env_var.get("name", "N/A")
                        description = env_var.get("description")
                        suffix = f" - {description}" if description else ""
                        console.print(f"    env {name} ({required}{secret}){suffix}")

        remotes = payload.get("remotes")
        if isinstance(remotes, list) and remotes:
            console.print("\n[bold]Remote transports:[/bold]")
            for remote in remotes[:5]:
                if not isinstance(remote, dict):
                    continue
                transport_type = remote.get("type", "remote")
                url = remote.get("url", "N/A")
                console.print(f"  • [green]{transport_type}[/green] {url}")

    except ValueError as e:
        console.print(f"[bold red]Query failed:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Unexpected error:[/bold red] {e}")
        raise typer.Exit(1)
