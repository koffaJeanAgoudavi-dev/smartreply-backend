"""add admin V2 messages, suspensions and audit

Revision ID: a9c4e7f2b6d1
Revises: c5e7a9d1f3b6
Create Date: 2026-11-05 20:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a9c4e7f2b6d1"
down_revision: Union[str, Sequence[str], None] = "c5e7a9d1f3b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.add_column(
            sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("suspended_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("suspension_reason", sa.Text(), nullable=True))
        batch_op.create_index(
            "ix_companies_suspended_at",
            ["suspended_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_companies_suspended_until",
            ["suspended_until"],
            unique=False,
        )

    op.create_table(
        "admin_user_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_user_messages_company_created",
        "admin_user_messages",
        ["company_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_user_messages_company_read",
        "admin_user_messages",
        ["company_id", "read_at"],
        unique=False,
    )

    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("related_message_id", sa.Integer(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ("
            "'message_sent', 'company_suspended', 'company_resumed'"
            ")",
            name="ck_admin_audit_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["related_message_id"],
            ["admin_user_messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_audit_company_occurred",
        "admin_audit_events",
        ["company_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_audit_company_occurred",
        table_name="admin_audit_events",
    )
    op.drop_table("admin_audit_events")
    op.drop_index(
        "ix_admin_user_messages_company_read",
        table_name="admin_user_messages",
    )
    op.drop_index(
        "ix_admin_user_messages_company_created",
        table_name="admin_user_messages",
    )
    op.drop_table("admin_user_messages")

    with op.batch_alter_table("companies") as batch_op:
        batch_op.drop_index("ix_companies_suspended_until")
        batch_op.drop_index("ix_companies_suspended_at")
        batch_op.drop_column("suspension_reason")
        batch_op.drop_column("suspended_until")
        batch_op.drop_column("suspended_at")
