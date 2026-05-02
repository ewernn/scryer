"""swap results index to (run_id, record_id) WHERE invalidated_at IS NULL

`list_results` orders by `record_id`, not `score_value`. The original index
covered the wrong sort key and forced a sort+filter at query time. The new
partial index matches the hot read path: scan in record_id order, only
non-invalidated rows.

Revision ID: b3a2c1d8e0a4
Revises: 51ab39f0910d
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b3a2c1d8e0a4"
down_revision: str | None = "51ab39f0910d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_results_run_id_score_value", table_name="results")
    op.create_index(
        "ix_results_run_id_record_id",
        "results",
        ["run_id", "record_id"],
        postgresql_where="invalidated_at IS NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_results_run_id_record_id", table_name="results")
    op.create_index(
        "ix_results_run_id_score_value",
        "results",
        ["run_id", "score_value"],
    )
