"""Tests de non-régression du hardening de sécurité PR1."""

import math
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import urljoin

from fastapi import APIRouter
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import settings
from app.core.rate_limit import (
    InMemoryRateLimiter,
    RateLimitExceeded,
    RedisRateLimiter,
    ResilientRateLimiter,
    _build_rate_limiter,
    get_rate_limiter,
)
from app.db.database import get_db
from app.main import app, create_app
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.security import create_access_token
from app.modules.auth.service import normalize_email
from app.modules.gmail_oauth import router as gmail_router


class FakeRedis:
    """Double déterministe du script Redis, avec horloge et expirations."""

    def __init__(self):
        self.now = 0.0
        self.values: dict[str, tuple[int, float]] = {}
        self.eval_calls = 0
        self.seen_keys: list[str] = []

    def eval(self, _script, number_of_keys, *keys_and_windows):
        self.eval_calls += 1
        keys = list(keys_and_windows[:number_of_keys])
        windows = [int(value) for value in keys_and_windows[number_of_keys:]]
        self.seen_keys.extend(keys)
        result = []
        for key, window in zip(keys, windows, strict=True):
            current = self.values.get(key)
            if current is None or current[1] <= self.now:
                count = 1
                expires_at = self.now + window
            else:
                count = current[0] + 1
                expires_at = current[1]
            self.values[key] = (count, expires_at)
            ttl = max(1, math.ceil(expires_at - self.now))
            result.extend((count, ttl))
        return result

    def advance(self, seconds: float):
        self.now += seconds


class FailingRedis:
    def __init__(self):
        self.eval_calls = 0

    def eval(self, *_args):
        self.eval_calls += 1
        raise RedisConnectionError("redis unavailable")


class SwitchableRedis(FakeRedis):
    def __init__(self):
        super().__init__()
        self.unavailable = False

    def eval(self, *args):
        if self.unavailable:
            self.eval_calls += 1
            raise RedisConnectionError("redis became unavailable")
        return super().eval(*args)


class AuthRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = {
            "JWT_SECRET": settings.JWT_SECRET,
            "INVITATION_CODES": settings.INVITATION_CODES,
            "CLIENT_IP_HEADER": settings.CLIENT_IP_HEADER,
            "ENVIRONMENT": settings.ENVIRONMENT,
            "REDIS_URL": settings.REDIS_URL,
            "RATE_LIMIT_HMAC_SECRET": settings.RATE_LIMIT_HMAC_SECRET,
            "LOGIN_IP_EMAIL_RATE_LIMIT": settings.LOGIN_IP_EMAIL_RATE_LIMIT,
            "LOGIN_IP_RATE_LIMIT": settings.LOGIN_IP_RATE_LIMIT,
            "LOGIN_EMAIL_RATE_LIMIT": settings.LOGIN_EMAIL_RATE_LIMIT,
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS": (
                settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
            ),
            "REGISTER_IP_RATE_LIMIT": settings.REGISTER_IP_RATE_LIMIT,
            "REGISTER_RATE_LIMIT_WINDOW_SECONDS": (
                settings.REGISTER_RATE_LIMIT_WINDOW_SECONDS
            ),
        }
        settings.JWT_SECRET = "hardening-test-jwt-secret-at-least-32-bytes"
        settings.INVITATION_CODES = "invite-alpha"
        settings.CLIENT_IP_HEADER = "X-Test-Client-IP"
        settings.ENVIRONMENT = "development"
        settings.LOGIN_IP_EMAIL_RATE_LIMIT = 5
        settings.LOGIN_IP_RATE_LIMIT = 20
        settings.LOGIN_EMAIL_RATE_LIMIT = 5
        settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS = 15 * 60
        settings.REGISTER_IP_RATE_LIMIT = 5
        settings.REGISTER_RATE_LIMIT_WINDOW_SECONDS = 60 * 60

        self.db = MagicMock()

        def override_get_db():
            yield self.db

        _build_rate_limiter.cache_clear()
        self.redis = FakeRedis()
        self.limiter = RedisRateLimiter(
            self.redis,
            "hardening-test-rate-limit-secret-at-least-32-bytes",
        )
        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_rate_limiter] = lambda: self.limiter
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        _build_rate_limiter.cache_clear()
        for name, value in self.original_settings.items():
            setattr(settings, name, value)

    def login(
        self,
        email: str,
        *,
        password: str = "bad-password",
        client_ip: str = "192.0.2.10",
        extra_headers: dict[str, str] | None = None,
    ):
        headers = {"X-Test-Client-IP": client_ip}
        headers.update(extra_headers or {})
        return self.client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers=headers,
        )

    def register(self, client_ip: str = "192.0.2.10"):
        return self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "new-user@example.com",
                "password": "motdepasse123",
                "invitation_code": "wrong-code",
            },
            headers={"X-Test-Client-IP": client_ip},
        )

    def test_login_ip_email_limit_counts_successes_and_failures_without_reset(self):
        user = SimpleNamespace(id=7, email="alice@example.com")

        def authenticate(_db, _email, password):
            return user if password == "good-password" else None

        with patch(
            "app.modules.auth.router.authenticate_user",
            side_effect=authenticate,
        ) as authenticate_user:
            responses = [
                self.login("Alice@Example.com", password="bad-password"),
                self.login("alice@example.com", password="good-password"),
                self.login("alice@example.com", password="bad-password"),
                self.login("alice@example.com", password="good-password"),
                self.login("alice@example.com", password="bad-password"),
            ]
            blocked = self.login("alice@example.com", password="good-password")

        self.assertEqual(
            [response.status_code for response in responses], [401, 200, 401, 200, 401]
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["Retry-After"], "900")
        self.assertEqual(authenticate_user.call_count, 5)
        self.assertNotIn("alice@example.com", " ".join(self.redis.seen_keys))

    def test_success_on_one_account_does_not_reset_another_account(self):
        user = SimpleNamespace(id=8, email="bob@example.com")

        def authenticate(_db, email, password):
            if normalize_email(email) == "bob@example.com" and password == "good":
                return user
            return None

        with patch(
            "app.modules.auth.router.authenticate_user",
            side_effect=authenticate,
        ) as authenticate_user:
            alice_before_success = [
                self.login("alice@example.com") for _ in range(4)
            ]
            bob_success = self.login("bob@example.com", password="good")
            alice_last_allowed = self.login("alice@example.com")
            alice_blocked = self.login("alice@example.com")

        self.assertEqual(
            [response.status_code for response in alice_before_success],
            [401] * 4,
        )
        self.assertEqual(bob_success.status_code, 200)
        self.assertEqual(alice_last_allowed.status_code, 401)
        self.assertEqual(alice_blocked.status_code, 429)
        self.assertEqual(authenticate_user.call_count, 6)

    def test_login_global_ip_limit_is_independent_from_email_limit(self):
        with patch(
            "app.modules.auth.router.authenticate_user", return_value=None
        ) as authenticate_user:
            for email_index in range(4):
                for _ in range(5):
                    response = self.login(f"user-{email_index}@example.com")
                    self.assertEqual(response.status_code, 401, response.text)
            blocked = self.login("fifth-user@example.com")
            independent_ip = self.login(
                "fifth-user@example.com",
                client_ip="198.51.100.20",
            )

        self.assertEqual(blocked.status_code, 429)
        self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)
        self.assertEqual(independent_ip.status_code, 401)
        self.assertEqual(authenticate_user.call_count, 21)

    def test_registration_limit_runs_before_invitation_validation(self):
        with patch(
            "app.modules.auth.router._invitation_code_is_valid",
            return_value=False,
        ) as invitation_check:
            responses = [self.register() for _ in range(5)]
            blocked = self.register()
            independent_ip = self.register("198.51.100.22")

        self.assertEqual([response.status_code for response in responses], [403] * 5)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["Retry-After"], "3600")
        self.assertEqual(independent_ip.status_code, 403)
        self.assertEqual(invitation_check.call_count, 6)

    def test_expired_login_counters_are_isolated_and_usable_again(self):
        buckets = self.limiter.login_buckets("192.0.2.10", "alice@example.com")
        for _ in range(5):
            self.limiter.consume(buckets)
        with self.assertRaises(RateLimitExceeded):
            self.limiter.consume(buckets)

        other_identity = self.limiter.login_buckets("192.0.2.10", "bob@example.com")
        self.limiter.consume(other_identity)
        self.redis.advance(901)
        self.limiter.consume(buckets)

        counts = [self.redis.values[bucket.key][0] for bucket in buckets]
        self.assertEqual(counts, [1, 1])

    def test_redis_failure_falls_back_to_a_closed_in_process_limit(self):
        failing_redis = FailingRedis()
        resilient_limiter = ResilientRateLimiter(
            failing_redis,
            "hardening-test-rate-limit-secret-at-least-32-bytes",
        )
        app.dependency_overrides[get_rate_limiter] = lambda: resilient_limiter

        with (
            patch("app.core.rate_limit.logger.warning") as warning,
            patch(
                "app.modules.auth.router.authenticate_user",
                return_value=None,
            ) as authenticate_user,
        ):
            responses = [self.login("alice@example.com") for _ in range(6)]

        self.assertEqual(
            [response.status_code for response in responses[:5]],
            [401] * 5,
        )
        self.assertEqual(responses[5].status_code, 429)
        self.assertEqual(responses[5].headers["Retry-After"], "900")
        self.assertEqual(failing_redis.eval_calls, 6)
        self.assertEqual(authenticate_user.call_count, 5)
        warning.assert_called_once()

    def test_fallback_keeps_the_local_history_when_redis_fails_mid_window(self):
        switchable_redis = SwitchableRedis()
        resilient_limiter = ResilientRateLimiter(
            switchable_redis,
            "hardening-test-rate-limit-secret-at-least-32-bytes",
        )
        buckets = resilient_limiter.login_buckets(
            "192.0.2.10",
            "alice@example.com",
        )
        for _ in range(5):
            resilient_limiter.consume(buckets)

        switchable_redis.unavailable = True
        with (
            patch("app.core.rate_limit.logger.warning"),
            self.assertRaises(RateLimitExceeded) as raised,
        ):
            resilient_limiter.consume(buckets)

        self.assertEqual(raised.exception.retry_after, 900)

    def test_missing_redis_url_uses_the_same_in_process_limit(self):
        settings.REDIS_URL = ""
        settings.RATE_LIMIT_HMAC_SECRET = (
            "hardening-test-rate-limit-secret-at-least-32-bytes"
        )
        app.dependency_overrides.pop(get_rate_limiter)

        with patch(
            "app.modules.auth.router.authenticate_user",
            return_value=None,
        ) as authenticate_user:
            responses = [self.login("alice@example.com") for _ in range(6)]

        self.assertEqual(
            [response.status_code for response in responses[:5]],
            [401] * 5,
        )
        self.assertEqual(responses[5].status_code, 429)
        self.assertEqual(responses[5].headers["Retry-After"], "900")
        self.assertEqual(authenticate_user.call_count, 5)

    def test_invalid_redis_url_uses_the_same_closed_in_process_limit(self):
        invalid_redis_url = "not-a-redis-url"
        with self.assertRaises(ValueError):
            Redis.from_url(invalid_redis_url)

        settings.REDIS_URL = invalid_redis_url
        settings.RATE_LIMIT_HMAC_SECRET = (
            "hardening-test-rate-limit-secret-at-least-32-bytes"
        )
        app.dependency_overrides.pop(get_rate_limiter)
        _build_rate_limiter.cache_clear()

        with patch("app.core.rate_limit.logger.warning") as warning:
            fallback_limiter = get_rate_limiter()

        self.assertIsInstance(fallback_limiter, ResilientRateLimiter)
        self.assertIsNone(fallback_limiter._client)
        self.assertIsInstance(fallback_limiter._memory, InMemoryRateLimiter)
        buckets = fallback_limiter.login_buckets(
            "192.0.2.10",
            "alice@example.com",
        )
        self.assertEqual(
            [(bucket.limit, bucket.window_seconds) for bucket in buckets],
            [(5, 900), (20, 900)],
        )
        warning.assert_called_once_with(
            "Configuration Redis invalide; repli mémoire par processus actif"
        )

        with patch(
            "app.modules.auth.router.authenticate_user",
            return_value=None,
        ) as authenticate_user:
            responses = [self.login("alice@example.com") for _ in range(6)]

        self.assertEqual(
            [response.status_code for response in responses],
            [401, 401, 401, 401, 401, 429],
        )
        self.assertNotIn(503, [response.status_code for response in responses])
        self.assertEqual(responses[5].headers["Retry-After"], "900")
        self.assertEqual(authenticate_user.call_count, 5)

    def test_in_process_fallback_purges_expired_counters(self):
        now = [100.0]
        limiter = InMemoryRateLimiter(
            "hardening-test-rate-limit-secret-at-least-32-bytes",
            clock=lambda: now[0],
        )
        alice_buckets = limiter.login_buckets(
            "192.0.2.10",
            "alice@example.com",
        )
        limiter.consume(alice_buckets)

        now[0] += 901
        bob_buckets = limiter.login_buckets(
            "192.0.2.10",
            "bob@example.com",
        )
        limiter.consume(bob_buckets)

        self.assertNotIn(alice_buckets[0].key, limiter._entries)
        self.assertEqual(set(limiter._entries), {bucket.key for bucket in bob_buckets})

    def test_forged_x_forwarded_for_never_creates_a_new_bucket(self):
        with patch(
            "app.modules.auth.router.authenticate_user", return_value=None
        ) as authenticate_user:
            responses = [
                self.login(
                    "alice@example.com",
                    extra_headers={"X-Forwarded-For": f"203.0.113.{index}"},
                )
                for index in range(1, 7)
            ]

        self.assertEqual(
            [response.status_code for response in responses[:5]], [401] * 5
        )
        self.assertEqual(responses[5].status_code, 429)
        self.assertEqual(authenticate_user.call_count, 5)

    def test_email_normalization_produces_the_same_hmac_key(self):
        first = self.limiter.login_buckets(
            "192.0.2.10",
            normalize_email(" Alice@Example.COM "),
        )[0]
        second = self.limiter.login_buckets(
            "192.0.2.10",
            normalize_email("alice@example.com"),
        )[0]

        self.assertEqual(first.key, second.key)
        self.assertNotIn("alice", first.key)

    def test_login_keeps_an_email_limit_when_the_client_ip_is_untrusted(self):
        settings.ENVIRONMENT = "production"
        settings.CLIENT_IP_HEADER = ""
        with patch(
            "app.modules.auth.router.authenticate_user",
            return_value=None,
        ) as authenticate_user:
            responses = [self.login("alice@example.com") for _ in range(6)]

        self.assertEqual(
            [response.status_code for response in responses[:5]],
            [401] * 5,
        )
        self.assertEqual(responses[5].status_code, 429)
        self.assertEqual(responses[5].headers["Retry-After"], "900")
        self.assertEqual(authenticate_user.call_count, 5)
        self.assertTrue(
            all(":login:email:" in key for key in self.redis.seen_keys)
        )

    def test_register_fails_closed_when_the_client_ip_is_untrusted(self):
        settings.ENVIRONMENT = "production"
        settings.CLIENT_IP_HEADER = ""
        with patch(
            "app.modules.auth.router._invitation_code_is_valid",
        ) as invitation_check:
            response = self.register()

        self.assertEqual(response.status_code, 503)
        invitation_check.assert_not_called()
        self.assertEqual(self.redis.eval_calls, 0)

    def test_rate_limit_values_are_configurable(self):
        settings.LOGIN_IP_EMAIL_RATE_LIMIT = 7
        settings.LOGIN_IP_RATE_LIMIT = 23
        settings.LOGIN_EMAIL_RATE_LIMIT = 11
        settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS = 321
        settings.REGISTER_IP_RATE_LIMIT = 9
        settings.REGISTER_RATE_LIMIT_WINDOW_SECONDS = 654

        trusted_login = self.limiter.login_buckets(
            "192.0.2.10",
            "alice@example.com",
        )
        untrusted_login = self.limiter.login_buckets(None, "alice@example.com")
        registration = self.limiter.register_buckets("192.0.2.10")

        self.assertEqual(
            [(bucket.limit, bucket.window_seconds) for bucket in trusted_login],
            [(7, 321), (23, 321)],
        )
        self.assertEqual(
            [(bucket.limit, bucket.window_seconds) for bucket in untrusted_login],
            [(11, 321)],
        )
        self.assertEqual(
            [(bucket.limit, bucket.window_seconds) for bucket in registration],
            [(9, 654)],
        )


class SecurityHeadersAndDocsTests(unittest.TestCase):
    expected_api_routes = {
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/register"),
        ("POST", "/api/v1/companies"),
        ("GET", "/api/v1/companies/me"),
        ("POST", "/api/v1/companies/me/pipeline-access"),
        ("GET", "/api/v1/dashboard/overview"),
        ("GET", "/api/v1/dashboard/recent-emails"),
        ("GET", "/api/v1/gmail/callback"),
        ("GET", "/api/v1/gmail/connect"),
        ("GET", "/api/v1/gmail/status"),
        ("POST", "/api/v1/pipeline/run"),
        ("POST", "/api/v1/telegram/link-code"),
        ("GET", "/api/v1/telegram/status"),
        ("POST", "/api/v1/telegram/webhook"),
    }
    expected_public_routes = {
        ("GET", "/"),
        ("GET", "/dashboard"),
        ("GET", "/health"),
        ("MOUNT", "/dashboard"),
    }
    expected_docs_routes = {
        ("GET", "/docs"),
        ("GET", "/docs-assets/swagger-initializer.js"),
        ("GET", "/redoc"),
        ("GET", "/openapi.json"),
        ("HEAD", "/openapi.json"),
    }

    @staticmethod
    def registered_route_inventory(application):
        inventory = set()
        for route in application.routes:
            original_router = getattr(route, "original_router", None)
            source_routes = (
                original_router.routes if original_router is not None else (route,)
            )
            for source_route in source_routes:
                path = getattr(source_route, "path", None)
                methods = getattr(source_route, "methods", None)
                if path is None:
                    continue
                if methods:
                    inventory.update(
                        (method, path)
                        for method in methods
                        if method != "OPTIONS"
                    )
                elif source_route.__class__.__name__ == "Mount":
                    inventory.add(("MOUNT", path))
        return inventory

    def assert_security_headers(self, response, *, api=False):
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("clipboard-write=(self)", response.headers["Permissions-Policy"])
        csp = response.headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("base-uri 'self'", csp)
        self.assertIn("form-action 'self'", csp)
        self.assertIn("img-src 'self' data:", csp)
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertNotIn("'unsafe-eval'", csp)
        if api:
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_headers_cover_api_dashboard_static_assets_and_errors(self):
        application = create_app("development")

        def override_get_db():
            return MagicMock()

        application.dependency_overrides[get_db] = override_get_db
        with TestClient(application) as client:
            api_error = client.get("/api/v1/not-a-route")
            oauth_callback = client.get("/api/v1/gmail/callback")
            telegram_webhook = client.post("/api/v1/telegram/webhook")
            dashboard = client.get("/dashboard/")
            javascript = client.get("/dashboard/app.js")
            stylesheet = client.get("/dashboard/styles.css")
            static_error = client.get("/dashboard/not-a-file")

        self.assertEqual(api_error.status_code, 404)
        self.assertEqual(oauth_callback.status_code, 400)
        self.assertEqual(telegram_webhook.status_code, 401)
        for response in (api_error, oauth_callback, telegram_webhook):
            self.assert_security_headers(response, api=True)
        for response in (dashboard, javascript, stylesheet, static_error):
            self.assert_security_headers(response)
            self.assertNotIn(
                "cdn.jsdelivr.net", response.headers["Content-Security-Policy"]
            )
        self.assertNotIn("style=", dashboard.text)
        self.assertNotIn(".style.", javascript.text)
        self.assertIn("navigator.clipboard.writeText", javascript.text)
        self.assertIn("clipboard-fallback-input", javascript.text)

    def test_unhandled_500_response_keeps_security_headers(self):
        application = create_app("development")
        test_router = APIRouter()

        @test_router.get("/api/v1/test-unhandled-error")
        def raise_unhandled_error():
            raise RuntimeError("test failure")

        application.include_router(test_router)
        with (
            patch("app.main.logger.error"),
            TestClient(application, raise_server_exceptions=False) as client,
        ):
            response = client.get("/api/v1/test-unhandled-error")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Erreur interne du serveur"})
        self.assert_security_headers(response, api=True)

    def test_hsts_only_on_production_https(self):
        production = create_app("production")
        development = create_app("development")
        with TestClient(production, base_url="https://testserver") as client:
            production_https = client.get("/health")
        with TestClient(production, base_url="http://testserver") as client:
            production_http = client.get("/health")
        with TestClient(development, base_url="https://testserver") as client:
            development_https = client.get("/health")

        hsts = production_https.headers["Strict-Transport-Security"]
        self.assertEqual(hsts, "max-age=31536000")
        self.assertNotIn("includeSubDomains", hsts)
        self.assertNotIn("preload", hsts)
        self.assertNotIn("Strict-Transport-Security", production_http.headers)
        self.assertNotIn("Strict-Transport-Security", development_https.headers)

    def test_oauth_callback_uri_requires_https_in_production(self):
        with (
            patch.object(settings, "ENVIRONMENT", "production"),
            patch.object(settings, "BASE_URL", "https://smartreply.example"),
        ):
            self.assertEqual(
                gmail_router.service.redirect_uri(),
                "https://smartreply.example/api/v1/gmail/callback",
            )

        with (
            patch.object(settings, "ENVIRONMENT", "production"),
            patch.object(settings, "BASE_URL", "http://smartreply.example"),
            self.assertRaisesRegex(RuntimeError, "HTTPS"),
        ):
            gmail_router.service.redirect_uri()

    def test_render_trusted_scheme_requires_the_configured_overwritten_header(self):
        original_header = settings.CLIENT_IP_HEADER
        settings.CLIENT_IP_HEADER = "CF-Connecting-IP"
        try:
            production = create_app("production")
            with TestClient(production) as client:
                trusted_headers = {
                    "CF-Connecting-IP": "203.0.113.40",
                    "X-Forwarded-Proto": "https",
                }
                trusted = client.get("/health", headers=trusted_headers)
                dashboard_redirect = client.get(
                    "/dashboard",
                    headers=trusted_headers,
                    follow_redirects=False,
                )
                spoofed = client.get(
                    "/health",
                    headers={"X-Forwarded-Proto": "https"},
                )
        finally:
            settings.CLIENT_IP_HEADER = original_header

        self.assertIn("Strict-Transport-Security", trusted.headers)
        self.assertEqual(dashboard_redirect.headers["location"], "/dashboard/")
        self.assertEqual(
            urljoin(
                "https://smartreply.example/dashboard",
                dashboard_redirect.headers["location"],
            ),
            "https://smartreply.example/dashboard/",
        )
        self.assertIn("Strict-Transport-Security", dashboard_redirect.headers)
        self.assertNotIn("Strict-Transport-Security", spoofed.headers)

    def test_docs_environment_matrix_and_strict_docs_csp(self):
        cases = {
            "production": (404, 404, 404),
            "development": (200, 200, 200),
            "staging-typo": (200, 200, 200),
        }
        for environment, expected_statuses in cases.items():
            with self.subTest(environment=environment):
                application = create_app(environment)
                with TestClient(application) as client:
                    responses = (
                        client.get("/docs"),
                        client.get("/redoc"),
                        client.get("/openapi.json"),
                    )
                self.assertEqual(
                    tuple(response.status_code for response in responses),
                    expected_statuses,
                )

        development = create_app("development")
        with TestClient(development) as client:
            docs = client.get("/docs")
            initializer = client.get("/docs-assets/swagger-initializer.js")
        self.assertIn("swagger-ui", docs.text)
        self.assertIn("validatorUrl: null", initializer.text)
        self.assert_security_headers(docs)
        self.assertIn("cdn.jsdelivr.net", docs.headers["Content-Security-Policy"])

    def test_root_is_minimal_and_health_is_unchanged(self):
        application = create_app("development")
        with TestClient(application) as client:
            root = client.get("/")
            health = client.get("/health")

        self.assertEqual(root.json(), {"status": "online"})
        self.assertEqual(health.json(), {"status": "ok"})

    def test_sensitive_route_inventory_and_rate_limit_dependencies_are_exact(self):
        application = create_app("development")
        production = create_app("production")
        api_routes = {
            (method.upper(), path)
            for path, operations in application.openapi()["paths"].items()
            if path.startswith("/api/v1/")
            for method in operations
        }
        self.assertEqual(api_routes, self.expected_api_routes)
        self.assertEqual(
            self.registered_route_inventory(application),
            self.expected_api_routes
            | self.expected_public_routes
            | self.expected_docs_routes,
        )
        self.assertEqual(
            self.registered_route_inventory(production),
            self.expected_api_routes | self.expected_public_routes,
        )

        with TestClient(application) as client:
            for method, path in sorted(self.expected_api_routes):
                with self.subTest(method=method, path=path):
                    response = client.request(method, path)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")

        # FastAPI récent conserve les routeurs inclus comme nœuds paresseux ;
        # l'inventaire des dépendances parcourt leurs routes sources.
        source_routes = []
        for route in application.routes:
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                source_routes.extend(original_router.routes)
            else:
                source_routes.append(route)

        limited_routes = set()
        for route in source_routes:
            if not isinstance(route, APIRoute):
                continue
            dependencies = {
                dependency.call for dependency in route.dependant.dependencies
            }
            if get_rate_limiter in dependencies:
                limited_routes.update(
                    (method, route.path)
                    for method in route.methods
                    if method not in {"HEAD", "OPTIONS"}
                )
        self.assertEqual(
            limited_routes,
            {
                ("POST", "/api/v1/auth/login"),
                ("POST", "/api/v1/auth/register"),
            },
        )

        user_protected_routes = set()
        for route in source_routes:
            if not isinstance(route, APIRoute):
                continue
            dependencies = {
                dependency.call for dependency in route.dependant.dependencies
            }
            if get_current_user in dependencies:
                user_protected_routes.update(
                    (method, route.path)
                    for method in route.methods
                    if method not in {"HEAD", "OPTIONS"}
                )
        self.assertEqual(
            user_protected_routes,
            {
                ("POST", "/api/v1/companies"),
                ("GET", "/api/v1/companies/me"),
                ("POST", "/api/v1/companies/me/pipeline-access"),
                ("GET", "/api/v1/dashboard/overview"),
                ("GET", "/api/v1/dashboard/recent-emails"),
                ("GET", "/api/v1/gmail/connect"),
                ("GET", "/api/v1/gmail/status"),
                ("POST", "/api/v1/telegram/link-code"),
                ("GET", "/api/v1/telegram/status"),
            },
        )


class GmailUserReloadTests(unittest.TestCase):
    def setUp(self):
        self.original_jwt_secret = settings.JWT_SECRET
        settings.JWT_SECRET = "gmail-reload-test-secret-at-least-32-bytes"
        self.db = MagicMock()

        def override_get_db():
            yield self.db

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.token = create_access_token(42, "user@example.com")
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        settings.JWT_SECRET = self.original_jwt_secret

    def test_existing_user_can_access_connect_and_status(self):
        self.db.get.return_value = SimpleNamespace(id=42, email="user@example.com")
        with (
            patch.object(
                gmail_router.service,
                "build_authorization_url",
                return_value="https://accounts.google.com/authorize",
            ) as build_url,
            patch.object(
                gmail_router.service,
                "is_connected",
                return_value=True,
            ) as is_connected,
        ):
            connect = self.client.get("/api/v1/gmail/connect", headers=self.headers)
            status_response = self.client.get(
                "/api/v1/gmail/status",
                headers=self.headers,
            )

        self.assertEqual(connect.status_code, 200)
        self.assertEqual(status_response.json(), {"connected": True})
        build_url.assert_called_once()
        is_connected.assert_called_once_with(self.db, 42)

    def test_deleted_user_is_immediately_refused_by_connect_and_status(self):
        self.db.get.return_value = None
        with (
            patch.object(gmail_router.service, "build_authorization_url") as build_url,
            patch.object(gmail_router.service, "is_connected") as is_connected,
        ):
            connect = self.client.get("/api/v1/gmail/connect", headers=self.headers)
            status_response = self.client.get(
                "/api/v1/gmail/status",
                headers=self.headers,
            )

        self.assertEqual(connect.status_code, 401)
        self.assertEqual(status_response.status_code, 401)
        self.assertEqual(connect.json(), {"detail": "Utilisateur introuvable"})
        build_url.assert_not_called()
        is_connected.assert_not_called()


if __name__ == "__main__":
    unittest.main()
