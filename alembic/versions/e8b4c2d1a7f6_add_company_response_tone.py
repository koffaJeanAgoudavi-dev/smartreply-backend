"""add company response tone

Revision ID: e8b4c2d1a7f6
Revises: d3a7f9c1e5b2
Create Date: 2026-11-04 03:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e8b4c2d1a7f6"
down_revision: Union[str, Sequence[str], None] = "d3a7f9c1e5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ajoute un ton sûr avec défaut pour les entreprises existantes."""
    with op.batch_alter_table("companies") as batch_op:
        batch_op.add_column(
            sa.Column(
                "response_tone",
                sa.String(length=16),
                server_default=sa.text("'professional'"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            "ck_companies_response_tone",
            "response_tone IN ('professional', 'friendly', 'direct', 'warm')",
        )


def downgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.drop_constraint("ck_companies_response_tone", type_="check")
        batch_op.drop_column("response_tone")
