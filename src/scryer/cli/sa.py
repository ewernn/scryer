"""scryer sa — manage workspace ServiceAccounts and their API keys."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from scryer.cli._client import DEFAULT_PROFILE, Profile, client_for, load_profile

app = typer.Typer(no_args_is_help=True, help="Manage ServiceAccounts (machine principals)")
_console = Console()


def _need_token(profile: str) -> Profile:
    p = load_profile(profile)
    if not p.token:
        typer.echo(f"No token in profile {profile!r}; run `scryer auth login`", err=True)
        raise typer.Exit(1)
    return p


@app.command("create")
def create(
    workspace: str = typer.Argument(..., help="Workspace slug"),
    name: str = typer.Argument(...),
    requires_approval: bool = typer.Option(
        False, "--requires-approval", help="Mark this SA's runs as needing manual approval"
    ),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
) -> None:
    """Create a ServiceAccount in the given workspace. Owner role required."""
    pf = _need_token(profile)
    with client_for(pf) as client:
        r = client.post(
            f"/api/v1/workspaces/{workspace}/service-accounts",
            json={"name": name, "requires_approval": requires_approval},
        )
    if r.status_code != 201:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(r.json(), indent=2))


@app.command("list")
def list_(
    workspace: str = typer.Argument(..., help="Workspace slug"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
) -> None:
    pf = _need_token(profile)
    with client_for(pf) as client:
        r = client.get(f"/api/v1/workspaces/{workspace}/service-accounts")
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    rows = r.json()
    if not rows:
        typer.echo("(no service accounts)")
        return
    t = Table("id", "name", "active", "needs_approval", "created_at")
    for row in rows:
        t.add_row(
            row["id"][:12] + "…",
            row["name"],
            "✓" if row["is_active"] else "✗",
            "✓" if row["requires_approval"] else "",
            row["created_at"][:19],
        )
    _console.print(t)


@app.command("archive")
def archive(
    workspace: str = typer.Argument(..., help="Workspace slug"),
    sa_id: str = typer.Argument(...),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
) -> None:
    """Soft-delete a ServiceAccount. Existing api_keys remain valid until revoked
    separately — archive is for hiding from the list, not killing access."""
    pf = _need_token(profile)
    with client_for(pf) as client:
        r = client.delete(f"/api/v1/workspaces/{workspace}/service-accounts/{sa_id}")
    if r.status_code != 204:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    typer.echo(f"archived service-account {sa_id}")


@app.command("key")
def key(
    workspace: str = typer.Argument(..., help="Workspace slug"),
    sa_id: str = typer.Argument(...),
    name: str | None = typer.Option(None, "--name", "-n", help="Key label"),
    scopes: str = typer.Option(
        "read,write", "--scopes", "-s", help="Comma-separated subset of read,write,admin"
    ),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
) -> None:
    """Issue a new API key for the SA. The full_key is returned ONCE — copy it now."""
    pf = _need_token(profile)
    payload: dict[str, object] = {"scopes": [s.strip() for s in scopes.split(",") if s.strip()]}
    if name:
        payload["name"] = name
    with client_for(pf) as client:
        r = client.post(
            f"/api/v1/workspaces/{workspace}/service-accounts/{sa_id}/api-keys",
            json=payload,
        )
    if r.status_code != 201:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(r.json(), indent=2))
