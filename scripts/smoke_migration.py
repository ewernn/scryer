"""Smoke test against a freshly-migrated DB.

Runs after `alembic upgrade head`. Performs a minimal ORM round-trip on
critical tables to prove the migrated schema matches what SQLAlchemy
expects. Catches schema drift the moment it's introduced.

Each round-trip uses a generated UUID-suffixed slug to stay collision-free
when the script is run multiple times against the same DB.

Reads DATABASE_URL from env (set by test_migration.sh)."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


async def _round_trip() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    # Import lazily so a broken migration doesn't blow up at import time.
    from scryer.server.models.auth import User, Workspace, WorkspaceMember
    from scryer.server.models.enums import WorkspaceRole

    engine = create_async_engine(db_url, echo=False)
    try:
        async with AsyncSession(bind=engine, expire_on_commit=False) as s:
            tag = uuid.uuid4().hex[:8]
            user = User(
                email=f"smoke_{tag}@example.com",
                password_hash="x" * 32,  # not a real hash; we never verify
                display_name="Smoke User",
            )
            s.add(user)
            await s.flush()

            ws = Workspace(slug=f"smoke-{tag}", name="Smoke WS", owner_user_id=user.id)
            s.add(ws)
            await s.flush()

            member = WorkspaceMember(
                workspace_id=ws.id, user_id=user.id, role=WorkspaceRole.owner
            )
            s.add(member)
            await s.commit()

            # Round-trip read
            from sqlalchemy import select

            got = (
                await s.execute(
                    select(User, Workspace).join(
                        Workspace, Workspace.owner_user_id == User.id
                    ).where(User.id == user.id)
                )
            ).one()
            assert got[0].email == f"smoke_{tag}@example.com"
            assert got[1].slug == f"smoke-{tag}"

            print(f"    OK: inserted + read back user {got[0].email}, ws {got[1].slug}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_round_trip())
