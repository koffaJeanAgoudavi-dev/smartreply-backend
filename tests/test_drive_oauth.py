"""Tests du Drive OAuth client sans appel réel à Google ni à Neon."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from google.auth.exceptions import RefreshError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base, get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.auth.security import create_access_token
from app.modules.companies import service as companies_service
from app.modules.companies.models import Company
from app.modules.drive_oauth import router as drive_router
from app.modules.drive_oauth import service as drive_service
from app.modules.drive_oauth.models import DriveConnection
from app.modules.drive_oauth.security import sign_state as sign_drive_state
from app.modules.drive_oauth.security import verify_state as verify_drive_state
from app.modules.gmail_oauth.models import GmailConnection
from app.modules.gmail_oauth.security import sign_state as sign_gmail_state
from app.modules.gmail_oauth.security import verify_state as verify_gmail_state
from app.modules.pipeline_selection.service import select_eligible_company_plans
from app.modules.sheets.service import DRAFT_HEADERS, EMAIL_HEADERS

SERVICE_ACCOUNT_EMAIL = "pipeline-sa@example.iam.gserviceaccount.com"


class FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None):
        self.status_code = status_code
        self._payload = {} if payload is None else payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeDriveClient:
    def __init__(self, *, posts=None, gets=None, deletes=None):
        self.post_results = list(posts or [])
        self.get_results = list(gets or [])
        self.delete_results = list(deletes or [])
        self.posts = []
        self.gets = []
        self.deletes = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    @staticmethod
    def _result(results):
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return self._result(self.post_results)

    def get(self, url, **kwargs):
        self.gets.append({"url": url, **kwargs})
        return self._result(self.get_results)

    def delete(self, url, **kwargs):
        self.deletes.append({"url": url, **kwargs})
        if self.delete_results:
            return self._result(self.delete_results)
        return FakeResponse(204)


class FakeWorksheet:
    def __init__(self, row):
        self.row = row

    def get(self, _range):
        return [list(self.row)]


class FakeWorkbook:
    def __init__(self, email_headers=EMAIL_HEADERS, draft_headers=DRAFT_HEADERS):
        self.sheets = {
            "Emails": FakeWorksheet(email_headers),
            "Brouillons": FakeWorksheet(draft_headers),
        }

    def worksheet(self, name):
        return self.sheets[name]


class DriveOAuthUrlTests(unittest.TestCase):
    def setUp(self):
        self.original = (
            settings.BASE_URL,
            settings.ENVIRONMENT,
            settings.GOOGLE_DRIVE_CLIENT_ID,
            settings.GOOGLE_DRIVE_CLIENT_SECRET,
        )
        settings.BASE_URL = "https://smartreply.example"
        settings.ENVIRONMENT = "production"
        settings.GOOGLE_DRIVE_CLIENT_ID = "drive-client-id"
        settings.GOOGLE_DRIVE_CLIENT_SECRET = "drive-client-secret"

    def tearDown(self):
        (
            settings.BASE_URL,
            settings.ENVIRONMENT,
            settings.GOOGLE_DRIVE_CLIENT_ID,
            settings.GOOGLE_DRIVE_CLIENT_SECRET,
        ) = self.original

    def test_authorization_is_separate_and_uses_only_drive_file(self):
        url = drive_service.build_authorization_url("signed-state")
        query = parse_qs(urlsplit(url).query)

        self.assertEqual(query["scope"], [drive_service.DRIVE_FILE_SCOPE])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["include_granted_scopes"], ["false"])
        self.assertEqual(
            query["redirect_uri"],
            ["https://smartreply.example/api/v1/drive/callback"],
        )
        self.assertNotIn("gmail", query["scope"][0])


class DriveOAuthApiTests(unittest.TestCase):
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

        def override_get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.original = (
            settings.BASE_URL,
            settings.ENVIRONMENT,
            settings.GOOGLE_DRIVE_CLIENT_ID,
            settings.GOOGLE_DRIVE_CLIENT_SECRET,
            settings.JWT_SECRET,
            settings.TOKEN_ENCRYPTION_KEY,
        )
        settings.BASE_URL = "https://smartreply.example"
        settings.ENVIRONMENT = "production"
        settings.GOOGLE_DRIVE_CLIENT_ID = "drive-client-id"
        settings.GOOGLE_DRIVE_CLIENT_SECRET = "drive-client-secret"
        settings.JWT_SECRET = "drive-api-test-jwt-secret-at-least-32-bytes"
        settings.TOKEN_ENCRYPTION_KEY = "drive-api-test-encryption-key"
        with self.session_factory() as db:
            user = User(email="owner@example.com", hashed_password="unused")
            db.add(user)
            db.commit()
            db.refresh(user)
            self.user_id = user.id
        self.token = create_access_token(self.user_id, "owner@example.com")
        self.authorization = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        (
            settings.BASE_URL,
            settings.ENVIRONMENT,
            settings.GOOGLE_DRIVE_CLIENT_ID,
            settings.GOOGLE_DRIVE_CLIENT_SECRET,
            settings.JWT_SECRET,
            settings.TOKEN_ENCRYPTION_KEY,
        ) = self.original
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_connect_requires_jwt_and_returns_no_token(self):
        unauthorized = self.client.get("/api/v1/drive/connect")
        authorized = self.client.get(
            "/api/v1/drive/connect",
            headers=self.authorization,
        )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200, authorized.text)
        self.assertEqual(
            urlsplit(authorized.json()["authorization_url"]).hostname,
            "accounts.google.com",
        )
        self.assertNotIn("refresh_token", authorized.text)

    def test_callback_encrypts_token_and_redirects_without_exposing_it(self):
        with (
            patch.object(drive_router, "verify_state", return_value=self.user_id),
            patch.object(
                drive_router.service,
                "exchange_code_for_refresh_token",
                new=AsyncMock(return_value="plain-refresh-token"),
            ),
        ):
            response = self.client.get(
                "/api/v1/drive/callback?code=google-code&state=signed-state",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(response.headers["location"], "/dashboard/?drive=connected")
        self.assertNotIn("plain-refresh-token", response.text)
        self.assertNotIn("plain-refresh-token", response.headers["location"])
        with self.session_factory() as db:
            connection = db.execute(
                select(DriveConnection).where(DriveConnection.user_id == self.user_id)
            ).scalar_one()
            self.assertNotEqual(
                connection.encrypted_refresh_token,
                "plain-refresh-token",
            )
            self.assertNotIn(
                "plain-refresh-token",
                connection.encrypted_refresh_token,
            )

    def test_deleted_user_callback_stops_before_google_exchange(self):
        exchange = AsyncMock()
        with (
            patch.object(drive_router, "verify_state", return_value=999999),
            patch.object(
                drive_router.service,
                "exchange_code_for_refresh_token",
                new=exchange,
            ),
        ):
            response = self.client.get(
                "/api/v1/drive/callback?code=google-code&state=signed-state",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Utilisateur introuvable"})
        exchange.assert_not_awaited()

    def test_drive_and_gmail_states_cannot_be_replayed_across_callbacks(self):
        drive_state = sign_drive_state(self.user_id)
        gmail_state = sign_gmail_state(self.user_id)

        self.assertEqual(verify_drive_state(drive_state), self.user_id)
        self.assertEqual(verify_gmail_state(gmail_state), self.user_id)
        self.assertIsNone(verify_drive_state(gmail_state))
        self.assertIsNone(verify_gmail_state(drive_state))


class ClientOwnedSheetTests(unittest.TestCase):
    def setUp(self):
        self.original_service_account = settings.GOOGLE_SERVICE_ACCOUNT_JSON
        settings.GOOGLE_SERVICE_ACCOUNT_JSON = (
            '{"client_email":"pipeline-sa@example.iam.gserviceaccount.com"}'
        )

    def tearDown(self):
        settings.GOOGLE_SERVICE_ACCOUNT_JSON = self.original_service_account

    def test_sheet_is_created_by_client_token_and_only_service_account_is_shared(self):
        client = FakeDriveClient(
            posts=[
                FakeResponse(200, {"spreadsheetId": "client-owned-sheet"}),
                FakeResponse(200, {"id": "service-writer"}),
            ],
            gets=[
                FakeResponse(
                    200,
                    {
                        "id": "client-owned-sheet",
                        "trashed": False,
                        "ownedByMe": True,
                        "owners": [{"emailAddress": "google-owner@example.com"}],
                    },
                )
            ],
        )
        sheets_client = MagicMock()
        with (
            patch.object(companies_service.httpx, "Client", return_value=client),
            patch.object(
                companies_service,
                "get_sheets_client",
                return_value=sheets_client,
            ),
        ):
            sheet_id = companies_service.create_client_owned_sheet(
                "Acme",
                "client-oauth-access-token",
            )

        self.assertEqual(sheet_id, "client-owned-sheet")
        self.assertEqual(len(client.posts), 2)
        creation = client.posts[0]
        self.assertEqual(
            creation["url"],
            "https://sheets.googleapis.com/v4/spreadsheets",
        )
        self.assertEqual(
            creation["headers"]["Authorization"],
            "Bearer client-oauth-access-token",
        )
        self.assertEqual(
            [sheet["properties"]["title"] for sheet in creation["json"]["sheets"]],
            ["Emails", "Brouillons"],
        )
        email_values = creation["json"]["sheets"][0]["data"][0]["rowData"][0]["values"]
        self.assertEqual(
            [value["userEnteredValue"]["stringValue"] for value in email_values],
            EMAIL_HEADERS,
        )
        permission = client.posts[1]
        self.assertEqual(permission["json"]["role"], "writer")
        self.assertEqual(
            permission["json"]["emailAddress"],
            SERVICE_ACCOUNT_EMAIL,
        )
        self.assertEqual(permission["params"]["sendNotificationEmail"], "false")
        self.assertEqual(client.deletes, [])
        self.assertIn("ownedByMe", client.gets[0]["params"]["fields"])
        sheets_client.open_by_key.assert_called_once_with("client-owned-sheet")

    def test_google_must_confirm_the_authorized_client_owns_the_file(self):
        client = FakeDriveClient(
            posts=[FakeResponse(200, {"spreadsheetId": "not-owned-sheet"})],
            gets=[
                FakeResponse(
                    200,
                    {
                        "id": "not-owned-sheet",
                        "trashed": False,
                        "ownedByMe": False,
                        "owners": [{"emailAddress": "other@example.com"}],
                    },
                )
            ],
        )
        with (
            patch.object(companies_service.httpx, "Client", return_value=client),
            self.assertRaises(companies_service.DriveOperationError),
        ):
            companies_service.create_client_owned_sheet("Acme", "client-token")

        self.assertEqual(len(client.posts), 1)
        self.assertEqual(len(client.deletes), 1)

    def test_share_failure_deletes_the_unpersisted_client_file(self):
        client = FakeDriveClient(
            posts=[
                FakeResponse(200, {"spreadsheetId": "orphan-sheet"}),
                FakeResponse(403),
            ],
            gets=[
                FakeResponse(
                    200,
                    {
                        "id": "orphan-sheet",
                        "trashed": False,
                        "ownedByMe": True,
                        "owners": [{"emailAddress": "owner@example.com"}],
                    },
                )
            ],
        )
        with (
            patch.object(companies_service.httpx, "Client", return_value=client),
            self.assertRaises(companies_service.DriveOperationError),
        ):
            companies_service.create_client_owned_sheet("Acme", "client-token")

        self.assertEqual(len(client.deletes), 1)
        self.assertTrue(client.deletes[0]["url"].endswith("/files/orphan-sheet"))


class DriveStatusTests(unittest.TestCase):
    def setUp(self):
        self.original_service_account = settings.GOOGLE_SERVICE_ACCOUNT_JSON
        settings.GOOGLE_SERVICE_ACCOUNT_JSON = (
            '{"client_email":"pipeline-sa@example.iam.gserviceaccount.com"}'
        )
        self.company = SimpleNamespace(
            sheet_id="client-sheet",
            sheet_owner_mode="client",
        )
        self.connection = SimpleNamespace(
            encrypted_refresh_token="encrypted",
            status="connected",
        )

    def tearDown(self):
        settings.GOOGLE_SERVICE_ACCOUNT_JSON = self.original_service_account

    def status_with(self, client, workbook=None):
        sheets_client = MagicMock()
        sheets_client.open_by_key.return_value = workbook or FakeWorkbook()
        with (
            patch.object(
                companies_service,
                "get_company_by_user_id",
                return_value=self.company,
            ),
            patch.object(
                companies_service.drive_oauth_service,
                "get_connection",
                return_value=self.connection,
            ),
            patch.object(
                companies_service.drive_oauth_service,
                "get_user_access_token",
                return_value="access-token",
            ),
            patch.object(companies_service.httpx, "Client", return_value=client),
            patch.object(
                companies_service,
                "get_sheets_client",
                return_value=sheets_client,
            ),
        ):
            return companies_service.get_drive_status(MagicMock(), 42)

    def test_ready_requires_file_permission_and_expected_structure(self):
        client = FakeDriveClient(
            gets=[
                FakeResponse(
                    200, {"id": "client-sheet", "trashed": False, "ownedByMe": True}
                ),
                FakeResponse(
                    200,
                    {
                        "permissions": [
                            {
                                "id": "service-permission",
                                "emailAddress": SERVICE_ACCOUNT_EMAIL,
                                "role": "writer",
                                "type": "user",
                            }
                        ]
                    },
                ),
            ]
        )
        status = self.status_with(client)
        self.assertEqual(
            status,
            {
                "connected": True,
                "ownership": "client",
                "sheet_state": "ready",
                "pipeline_access": True,
            },
        )

    def test_deleted_trashed_permission_and_structure_states_are_distinct(self):
        missing = self.status_with(FakeDriveClient(gets=[FakeResponse(404)]))
        trashed = self.status_with(
            FakeDriveClient(gets=[FakeResponse(200, {"trashed": True})])
        )
        ownership_changed = self.status_with(
            FakeDriveClient(
                gets=[
                    FakeResponse(
                        200,
                        {"trashed": False, "ownedByMe": False},
                    )
                ]
            )
        )
        permission_removed = self.status_with(
            FakeDriveClient(
                gets=[
                    FakeResponse(200, {"trashed": False, "ownedByMe": True}),
                    FakeResponse(200, {"permissions": []}),
                ]
            )
        )
        invalid_structure = self.status_with(
            FakeDriveClient(
                gets=[
                    FakeResponse(200, {"trashed": False, "ownedByMe": True}),
                    FakeResponse(
                        200,
                        {
                            "permissions": [
                                {
                                    "emailAddress": SERVICE_ACCOUNT_EMAIL,
                                    "role": "writer",
                                }
                            ]
                        },
                    ),
                ]
            ),
            workbook=FakeWorkbook(email_headers=["Wrong"]),
        )

        self.assertEqual(missing["sheet_state"], "missing")
        self.assertEqual(trashed["sheet_state"], "trashed")
        self.assertEqual(
            ownership_changed["sheet_state"],
            "ownership_changed",
        )
        self.assertEqual(permission_removed["sheet_state"], "access_revoked")
        self.assertEqual(invalid_structure["sheet_state"], "structure_invalid")
        self.assertFalse(missing["pipeline_access"])
        self.assertFalse(permission_removed["pipeline_access"])


class DriveDisconnectAndEligibilityTests(unittest.TestCase):
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
        self.original_service_account = settings.GOOGLE_SERVICE_ACCOUNT_JSON
        settings.GOOGLE_SERVICE_ACCOUNT_JSON = (
            '{"client_email":"pipeline-sa@example.iam.gserviceaccount.com"}'
        )

    def tearDown(self):
        settings.GOOGLE_SERVICE_ACCOUNT_JSON = self.original_service_account
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create_client_company(self, db):
        user = User(email="owner@example.com", hashed_password="unused")
        db.add(user)
        db.flush()
        company = Company(
            user_id=user.id,
            nom_entreprise="Acme",
            secteur="Conseil",
            sheet_id="client-sheet",
            sheet_owner_mode="client",
        )
        connection = DriveConnection(
            user_id=user.id,
            encrypted_refresh_token="encrypted-drive-token",
        )
        gmail = GmailConnection(
            user_id=user.id,
            encrypted_refresh_token="encrypted-gmail-token",
            status="connected",
        )
        db.add_all([company, connection, gmail])
        db.commit()
        return user, company

    def test_disconnect_removes_service_permission_but_keeps_client_file_record(self):
        with self.session_factory() as db:
            user, company = self.create_client_company(db)
            user_id = user.id
            company_id = company.id
            client = FakeDriveClient(
                gets=[
                    FakeResponse(
                        200,
                        {
                            "permissions": [
                                {
                                    "id": "service-permission",
                                    "emailAddress": SERVICE_ACCOUNT_EMAIL,
                                    "role": "writer",
                                }
                            ]
                        },
                    )
                ],
                deletes=[FakeResponse(204)],
            )
            with (
                patch.object(
                    companies_service.drive_oauth_service,
                    "get_user_access_token",
                    return_value="access-token",
                ),
                patch.object(
                    companies_service.drive_oauth_service,
                    "revoke_refresh_token",
                    return_value=True,
                ) as revoke,
                patch.object(companies_service.httpx, "Client", return_value=client),
            ):
                result = companies_service.disconnect_client_drive(db, user_id)

            self.assertEqual(
                result,
                {
                    "disconnected": True,
                    "pipeline_access_removed": True,
                    "manual_action_required": False,
                },
            )
            self.assertIsNone(
                db.execute(
                    select(DriveConnection).where(DriveConnection.user_id == user_id)
                ).scalar_one_or_none()
            )
            persisted_company = db.get(Company, company_id)
            self.assertEqual(persisted_company.sheet_id, "client-sheet")
            self.assertEqual(persisted_company.sheet_owner_mode, "client")
            self.assertEqual(len(client.deletes), 1)
            self.assertTrue(
                client.deletes[0]["url"].endswith(
                    "/files/client-sheet/permissions/service-permission"
                )
            )
            revoke.assert_called_once_with("encrypted-drive-token")

            plans = select_eligible_company_plans(db, max_emails_per_company=10)
            self.assertEqual(plans, ())

    def test_google_invalid_grant_marks_connection_revoked_and_stops_eligibility(self):
        with self.session_factory() as db:
            user, _company = self.create_client_company(db)
            credentials = MagicMock()
            credentials.refresh.side_effect = RefreshError("invalid_grant")
            with (
                patch.object(drive_service, "_require_client_configuration"),
                patch.object(
                    drive_service, "decrypt_token", return_value="refresh-token"
                ),
                patch.object(drive_service, "Credentials", return_value=credentials),
                patch.object(drive_service, "Request", return_value=MagicMock()),
                self.assertRaises(drive_service.DriveAuthorizationRevokedError),
            ):
                drive_service.get_user_access_token(db, user.id)

            connection = db.execute(
                select(DriveConnection).where(DriveConnection.user_id == user.id)
            ).scalar_one()
            self.assertEqual(connection.status, "revoked")
            self.assertEqual(
                select_eligible_company_plans(db, max_emails_per_company=10),
                (),
            )

    def test_retryable_google_error_does_not_mark_connection_revoked(self):
        with self.session_factory() as db:
            user, _company = self.create_client_company(db)
            credentials = MagicMock()
            credentials.refresh.side_effect = RefreshError(
                "temporary",
                retryable=True,
            )
            with (
                patch.object(drive_service, "_require_client_configuration"),
                patch.object(
                    drive_service, "decrypt_token", return_value="refresh-token"
                ),
                patch.object(drive_service, "Credentials", return_value=credentials),
                patch.object(drive_service, "Request", return_value=MagicMock()),
                self.assertRaises(drive_service.DriveOAuthConfigurationError),
            ):
                drive_service.get_user_access_token(db, user.id)

            connection = db.execute(
                select(DriveConnection).where(DriveConnection.user_id == user.id)
            ).scalar_one()
            self.assertEqual(connection.status, "connected")
            self.assertEqual(
                [
                    plan.user_id
                    for plan in select_eligible_company_plans(
                        db,
                        max_emails_per_company=10,
                    )
                ],
                [user.id],
            )

    def test_client_company_is_eligible_only_while_drive_connection_exists(self):
        with self.session_factory() as db:
            user, _company = self.create_client_company(db)
            self.assertEqual(
                [
                    plan.user_id
                    for plan in select_eligible_company_plans(
                        db,
                        max_emails_per_company=10,
                    )
                ],
                [user.id],
            )
            connection = db.execute(
                select(DriveConnection).where(DriveConnection.user_id == user.id)
            ).scalar_one()
            connection.status = "revoked"
            db.commit()
            self.assertEqual(
                select_eligible_company_plans(db, max_emails_per_company=10),
                (),
            )
            connection.status = "connected"
            db.commit()
            db.delete(connection)
            db.commit()
            self.assertEqual(
                select_eligible_company_plans(db, max_emails_per_company=10),
                (),
            )


if __name__ == "__main__":
    unittest.main()
