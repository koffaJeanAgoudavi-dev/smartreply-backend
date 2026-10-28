"""add company Telegram notifications and draft actions

Revision ID: f2c4d6e8a0b1
Revises: a4f6c8d2e1b7
Create Date: 2026-10-28 00:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f2c4d6e8a0b1"
down_revision: Union[str, Sequence[str], None] = "a4f6c8d2e1b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("processed_emails") as batch_op:
        batch_op.add_column(
            sa.Column("callback_token", sa.String(length=43), nullable=True)
        )
        batch_op.add_column(
            sa.Column("draft_status", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "notification_attempts",
                sa.Integer(),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "notification_blocked_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "notification_claimed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "sheet_status_synced_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.create_check_constraint(
            "ck_processed_emails_draft_status",
            "draft_status IS NULL OR draft_status IN "
            "('pending', 'sending', 'sent', 'discarded', 'missing')",
        )
        batch_op.create_check_constraint(
            "ck_processed_emails_notification_attempts",
            "notification_attempts >= 0 AND notification_attempts <= 3",
        )
        batch_op.create_index(
            "uq_processed_emails_callback_token",
            ["callback_token"],
            unique=True,
        )
        batch_op.create_index(
            "ix_processed_emails_company_notification",
            ["company_id", "notified_at", "notification_attempts"],
            unique=False,
        )

    with op.batch_alter_table("gmail_connections") as batch_op:
        batch_op.add_column(
            sa.Column(
                "revocation_notified_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("gmail_connections") as batch_op:
        batch_op.drop_column("revocation_notified_at")

    with op.batch_alter_table("processed_emails") as batch_op:
        batch_op.drop_index("ix_processed_emails_company_notification")
        batch_op.drop_index("uq_processed_emails_callback_token")
        batch_op.drop_constraint(
            "ck_processed_emails_notification_attempts",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_processed_emails_draft_status",
            type_="check",
        )
        batch_op.drop_column("sheet_status_synced_at")
        batch_op.drop_column("notification_claimed_at")
        batch_op.drop_column("notification_blocked_at")
        batch_op.drop_column("notification_attempts")
        batch_op.drop_column("notified_at")
        batch_op.drop_column("draft_status")
        batch_op.drop_column("callback_token")
