"""Verify the RLS session listener sets `app.current_workspace_id` when
session.info["workspace_id"] is populated, and skips when empty."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from scryer.server.db import build_session_factory, with_workspace_context


async def test_listener_sets_guc_when_info_populated(engine: AsyncEngine) -> None:
    """When session.info["workspace_id"] is set BEFORE the first statement,
    after_begin fires SET LOCAL and current_setting returns the value."""
    factory = build_session_factory(engine)
    ws_id = uuid4()
    async with factory() as s:
        s.info["workspace_id"] = ws_id
        # First execute opens the transaction — listener fires before this.
        result = await s.execute(text("SELECT current_setting('app.current_workspace_id', true)"))
        got = result.scalar_one()
        assert got == str(ws_id)


async def test_listener_skips_when_info_empty(engine: AsyncEngine) -> None:
    """No SET LOCAL when info is empty; current_setting returns empty string
    (the `true` second-arg means 'missing returns empty, not error')."""
    factory = build_session_factory(engine)
    async with factory() as s:
        result = await s.execute(text("SELECT current_setting('app.current_workspace_id', true)"))
        got = result.scalar_one()
        assert got == ""


async def test_guc_is_transaction_scoped(engine: AsyncEngine) -> None:
    """SET LOCAL is scoped to the transaction. After commit, the next
    transaction starts fresh — listener re-fires with current info."""
    factory = build_session_factory(engine)
    ws_a = uuid4()
    ws_b = uuid4()
    async with factory() as s:
        s.info["workspace_id"] = ws_a
        result = await s.execute(text("SELECT current_setting('app.current_workspace_id', true)"))
        assert result.scalar_one() == str(ws_a)
        await s.commit()
        # Next transaction: switch context.
        s.info["workspace_id"] = ws_b
        result = await s.execute(text("SELECT current_setting('app.current_workspace_id', true)"))
        assert result.scalar_one() == str(ws_b)


async def test_with_workspace_context_helper(engine: AsyncEngine) -> None:
    """The async ctx mgr populates session.info before the next statement."""
    factory = build_session_factory(engine)
    ws_id = uuid4()
    async with factory() as s:
        async with with_workspace_context(s, ws_id):
            result = await s.execute(
                text("SELECT current_setting('app.current_workspace_id', true)")
            )
            assert result.scalar_one() == str(ws_id)
