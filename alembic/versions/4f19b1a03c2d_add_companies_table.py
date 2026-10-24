"""add companies table

Revision ID: 4f19b1a03c2d
Revises: 3e28a2ba5a4b
Create Date: 2026-10-23 15:50:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "4f19b1a03c2d"
down_revision: Union[str, Sequence[str], None] = "3e28a2ba5a4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crée le lien 1-1 utilisateur/entreprise/Sheet dans PostgreSQL."""
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("nom_entreprise", sa.String(length=255), nullable=False),
        sa.Column("secteur", sa.String(length=255), nullable=False),
        sa.Column("sheet_id", sa.String(length=255), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_companies_user_id"),
        sa.UniqueConstraint("sheet_id", name="uq_companies_sheet_id"),
    )


def downgrade() -> None:
    """Supprime la table companies."""
    op.drop_table("companies")
