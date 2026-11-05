"""add company reply profile

Revision ID: c5e7a9d1f3b6
Revises: e8b4c2d1a7f6
Create Date: 2026-11-05 01:15:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c5e7a9d1f3b6"
down_revision: Union[str, Sequence[str], None] = "e8b4c2d1a7f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROFILE_COLUMNS = (
    "business_description",
    "products_services",
    "business_hours",
    "response_signature",
    "business_rules",
    "response_instructions",
)


def upgrade() -> None:
    """Ajoute un profil vide et rétrocompatible aux entreprises existantes."""
    with op.batch_alter_table("companies") as batch_op:
        for column_name in PROFILE_COLUMNS:
            batch_op.add_column(
                sa.Column(
                    column_name,
                    sa.Text(),
                    server_default=sa.text("''"),
                    nullable=False,
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        for column_name in reversed(PROFILE_COLUMNS):
            batch_op.drop_column(column_name)
