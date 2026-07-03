"""Validate command for local artifact validation."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from typing_extensions import Annotated

from ..validation import validate_artifacts

console = Console()


def validate(
    server: Annotated[
        Optional[Path], typer.Option("--server", help="Path to server.json file")
    ] = None,
    docs: Annotated[
        Optional[Path], typer.Option("--docs", help="Path to docs.md file")
    ] = None,
    spec: Annotated[
        Optional[Path],
        typer.Option("--spec", help="Path to OpenAPI specification file"),
    ] = None,
) -> None:
    """Validate MCP server artifacts locally."""
    if not any([server, docs, spec]):
        console.print(
            "[red]Error: At least one file must be specified for validation[/red]"
        )
        console.print("Use --help to see available options.")
        raise typer.Exit(1)

    console.print("[bold blue]Validating MCP server artifacts...[/bold blue]")

    try:
        results = validate_artifacts(server_file=server, docs_file=docs, spec_file=spec)

        if results["valid"]:
            console.print("\n[bold green]✓ All artifacts are valid![/bold green]")

            if results["warnings"]:
                console.print(
                    f"\n[yellow]Note: {len(results['warnings'])} warning(s) found:[/yellow]"
                )
                for warning in results["warnings"]:
                    console.print(f"  [yellow]•[/yellow] {warning}")

            # Show summary of validated files
            validated_files = []
            if server and "server" in results["validated"]:
                validated_files.append("server.json")
            if docs and "docs" in results["validated"]:
                validated_files.append("docs.md")
            if spec and "spec" in results["validated"]:
                validated_files.append("OpenAPI specification")

            if validated_files:
                console.print(f"\nValidated files: {', '.join(validated_files)}")

        else:
            console.print(
                f"\n[bold red]Validation failed with {len(results['errors'])} error(s):[/bold red]"
            )
            for error in results["errors"]:
                console.print(f"  [red]•[/red] {error}")

            if results["warnings"]:
                console.print("\n[yellow]Additional warnings:[/yellow]")
                for warning in results["warnings"]:
                    console.print(f"  [yellow]•[/yellow] {warning}")

            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[bold red]Validation error:[/bold red] {e}")
        raise typer.Exit(1)
