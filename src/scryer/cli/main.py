"""scryer CLI root: assembles per-noun subapps and shared --profile flag."""

from __future__ import annotations

import typer

from scryer.cli import auth as auth_cli
from scryer.cli import project as project_cli
from scryer.cli import workspace as workspace_cli

app = typer.Typer(
    name="scryer",
    no_args_is_help=True,
    help="scryer — LLM evaluation harness CLI",
    add_completion=False,
)
app.add_typer(auth_cli.app, name="auth")
app.add_typer(workspace_cli.app, name="workspace")
app.add_typer(project_cli.app, name="project")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
