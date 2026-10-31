"""Documentation de développement compatible avec une CSP sans inline."""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response

from app.core.security_headers import REDOC_JS_URL, SWAGGER_CSS_URL, SWAGGER_JS_URL

_SWAGGER_INITIALIZER = """window.addEventListener("load", () => {
  window.ui = SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    deepLinking: true,
    validatorUrl: null,
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    plugins: [SwaggerUIBundle.plugins.DownloadUrl],
    layout: "StandaloneLayout"
  });
});
"""


def register_non_production_docs(app: FastAPI) -> None:
    """Ajoute Swagger/ReDoc uniquement lorsque create_app l'autorise."""

    @app.get("/docs", include_in_schema=False)
    def swagger_ui() -> HTMLResponse:
        return HTMLResponse(
            "\n".join(
                (
                    "<!doctype html>",
                    '<html lang="fr">',
                    "<head>",
                    '  <meta charset="utf-8">',
                    (
                        '  <meta name="viewport" '
                        'content="width=device-width, initial-scale=1">'
                    ),
                    "  <title>SmartReply API — Swagger UI</title>",
                    f'  <link rel="stylesheet" href="{SWAGGER_CSS_URL}">',
                    "</head>",
                    "<body>",
                    '  <div id="swagger-ui"></div>',
                    f'  <script src="{SWAGGER_JS_URL}" defer></script>',
                    (
                        '  <script src="/docs-assets/swagger-initializer.js" '
                        "defer></script>"
                    ),
                    "</body>",
                    "</html>",
                )
            )
        )

    @app.get("/docs-assets/swagger-initializer.js", include_in_schema=False)
    def swagger_initializer() -> Response:
        return Response(_SWAGGER_INITIALIZER, media_type="application/javascript")

    @app.get("/redoc", include_in_schema=False)
    def redoc() -> HTMLResponse:
        return HTMLResponse(
            "\n".join(
                (
                    "<!doctype html>",
                    '<html lang="fr">',
                    "<head>",
                    '  <meta charset="utf-8">',
                    (
                        '  <meta name="viewport" '
                        'content="width=device-width, initial-scale=1">'
                    ),
                    "  <title>SmartReply API — ReDoc</title>",
                    "</head>",
                    "<body>",
                    '  <redoc spec-url="/openapi.json"></redoc>',
                    f'  <script src="{REDOC_JS_URL}" defer></script>',
                    "</body>",
                    "</html>",
                )
            )
        )
