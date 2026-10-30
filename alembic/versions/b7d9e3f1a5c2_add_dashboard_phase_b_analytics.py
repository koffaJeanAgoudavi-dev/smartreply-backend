"""add dashboard Phase B analytics

Revision ID: b7d9e3f1a5c2
Revises: f2c4d6e8a0b1
Create Date: 2026-10-30 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7d9e3f1a5c2"
down_revision: Union[str, Sequence[str], None] = "f2c4d6e8a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("processed_emails") as batch_op:
        batch_op.drop_constraint("ck_processed_emails_status", type_="check")
        batch_op.create_check_constraint(
            "ck_processed_emails_status",
            "status IN ('done', 'skipped', 'excluded', 'failed', 'processing')",
        )
        batch_op.add_column(sa.Column("category", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("priority", sa.String(length=16), nullable=True))
        batch_op.add_column(
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_processed_emails_category",
            "category IS NULL OR category IN "
            "('devis', 'support', 'facturation', 'partenariat', "
            "'spam', 'newsletter', 'autre')",
        )
        batch_op.create_check_constraint(
            "ck_processed_emails_priority",
            "priority IS NULL OR priority IN ('haute', 'moyenne', 'basse')",
        )

    op.create_table(
        "company_activity_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("processed_email_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ("
            "'draft_created', 'email_processed', 'email_skipped', "
            "'notification_sent', 'draft_sent', 'draft_discarded'"
            ")",
            name="ck_company_activity_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["processed_email_id"],
            ["processed_emails.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "processed_email_id",
            "event_type",
            name="uq_company_activity_record_event",
        ),
    )
    op.create_index(
        "ix_company_activity_company_occurred",
        "company_activity_events",
        ["company_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_activity_company_occurred",
        table_name="company_activity_events",
    )
    op.drop_table("company_activity_events")

    with op.batch_alter_table("processed_emails") as batch_op:
        batch_op.drop_constraint("ck_processed_emails_status", type_="check")
        batch_op.create_check_constraint(
            "ck_processed_emails_status",
            "status IN ('done', 'skipped', 'failed', 'processing')",
        )
        batch_op.drop_constraint("ck_processed_emails_priority", type_="check")
        batch_op.drop_constraint("ck_processed_emails_category", type_="check")
        batch_op.drop_column("received_at")
        batch_op.drop_column("priority")
        batch_op.drop_column("category")
