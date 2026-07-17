"""User-facing managed update commands."""

from __future__ import annotations

import json as json_module
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from lightnow_cli import updates

console = Console()


def _render(state: dict, json_output: bool) -> None:
    if json_output:
        typer.echo(json_module.dumps(state, indent=2))
        return
    table = Table(title="LightNow updates")
    table.add_column("Component")
    table.add_column("Installed")
    table.add_column("Latest")
    table.add_column("Installer")
    table.add_column("Status")
    table.add_column("Result")
    for package, entry in state.get("packages", {}).items():
        table.add_row(
            package,
            entry.get("installed_version") or "not installed",
            entry.get("latest_version") or "unknown",
            entry.get("install_method") or "unknown",
            entry.get("status") or "unknown",
            entry.get("result") or "checked",
        )
        if entry.get("error"):
            table.add_row("", "", "", "", "", f"[red]{entry['error']}[/red]")
    console.print(table)


def update(
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Apply updates without prompting.")
    ] = False,
    check: Annotated[
        bool, typer.Option("--check", help="Check only; do not install updates.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print machine-readable JSON.")
    ] = False,
) -> None:
    """Check for and apply LightNow CLI and Local Proxy updates."""
    if check and yes:
        raise typer.BadParameter("--yes cannot be combined with --check")
    if json_output and not check and not yes:
        raise typer.BadParameter(
            "--json requires --check or --yes to avoid an interactive prompt"
        )
    try:
        state = updates.refresh_update_state()
    except Exception as error:
        if json_output:
            typer.echo(
                json_module.dumps({"error": str(error), "packages": {}}, indent=2)
            )
        else:
            console.print(f"[red]Could not check for LightNow updates:[/red] {error}")
        raise typer.Exit(1) from error

    outdated = updates.cached_outdated_packages(state)
    if check or not outdated:
        _render(state, json_output)
        return
    if not yes and not typer.confirm(
        f"Update {len(outdated)} LightNow component(s) now?"
    ):
        _render(state, json_output)
        return

    state, success = updates.apply_updates(state)
    _render(state, json_output)
    if not success:
        raise typer.Exit(1)


def refresh_update_state() -> None:
    """Refresh update state in a detached helper process."""
    try:
        updates.refresh_update_state()
    finally:
        Path(updates.UPDATE_PENDING_PATH).unlink(missing_ok=True)
