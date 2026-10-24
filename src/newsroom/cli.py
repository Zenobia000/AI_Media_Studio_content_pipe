"""newsroom CLI entry point (tool surface only; IF-020)."""

import typer

from newsroom import __version__

app = typer.Typer(help="AI Media Studio content pipeline", no_args_is_help=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """AI Media Studio content pipeline."""
