"""Tests des fondations de sélection du pipeline multi-entreprises."""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.modules.ai import service as ai_service
from app.modules.auth.models import User
from app.modules.companies.models import Company
from app.modules.drafts import service as drafts_service
from app.modules.filtering import service as filtering_service
from app.modules.gmail import service as gmail_service
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.notifications import service as notifications_service
from app.modules.pipeline_selection import service
from app.modules.sheets import service as sheets_service
from app.modules.telegram.models import TelegramConnection


class PipelineSelectionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        Base.metadata.create_all(self.engine)
        self.sequence = 0

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create_candidate(
        self,
        integration_count: int,
        *,
        gmail_status: str = "connected",
        activated_at: datetime | None = None,
        last_processed_at: datetime | None = None,
    ) -> tuple[int, int | None]:
        self.sequence += 1
        with self.session_factory() as db:
            user = User(
                email=f"company-{self.sequence}@example.com",
                hashed_password="not-used",
            )
            db.add(user)
            db.flush()
            company = None
            if integration_count >= 1:
                company = Company(
                    user_id=user.id,
                    nom_entreprise=f"Company {self.sequence}",
                    secteur="Test",
                    sheet_id=f"sheet-{self.sequence}",
                    pipeline_activated_at=activated_at,
                    pipeline_last_processed_at=last_processed_at,
                )
                db.add(company)
            if integration_count >= 2:
                db.add(
                    GmailConnection(
                        user_id=user.id,
                        encrypted_refresh_token=f"encrypted-{self.sequence}",
                        status=gmail_status,
                    )
                )
            if integration_count >= 3:
                db.add(
                    TelegramConnection(
                        user_id=user.id,
                        telegram_chat_id=100_000 + self.sequence,
                        telegram_user_id=200_000 + self.sequence,
                    )
                )
            db.commit()
            return user.id, company.id if company else None

    def test_only_companies_with_all_three_integrations_are_eligible(self):
        self.create_candidate(0)
        self.create_candidate(1)
        self.create_candidate(2)
        _, eligible_company_id = self.create_candidate(3)

        with self.session_factory() as db:
            plans = service.select_eligible_company_plans(
                db,
                max_emails_per_company=10,
            )

        self.assertEqual(
            [plan.company_id for plan in plans],
            [eligible_company_id],
        )

    def test_revoked_gmail_connection_makes_company_ineligible(self):
        self.create_candidate(3, gmail_status="revoked")
        _, connected_company_id = self.create_candidate(3)

        with self.session_factory() as db:
            plans = service.select_eligible_company_plans(
                db,
                max_emails_per_company=10,
            )

        self.assertEqual(
            [plan.company_id for plan in plans],
            [connected_company_id],
        )

    def test_cutoff_accepts_only_messages_strictly_after_activation(self):
        activation = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)

        self.assertFalse(
            service.received_after_activation(
                activation - timedelta(microseconds=1),
                activation,
            )
        )
        self.assertFalse(service.received_after_activation(activation, activation))
        self.assertTrue(
            service.received_after_activation(
                activation + timedelta(microseconds=1),
                activation,
            )
        )
        self.assertFalse(
            service.received_after_activation(
                activation + timedelta(days=1),
                None,
            )
        )

    def test_fairness_orders_never_processed_then_oldest_first(self):
        now = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
        _, newest_id = self.create_candidate(
            3,
            last_processed_at=now - timedelta(minutes=5),
        )
        _, never_processed_id = self.create_candidate(3)
        _, oldest_id = self.create_candidate(
            3,
            last_processed_at=now - timedelta(days=1),
        )

        with self.session_factory() as db:
            plans = service.select_eligible_company_plans(
                db,
                max_emails_per_company=10,
            )

        self.assertEqual(
            [plan.company_id for plan in plans],
            [never_processed_id, oldest_id, newest_id],
        )

    def test_simulation_applies_per_company_cap_and_total_counter(self):
        first_activation = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
        self.create_candidate(3, activated_at=first_activation)
        self.create_candidate(3)

        with self.session_factory() as db:
            result, plans = service.build_simulation_result(
                db,
                max_emails_per_company=3,
                time_budget_seconds=45,
            )

        self.assertEqual(result.eligible_company_count, 2)
        self.assertEqual(result.would_process_email_count, 6)
        self.assertEqual(result.max_emails_per_company, 3)
        self.assertEqual(result.time_budget_seconds, 45)
        self.assertEqual([plan.email_limit for plan in plans], [3, 3])
        self.assertEqual(plans[0].pipeline_activated_at, first_activation)

    def test_time_budget_uses_a_monotonic_deadline(self):
        budget = service.PipelineRunBudget(
            total_seconds=10,
            started_monotonic=100.0,
        )
        self.assertTrue(budget.has_time_remaining(now_monotonic=109.999))
        self.assertFalse(budget.has_time_remaining(now_monotonic=110.0))

    def test_simulation_is_read_only_and_calls_no_email_module(self):
        self.create_candidate(3)
        sql_statements = []

        def capture_sql(_conn, _cursor, statement, _parameters, _context, _many):
            sql_statements.append(statement.strip().upper())

        with self.session_factory() as db:
            before = db.execute(
                select(
                    Company.id,
                    Company.pipeline_activated_at,
                    Company.pipeline_last_processed_at,
                )
            ).all()
            event.listen(self.engine, "before_cursor_execute", capture_sql)
            try:
                with (
                    patch.object(
                        gmail_service,
                        "list_unread_emails",
                        side_effect=AssertionError("Gmail must not be read"),
                    ),
                    patch.object(
                        filtering_service,
                        "should_process",
                        side_effect=AssertionError("Filtering must not run"),
                    ),
                    patch.object(
                        ai_service,
                        "analyze_email",
                        side_effect=AssertionError("AI must not run"),
                    ),
                    patch.object(
                        sheets_service,
                        "append_analyzed_email",
                        side_effect=AssertionError("Sheets must not be written"),
                    ),
                    patch.object(
                        drafts_service,
                        "create_gmail_draft",
                        side_effect=AssertionError("Drafts must not be created"),
                    ),
                    patch.object(
                        notifications_service,
                        "send_email_notification",
                        side_effect=AssertionError("Telegram must not be called"),
                    ),
                ):
                    result, _ = service.build_simulation_result(db)
            finally:
                event.remove(self.engine, "before_cursor_execute", capture_sql)

            after = db.execute(
                select(
                    Company.id,
                    Company.pipeline_activated_at,
                    Company.pipeline_last_processed_at,
                )
            ).all()

        self.assertEqual(result.eligible_company_count, 1)
        self.assertEqual(before, after)
        self.assertTrue(sql_statements)
        self.assertTrue(
            all(statement.startswith("SELECT") for statement in sql_statements)
        )

    def test_public_result_contains_only_internal_ids_and_counters(self):
        _, company_id = self.create_candidate(3)
        with self.session_factory() as db:
            result, _ = service.build_simulation_result(db)

        payload = result.to_public_dict()
        self.assertEqual(
            set(payload),
            {
                "status",
                "company_ids",
                "eligible_company_count",
                "would_process_email_count",
                "max_emails_per_company",
                "time_budget_seconds",
            },
        )
        self.assertEqual(payload["company_ids"], [company_id])
        serialized = str(payload)
        self.assertNotIn("@example.com", serialized)
        self.assertNotIn("encrypted-", serialized)
        self.assertNotIn("telegram_chat", serialized)


class PipelineLockTests(unittest.TestCase):
    def test_lock_is_transaction_scoped_for_neon_pooler_compatibility(self):
        db = MagicMock()
        db.execute.return_value.scalar_one.return_value = True

        acquired = service.try_acquire_pipeline_lock(db)

        self.assertTrue(acquired)
        statement = str(db.execute.call_args.args[0])
        self.assertIn("pg_try_advisory_xact_lock", statement)
        self.assertNotIn("pg_try_advisory_lock(", statement)
        self.assertEqual(
            db.execute.call_args.args[1],
            {"lock_key": service.MULTI_TENANT_PIPELINE_LOCK_KEY},
        )

    def test_busy_lock_returns_without_selection_and_closes_session(self):
        db = MagicMock()
        with (
            patch.object(service, "try_acquire_pipeline_lock", return_value=False),
            patch.object(
                service,
                "build_simulation_result",
                side_effect=AssertionError("selection must not run"),
            ),
        ):
            prepared = service.prepare_multi_tenant_simulation(lambda: db)

        self.assertFalse(prepared.lock_acquired)
        self.assertEqual(prepared.result.status, "already_running")
        db.rollback.assert_called_once()
        db.close.assert_called_once()
        db.commit.assert_not_called()

    def test_background_completion_logs_only_safe_summary_and_rolls_back(self):
        db = MagicMock()
        prepared = service.PreparedSimulationRun(
            result=service.SimulationResult(
                status="accepted",
                company_ids=(12, 34),
                eligible_company_count=2,
                would_process_email_count=20,
                max_emails_per_company=10,
                time_budget_seconds=240,
            ),
            lock_acquired=True,
            budget=service.PipelineRunBudget(total_seconds=240),
            db=db,
        )

        with patch.object(service.logger, "info") as log_info:
            asyncio.run(service.complete_multi_tenant_simulation(prepared))

        db.rollback.assert_called_once()
        db.close.assert_called_once()
        db.commit.assert_not_called()
        log_format, *log_values = log_info.call_args.args
        rendered = log_format % tuple(log_values)
        self.assertIn("company_ids=[12, 34]", rendered)
        self.assertNotIn("@", rendered)
        self.assertNotIn("token", rendered.lower())
        self.assertNotIn("chat", rendered.lower())


if __name__ == "__main__":
    unittest.main()
