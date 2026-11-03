"""add per-user Drive OAuth connections and Sheet ownership mode

Revision ID: d3a7f9c1e5b2
Revises: b7d9e3f1a5c2
Create Date: 2026-11-03 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d3a7f9c1e5b2"
down_revision: Union[str, Sequence[str], None] = "b7d9e3f1a5c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute les grants Drive chiffrés sans migrer la propriété historique."""
    op.create_table(
        "drive_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.String(length=1024), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'connected'"),
            nullable=False,
        ),
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
            "status IN ('connected', 'revoked')",
            name="ck_drive_connections_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_drive_connections_user_id"),
    )
    op.create_index(
        "ix_drive_connections_status",
        "drive_connections",
        ["status"],
        unique=False,
    )

    with op.batch_alter_table("companies") as batch_op:
        batch_op.add_column(
            sa.Column(
                "sheet_owner_mode",
                sa.String(length=16),
                server_default=sa.text("'platform'"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_companies_sheet_owner_mode",
            "sheet_owner_mode IN ('platform', 'client')",
        )


def downgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.drop_constraint(
            "ck_companies_sheet_owner_mode",
            type_="check",
        )
        batch_op.drop_column("sheet_owner_mode")
    op.drop_index(
        "ix_drive_connections_status",
        table_name="drive_connections",
    )
    op.drop_table("drive_connections")
