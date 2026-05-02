"""scryer CLI root: assembles per-noun subapps and shared --profile flag."""

from __future__ import annotations

import typer

from scryer.cli import auth as auth_cli
from scryer.cli import dataset as dataset_cli
from scryer.cli import invite as invite_cli
from scryer.cli import project as project_cli
from scryer.cli import run as run_cli
from scryer.cli import scorer as scorer_cli
from scryer.cli import task as task_cli
from scryer.cli import workspace as workspace_cli

app = typer.Typer(
    name="scryer",
    no_args_is_help=True,
    help="scryer — LLM evaluation harness CLI",
    add_completion=False,
)
app.add_typer(auth_cli.app, name="auth")
app.add_typer(invite_cli.app, name="invite")
app.add_typer(workspace_cli.app, name="workspace")
app.add_typer(project_cli.app, name="project")
app.add_typer(dataset_cli.app, name="dataset")
app.add_typer(scorer_cli.app, name="scorer")
app.add_typer(task_cli.app, name="task")
app.add_typer(run_cli.app, name="run")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
