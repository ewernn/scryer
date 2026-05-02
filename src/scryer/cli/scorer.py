"""scryer scorer — push, list, get."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from scryer.cli._client import DEFAULT_PROFILE, Profile, client_for, load_profile

app = typer.Typer(no_args_is_help=True, help="Manage scorers")
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
    source: Path = typer.Option(..., "--source", "-f", help="Path to .py source"),
    description: str | None = typer.Option(None, "--description"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    pf = _need_token(profile)
    src_text = source.read_text()
    with client_for(pf) as client:
        r = client.post(
            f"/api/v1/workspaces/{workspace}/projects/{project}/scorers",
            json={"slug": slug, "name": name, "source_text": src_text, "description": description},
        )
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    body = r.json()
    typer.echo(f"pushed scorer {body['slug']}@v{body['version']}")
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
        r = client.get(f"/api/v1/workspaces/{workspace}/projects/{project}/scorers")
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    rows = r.json()
    if output == "json":
        typer.echo(json.dumps(rows, indent=2))
        return
    t = Table("slug", "version", "name", "id", "created_at")
    for row in rows:
        t.add_row(
            row["slug"], str(row["version"]), row["name"], row["id"][:8] + "…", row["created_at"]
        )
    _console.print(t)
