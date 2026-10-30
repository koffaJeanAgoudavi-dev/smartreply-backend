"""Tests de l'endpoint permanent du pipeline multi-utilisateur."""

import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.modules.pipeline import router as pipeline_router
from app.modules.pipeline_selection import service


class PipelineFoundationsRouterTests(unittest.TestCase):
    def setUp(self):
        self.original_max = settings.MAX_EMAILS_PER_COMPANY_PER_RUN
        self.original_budget = settings.PIPELINE_RUN_TIME_BUDGET_SECONDS
        self.original_cron_secret = pipeline_router.CRON_SECRET
        settings.MAX_EMAILS_PER_COMPANY_PER_RUN = 10
        settings.PIPELINE_RUN_TIME_BUDGET_SECONDS = 240
        pipeline_router.CRON_SECRET = "expected-secret"
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        settings.MAX_EMAILS_PER_COMPANY_PER_RUN = self.original_max
        settings.PIPELINE_RUN_TIME_BUDGET_SECONDS = self.original_budget
        pipeline_router.CRON_SECRET = self.original_cron_secret

    @property
    def cron_headers(self) -> dict[str, str]:
        return {"X-CRON-SECRET": pipeline_router.CRON_SECRET}

    @staticmethod
    def prepared_run(*, lock_acquired: bool = True):
        return service.PreparedSimulationRun(
            result=service.SimulationResult(
                status="accepted" if lock_acquired else "already_running",
                company_ids=(7, 9) if lock_acquired else (),
                processing_company_ids=(7,) if lock_acquired else (),
                eligible_company_count=2 if lock_acquired else 0,
                processing_company_count=1 if lock_acquired else 0,
                simulation_company_count=1 if lock_acquired else 0,
                max_emails_cap=20 if lock_acquired else 0,
                processing_max_emails_cap=10 if lock_acquired else 0,
                max_emails_per_company=10,
                time_budget_seconds=240,
            ),
            plans=(),
            lock_acquired=lock_acquired,
            budget=service.PipelineRunBudget(total_seconds=240),
        )

    def test_valid_secret_returns_a_fast_202_and_schedules_background(self):
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
        ):
            started = time.perf_counter()
            response = self.client.post(
                "/api/v1/pipeline/run",
                headers=self.cron_headers,
            )
            elapsed = time.perf_counter() - started

        self.assertEqual(response.status_code, 202, response.text)
        self.assertLess(elapsed, 0.5)
        self.assertEqual(
            response.json(),
            {
                "status": "accepted",
                "company_ids": [7, 9],
                "processing_company_ids": [7],
                "eligible_company_count": 2,
                "processing_company_count": 1,
                "simulation_company_count": 1,
                "max_emails_cap": 20,
                "processing_max_emails_cap": 10,
                "max_emails_per_company": 10,
                "time_budget_seconds": 240,
            },
        )
        prepare.assert_called_once_with()
        add_task.assert_called_once_with(
            pipeline_router.complete_multi_tenant_run,
            prepared,
        )

    def test_busy_lock_returns_202_without_scheduling_another_passage(self):
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
            response = self.client.post(
                "/api/v1/pipeline/run",
                headers=self.cron_headers,
            )

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "already_running")
        self.assertEqual(response.json()["company_ids"], [])
        add_task.assert_not_called()

    def test_cron_fails_closed_when_server_secret_is_empty(self):
        pipeline_router.CRON_SECRET = ""
        with patch.object(
            pipeline_router,
            "prepare_multi_tenant_simulation",
        ) as prepare:
            response = self.client.post(
                "/api/v1/pipeline/run",
                headers={"X-CRON-SECRET": "provided-but-not-configured"},
            )

        self.assertEqual(response.status_code, 401, response.text)
        prepare.assert_not_called()

    def test_cron_rejects_missing_and_incorrect_headers(self):
        with patch.object(
            pipeline_router,
            "prepare_multi_tenant_simulation",
        ) as prepare:
            missing = self.client.post("/api/v1/pipeline/run")
            incorrect = self.client.post(
                "/api/v1/pipeline/run",
                headers={"X-CRON-SECRET": "wrong-secret"},
            )

        self.assertEqual(missing.status_code, 401, missing.text)
        self.assertEqual(incorrect.status_code, 401, incorrect.text)
        prepare.assert_not_called()

    def test_preparation_error_logs_only_its_type(self):
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
            response = self.client.post(
                "/api/v1/pipeline/run",
                headers=self.cron_headers,
            )

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
