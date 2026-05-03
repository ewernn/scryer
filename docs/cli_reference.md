# CLI Reference

The `scryer` CLI is a Typer multi-app: one subcommand per noun. Auth state
lives in `~/Library/Application Support/scryer/credentials.json` (macOS) or the
platformdirs equivalent on Linux/Windows. Use `--profile <name>` to switch
between API hosts.

```bash
scryer --help        # top-level help, lists all subapps
scryer <noun> --help # subapp help
```

## scryer auth

```bash
scryer auth login --email you@example.com [--url https://prod] [--profile work]
scryer auth whoami
scryer auth logout
```

Login prompts for password and stores the JWT in the profile. `whoami` calls
`/api/v1/auth/me` and prints the principal.

## scryer invite

```bash
scryer invite create -w <ws-slug> [-e email] [-r viewer|member|owner] [--ttl-days 7]
scryer invite redeem -t <token> -e <email> [--name "Display Name"] [--url URL]
```

`create` (owner-only) returns a one-shot token. `redeem` is the unauthenticated
signup path: trades token + email + password → new account + JWT, saved to the
profile.

## scryer workspace

```bash
scryer workspace list [-o json]
scryer workspace get <slug>
```

## scryer project

```bash
scryer project list -w <ws-slug> [-o json]
scryer project get -w <ws-slug> <slug>
```

## scryer dataset

```bash
scryer dataset push -w <ws> -p <proj> -s <slug> -n <name> -f records.json [--description "…"]
scryer dataset list -w <ws> -p <proj> [-o json]
scryer dataset get  -w <ws> -p <proj> <slug>
```

`records.json` is an array of `{inputs, expected?, metadata?}`. Pushing a new
version mints a fresh row + bumps the version number; the prior version is
kept and addressable.

## scryer scorer

```bash
scryer scorer push -w <ws> -p <proj> -s <slug> -n <name> -f scorer.py [--description "…"]
scryer scorer list -w <ws> -p <proj> [-o json]
scryer scorer get  -w <ws> -p <proj> <slug>
```

The Scorer source must define `def score(inputs, expected, metadata) -> dict`.
The returned dict's `score` / `value` / `result` key (whichever is present and
numeric) is coerced into `Result.score_value`. Sandboxed: stripped env, ulimits,
side-channel JSON envelope.

## scryer task

```bash
scryer task push -w <ws> -p <proj> -f task.json
scryer task list -w <ws> -p <proj> [-o json]
scryer task get  -w <ws> -p <proj> <slug>
```

`task.json` shape:

```json
{
  "slug": "coh-vs-golden",
  "name": "Coherence on Golden",
  "dataset_id": "<uuid>", "dataset_version": 3,
  "scorer_id":  "<uuid>", "scorer_version": 2,
  "agent_id": null, "agent_version": null,
  "prompt_id": null, "prompt_version": null,
  "params_json": null
}
```

## scryer run

```bash
scryer run start <task_id> [--supersede]
scryer run get   <run_id>
scryer run results <run_id> [-o table|json]
```

`start` queues + executes synchronously (v0). `--supersede` atomically marks
prior queued/running Runs of the same Task as `superseded` so a stale executor
won't re-fire them. `results` defaults to a Rich table; pipe `-o json` for
machine consumption.

## Profiles

```bash
scryer auth login --profile work --email me@work.com --url https://work-scryer
scryer auth login --profile staging --url https://staging-scryer
# Subsequent commands switch via --profile work / --profile staging
```

Default profile name: `default`. Stored at:

- macOS: `~/Library/Application Support/scryer/credentials.json`
- Linux: `~/.config/scryer/credentials.json`
- Windows: `%APPDATA%\scryer\credentials.json`
