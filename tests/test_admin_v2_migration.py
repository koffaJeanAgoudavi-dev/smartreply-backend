"""Validation de la migration V2 sur une base vierge."""

import tempfile
import unittest
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.core.config import settings


class AdminV2MigrationTests(unittest.TestCase):
    def test_upgrade_head_creates_suspension_message_and_audit_storage(self):
        repository_root = Path(__file__).resolve().parents[1]
        original_database_url = settings.DATABASE_URL
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "admin-v2.db"
            database_url = f"sqlite:///{database_path}"
            settings.DATABASE_URL = database_url
            try:
                config = Config(str(repository_root / "alembic.ini"))
                config.set_main_option(
                    "script_location",
                    str(repository_root / "alembic"),
                )
                command.upgrade(config, "head")

                engine = create_engine(database_url)
                schema = inspect(engine)
                self.assertTrue(schema.has_table("admin_user_messages"))
                self.assertTrue(schema.has_table("admin_audit_events"))
                company_columns = {
                    column["name"] for column in schema.get_columns("companies")
                }
                self.assertTrue(
                    {
                        "suspended_at",
                        "suspended_until",
                        "suspension_reason",
                    }.issubset(company_columns)
                )
                message_columns = {
                    column["name"]
                    for column in schema.get_columns("admin_user_messages")
                }
                self.assertEqual(
                    message_columns,
                    {
                        "id",
                        "company_id",
                        "created_by_user_id",
                        "title",
                        "body",
                        "created_at",
                        "read_at",
                    },
                )
                audit_columns = {
                    column["name"]
                    for column in schema.get_columns("admin_audit_events")
                }
                self.assertNotIn("message_body", audit_columns)
                self.assertNotIn("body", audit_columns)
                with engine.connect() as connection:
                    revision = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(revision, "a9c4e7f2b6d1")
                engine.dispose()
            finally:
                settings.DATABASE_URL = original_database_url


if __name__ == "__main__":
    unittest.main()
