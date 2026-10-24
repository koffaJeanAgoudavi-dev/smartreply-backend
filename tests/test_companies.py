"""Tests du module Companies sans appel réel à Google ou Neon."""

import unittest
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base, get_db
from app.main import app
from app.modules.auth.models import User
from app.modules.auth.security import create_access_token
from app.modules.companies import service
from app.modules.companies.schemas import CompanyCreate


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class FakeDriveClient:
    def __init__(self, post_results: list[FakeResponse | Exception]):
        self.post_results = list(post_results)
        self.posts: list[dict] = []
        self.deletes: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def post(self, url: str, **kwargs):
        self.posts.append({"url": url, **kwargs})
        result = self.post_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def delete(self, url: str, **kwargs):
        self.deletes.append({"url": url, **kwargs})
        return FakeResponse(204)


class DriveServiceTests(unittest.TestCase):
    def test_copy_and_share_returns_a_new_sheet_id(self):
        client = FakeDriveClient(
            [
                FakeResponse(200, {"id": "sheet-new"}),
                FakeResponse(200, {"id": "permission"}),
            ]
        )
        with (
            patch.object(
                service, "_get_drive_access_token", return_value="access-token"
            ),
            patch.object(service.httpx, "Client", return_value=client),
        ):
            result = service.duplicate_and_share_sheet("Acme", "owner@example.com")

        self.assertEqual(result, "sheet-new")
        self.assertEqual(len(client.posts), 2)
        self.assertIn(settings.GOOGLE_DRIVE_TEMPLATE_ID, client.posts[0]["url"])
        self.assertEqual(client.posts[0]["json"]["name"], "SmartReply Data - Acme")
        self.assertEqual(client.posts[1]["json"]["role"], "writer")
        self.assertEqual(client.posts[1]["json"]["emailAddress"], "owner@example.com")
        self.assertEqual(client.deletes, [])

    def test_owner_already_has_access_without_extra_permission(self):
        client = FakeDriveClient(
            [
                FakeResponse(
                    200,
                    {
                        "id": "sheet-owner",
                        "owners": [{"emailAddress": "owner@example.com"}],
                    },
                )
            ]
        )
        with (
            patch.object(
                service,
                "_get_drive_access_token",
                return_value="access-token",
            ),
            patch.object(service.httpx, "Client", return_value=client),
        ):
            result = service.duplicate_and_share_sheet("Acme", "OWNER@example.com")

        self.assertEqual(result, "sheet-owner")
        self.assertEqual(len(client.posts), 1)
        self.assertEqual(client.deletes, [])

    def test_permission_http_error_deletes_the_copy(self):
        client = FakeDriveClient(
            [FakeResponse(200, {"id": "sheet-orphan"}), FakeResponse(403)]
        )
        with (
            patch.object(
                service, "_get_drive_access_token", return_value="access-token"
            ),
            patch.object(service.httpx, "Client", return_value=client),
            self.assertRaises(service.DriveOperationError),
        ):
            service.duplicate_and_share_sheet("Acme", "owner@example.com")

        self.assertEqual(len(client.deletes), 1)
        self.assertTrue(client.deletes[0]["url"].endswith("/files/sheet-orphan"))

    def test_permission_network_error_deletes_the_copy(self):
        client = FakeDriveClient(
            [
                FakeResponse(200, {"id": "sheet-orphan"}),
                httpx.ReadTimeout("timeout"),
            ]
        )
        with (
            patch.object(
                service, "_get_drive_access_token", return_value="access-token"
            ),
            patch.object(service.httpx, "Client", return_value=client),
            self.assertRaises(service.DriveOperationError),
        ):
            service.duplicate_and_share_sheet("Acme", "owner@example.com")

        self.assertEqual(len(client.deletes), 1)

    def test_missing_owner_oauth_configuration_is_rejected(self):
        original_values = (
            settings.GOOGLE_DRIVE_CLIENT_ID,
            settings.GOOGLE_DRIVE_CLIENT_SECRET,
            settings.GOOGLE_DRIVE_REFRESH_TOKEN,
        )
        settings.GOOGLE_DRIVE_CLIENT_ID = ""
        settings.GOOGLE_DRIVE_CLIENT_SECRET = ""
        settings.GOOGLE_DRIVE_REFRESH_TOKEN = ""
        try:
            with self.assertRaises(service.DriveConfigurationError):
                service._get_drive_access_token()
        finally:
            (
                settings.GOOGLE_DRIVE_CLIENT_ID,
                settings.GOOGLE_DRIVE_CLIENT_SECRET,
                settings.GOOGLE_DRIVE_REFRESH_TOKEN,
            ) = original_values

    def test_owner_oauth_refresh_returns_an_access_token(self):
        original_values = (
            settings.GOOGLE_DRIVE_CLIENT_ID,
            settings.GOOGLE_DRIVE_CLIENT_SECRET,
            settings.GOOGLE_DRIVE_REFRESH_TOKEN,
        )
        settings.GOOGLE_DRIVE_CLIENT_ID = "client-id"
        settings.GOOGLE_DRIVE_CLIENT_SECRET = "client-secret"
        settings.GOOGLE_DRIVE_REFRESH_TOKEN = "refresh-token"
        credentials = MagicMock()
        credentials.token = "refreshed-access-token"
        try:
            with (
                patch.object(
                    service, "Credentials", return_value=credentials
                ) as constructor,
                patch.object(service, "Request", return_value=MagicMock()),
            ):
                token = service._get_drive_access_token()
        finally:
            (
                settings.GOOGLE_DRIVE_CLIENT_ID,
                settings.GOOGLE_DRIVE_CLIENT_SECRET,
                settings.GOOGLE_DRIVE_REFRESH_TOKEN,
            ) = original_values

        self.assertEqual(token, "refreshed-access-token")
        constructor.assert_called_once_with(
            token=None,
            refresh_token="refresh-token",
            token_uri=service.DRIVE_TOKEN_URI,
            client_id="client-id",
            client_secret="client-secret",
            scopes=[service.DRIVE_SCOPE],
        )
        credentials.refresh.assert_called_once()

    def test_database_race_deletes_the_losing_copy(self):
        db = MagicMock()
        db.commit.side_effect = IntegrityError("insert", {}, Exception("unique"))
        data = CompanyCreate(nom_entreprise="Acme", secteur="Conseil")
        existing_company = MagicMock()

        with (
            patch.object(
                service,
                "get_company_by_user_id",
                side_effect=[None, existing_company],
            ),
            patch.object(service, "get_company_by_sheet_id", return_value=None),
            patch.object(
                service,
                "duplicate_and_share_sheet",
                return_value="sheet-losing-race",
            ),
            patch.object(
                service, "delete_orphaned_sheet", return_value=True
            ) as cleanup,
            self.assertRaises(service.CompanyAlreadyExistsError),
        ):
            service.create_company_record(
                db=db,
                user_id=1,
                user_email="owner@example.com",
                data=data,
            )

        db.rollback.assert_called_once()
        cleanup.assert_called_once_with("sheet-losing-race")


class CompaniesApiTests(unittest.TestCase):
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
        self.original_jwt_secret = settings.JWT_SECRET
        settings.JWT_SECRET = "companies-tests-secret-at-least-32-bytes"

        with self.session_factory() as db:
            user = User(email="user@example.com", hashed_password="not-used")
            db.add(user)
            db.commit()
            db.refresh(user)
            self.user_id = user.id
        self.token = create_access_token(self.user_id, "user@example.com")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        settings.JWT_SECRET = self.original_jwt_secret
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @property
    def authorization(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def test_create_get_and_reject_a_second_company(self):
        with patch.object(
            service,
            "duplicate_and_share_sheet",
            return_value="sheet-company-one",
        ) as duplicate:
            created = self.client.post(
                "/api/v1/companies",
                headers=self.authorization,
                json={"nom_entreprise": "  Acme  ", "secteur": "  Conseil  "},
            )
            fetched = self.client.get(
                "/api/v1/companies/me",
                headers=self.authorization,
            )
            duplicate_attempt = self.client.post(
                "/api/v1/companies",
                headers=self.authorization,
                json={"nom_entreprise": "Autre", "secteur": "Commerce"},
            )

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["sheet_id"], "sheet-company-one")
        self.assertEqual(created.json()["nom_entreprise"], "Acme")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["id"], created.json()["id"])
        self.assertEqual(duplicate_attempt.status_code, 409, duplicate_attempt.text)
        duplicate.assert_called_once_with("Acme", "user@example.com")

    def test_two_users_receive_two_distinct_sheets(self):
        with self.session_factory() as db:
            second_user = User(email="second@example.com", hashed_password="not-used")
            db.add(second_user)
            db.commit()
            db.refresh(second_user)
            second_token = create_access_token(second_user.id, second_user.email)

        with patch.object(
            service,
            "duplicate_and_share_sheet",
            side_effect=["sheet-user-one", "sheet-user-two"],
        ):
            first_response = self.client.post(
                "/api/v1/companies",
                headers=self.authorization,
                json={"nom_entreprise": "Première", "secteur": "Conseil"},
            )
            second_response = self.client.post(
                "/api/v1/companies",
                headers={"Authorization": f"Bearer {second_token}"},
                json={"nom_entreprise": "Deuxième", "secteur": "Commerce"},
            )

        self.assertEqual(first_response.status_code, 201, first_response.text)
        self.assertEqual(second_response.status_code, 201, second_response.text)
        self.assertNotEqual(
            first_response.json()["sheet_id"],
            second_response.json()["sheet_id"],
        )

    def test_get_returns_404_when_no_company_exists(self):
        response = self.client.get(
            "/api/v1/companies/me",
            headers=self.authorization,
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_jwt_is_required(self):
        response = self.client.get("/api/v1/companies/me")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_whitespace_only_company_name_is_rejected(self):
        response = self.client.post(
            "/api/v1/companies",
            headers=self.authorization,
            json={"nom_entreprise": "   ", "secteur": "Conseil"},
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
