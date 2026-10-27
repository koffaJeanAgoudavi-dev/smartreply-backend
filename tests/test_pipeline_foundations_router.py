"""Tests du branchement minimal du pipeline multi-tenant à son endpoint."""

import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.modules.pipeline import router as pipeline_router
from app.modules.pipeline_selection import service


class PipelineFoundationsRouterTests(unittest.TestCase):
    def setUp(self):
        self.original_enabled = settings.MULTI_TENANT_PIPELINE_ENABLED
        self.original_max = settings.MAX_EMAILS_PER_COMPANY_PER_RUN
        self.original_budget = settings.PIPELINE_RUN_TIME_BUDGET_SECONDS
        self.original_cron_secret = pipeline_router.CRON_SECRET
        settings.MULTI_TENANT_PIPELINE_ENABLED = False
        settings.MAX_EMAILS_PER_COMPANY_PER_RUN = 10
        settings.PIPELINE_RUN_TIME_BUDGET_SECONDS = 240
        pipeline_router.CRON_SECRET = ""
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        settings.MULTI_TENANT_PIPELINE_ENABLED = self.original_enabled
        settings.MAX_EMAILS_PER_COMPANY_PER_RUN = self.original_max
        settings.PIPELINE_RUN_TIME_BUDGET_SECONDS = self.original_budget
        pipeline_router.CRON_SECRET = self.original_cron_secret

    @staticmethod
    def prepared_run(*, lock_acquired: bool = True):
        return service.PreparedSimulationRun(
            result=service.SimulationResult(
                status="accepted" if lock_acquired else "already_running",
                company_ids=(7, 9) if lock_acquired else (),
                eligible_company_count=2 if lock_acquired else 0,
                would_process_email_count=20 if lock_acquired else 0,
                max_emails_per_company=10,
                time_budget_seconds=240,
            ),
            lock_acquired=lock_acquired,
            budget=service.PipelineRunBudget(total_seconds=240),
        )

    def test_disabled_switch_preserves_the_historical_200_response(self):
        historical_result = {
            "emails_traites": 1,
            "resultats": [{"historical": True}],
        }
        with (
            patch.object(
                pipeline_router,
                "run_pipeline",
                new=AsyncMock(return_value=historical_result),
            ) as old_pipeline,
            patch.object(
                pipeline_router,
                "prepare_multi_tenant_simulation",
                side_effect=AssertionError("new path must stay disabled"),
            ),
        ):
            response = self.client.post("/api/v1/pipeline/run")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), historical_result)
        old_pipeline.assert_awaited_once_with()

    def test_enabled_switch_returns_a_fast_202_and_schedules_background(self):
        settings.MULTI_TENANT_PIPELINE_ENABLED = True
        prepared = self.prepared_run()
        with (
            patch.object(
                pipeline_router,
                "prepare_multi_tenant_simulation",
                return_value=prepared,
            ) as prepare,
            patch.object(
                pipeline_router.BackgroundTasks,
                "add_task",
            ) as add_task,
            patch.object(
                pipeline_router,
                "run_pipeline",
                new=AsyncMock(side_effect=AssertionError("legacy path must not run")),
            ),
        ):
            started = time.perf_counter()
            response = self.client.post("/api/v1/pipeline/run")
            elapsed = time.perf_counter() - started

        self.assertEqual(response.status_code, 202, response.text)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(
            response.json(),
            {
                "status": "accepted",
                "company_ids": [7, 9],
                "eligible_company_count": 2,
                "would_process_email_count": 20,
                "max_emails_per_company": 10,
                "time_budget_seconds": 240,
            },
        )
        prepare.assert_called_once_with()
        add_task.assert_called_once_with(
            pipeline_router.complete_multi_tenant_simulation,
            prepared,
        )

    def test_busy_lock_returns_202_without_scheduling_another_passage(self):
        settings.MULTI_TENANT_PIPELINE_ENABLED = True
        prepared = self.prepared_run(lock_acquired=False)
        with (
            patch.object(
                pipeline_router,
                "prepare_multi_tenant_simulation",
                return_value=prepared,
            ),
            patch.object(
                pipeline_router.BackgroundTasks,
                "add_task",
            ) as add_task,
        ):
            response = self.client.post("/api/v1/pipeline/run")

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "already_running")
        self.assertEqual(response.json()["company_ids"], [])
        add_task.assert_not_called()

    def test_cron_secret_is_checked_before_both_paths(self):
        pipeline_router.CRON_SECRET = "expected-secret"
        with patch.object(
            pipeline_router,
            "prepare_multi_tenant_simulation",
        ) as prepare:
            response = self.client.post(
                "/api/v1/pipeline/run",
                headers={"X-CRON-SECRET": "wrong-secret"},
            )

        self.assertEqual(response.status_code, 401, response.text)
        prepare.assert_not_called()

    def test_preparation_error_logs_only_its_type(self):
        settings.MULTI_TENANT_PIPELINE_ENABLED = True
        private_error = RuntimeError(
            "person@example.com refresh-token telegram-chat-123"
        )
        with (
            patch.object(
                pipeline_router,
                "prepare_multi_tenant_simulation",
                side_effect=private_error,
            ),
            patch.object(pipeline_router.logger, "error") as log_error,
        ):
            response = self.client.post("/api/v1/pipeline/run")

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(
            response.json(),
            {"detail": "Simulation multi-tenant temporairement indisponible"},
        )
        log_format, *log_values = log_error.call_args.args
        rendered = log_format % tuple(log_values)
        self.assertIn("RuntimeError", rendered)
        self.assertNotIn("person@example.com", rendered)
        self.assertNotIn("refresh-token", rendered)
        self.assertNotIn("telegram-chat", rendered)


if __name__ == "__main__":
    unittest.main()
