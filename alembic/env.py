"""
SmartReply Agent — configuration Alembic (Étape 2)
==================================================
Migrations de la couche multi-utilisateur (table users) dans la base Neon
pointée par la variable d'environnement DATABASE_URL (déjà configurée sur
Render). Exécution : alembic upgrade head (le procfile le fait au démarrage).
"""
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

# Permet `import app...` quel que soit le répertoire d'appel (local et Render)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.database import Base, _normalize_url  # noqa: E402
from app.modules.auth import models  # noqa: E402, F401 — table users dans Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (émet le SQL sans se connecter)."""
    context.configure(
        url=_normalize_url(settings.DATABASE_URL),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (se connecte à Neon via DATABASE_URL)."""
    connectable = create_engine(
        _normalize_url(settings.DATABASE_URL),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
