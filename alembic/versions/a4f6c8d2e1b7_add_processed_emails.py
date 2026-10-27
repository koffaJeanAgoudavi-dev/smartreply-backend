"""add processed emails registry

Revision ID: a4f6c8d2e1b7
Revises: e1a5c7d9b3f2
Create Date: 2026-10-27 20:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a4f6c8d2e1b7"
down_revision: Union[str, Sequence[str], None] = "e1a5c7d9b3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_emails",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("gmail_message_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="processing",
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= 3",
            name="ck_processed_emails_attempts",
        ),
        sa.CheckConstraint(
            "status IN ('done', 'skipped', 'failed', 'processing')",
            name="ck_processed_emails_status",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "gmail_message_id",
            name="uq_processed_emails_company_message",
        ),
    )
    op.create_index(
        "ix_processed_emails_company_status",
        "processed_emails",
        ["company_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processed_emails_company_status",
        table_name="processed_emails",
    )
    op.drop_table("processed_emails")
