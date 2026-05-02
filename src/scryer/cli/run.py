"""scryer run — start a Run for a Task; show results."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from scryer.cli._client import DEFAULT_PROFILE, Profile, client_for, load_profile

app = typer.Typer(no_args_is_help=True, help="Execute and inspect Runs")
_console = Console()


def _need_token(profile: str) -> Profile:
    p = load_profile(profile)
    if not p.token:
        typer.echo(f"No token in profile {profile!r}; run `scryer auth login`", err=True)
        raise typer.Exit(1)
    return p


@app.command("start")
def start(
    task_id: str = typer.Argument(...),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    """Queue a Run for the given Task and execute synchronously."""
    pf = _need_token(profile)
    with client_for(pf) as client:
        r = client.post("/api/v1/runs", json={"task_id": task_id, "execute_now": True})
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    body = r.json()
    typer.echo(
        f"run {body['id'][:12]}… status={body['status']} "
        f"({body['n_done']}/{body['n_records']} done, {body['n_failed']} failed)"
    )


@app.command("get")
def get(
    run_id: str = typer.Argument(...),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
) -> None:
    pf = _need_token(profile)
    with client_for(pf) as client:
        r = client.get(f"/api/v1/runs/{run_id}")
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(r.json(), indent=2))


@app.command("results")
def results(
    run_id: str = typer.Argument(...),
    profile: str = typer.Option(DEFAULT_PROFILE, "--profile"),
    output: str = typer.Option("table", "--output", "-o"),
) -> None:
    """List per-record Results for a Run."""
    pf = _need_token(profile)
    with client_for(pf) as client:
        r = client.get(f"/api/v1/runs/{run_id}/results")
    if r.status_code != 200:
        typer.echo(f"{r.status_code} {r.text}", err=True)
        raise typer.Exit(1)
    rows = r.json()
    if output == "json":
        typer.echo(json.dumps(rows, indent=2))
        return
    t = Table("record_id", "score_value", "duration_ms", "error")
    for row in rows:
        t.add_row(
            str(row["record_id"]),
            f"{row['score_value']:.4f}" if row["score_value"] is not None else "—",
            str(row["duration_ms"]) if row["duration_ms"] else "—",
            (row["error"] or "")[:60],
        )
    _console.print(t)
