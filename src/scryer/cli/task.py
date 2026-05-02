"""scryer task — push, list, get."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from scryer.cli._client import DEFAULT_PROFILE, Profile, client_for, load_profile

app = typer.Typer(no_args_is_help=True, help="Manage Tasks (Dataset × Scorer bindings)")
_console = Console()


def _need_token(profile: str) -> Profile:
    p = load_profile(profile)
    if not p.token:
        typer.echo(f"No token in profile {profile!r}; run `scryer auth login`", err=True)
        raise typer.Exit(1)
    return p


@app.command("push")
def push(
    workspace: str = typer.Option(..., "--workspace", "-w"),
    project: str = typer.Option(..., "--project", "-p"),
    spec_file: Path = typer.Option(
        ...,
        "--spec",
        "-f",
        help=(
            "JSON file with the Task spec (slug, name, dataset_id, dataset_version, "
            "scorer_id, scorer_version, optional agent/prompt fields)."
        ),
    ),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Push a new Task version. Spec mirrors POST /tasks JSON body."""
    pf = _need_token(profile)
    spec = json.loads(spec_file.read_text())
    with client_for(pf) as client:
        r = client.post(
            f"/api/v1/workspaces/{workspace}/projects/{project}/tasks",
            json=spec,
        )
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    body = r.json()
    typer.echo(f"pushed task {body['slug']}@v{body['version']}  id={body['id']}")


@app.command("list")
def list_(
    workspace: str = typer.Option(..., "--workspace", "-w"),
    project: str = typer.Option(..., "--project", "-p"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
    output: str = typer.Option("table", "--output", "-o"),
) -> None:
    pf = _need_token(profile)
    with client_for(pf) as client:
        r = client.get(f"/api/v1/workspaces/{workspace}/projects/{project}/tasks")
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    rows = r.json()
    if output == "json":
        typer.echo(json.dumps(rows, indent=2))
        return
    t = Table("slug", "version", "name", "dataset", "scorer", "id")
    for row in rows:
        t.add_row(
            row["slug"],
            str(row["version"]),
            row["name"],
            row["dataset_id"][:8] + "…",
            row["scorer_id"][:8] + "…",
            row["id"][:8] + "…",
        )
    _console.print(t)


@app.command("get")
def get(
    workspace: str = typer.Option(..., "--workspace", "-w"),
    project: str = typer.Option(..., "--project", "-p"),
    slug: str = typer.Argument(...),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    pf = _need_token(profile)
    with client_for(pf) as client:
        r = client.get(f"/api/v1/workspaces/{workspace}/projects/{project}/tasks/{slug}")
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(r.json(), indent=2))
