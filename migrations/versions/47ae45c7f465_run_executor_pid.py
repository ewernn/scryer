"""Add Run.executor_pid for PID-tracked cancellation.

Wave 3: bridges the executor process with cancel_run by recording which
local PID is running each Run. cancel_run can then terminate the
subprocess group when the canceller is the same process; remote
cancellations land via the existing cooperative status check.

The column is nullable: queued Runs and historical Runs have no PID.
Cleared back to NULL on completion (done/failed/cancelled/superseded)
to avoid stale-PID confusion if PIDs roll over.

Revision ID: 47ae45c7f465
Revises: 9db64f8a534b
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "47ae45c7f465"
down_revision: str | None = "9db64f8a534b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("executor_pid", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "executor_pid")
