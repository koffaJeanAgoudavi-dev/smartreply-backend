"""En-têtes HTTP de durcissement appliqués à toutes les réponses."""

from __future__ import annotations

from collections.abc import MutableMapping

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.client_ip import is_external_https

SWAGGER_JS_URL = (
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"
)
SWAGGER_CSS_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css"
REDOC_JS_URL = "https://cdn.jsdelivr.net/npm/redoc@2.1.5/bundles/redoc.standalone.js"

DASHBOARD_CSP = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'self'",
        "font-src 'self'",
        "img-src 'self' data:",
        "frame-src 'none'",
        "manifest-src 'self'",
        "worker-src 'self'",
    )
)

DOCS_CSP = "; ".join(
    (
        "default-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        f"script-src 'self' {SWAGGER_JS_URL} {REDOC_JS_URL}",
        f"style-src 'self' {SWAGGER_CSS_URL}",
        "connect-src 'self'",
        "font-src 'self'",
        "img-src 'self' data:",
    )
)

PERMISSIONS_POLICY = ", ".join(
    (
        "accelerometer=()",
        "autoplay=()",
        "camera=()",
        "clipboard-read=()",
        "clipboard-write=(self)",
        "display-capture=()",
        "encrypted-media=()",
        "fullscreen=()",
        "geolocation=()",
        "gyroscope=()",
        "magnetometer=()",
        "microphone=()",
        "midi=()",
        "payment=()",
        "picture-in-picture=()",
        "publickey-credentials-get=()",
        "screen-wake-lock=()",
        "usb=()",
    )
)


def _is_docs_path(path: str) -> bool:
    return path in {"/docs", "/redoc"} or path.startswith("/docs-assets/")


def security_header_values(request: Request, *, production: bool) -> dict[str, str]:
    values = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": PERMISSIONS_POLICY,
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": (
            DOCS_CSP if _is_docs_path(request.url.path) else DASHBOARD_CSP
        ),
    }
    if request.url.path.startswith("/api/v1/"):
        values["Cache-Control"] = "no-store"
    if production and is_external_https(request):
        values["Strict-Transport-Security"] = "max-age=31536000"
    return values


def apply_security_headers(
    headers: MutableMapping[str, str],
    request: Request,
    *,
    production: bool,
) -> None:
    for name, value in security_header_values(request, production=production).items():
        headers[name] = value


class SecurityHeadersMiddleware:
    """Middleware ASGI sans buffering, y compris pour erreurs et fichiers statiques."""

    def __init__(self, app: ASGIApp, *, production: bool):
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                apply_security_headers(
                    headers,
                    request,
                    production=self.production,
                )
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
