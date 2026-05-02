"""scryer dataset — push, list, get, list records."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from scryer.cli._client import DEFAULT_PROFILE, Profile, client_for, load_profile

app = typer.Typer(no_args_is_help=True, help="Manage datasets")
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
    slug: str = typer.Option(..., "--slug", "-s"),
    name: str = typer.Option(..., "--name", "-n"),
    records_file: Path = typer.Option(
        ..., "--records", "-f", help="JSON file: list of {inputs, expected?, metadata?}"
    ),
    description: str | None = typer.Option(None, "--description"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Push a new Dataset version."""
    pf = _need_token(profile)
    records = json.loads(records_file.read_text())
    with client_for(pf) as client:
        r = client.post(
            f"/api/v1/workspaces/{workspace}/projects/{project}/datasets",
            json={"slug": slug, "name": name, "records": records, "description": description},
        )
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    body = r.json()
    typer.echo(f"pushed {body['slug']}@v{body['version']} ({body['record_count']} records)")
    typer.echo(f"id: {body['id']}")


@app.command("list")
def list_(
    workspace: str = typer.Option(..., "--workspace", "-w"),
    project: str = typer.Option(..., "--project", "-p"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
    output: str = typer.Option("table", "--output", "-o"),
) -> None:
    pf = _need_token(profile)
    with client_for(pf) as client:
        r = client.get(f"/api/v1/workspaces/{workspace}/projects/{project}/datasets")
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    rows = r.json()
    if output == "json":
        typer.echo(json.dumps(rows, indent=2))
        return
    t = Table("slug", "version", "name", "records", "id", "created_at")
    for row in rows:
        t.add_row(
            row["slug"],
            str(row["version"]),
            row["name"],
            str(row["record_count"]),
            row["id"][:8] + "…",
            row["created_at"],
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
        r = client.get(f"/api/v1/workspaces/{workspace}/projects/{project}/datasets/{slug}")
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(r.json(), indent=2))
