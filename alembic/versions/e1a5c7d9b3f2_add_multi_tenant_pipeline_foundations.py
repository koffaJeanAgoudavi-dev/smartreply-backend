"""add multi-tenant pipeline foundations

Revision ID: e1a5c7d9b3f2
Revises: c8d4e2f7a1b9
Create Date: 2026-10-27 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e1a5c7d9b3f2"
down_revision: Union[str, Sequence[str], None] = "c8d4e2f7a1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("gmail_connections") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=16),
                server_default=sa.text("'connected'"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_gmail_connections_status",
            "status IN ('connected', 'revoked')",
        )
        batch_op.create_index(
            "ix_gmail_connections_status",
            ["status"],
            unique=False,
        )

    with op.batch_alter_table("companies") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pipeline_activated_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "pipeline_last_processed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_companies_pipeline_last_processed_at",
            ["pipeline_last_processed_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.drop_index("ix_companies_pipeline_last_processed_at")
        batch_op.drop_column("pipeline_last_processed_at")
        batch_op.drop_column("pipeline_activated_at")

    with op.batch_alter_table("gmail_connections") as batch_op:
        batch_op.drop_index("ix_gmail_connections_status")
        batch_op.drop_constraint(
            "ck_gmail_connections_status",
            type_="check",
        )
        batch_op.drop_column("status")
