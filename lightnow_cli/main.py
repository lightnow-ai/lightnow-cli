"""Main CLI application entry point."""

import typer
from typing_extensions import Annotated

from . import __version__, updates
from .commands import (
    auth,
    context,
    integrations,
    publish,
    query,
    runner,
)
from .commands import updates as update_commands
from .commands import (
    validate,
)
from .tls import configure_tls_trust_store

configure_tls_trust_store()

app = typer.Typer(
    name="lightnow",
    help="LightNow CLI - publish MCP servers and sync integration profiles.",
    no_args_is_help=True,
)

app.command("login")(auth.login)
app.command("logout")(auth.logout)
app.command("status")(auth.status)
app.command("whoami")(auth.whoami)
app.command("context")(context.context)
app.command("publish")(publish.publish)
app.command("search")(query.search_servers)
app.command("favorites")(query.favorite_servers)
app.command("info")(query.server_info)
app.command("validate")(validate.validate)
app.command("import-config")(integrations.import_config)
app.command("sync")(integrations.sync)
app.command("config-status")(integrations.config_status)
app.command("run")(runner.run)
app.command("update")(update_commands.update)
app.command("_refresh-update-state", hidden=True)(update_commands.refresh_update_state)


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"LightNow CLI {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=version_callback, help="Show version")
    ] = False,
) -> None:
    """LightNow CLI."""
    if updates.should_check_automatically():
        outdated = updates.cached_outdated_packages(updates.read_update_state())
        if outdated:
            typer.echo(
                f"LightNow update available for {', '.join(outdated)}. Run `lightnow update`.",
                err=True,
            )
        updates.start_background_refresh()


def cli() -> None:
    """Run the LightNow command-line application."""
    app()


if __name__ == "__main__":
    cli()
