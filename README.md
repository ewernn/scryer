# scryer

> Project-agnostic LLM evaluation harness with a control-plane-shaped resource model.

**Status:** Pre-alpha. Phase 0 (repo bootstrap) in progress.

## What it is

scryer is an eval framework — peer to [Inspect](https://inspect.aisi.org.uk/) (UK AISI),
[lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness),
[Braintrust](https://braintrust.dev/), [Phoenix](https://phoenix.arize.com/),
LangSmith, OpenAI evals.

It applies the 6 control-plane primitives (Identity+Auth, Resource model,
Scheduling/Execution, State/Persistence, Observability, Policy/Governance) to
the eval vertical. Multi-user, server-side execution, content-hashed versioning,
cron-triggered evals, outbound webhooks, MCP server.

## Status

Design locked; implementation in Phase 0 of 8 phases. See the design plan
(stored in the originating traitinterp repo) for the full noun set, schema clusters,
and deferred items.

## Stack

- Python 3.12+
- FastAPI + SQLAlchemy 2.x async + Pydantic 2.x
- PostgreSQL (Neon) + Cloudflare R2 for blobs
- Server-rendered Jinja + htmx + Tailwind dashboard
- MCP server at `/mcp`
- Hosted on Railway

## License

Apache-2.0.
