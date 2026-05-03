"""Verify the RLS session listener sets `app.current_workspace_id` and
`app.current_user_id` from session.info, and skips each independently
when its key is empty."""

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
    or None depending on PG version (the `true` second-arg means 'missing OK
    — don't error'). Either is "unset"; both are falsy."""
    factory = build_session_factory(engine)
    async with factory() as s:
        result = await s.execute(text("SELECT current_setting('app.current_workspace_id', true)"))
        got = result.scalar_one()
        assert not got, f"expected falsy (unset), got {got!r}"


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


async def test_listener_sets_user_id_when_info_populated(engine: AsyncEngine) -> None:
    """`current_user_id` GUC is set from session.info["current_user_id"]."""
    factory = build_session_factory(engine)
    user_id = uuid4()
    async with factory() as s:
        s.info["current_user_id"] = user_id
        result = await s.execute(text("SELECT current_setting('app.current_user_id', true)"))
        assert result.scalar_one() == str(user_id)


async def test_listener_sets_both_gucs_independently(engine: AsyncEngine) -> None:
    """Workspace and user GUCs are independent — set one, the other stays
    unset; set both, both come through."""
    factory = build_session_factory(engine)
    ws_id = uuid4()
    user_id = uuid4()
    async with factory() as s:
        s.info["workspace_id"] = ws_id
        s.info["current_user_id"] = user_id
        ws_got = (
            await s.execute(text("SELECT current_setting('app.current_workspace_id', true)"))
        ).scalar_one()
        user_got = (
            await s.execute(text("SELECT current_setting('app.current_user_id', true)"))
        ).scalar_one()
        assert ws_got == str(ws_id)
        assert user_got == str(user_id)


async def test_listener_skips_user_id_when_info_empty(engine: AsyncEngine) -> None:
    """No SET LOCAL when current_user_id key is absent. Skipping is per-key
    (workspace_id absence shouldn't suppress user_id set, and vice versa)."""
    factory = build_session_factory(engine)
    ws_id = uuid4()
    async with factory() as s:
        s.info["workspace_id"] = ws_id  # workspace set, user NOT set
        ws_got = (
            await s.execute(text("SELECT current_setting('app.current_workspace_id', true)"))
        ).scalar_one()
        user_got = (
            await s.execute(text("SELECT current_setting('app.current_user_id', true)"))
        ).scalar_one()
        assert ws_got == str(ws_id)
        assert not user_got, f"expected falsy (unset), got {user_got!r}"


async def test_with_workspace_context_takes_user_id(engine: AsyncEngine) -> None:
    """The user_id kwarg sets the user GUC alongside the workspace GUC."""
    factory = build_session_factory(engine)
    ws_id = uuid4()
    user_id = uuid4()
    async with factory() as s:
        async with with_workspace_context(s, ws_id, user_id=user_id):
            ws_got = (
                await s.execute(text("SELECT current_setting('app.current_workspace_id', true)"))
            ).scalar_one()
            user_got = (
                await s.execute(text("SELECT current_setting('app.current_user_id', true)"))
            ).scalar_one()
            assert ws_got == str(ws_id)
            assert user_got == str(user_id)
