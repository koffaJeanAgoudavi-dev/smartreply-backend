"""add gmail_connections table

Étape 3 — Gmail OAuth par utilisateur : table `gmail_connections` de la base
Neon (DATABASE_URL), lien 1-1 avec `users` (user_id unique). Le refresh token
OAuth Gmail y est stocké CHIFFRÉ (Fernet / TOKEN_ENCRYPTION_KEY) — jamais en
clair, jamais dans un Google Sheet, jamais exposé par une API.

Revision ID: 3e28a2ba5a4b
Revises: 7c82d4ab070c
Create Date: 2026-09-24 09:02:12.260224

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3e28a2ba5a4b'
down_revision: Union[str, Sequence[str], None] = '7c82d4ab070c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Crée la table des connexions Gmail par utilisateur (refresh tokens chiffrés)."""
    op.create_table(
        'gmail_connections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('encrypted_refresh_token', sa.String(length=1024), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )


def downgrade() -> None:
    """Supprime la table des connexions Gmail."""
    op.drop_table('gmail_connections')
