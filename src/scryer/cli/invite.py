"""scryer invite — create invitations and redeem them via signup."""

from __future__ import annotations

import getpass

import typer
from rich import print

from scryer.cli._client import (
    DEFAULT_PROFILE,
    Profile,
    client_for,
    credentials_path,
    load_profile,
    save_profile,
)

app = typer.Typer(no_args_is_help=True, help="Create and accept workspace invitations")


def _need_token(profile: str) -> Profile:
    p = load_profile(profile)
    if not p.token:
        typer.echo(f"No token in profile {profile!r}; run `scryer auth login`", err=True)
        raise typer.Exit(1)
    return p


@app.command("create")
def create(
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace slug"),
    email: str | None = typer.Option(
        None, "--email", "-e", help="Pin invitation to this email (optional)"
    ),
    role: str = typer.Option("member", "--role", "-r", help="viewer | member | owner"),
    ttl_days: int = typer.Option(7, "--ttl-days", min=1, max=90),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
) -> None:
    """Create an invitation token. The token is shown ONCE — copy it now."""
    pf = _need_token(profile)
    payload: dict[str, object] = {"workspace_role": role, "ttl_days": ttl_days}
    if email:
        payload["email"] = email
    with client_for(pf) as client:
        r = client.post(f"/api/v1/workspaces/{workspace}/invitations", json=payload)
    if r.status_code != 201:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    body = r.json()
    print(f"[green]Invitation created[/green] (expires {body['expires_at']})")
    print(f"  workspace: {body['workspace_id']}")
    print(f"  role:      {body['workspace_role']}")
    if body.get("email"):
        print(f"  email:     {body['email']}")
    print()
    print(f"  [bold]token:[/bold] {body['token']}")
    print()
    print("[yellow]Send this token via a private channel — it grants account access.[/yellow]")


@app.command("redeem")
def redeem(
    token: str = typer.Option(..., "--token", "-t"),
    email: str = typer.Option(..., "--email", "-e"),
    display_name: str | None = typer.Option(None, "--name", "-n"),
    base_url: str | None = typer.Option(
        None, "--url", help="Override API URL (otherwise uses profile)"
    ),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
) -> None:
    """Trade an invitation token for a new account; saves the JWT to the profile."""
    password = getpass.getpass("Password (min 8 chars): ")
    if len(password) < 8:
        typer.echo("Password must be at least 8 characters.", err=True)
        raise typer.Exit(1)
    p = load_profile(profile)
    target = Profile(name=profile, base_url=base_url or p.base_url)
    with client_for(target) as client:
        r = client.post(
            "/api/v1/auth/signup",
            json={
                "token": token,
                "email": email,
                "password": password,
                "display_name": display_name,
            },
        )
    if r.status_code != 201:
        print(f"[red]Signup failed:[/red] {r.status_code} {r.text}")
        raise typer.Exit(1)
    body = r.json()
    save_profile(Profile(name=profile, base_url=target.base_url, token=body["access_token"]))
    print(f"[green]Account created.[/green] Token saved → {credentials_path()}")
    print(f"  user_id:      {body['user_id']}")
    print(f"  workspace_id: {body['workspace_id']}")
