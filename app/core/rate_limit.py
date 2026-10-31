"""Rate limiting Redis atomique pour les opérations d'authentification."""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache

from fastapi import HTTPException, status
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)
_PROCESS_HMAC_SECRET = secrets.token_urlsafe(32)

# Une seule commande Lua incrémente tous les compteurs d'une tentative et pose
# leur expiration dans la même opération atomique, sur toutes les instances.
_CONSUME_SCRIPT = """
local result = {}
for index, key in ipairs(KEYS) do
    local count = redis.call('INCR', key)
    local ttl = redis.call('TTL', key)
    if count == 1 or ttl < 0 then
        redis.call('EXPIRE', key, tonumber(ARGV[index]))
        ttl = tonumber(ARGV[index])
    end
    table.insert(result, count)
    table.insert(result, ttl)
end
return result
"""


@dataclass(frozen=True)
class RateLimitBucket:
    """Un compteur Redis à fenêtre fixe."""

    key: str
    limit: int
    window_seconds: int


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: int):
        self.retry_after = max(1, int(retry_after))
        super().__init__("Rate limit exceeded")


class RateLimitUnavailable(RuntimeError):
    """Redis ou la configuration de sécurité est indisponible."""


class RedisRateLimiter:
    """Compteurs à fenêtre fixe, atomiques, partagés et expirants."""

    def __init__(self, client: Redis | None, hmac_secret: str):
        if not hmac_secret:
            raise RateLimitUnavailable("Secret HMAC du rate limiter absent")
        self._client = client
        self._secret = hmac_secret.encode("utf-8")

    def _identity_digest(self, value: str) -> str:
        return hmac.new(self._secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def login_buckets(
        self,
        client_ip: str | None,
        normalized_email: str,
    ) -> tuple[RateLimitBucket, ...]:
        if client_ip is None:
            email_digest = self._identity_digest(f"email\0{normalized_email}")
            return (
                RateLimitBucket(
                    key=f"smartreply:rl:v1:login:email:{email_digest}",
                    limit=settings.LOGIN_EMAIL_RATE_LIMIT,
                    window_seconds=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
                ),
            )

        ip_digest = self._identity_digest(f"ip\0{client_ip}")
        pair_digest = self._identity_digest(
            f"ip-email\0{client_ip}\0{normalized_email}"
        )
        return (
            RateLimitBucket(
                key=f"smartreply:rl:v1:login:ip-email:{pair_digest}",
                limit=settings.LOGIN_IP_EMAIL_RATE_LIMIT,
                window_seconds=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
            ),
            RateLimitBucket(
                key=f"smartreply:rl:v1:login:ip:{ip_digest}",
                limit=settings.LOGIN_IP_RATE_LIMIT,
                window_seconds=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
            ),
        )

    def register_buckets(self, client_ip: str) -> tuple[RateLimitBucket, ...]:
        ip_digest = self._identity_digest(f"ip\0{client_ip}")
        return (
            RateLimitBucket(
                key=f"smartreply:rl:v1:register:ip:{ip_digest}",
                limit=settings.REGISTER_IP_RATE_LIMIT,
                window_seconds=settings.REGISTER_RATE_LIMIT_WINDOW_SECONDS,
            ),
        )

    def consume(self, buckets: Iterable[RateLimitBucket]) -> None:
        bucket_list = tuple(buckets)
        if not bucket_list:
            return
        if any(
            bucket.limit <= 0 or bucket.window_seconds <= 0
            for bucket in bucket_list
        ):
            raise RateLimitUnavailable("Configuration du rate limiter invalide")
        if self._client is None:
            raise RateLimitUnavailable("Stockage Redis non configuré")
        keys = [bucket.key for bucket in bucket_list]
        windows = [bucket.window_seconds for bucket in bucket_list]
        try:
            raw_result = self._client.eval(
                _CONSUME_SCRIPT,
                len(keys),
                *keys,
                *windows,
            )
        except RedisError as exc:
            raise RateLimitUnavailable("Stockage Redis indisponible") from exc

        if not isinstance(raw_result, (list, tuple)) or len(raw_result) != 2 * len(
            bucket_list
        ):
            raise RateLimitUnavailable("Réponse Redis inattendue")

        retry_after = 0
        for index, bucket in enumerate(bucket_list):
            try:
                count = int(raw_result[index * 2])
                ttl = int(raw_result[index * 2 + 1])
            except (TypeError, ValueError) as exc:
                raise RateLimitUnavailable("Réponse Redis invalide") from exc
            if count > bucket.limit:
                retry_after = max(retry_after, ttl, 1)

        if retry_after:
            raise RateLimitExceeded(retry_after)


class InMemoryRateLimiter(RedisRateLimiter):
    """Compteurs de secours atomiques dans le processus, avec purge par échéance."""

    def __init__(
        self,
        hmac_secret: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        super().__init__(None, hmac_secret)
        self._clock = clock
        self._entries: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def _consume(
        self,
        buckets: Iterable[RateLimitBucket],
        *,
        enforce: bool,
    ) -> None:
        bucket_list = tuple(buckets)
        if not bucket_list:
            return
        if any(
            bucket.limit <= 0 or bucket.window_seconds <= 0
            for bucket in bucket_list
        ):
            raise RateLimitUnavailable("Configuration du rate limiter invalide")

        now = self._clock()
        retry_after = 0
        with self._lock:
            expired_keys = [
                key
                for key, (_, expires_at) in self._entries.items()
                if expires_at <= now
            ]
            for key in expired_keys:
                del self._entries[key]

            for bucket in bucket_list:
                current = self._entries.get(bucket.key)
                if current is None:
                    count = 1
                    expires_at = now + bucket.window_seconds
                else:
                    count = current[0] + 1
                    expires_at = current[1]
                self._entries[bucket.key] = (count, expires_at)
                if count > bucket.limit:
                    retry_after = max(
                        retry_after,
                        math.ceil(expires_at - now),
                        1,
                    )

        if enforce and retry_after:
            raise RateLimitExceeded(retry_after)

    def consume(self, buckets: Iterable[RateLimitBucket]) -> None:
        self._consume(buckets, enforce=True)

    def record(self, buckets: Iterable[RateLimitBucket]) -> None:
        """Maintient un compteur chaud sans remplacer la décision de Redis."""
        self._consume(buckets, enforce=False)


class ResilientRateLimiter(RedisRateLimiter):
    """Utilise Redis et bascule sans ouverture vers les compteurs du processus."""

    def __init__(self, client: Redis | None, hmac_secret: str):
        super().__init__(client, hmac_secret)
        self._memory = InMemoryRateLimiter(hmac_secret)
        self._redis_warning_logged = False

    def consume(self, buckets: Iterable[RateLimitBucket]) -> None:
        bucket_list = tuple(buckets)
        if self._client is None:
            self._memory.consume(bucket_list)
            return

        try:
            super().consume(bucket_list)
        except RateLimitExceeded:
            self._memory.record(bucket_list)
            raise
        except RateLimitUnavailable:
            if not self._redis_warning_logged:
                logger.warning(
                    "Rate limiter Redis indisponible; repli mémoire par processus actif"
                )
                self._redis_warning_logged = True
            self._memory.consume(bucket_list)
        else:
            # Le miroir local évite une fenêtre neuve si Redis tombe plus tard.
            self._memory.record(bucket_list)


@lru_cache(maxsize=4)
def _build_rate_limiter(
    redis_url: str,
    hmac_secret: str,
) -> ResilientRateLimiter:
    effective_secret = hmac_secret or _PROCESS_HMAC_SECRET
    client = None
    if redis_url and hmac_secret:
        try:
            client = Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except (RedisError, ValueError):
            logger.warning(
                "Configuration Redis invalide; repli mémoire par processus actif"
            )
    elif redis_url:
        logger.warning(
            "RATE_LIMIT_HMAC_SECRET absent; Redis désactivé et repli mémoire actif"
        )
    return ResilientRateLimiter(client, effective_secret)


def get_rate_limiter() -> ResilientRateLimiter:
    """Retourne Redis avec un repli mémoire fermé, jamais un accès non limité."""

    return _build_rate_limiter(
        settings.REDIS_URL.strip(),
        settings.RATE_LIMIT_HMAC_SECRET,
    )


def enforce_rate_limit(
    limiter: RedisRateLimiter, buckets: Iterable[RateLimitBucket]
) -> None:
    """Traduit les résultats internes en réponses HTTP sans exposer Redis."""

    try:
        limiter.consume(buckets)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de tentatives. Réessayez plus tard.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except RateLimitUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Protection anti-abus temporairement indisponible",
        ) from exc
