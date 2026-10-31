"""Résolution explicite de l'adresse cliente derrière un proxy de confiance."""

from __future__ import annotations

import ipaddress

from fastapi import Request

from app.core.config import settings


class ClientAddressError(RuntimeError):
    """La configuration ne permet pas d'identifier un client de façon fiable."""


def _canonical_ip(value: str) -> str:
    candidate = value.strip()
    if not candidate or "," in candidate:
        raise ClientAddressError("Adresse client absente ou ambiguë")
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError as exc:
        raise ClientAddressError("Adresse client invalide") from exc


def get_client_ip(request: Request) -> str:
    """Retourne une IP canonique sans jamais lire X-Forwarded-For.

    Par défaut, seule l'adresse de la connexion TCP est utilisée. Sur une
    plateforme qui remplace elle-même un en-tête dédié, CLIENT_IP_HEADER peut
    nommer cet en-tête (sur un service web public Render : CF-Connecting-IP).
    Un en-tête configuré mais absent/invalide fait échouer les routes limitées
    plutôt que de regrouper silencieusement tous les clients.
    """

    configured_header = settings.CLIENT_IP_HEADER.strip()
    if configured_header:
        value = request.headers.get(configured_header)
        if value is None:
            raise ClientAddressError("En-tête d'adresse client fiable absent")
        return _canonical_ip(value)

    if settings.ENVIRONMENT.strip().lower() == "production":
        raise ClientAddressError(
            "CLIENT_IP_HEADER requis derrière le proxy de production"
        )
    if request.client is None:
        raise ClientAddressError("Adresse de connexion indisponible")
    return _canonical_ip(request.client.host)


def has_trusted_proxy_headers(request: Request) -> bool:
    """Indique si l'en-tête client déclaré est présent et syntaxiquement valide."""

    configured_header = settings.CLIENT_IP_HEADER.strip()
    if not configured_header:
        return False
    value = request.headers.get(configured_header)
    if value is None:
        return False
    try:
        _canonical_ip(value)
    except ClientAddressError:
        return False
    return True


def is_external_https(request: Request) -> bool:
    """Détecte HTTPS directement ou via le proxy explicitement déclaré."""

    if request.url.scheme == "https":
        return True
    if not has_trusted_proxy_headers(request):
        return False
    return request.headers.get("x-forwarded-proto", "").strip().lower() == "https"
