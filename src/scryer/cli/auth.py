"""scryer auth — login, logout, whoami."""

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

app = typer.Typer(no_args_is_help=True, help="Authenticate with the scryer API")


@app.command()
def login(
    email: str = typer.Option(..., "--email", "-e", help="Account email"),
    base_url: str | None = typer.Option(None, "--url", help="Override API URL"),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p"),
) -> None:
    """Exchange email + password for an access token; cache in credentials.json."""
    password = getpass.getpass("Password: ")
    p = load_profile(profile)
    if base_url:
        p = Profile(name=profile, base_url=base_url, token=None)
    with client_for(Profile(name=profile, base_url=p.base_url)) as client:
        r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        print(f"[red]Login failed:[/red] {r.status_code} {r.text}")
        raise typer.Exit(1)
    token = r.json()["access_token"]
    save_profile(Profile(name=profile, base_url=p.base_url, token=token))
    print(f"[green]Logged in[/green] as {email} → {credentials_path()}")


@app.command()
def whoami(profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p")) -> None:
    """Show current principal."""
    p = load_profile(profile)
    if not p.token:
        print(f"[yellow]No token in profile {profile!r}[/yellow]; run `scryer auth login`")
        raise typer.Exit(1)
    with client_for(p) as client:
        r = client.get("/api/v1/auth/me")
    if r.status_code != 200:
        print(f"[red]{r.status_code}[/red] {r.text}")
        raise typer.Exit(1)
    body = r.json()
    print(body)


@app.command()
def logout(profile: str = typer.Option(DEFAULT_PROFILE, "--profile", "-p")) -> None:
    """Drop the cached token for this profile."""
    p = load_profile(profile)
    save_profile(Profile(name=profile, base_url=p.base_url, token=None))
    print(f"[green]Logged out[/green] of profile {profile!r}")
