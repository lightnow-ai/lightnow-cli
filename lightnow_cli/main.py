"""Main CLI application entry point."""

import typer
from typing_extensions import Annotated

from . import __version__
from .commands import auth, context, integrations, publish, query, runner, validate
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
app.command("sync")(integrations.sync)
app.command("run")(runner.run)


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
    pass


def cli() -> None:
    """Run the LightNow command-line application."""
    app()


if __name__ == "__main__":
    cli()
