"""Tests du flux multi-entreprises réel et de son isolation stricte."""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.modules.auth.models import User
from app.modules.companies.models import Company
from app.modules.dashboard.models import CompanyActivityEvent
from app.modules.dashboard.service import get_dashboard_overview
from app.modules.gmail.service import GmailTokenRevokedError
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.pipeline_execution import service
from app.modules.pipeline_selection.service import (
    CompanyRunPlan,
    PipelineRunBudget,
    PreparedSimulationRun,
    SimulationResult,
)
from app.modules.processed_emails.models import ProcessedEmail
from app.modules.processed_emails.service import (
    claim_notification_attempt,
    mark_failed,
    release_notification_claim,
    reserve_email,
)
from app.modules.telegram.models import TelegramConnection


class PipelineExecutionTests(unittest.TestCase):
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
        self.activation = datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc)
        self.original_body_limit = service.settings.GEMINI_MAX_EMAIL_BODY_CHARS
        self.original_stale_seconds = (
            service.settings.PIPELINE_PROCESSING_STALE_SECONDS
        )
        self.original_telegram_token = service.settings.TELEGRAM_BOT_TOKEN
        self.original_telegram_secret = service.settings.TELEGRAM_WEBHOOK_SECRET
        service.settings.GEMINI_MAX_EMAIL_BODY_CHARS = 12
        service.settings.PIPELINE_PROCESSING_STALE_SECONDS = 60
        service.settings.TELEGRAM_BOT_TOKEN = "pipeline-test-token"
        service.settings.TELEGRAM_WEBHOOK_SECRET = "pipeline-test-webhook-secret"

    def tearDown(self):
        service.settings.GEMINI_MAX_EMAIL_BODY_CHARS = self.original_body_limit
        service.settings.PIPELINE_PROCESSING_STALE_SECONDS = (
            self.original_stale_seconds
        )
        service.settings.TELEGRAM_BOT_TOKEN = self.original_telegram_token
        service.settings.TELEGRAM_WEBHOOK_SECRET = self.original_telegram_secret
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create_company(
        self,
        label: str,
        *,
        activated_at: datetime | None = None,
        email_limit: int = 10,
        with_telegram: bool = True,
    ) -> CompanyRunPlan:
        self.sequence += 1
        with self.session_factory() as db:
            user = User(
                email=f"user-{label.lower()}@test.invalid",
                hashed_password="unused",
            )
            db.add(user)
            db.flush()
            company = Company(
                user_id=user.id,
                nom_entreprise=f"Company {label}",
                secteur="Test",
                sheet_id=f"sheet-{label}",
                pipeline_activated_at=activated_at,
            )
            db.add(company)
            db.flush()
            db.add(
                GmailConnection(
                    user_id=user.id,
                    encrypted_refresh_token=f"encrypted-{label}",
                    status="connected",
                )
            )
            if with_telegram:
                db.add(
                    TelegramConnection(
                        user_id=user.id,
                        telegram_chat_id=100_000 + self.sequence,
                        telegram_user_id=200_000 + self.sequence,
                        connected_at=self.activation - timedelta(days=1),
                    )
                )
            db.commit()
            return CompanyRunPlan(
                company_id=company.id,
                user_id=user.id,
                pipeline_activated_at=activated_at,
                pipeline_last_processed_at=None,
                email_limit=email_limit,
            )

    @staticmethod
    def prepared(plans: list[CompanyRunPlan]) -> PreparedSimulationRun:
        company_ids = tuple(plan.company_id for plan in plans)
        total_cap = sum(plan.email_limit for plan in plans)
        return PreparedSimulationRun(
            result=SimulationResult(
                status="accepted",
                company_ids=company_ids,
                processing_company_ids=company_ids,
                eligible_company_count=len(plans),
                processing_company_count=len(plans),
                simulation_company_count=0,
                max_emails_cap=total_cap,
                processing_max_emails_cap=total_cap,
                max_emails_per_company=max(
                    (plan.email_limit for plan in plans),
                    default=0,
                ),
                time_budget_seconds=240,
            ),
            plans=tuple(plans),
            lock_acquired=True,
            budget=PipelineRunBudget(total_seconds=240),
        )

    def email(self, label: str, *, received_at: datetime | None = None) -> dict:
        return {
            "id_gmail": f"message-{label}",
            "thread_id": f"thread-{label}",
            "expediteur": f"Sender {label} <sender-{label}@test.invalid>",
            "objet": f"Subject {label}",
            "message_id_header": f"<{label}@test.invalid>",
            "list_unsubscribe": "",
            "snippet": f"snippet-{label}",
            "corps": f"body-{label}-with-extra-private-content",
            "received_at": received_at or self.activation + timedelta(seconds=1),
        }

    @staticmethod
    async def successful_analysis(*_args):
        return {
            "urgence": "moyenne",
            "categorie": "support",
            "resume": "Résumé de test",
            "brouillon": "Brouillon de test",
        }

    def test_isolation_uses_only_each_company_token_and_sheet_and_continues(self):
        plan_a = self.create_company("A", activated_at=self.activation)
        plan_b = self.create_company("B", activated_at=self.activation)
        prepared = self.prepared([plan_a, plan_b])
        calls: dict[str, list] = {
            "refresh": [],
            "list": [],
            "detail": [],
            "sheet": [],
            "draft": [],
            "sheet_write": [],
        }

        async def access_token(refresh_token):
            calls["refresh"].append(refresh_token)
            return f"access-{refresh_token[-1]}"

        async def list_page(access_token, **_kwargs):
            calls["list"].append(access_token)
            return ((f"message-{access_token[-1]}",), None)

        async def get_email(access_token, gmail_message_id):
            calls["detail"].append((access_token, gmail_message_id))
            return self.email(access_token[-1])

        def workbook(sheet_id):
            calls["sheet"].append(sheet_id)
            return f"workbook-{sheet_id[-1]}"

        def write_email(workbook_value, **_kwargs):
            calls["sheet_write"].append(workbook_value)

        async def create_draft(access_token, **kwargs):
            calls["draft"].append((access_token, kwargs["thread_id"]))
            if access_token == "access-A":
                raise RuntimeError("private failure from company A")
            return "draft-B"

        with (
            patch.object(
                service,
                "decrypt_token",
                side_effect=lambda encrypted: f"refresh-{encrypted[-1]}",
            ),
            patch.object(service, "get_company_access_token", side_effect=access_token),
            patch.object(service, "list_company_message_page", side_effect=list_page),
            patch.object(service, "get_company_email", side_effect=get_email),
            patch.object(
                service, "should_process", return_value={"decision": "traiter"}
            ),
            patch.object(
                service, "analyze_email", side_effect=self.successful_analysis
            ),
            patch.object(service, "get_company_workbook", side_effect=workbook),
            patch.object(service, "upsert_company_email", side_effect=write_email),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(
                service, "create_company_gmail_draft", side_effect=create_draft
            ),
            patch.object(service, "upsert_company_draft"),
            patch.object(service, "mark_company_email_draft_ready"),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    prepared,
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(calls["refresh"], ["refresh-A", "refresh-B"])
        self.assertEqual(calls["list"], ["access-A", "access-B"])
        self.assertEqual(
            calls["detail"],
            [("access-A", "message-A"), ("access-B", "message-B")],
        )
        self.assertEqual(calls["sheet"], ["sheet-A", "sheet-B"])
        self.assertEqual(calls["sheet_write"], ["workbook-A", "workbook-B"])
        self.assertEqual(
            calls["draft"],
            [("access-A", "thread-A"), ("access-B", "thread-B")],
        )
        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.failed, 1)
        with self.session_factory() as db:
            records = db.execute(select(ProcessedEmail)).scalars().all()
            statuses = {row.company_id: row.status for row in records}
            error_codes = {row.company_id: row.last_error_code for row in records}
            companies = db.execute(select(Company)).scalars().all()
        self.assertEqual(statuses[plan_a.company_id], "failed")
        self.assertEqual(error_codes[plan_a.company_id], "gmail_draft_error")
        self.assertNotIn("private", str(error_codes))
        self.assertEqual(statuses[plan_b.company_id], "done")
        self.assertTrue(
            all(company.pipeline_last_processed_at for company in companies)
        )

    def test_empty_allowlist_keeps_eligible_company_in_read_only_simulation(self):
        plan = self.create_company("Simulated", activated_at=None)
        prepared = PreparedSimulationRun(
            result=SimulationResult(
                status="accepted",
                company_ids=(plan.company_id,),
                processing_company_ids=(),
                eligible_company_count=1,
                processing_company_count=0,
                simulation_company_count=1,
                max_emails_cap=plan.email_limit,
                processing_max_emails_cap=0,
                max_emails_per_company=plan.email_limit,
                time_budget_seconds=240,
            ),
            plans=(plan,),
            lock_acquired=True,
            budget=PipelineRunBudget(total_seconds=240),
        )
        with (
            patch.object(
                service,
                "get_company_access_token",
                side_effect=AssertionError("Gmail forbidden in simulation"),
            ),
            patch.object(
                service,
                "get_company_workbook",
                side_effect=AssertionError("Sheets forbidden in simulation"),
            ),
            patch.object(
                service,
                "analyze_email",
                side_effect=AssertionError("Gemini forbidden in simulation"),
            ),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    prepared,
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.attempted_company_ids, [])
        with self.session_factory() as db:
            company = db.get(Company, plan.company_id)
            self.assertIsNone(company.pipeline_activated_at)
            self.assertIsNone(company.pipeline_last_processed_at)
            self.assertEqual(db.scalar(select(func.count(ProcessedEmail.id))), 0)

    def test_first_real_pass_activates_before_any_gmail_read(self):
        plan = self.create_company("First", activated_at=None)
        prepared = self.prepared([plan])
        observed_activation = []

        async def access_token(_refresh_token):
            with self.session_factory() as db:
                activated_at = db.get(Company, plan.company_id).pipeline_activated_at
            observed_activation.append(activated_at)
            return "access-First"

        async def list_page(_access_token, **kwargs):
            with self.session_factory() as db:
                activated_at = db.get(Company, plan.company_id).pipeline_activated_at
            self.assertIsNotNone(activated_at)
            self.assertEqual(
                kwargs["after_epoch_seconds"], int(activated_at.timestamp())
            )
            return (), None

        with (
            patch.object(service, "decrypt_token", return_value="refresh-First"),
            patch.object(service, "get_company_access_token", side_effect=access_token),
            patch.object(service, "list_company_message_page", side_effect=list_page),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    prepared,
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.processed, 0)
        self.assertEqual(len(observed_activation), 1)
        self.assertIsNotNone(observed_activation[0])
        with self.session_factory() as db:
            company = db.get(Company, plan.company_id)
            self.assertIsNotNone(company.pipeline_activated_at)
            self.assertIsNotNone(company.pipeline_last_processed_at)
            self.assertEqual(db.scalar(select(func.count(ProcessedEmail.id))), 0)

    def test_company_without_telegram_processes_without_notification_attempt(self):
        plan = self.create_company(
            "NoTelegram",
            activated_at=self.activation,
            with_telegram=False,
        )
        notify = AsyncMock()
        upsert_email = Mock()
        upsert_draft = Mock()
        mark_draft_ready = Mock()
        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=(("message-no-telegram",), None)),
            ),
            patch.object(
                service,
                "get_company_email",
                new=AsyncMock(return_value=self.email("no-telegram")),
            ),
            patch.object(
                service,
                "should_process",
                return_value={"decision": "traiter"},
            ),
            patch.object(
                service,
                "analyze_email",
                side_effect=self.successful_analysis,
            ),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email", upsert_email),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(
                service,
                "create_company_gmail_draft",
                new=AsyncMock(return_value="draft-no-telegram"),
            ),
            patch.object(service, "upsert_company_draft", upsert_draft),
            patch.object(
                service,
                "mark_company_email_draft_ready",
                mark_draft_ready,
            ),
            patch.object(service, "send_company_email_notification", notify),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.failed, 0)
        notify.assert_not_awaited()
        upsert_email.assert_called()
        upsert_draft.assert_called_once()
        mark_draft_ready.assert_called_once()
        with self.session_factory() as db:
            record = db.scalar(
                select(ProcessedEmail).where(
                    ProcessedEmail.company_id == plan.company_id
                )
            )
        self.assertEqual(record.status, "done")
        self.assertEqual(record.draft_status, "pending")
        self.assertEqual(record.gmail_draft_id, "draft-no-telegram")
        self.assertIsNotNone(record.callback_token)
        self.assertIsNone(record.notified_at)
        self.assertEqual(record.notification_attempts, 0)
        self.assertIsNone(record.notification_claimed_at)
        self.assertIsNone(record.notification_blocked_at)

    def test_connected_telegram_keeps_nominal_notification_behavior(self):
        plan = self.create_company("TelegramNominal", activated_at=self.activation)
        notify = AsyncMock()
        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=(("message-telegram",), None)),
            ),
            patch.object(
                service,
                "get_company_email",
                new=AsyncMock(return_value=self.email("telegram")),
            ),
            patch.object(
                service, "should_process", return_value={"decision": "traiter"}
            ),
            patch.object(
                service,
                "analyze_email",
                side_effect=self.successful_analysis,
            ),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email"),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(
                service,
                "create_company_gmail_draft",
                new=AsyncMock(return_value="draft-telegram"),
            ),
            patch.object(service, "upsert_company_draft"),
            patch.object(service, "mark_company_email_draft_ready"),
            patch.object(service, "send_company_email_notification", notify),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.failed, 0)
        notify.assert_awaited_once()
        notification_kwargs = notify.await_args.kwargs
        self.assertEqual(notification_kwargs["objet"], "Subject telegram")
        self.assertEqual(notification_kwargs["categorie"], "support")
        self.assertTrue(notification_kwargs["view_callback_data"].startswith("mt_view_"))
        self.assertNotIn("draft-telegram", notification_kwargs["view_callback_data"])
        with self.session_factory() as db:
            record = db.scalar(
                select(ProcessedEmail).where(
                    ProcessedEmail.company_id == plan.company_id
                )
            )
            telegram_connection = db.scalar(
                select(TelegramConnection).where(
                    TelegramConnection.user_id == plan.user_id
                )
            )
        self.assertEqual(
            notify.await_args.args[0],
            telegram_connection.telegram_chat_id,
        )
        self.assertEqual(record.status, "done")
        self.assertEqual(record.gmail_draft_id, "draft-telegram")
        self.assertEqual(record.notification_attempts, 1)
        self.assertIsNotNone(record.notified_at)
        self.assertIsNone(record.notification_blocked_at)

    def test_missing_bot_token_with_existing_connection_has_no_attempt(self):
        plan = self.create_company("NoBotToken", activated_at=self.activation)
        notify = AsyncMock()
        analyze = AsyncMock(side_effect=self.successful_analysis)
        create_draft = AsyncMock(return_value="draft-no-token")
        with (
            patch.object(service.settings, "TELEGRAM_BOT_TOKEN", ""),
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=(("message-no-token",), None)),
            ),
            patch.object(
                service,
                "get_company_email",
                new=AsyncMock(return_value=self.email("no-token")),
            ),
            patch.object(
                service, "should_process", return_value={"decision": "traiter"}
            ),
            patch.object(service, "analyze_email", analyze),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email"),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(service, "create_company_gmail_draft", create_draft),
            patch.object(service, "upsert_company_draft"),
            patch.object(service, "mark_company_email_draft_ready"),
            patch.object(service, "send_company_email_notification", notify),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )
            retry_summary = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(retry_summary.processed, 0)
        analyze.assert_awaited_once()
        create_draft.assert_awaited_once()
        notify.assert_not_awaited()
        with self.session_factory() as db:
            record = db.scalar(
                select(ProcessedEmail).where(
                    ProcessedEmail.company_id == plan.company_id
                )
            )
            telegram_connection = db.scalar(
                select(TelegramConnection).where(
                    TelegramConnection.user_id == plan.user_id
                )
            )
        self.assertIsNotNone(telegram_connection)
        self.assertEqual(record.status, "done")
        self.assertEqual(record.draft_status, "pending")
        self.assertEqual(record.gmail_draft_id, "draft-no-token")
        self.assertIsNone(record.notified_at)
        self.assertEqual(record.notification_attempts, 0)
        self.assertIsNone(record.notification_claimed_at)

    def test_cutoff_is_strict_and_body_is_truncated_for_gemini(self):
        plan = self.create_company("Cutoff", activated_at=self.activation)
        prepared = self.prepared([plan])
        analyzed_bodies = []
        filter_email = Mock(return_value={"decision": "traiter"})

        async def get_email(_token, gmail_message_id):
            if gmail_message_id == "at-cutoff":
                return self.email("old", received_at=self.activation)
            return self.email(
                "new",
                received_at=self.activation + timedelta(milliseconds=1),
            )

        async def analyze(_sender, _subject, body):
            analyzed_bodies.append(body)
            return await self.successful_analysis()

        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=(("at-cutoff", "after-cutoff"), None)),
            ),
            patch.object(service, "get_company_email", side_effect=get_email),
            patch.object(service, "should_process", side_effect=filter_email),
            patch.object(service, "analyze_email", side_effect=analyze),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email"),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(
                service,
                "create_company_gmail_draft",
                new=AsyncMock(return_value="draft"),
            ),
            patch.object(service, "upsert_company_draft"),
            patch.object(service, "mark_company_email_draft_ready"),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    prepared,
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.excluded, 1)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.processed, 1)
        self.assertEqual(filter_email.call_count, 1)
        self.assertEqual(len(analyzed_bodies), 1)
        self.assertEqual(len(analyzed_bodies[0]), 12)
        with self.session_factory() as db:
            records = db.scalars(
                select(ProcessedEmail).order_by(ProcessedEmail.id)
            ).all()
            events = db.scalars(
                select(CompanyActivityEvent).order_by(CompanyActivityEvent.id)
            ).all()
            overview = get_dashboard_overview(
                db,
                company_id=plan.company_id,
            )
        self.assertEqual(records[0].status, "excluded")
        self.assertIsNone(records[0].received_at)
        self.assertIsNone(records[0].category)
        self.assertEqual(records[1].status, "done")
        self.assertEqual(records[1].category, "support")
        self.assertEqual(records[1].priority, "moyenne")
        self.assertEqual(
            records[1].received_at.replace(tzinfo=timezone.utc),
            self.activation + timedelta(milliseconds=1),
        )
        self.assertEqual(
            {event.event_type for event in events},
            {"draft_created", "email_processed"},
        )
        self.assertEqual(overview.metrics.analyzed_emails, 1)
        self.assertEqual(overview.metrics.processed_emails, 1)
        self.assertEqual(overview.metrics.ignored_emails, 0)
        self.assertEqual(sum(point.count for point in overview.volume), 1)
        self.assertNotIn(
            "email_skipped",
            {item.event_type for item in overview.recent_activity},
        )

    def test_repeated_pass_does_not_process_the_same_email_twice(self):
        plan = self.create_company("Dedupe", activated_at=self.activation)
        prepared_one = self.prepared([plan])
        prepared_two = self.prepared([plan])
        analyze = AsyncMock(side_effect=self.successful_analysis)
        draft = AsyncMock(return_value="draft")
        patches = (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=(("same-message",), None)),
            ),
            patch.object(
                service,
                "get_company_email",
                new=AsyncMock(return_value=self.email("same")),
            ),
            patch.object(
                service, "should_process", return_value={"decision": "traiter"}
            ),
            patch.object(service, "analyze_email", analyze),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email"),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(service, "create_company_gmail_draft", draft),
            patch.object(service, "upsert_company_draft"),
            patch.object(service, "mark_company_email_draft_ready"),
        )
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patches[7],
            patches[8],
            patches[9],
            patches[10],
            patches[11],
        ):
            first = asyncio.run(
                service.execute_multi_tenant_run(
                    prepared_one,
                    session_factory=self.session_factory,
                )
            )
            second = asyncio.run(
                service.execute_multi_tenant_run(
                    prepared_two,
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(first.processed, 1)
        self.assertEqual(second.processed, 0)
        self.assertEqual(analyze.await_count, 1)
        self.assertEqual(draft.await_count, 1)
        with self.session_factory() as db:
            records = db.execute(select(ProcessedEmail)).scalars().all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "done")

    def test_sheet_failure_reuses_persisted_gmail_draft_on_retry(self):
        plan = self.create_company("DraftRetry", activated_at=self.activation)
        draft = AsyncMock(return_value="draft-persisted")
        write_draft = Mock(
            side_effect=[RuntimeError("sheet temporarily unavailable"), None]
        )

        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=(("same-message",), None)),
            ),
            patch.object(
                service,
                "get_company_email",
                new=AsyncMock(return_value=self.email("draft-retry")),
            ),
            patch.object(
                service, "should_process", return_value={"decision": "traiter"}
            ),
            patch.object(
                service, "analyze_email", side_effect=self.successful_analysis
            ),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email"),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(service, "create_company_gmail_draft", draft),
            patch.object(service, "upsert_company_draft", write_draft),
            patch.object(service, "mark_company_email_draft_ready"),
        ):
            first = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )
            with self.session_factory() as db:
                failed_record = db.scalar(
                    select(ProcessedEmail).where(
                        ProcessedEmail.company_id == plan.company_id
                    )
                )
                self.assertEqual(failed_record.status, "failed")
                self.assertEqual(
                    failed_record.gmail_draft_id,
                    "draft-persisted",
                )
            second = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(first.failed, 1)
        self.assertEqual(second.processed, 1)
        self.assertEqual(draft.await_count, 1)
        self.assertEqual(write_draft.call_count, 2)
        with self.session_factory() as db:
            record = db.scalar(
                select(ProcessedEmail).where(
                    ProcessedEmail.company_id == plan.company_id
                )
            )
        self.assertEqual(record.status, "done")
        self.assertEqual(record.attempts, 2)
        self.assertEqual(record.gmail_draft_id, "draft-persisted")

    def test_stale_processing_reservation_is_resumed_atomically_up_to_cap(self):
        plan = self.create_company("Stale", activated_at=self.activation)
        with self.session_factory() as db:
            first = reserve_email(
                db,
                company_id=plan.company_id,
                gmail_message_id="stale-message",
            )
        self.assertIsNotNone(first)

        # Une réservation fraîche ne peut pas être volée.
        with self.session_factory() as db:
            self.assertIsNone(
                reserve_email(
                    db,
                    company_id=plan.company_id,
                    gmail_message_id="stale-message",
                )
            )

        old_timestamp = datetime.now(timezone.utc) - timedelta(seconds=61)
        with self.session_factory() as db:
            db.execute(
                update(ProcessedEmail)
                .where(
                    ProcessedEmail.company_id == plan.company_id,
                    ProcessedEmail.gmail_message_id == "stale-message",
                )
                .values(updated_at=old_timestamp)
            )
            db.commit()
        with self.session_factory() as db:
            second = reserve_email(
                db,
                company_id=plan.company_id,
                gmail_message_id="stale-message",
            )
        self.assertIsNotNone(second)
        self.assertEqual(second.attempt, 2)

        # Le premier repreneur a rafraîchi updated_at : un second perd la course.
        with self.session_factory() as db:
            self.assertIsNone(
                reserve_email(
                    db,
                    company_id=plan.company_id,
                    gmail_message_id="stale-message",
                )
            )

        with self.session_factory() as db:
            db.execute(
                update(ProcessedEmail)
                .where(ProcessedEmail.id == second.record_id)
                .values(updated_at=old_timestamp)
            )
            db.commit()
        with self.session_factory() as db:
            third = reserve_email(
                db,
                company_id=plan.company_id,
                gmail_message_id="stale-message",
            )
        self.assertIsNotNone(third)
        self.assertEqual(third.attempt, 3)

        with self.session_factory() as db:
            db.execute(
                update(ProcessedEmail)
                .where(ProcessedEmail.id == third.record_id)
                .values(updated_at=old_timestamp)
            )
            db.commit()
        with self.session_factory() as db:
            self.assertIsNone(
                reserve_email(
                    db,
                    company_id=plan.company_id,
                    gmail_message_id="stale-message",
                )
            )
            record = db.get(ProcessedEmail, third.record_id)
        self.assertEqual(record.status, "processing")
        self.assertEqual(record.attempts, 3)

    def test_filter_ignored_emails_are_analyzed_and_respect_company_cap(self):
        plan = self.create_company("Cap", activated_at=self.activation, email_limit=2)
        prepared = self.prepared([plan])
        detail = AsyncMock(
            side_effect=lambda _token, message_id: self.email(message_id)
        )
        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=(("m1", "m2", "m3", "m4"), None)),
            ),
            patch.object(service, "get_company_email", detail),
            patch.object(
                service,
                "should_process",
                return_value={"decision": "ignorer", "raison": "test"},
            ),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email"),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    prepared,
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.skipped, 2)
        self.assertEqual(summary.excluded, 0)
        self.assertEqual(detail.await_count, 2)
        with self.session_factory() as db:
            records = db.scalars(select(ProcessedEmail)).all()
            events = db.scalars(select(CompanyActivityEvent)).all()
            overview = get_dashboard_overview(
                db,
                company_id=plan.company_id,
            )
        self.assertEqual(len(records), 2)
        self.assertEqual({record.status for record in records}, {"skipped"})
        self.assertEqual(
            [event.event_type for event in events],
            ["email_skipped", "email_skipped"],
        )
        self.assertEqual(overview.metrics.analyzed_emails, 2)
        self.assertEqual(overview.metrics.processed_emails, 0)
        self.assertEqual(overview.metrics.ignored_emails, 2)
        self.assertEqual(sum(point.count for point in overview.volume), 2)
        self.assertEqual(
            {item.event_type for item in overview.recent_activity},
            {"email_skipped"},
        )

    def test_three_failures_are_the_maximum(self):
        plan = self.create_company("Retry", activated_at=self.activation)
        with self.session_factory() as db:
            first = reserve_email(
                db,
                company_id=plan.company_id,
                gmail_message_id="retry-message",
            )
        self.assertIsNotNone(first)
        reservation = first
        for expected_attempt in (1, 2, 3):
            self.assertEqual(reservation.attempt, expected_attempt)
            with self.session_factory() as db:
                mark_failed(db, reservation, error_code="gemini_error")
            with self.session_factory() as db:
                next_reservation = reserve_email(
                    db,
                    company_id=plan.company_id,
                    gmail_message_id="retry-message",
                )
            if expected_attempt < 3:
                self.assertIsNotNone(next_reservation)
                reservation = next_reservation
            else:
                self.assertIsNone(next_reservation)
        with self.session_factory() as db:
            record = db.scalar(
                select(ProcessedEmail).where(
                    ProcessedEmail.company_id == plan.company_id
                )
            )
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.attempts, 3)
        self.assertEqual(record.last_error_code, "gemini_error")

    def test_notification_attempts_are_capped_at_three(self):
        plan = self.create_company("NotifyCap", activated_at=self.activation)
        with self.session_factory() as db:
            record = ProcessedEmail(
                company_id=plan.company_id,
                gmail_message_id="notification-cap-message",
                gmail_draft_id="notification-cap-draft",
                callback_token="N" * 32,
                draft_status="pending",
                status="done",
                attempts=1,
            )
            db.add(record)
            db.commit()
            record_id = record.id

        claims = []
        for attempt_index in range(4):
            with self.session_factory() as db:
                claimed = claim_notification_attempt(
                    db,
                    company_id=plan.company_id,
                    record_id=record_id,
                )
                claims.append(claimed)
            if claimed and attempt_index < 3:
                with self.session_factory() as db:
                    release_notification_claim(
                        db,
                        company_id=plan.company_id,
                        record_id=record_id,
                    )
        self.assertEqual(claims, [True, True, True, False])
        with self.session_factory() as db:
            record = db.get(ProcessedEmail, record_id)
        self.assertEqual(record.notification_attempts, 3)

    def test_quota_429_consumes_no_attempt_and_stops_only_that_company(self):
        plan_a = self.create_company("QuotaA", activated_at=self.activation)
        plan_b = self.create_company("QuotaB", activated_at=self.activation)
        prepared = self.prepared([plan_a, plan_b])

        async def list_page(access_token, **_kwargs):
            if access_token.endswith("QuotaA"):
                return (("quota-message", "must-not-be-read"), None)
            return (("company-b-message",), None)

        async def analyze(sender, *_args):
            if "QuotaA" in sender:
                raise RuntimeError("Gemini HTTP 429: private provider response")
            return await self.successful_analysis()

        def decrypted(encrypted):
            return encrypted.replace("encrypted-", "refresh-")

        async def token(refresh):
            return refresh.replace("refresh-", "access-")

        async def detail(access, message_id):
            label = "QuotaA" if access.endswith("QuotaA") else "QuotaB"
            return self.email(label)

        with (
            patch.object(service, "decrypt_token", side_effect=decrypted),
            patch.object(service, "get_company_access_token", side_effect=token),
            patch.object(service, "list_company_message_page", side_effect=list_page),
            patch.object(
                service, "get_company_email", side_effect=detail
            ) as get_detail,
            patch.object(
                service, "should_process", return_value={"decision": "traiter"}
            ),
            patch.object(service, "analyze_email", side_effect=analyze),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email"),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(
                service,
                "create_company_gmail_draft",
                new=AsyncMock(return_value="draft"),
            ),
            patch.object(service, "upsert_company_draft"),
            patch.object(service, "mark_company_email_draft_ready"),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    prepared,
                    session_factory=self.session_factory,
                )
            )

        self.assertIn(plan_a.company_id, summary.quota_stopped_company_ids)
        self.assertEqual(summary.processed, 1)
        self.assertEqual(get_detail.await_count, 2)
        with self.session_factory() as db:
            records = db.execute(select(ProcessedEmail)).scalars().all()
        quota_record = next(
            row for row in records if row.company_id == plan_a.company_id
        )
        self.assertEqual(quota_record.status, "failed")
        self.assertEqual(quota_record.attempts, 0)
        self.assertEqual(quota_record.last_error_code, "gemini_quota_429")

    def test_late_telegram_connection_does_not_catch_up_older_drafts(self):
        plan = self.create_company("LateTelegram", activated_at=self.activation)
        connected_at = self.activation + timedelta(hours=1)
        with self.session_factory() as db:
            connection = db.scalar(
                select(TelegramConnection).where(
                    TelegramConnection.user_id == plan.user_id
                )
            )
            connection.connected_at = connected_at
            old_record = ProcessedEmail(
                company_id=plan.company_id,
                gmail_message_id="old-before-telegram",
                gmail_draft_id="old-draft",
                callback_token="old_callback_token_01",
                draft_status="pending",
                status="done",
            )
            new_record = ProcessedEmail(
                company_id=plan.company_id,
                gmail_message_id="new-after-telegram",
                gmail_draft_id="new-draft",
                callback_token="new_callback_token_01",
                draft_status="pending",
                status="done",
            )
            db.add_all([old_record, new_record])
            db.flush()
            db.add_all(
                [
                    CompanyActivityEvent(
                        company_id=plan.company_id,
                        processed_email_id=old_record.id,
                        event_type="draft_created",
                        occurred_at=connected_at - timedelta(seconds=1),
                    ),
                    CompanyActivityEvent(
                        company_id=plan.company_id,
                        processed_email_id=new_record.id,
                        event_type="draft_created",
                        occurred_at=connected_at + timedelta(seconds=1),
                    ),
                ]
            )
            db.commit()
            old_record_id = old_record.id
            new_record_id = new_record.id

        notify = AsyncMock()
        notification_data = {
            "expediteur": "sender@test.invalid",
            "objet": "Subject",
            "categorie": "support",
            "priorite": "moyenne",
            "resume": "Résumé",
        }
        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=((), None)),
            ),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(
                service,
                "get_company_notification_data",
                return_value=notification_data,
            ) as get_notification_data,
            patch.object(service, "send_company_email_notification", notify),
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.processed, 0)
        get_notification_data.assert_called_once_with(
            "workbook",
            email_id=f"EM-{new_record_id}",
        )
        notify.assert_awaited_once()
        with self.session_factory() as db:
            old_record = db.get(ProcessedEmail, old_record_id)
            new_record = db.get(ProcessedEmail, new_record_id)
        self.assertEqual(old_record.notification_attempts, 0)
        self.assertIsNone(old_record.notified_at)
        self.assertEqual(new_record.notification_attempts, 1)
        self.assertIsNotNone(new_record.notified_at)

    def test_notification_failure_retries_without_gemini_or_new_gmail_draft(self):
        plan = self.create_company("NotifyRetry", activated_at=self.activation)
        analyze = AsyncMock(side_effect=self.successful_analysis)
        create_draft = AsyncMock(return_value="draft-notify")
        notify = AsyncMock(
            side_effect=[
                service.CompanyTelegramError("telegram_http_500", status_code=500),
                None,
            ]
        )
        notification_data = {
            "expediteur": "sender@test.invalid",
            "objet": "Subject",
            "categorie": "support",
            "priorite": "moyenne",
            "resume": "Résumé",
        }
        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=(("notify-message",), None)),
            ),
            patch.object(
                service,
                "get_company_email",
                new=AsyncMock(return_value=self.email("notify")),
            ),
            patch.object(
                service, "should_process", return_value={"decision": "traiter"}
            ),
            patch.object(service, "analyze_email", analyze),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email"),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(service, "create_company_gmail_draft", create_draft),
            patch.object(service, "upsert_company_draft"),
            patch.object(service, "mark_company_email_draft_ready"),
            patch.object(
                service,
                "get_company_notification_data",
                return_value=notification_data,
            ),
            patch.object(service, "send_company_email_notification", notify),
        ):
            first = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )
            second = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(first.processed, 1)
        self.assertEqual(second.processed, 0)
        self.assertEqual(analyze.await_count, 1)
        self.assertEqual(create_draft.await_count, 1)
        self.assertEqual(notify.await_count, 2)
        with self.session_factory() as db:
            record = db.scalar(
                select(ProcessedEmail).where(
                    ProcessedEmail.company_id == plan.company_id
                )
            )
        self.assertEqual(record.status, "done")
        self.assertEqual(record.notification_attempts, 2)
        self.assertIsNotNone(record.notified_at)

    def test_blocked_telegram_bot_is_not_retried(self):
        plan = self.create_company("NotifyBlocked", activated_at=self.activation)
        notify = AsyncMock(
            side_effect=service.CompanyTelegramBlockedError(
                "telegram_blocked",
                status_code=403,
            )
        )
        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(return_value="access"),
            ),
            patch.object(
                service,
                "list_company_message_page",
                new=AsyncMock(return_value=(("blocked-message",), None)),
            ),
            patch.object(
                service,
                "get_company_email",
                new=AsyncMock(return_value=self.email("blocked")),
            ),
            patch.object(
                service, "should_process", return_value={"decision": "traiter"}
            ),
            patch.object(
                service, "analyze_email", side_effect=self.successful_analysis
            ),
            patch.object(service, "get_company_workbook", return_value="workbook"),
            patch.object(service, "upsert_company_email"),
            patch.object(service, "get_company_draft_id", return_value=None),
            patch.object(
                service,
                "create_company_gmail_draft",
                new=AsyncMock(return_value="draft-blocked"),
            ),
            patch.object(service, "upsert_company_draft"),
            patch.object(service, "mark_company_email_draft_ready"),
            patch.object(service, "send_company_email_notification", notify),
        ):
            asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )
            asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(notify.await_count, 1)
        with self.session_factory() as db:
            record = db.scalar(
                select(ProcessedEmail).where(
                    ProcessedEmail.company_id == plan.company_id
                )
            )
        self.assertIsNotNone(record.notification_blocked_at)
        self.assertIsNone(record.notified_at)

    def test_invalid_grant_revokes_only_the_company(self):
        plan = self.create_company("Revoked", activated_at=None)
        prepared = self.prepared([plan])
        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(
                    side_effect=GmailTokenRevokedError(
                        "gmail_token_revoked",
                        status_code=400,
                    )
                ),
            ),
            patch.object(service, "list_company_message_page") as list_messages,
            patch.object(
                service,
                "send_company_gmail_revoked_notification",
                new_callable=AsyncMock,
            ) as revoked_notification,
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    prepared,
                    session_factory=self.session_factory,
                )
            )
            asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.revoked_company_ids, [plan.company_id])
        list_messages.assert_not_called()
        revoked_notification.assert_awaited_once()
        with self.session_factory() as db:
            connection = db.scalar(
                select(GmailConnection).where(GmailConnection.user_id == plan.user_id)
            )
            company = db.get(Company, plan.company_id)
        self.assertEqual(connection.status, "revoked")
        self.assertIsNotNone(connection.revocation_notified_at)
        self.assertIsNotNone(company.pipeline_activated_at)

    def test_invalid_grant_failed_notification_leaves_marker_empty(self):
        plan = self.create_company("RevokedNotifyFailure", activated_at=None)
        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(
                    side_effect=GmailTokenRevokedError(
                        "gmail_token_revoked",
                        status_code=400,
                    )
                ),
            ),
            patch.object(
                service,
                "send_company_gmail_revoked_notification",
                new=AsyncMock(
                    side_effect=service.CompanyTelegramError(
                        "telegram_transport_error"
                    )
                ),
            ) as revoked_notification,
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.revoked_company_ids, [plan.company_id])
        revoked_notification.assert_awaited_once()
        with self.session_factory() as db:
            connection = db.scalar(
                select(GmailConnection).where(GmailConnection.user_id == plan.user_id)
            )
        self.assertEqual(connection.status, "revoked")
        self.assertIsNone(connection.revocation_notified_at)

    def test_invalid_grant_without_telegram_revokes_without_notification_marker(self):
        plan = self.create_company(
            "RevokedNoTelegram",
            activated_at=None,
            with_telegram=False,
        )
        with (
            patch.object(service, "decrypt_token", return_value="refresh"),
            patch.object(
                service,
                "get_company_access_token",
                new=AsyncMock(
                    side_effect=GmailTokenRevokedError(
                        "gmail_token_revoked",
                        status_code=400,
                    )
                ),
            ),
            patch.object(
                service,
                "send_company_gmail_revoked_notification",
                new_callable=AsyncMock,
            ) as revoked_notification,
        ):
            summary = asyncio.run(
                service.execute_multi_tenant_run(
                    self.prepared([plan]),
                    session_factory=self.session_factory,
                )
            )

        self.assertEqual(summary.revoked_company_ids, [plan.company_id])
        revoked_notification.assert_not_awaited()
        with self.session_factory() as db:
            connection = db.scalar(
                select(GmailConnection).where(GmailConnection.user_id == plan.user_id)
            )
        self.assertEqual(connection.status, "revoked")
        self.assertIsNone(connection.revocation_notified_at)


if __name__ == "__main__":
    unittest.main()
