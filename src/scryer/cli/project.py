"""scryer project — list within a workspace."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from scryer.cli._client import DEFAULT_PROFILE, client_for, load_profile

app = typer.Typer(no_args_is_help=True, help="Manage projects")
_console = Console()


@app.command("list")
def list_(
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace slug"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
    output: str = typer.Option("table", "--output", "-o", help="table | json"),
) -> None:
    """List projects in a workspace."""
    p = load_profile(profile)
    if not p.token:
        typer.echo(f"No token in profile {profile!r}; run `scryer auth login`", err=True)
        raise typer.Exit(1)
    with client_for(p) as client:
        r = client.get(f"/api/v1/workspaces/{workspace}/projects")
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    rows = r.json()
    if output == "json":
        typer.echo(json.dumps(rows, indent=2))
        return
    t = Table("slug", "name", "visibility", "id", "created_at")
    for row in rows:
        t.add_row(
            row["slug"], row["name"], row["visibility"], row["id"][:8] + "…", row["created_at"]
        )
    _console.print(t)
