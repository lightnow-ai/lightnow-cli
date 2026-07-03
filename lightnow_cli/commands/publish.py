"""Publish command for uploading MCP server artifacts."""

import asyncio
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from typing_extensions import Annotated

from ..client import get_client
from ..validation import validate_artifacts

console = Console()


def publish(
    server: Annotated[Path, typer.Option("--server", help="Path to server.json file")],
    docs: Annotated[
        Optional[Path], typer.Option("--docs", help="Path to docs.md file")
    ] = None,
    spec: Annotated[
        Optional[Path],
        typer.Option("--spec", help="Path to OpenAPI specification file"),
    ] = None,
    tenant: Annotated[
        Optional[str], typer.Option("--tenant", help="Tenant context")
    ] = None,
    validate_only: Annotated[
        bool, typer.Option("--validate-only", help="Only validate files, don't publish")
    ] = False,
) -> None:
    """Publish MCP server artifacts to the registry."""
    console.print("[bold blue]Publishing MCP server...[/bold blue]")

    # Validate all artifacts first
    console.print("Validating artifacts...")

    try:
        validation_results = validate_artifacts(
            server_file=server, docs_file=docs, spec_file=spec
        )

        if not validation_results["valid"]:
            console.print(
                f"\n[bold red]Validation failed with {len(validation_results['errors'])} error(s):[/bold red]"
            )
            for error in validation_results["errors"]:
                console.print(f"  [red]•[/red] {error}")
            raise typer.Exit(1)

        if validation_results["warnings"]:
            console.print("\n[yellow]Warnings:[/yellow]")
            for warning in validation_results["warnings"]:
                console.print(f"  [yellow]•[/yellow] {warning}")

        console.print("[green]✓ All artifacts validated successfully[/green]")

        if validate_only:
            console.print(
                "[blue]Validation complete. Skipping publish due to --validate-only flag.[/blue]"
            )
            return

    except Exception as e:
        console.print(f"[bold red]Validation error:[/bold red] {e}")
        raise typer.Exit(1)

    # Prepare content for publishing
    server_data = validation_results["validated"]["server"]
    docs_content = validation_results["validated"].get("docs")
    spec_data = validation_results["validated"].get("spec")

    try:
        # Get client and publish
        client = get_client()
        console.print("Publishing to registry...")

        result = asyncio.run(
            client.publish_server(
                server_json=server_data,
                docs_content=docs_content,
                spec_content=spec_data,
                tenant=tenant,
            )
        )

        console.print("[bold green]✓ Published successfully![/bold green]")

        # Show publish result details
        if "server_id" in result:
            console.print(f"Server ID: [bold]{result['server_id']}[/bold]")
        if "version" in result:
            console.print(f"Version: [bold]{result['version']}[/bold]")
        if "url" in result:
            console.print(f"Registry URL: [link]{result['url']}[/link]")

        # Show what was published
        console.print("\nPublished artifacts:")
        console.print("  [green]•[/green] server.json")
        if docs_content:
            console.print("  [green]•[/green] docs.md")
        if spec_data:
            console.print("  [green]•[/green] OpenAPI specification")

    except ValueError as e:
        console.print(f"[bold red]Publish failed:[/bold red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]Unexpected error:[/bold red] {e}")
        raise typer.Exit(1)
