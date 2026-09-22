"""create users table

Étape 2 — couche multi-utilisateur : table `users` de la base Neon (DATABASE_URL).
Colonnes : id, email (unique), hashed_password (Argon2, jamais en clair),
created_at (horodatage UTC). Les refresh tokens Gmail par utilisateur viendront
plus tard dans cette même base, jamais dans un Google Sheet.

Revision ID: 7c82d4ab070c
Revises:
Create Date: 2026-09-22 19:47:53.627460

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c82d4ab070c'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crée la table utilisateurs."""
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )


def downgrade() -> None:
    """Supprime la table utilisateurs."""
    op.drop_table('users')
