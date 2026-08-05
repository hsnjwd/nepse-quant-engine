"""Smart API rate limiter with token bucket and exponential backoff.

``RateLimiter`` uses a token-bucket algorithm per provider, with automatic
backoff, jitter, and ``Retry-After`` header support.  HTTP 429 and 503
responses trigger provider cooldown.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProviderState:
    """Internal state for a single provider's rate limit and cooldown."""

    tokens: float = 10.0
    max_tokens: float = 10.0
    refill_rate: float = 1.0  # tokens per second
    last_refill: float = field(default_factory=time.time)
    cooldown_until: float = 0.0
    consecutive_failures: int = 0
    last_backoff: float = 0.0
    retry_after: float = 0.0  # from Retry-After header


@dataclass
class RateLimitStats:
    """Snapshot of rate limiter statistics."""

    provider: str = ""
    tokens_remaining: float = 0.0
    max_tokens: float = 0.0
    cooldown_active: bool = False
    cooldown_remaining: float = 0.0
    consecutive_failures: int = 0
    total_throttled: int = 0
    total_429: int = 0
    total_503: int = 0


class RateLimiter:
    """Token-bucket rate limiter with exponential backoff and jitter.

    Usage::

        limiter = RateLimiter()
        limiter.configure("nepse_scraper", max_tokens=10, refill_rate=1.0)
        with limiter.acquire("nepse_scraper"):
            response = requests.get(url)
            limiter.record_success("nepse_scraper")
    """

    def __init__(self) -> None:
        self._providers: dict[str, ProviderState] = {}
        self._lock = threading.RLock()
        self._total_throttled: int = 0
        self._total_429: int = 0
        self._total_503: int = 0
        self._default_max_tokens: float = 10.0
        self._default_refill_rate: float = 1.0
        self._backoff_base: float = 1.0
        self._backoff_max: float = 120.0  # max 2 minute backoff
        self._jitter: float = 0.1  # ±10% jitter

    # ── Configuration ────────────────────────────────────────────

    def configure(
        self,
        provider: str,
        max_tokens: float = 10.0,
        refill_rate: float = 1.0,
    ) -> None:
        """Configure rate limits for *provider*."""
        with self._lock:
            if provider not in self._providers:
                self._providers[provider] = ProviderState()
            ps = self._providers[provider]
            ps.max_tokens = max_tokens
            ps.refill_rate = refill_rate
            logger.info(
                "[RateLimiter] Configured %s: %d tokens @ %.1f/s",
                provider, int(max_tokens), refill_rate,
            )

    def configure_defaults(
        self,
        max_tokens: float = 10.0,
        refill_rate: float = 1.0,
        backoff_base: float = 1.0,
        backoff_max: float = 120.0,
        jitter: float = 0.1,
    ) -> None:
        """Set default per-provider limits and backoff parameters."""
        self._default_max_tokens = max_tokens
        self._default_refill_rate = refill_rate
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._jitter = jitter

    # ── Token bucket ─────────────────────────────────────────────

    def _ensure_provider(self, provider: str) -> ProviderState:
        """Get or create a provider state with defaults."""
        with self._lock:
            if provider not in self._providers:
                self._providers[provider] = ProviderState(
                    max_tokens=self._default_max_tokens,
                    refill_rate=self._default_refill_rate,
                )
            return self._providers[provider]

    def _refill(self, ps: ProviderState) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - ps.last_refill
        ps.tokens = min(ps.max_tokens, ps.tokens + elapsed * ps.refill_rate)
        ps.last_refill = now

    def acquire(self, provider: str, timeout: float = 10.0) -> bool:
        """Try to acquire a token for *provider*.

        Returns ``True`` if a token was acquired, ``False`` if rate-limited.
        Blocks up to *timeout* seconds waiting for a token.
        """
        ps = self._ensure_provider(provider)
        deadline = time.time() + timeout

        while time.time() < deadline:
            with self._lock:
                # Check cooldown
                now = time.time()
                if now < ps.cooldown_until:
                    remaining = ps.cooldown_until - now
                    logger.debug(
                        "[RateLimiter] %s in cooldown for %.1fs",
                        provider, remaining,
                    )
                    self._total_throttled += 1
                    time.sleep(min(remaining + 0.1, deadline - now))
                    continue

                # Refill and consume
                self._refill(ps)
                if ps.tokens >= 1.0:
                    ps.tokens -= 1.0
                    return True

            # No tokens available — wait a bit
            time.sleep(min(0.1, deadline - time.time()))

        self._total_throttled += 1
        logger.warning("[RateLimiter] %s: timeout waiting for token", provider)
        return False

    # ── Context manager ──────────────────────────────────────────

    def acquire_context(self, provider: str) -> _RateLimitContext:
        """Return a context manager that acquires/releases a token."""
        return _RateLimitContext(self, provider)

    # ── Success / failure recording ──────────────────────────────

    def record_success(self, provider: str) -> None:
        """Record a successful API call for *provider*."""
        with self._lock:
            ps = self._ensure_provider(provider)
            ps.consecutive_failures = 0
            ps.last_backoff = 0.0
            ps.retry_after = 0.0

    def record_failure(
        self,
        provider: str,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Record a failed API call, triggering backoff or cooldown."""
        with self._lock:
            ps = self._ensure_provider(provider)
            ps.consecutive_failures += 1

            if status_code == 429:
                self._total_429 += 1
                if retry_after:
                    ps.retry_after = retry_after
                    ps.cooldown_until = time.time() + retry_after
                    logger.warning(
                        "[RateLimiter] %s: 429, cooldown %.1fs",
                        provider, retry_after,
                    )
                    return

            if status_code == 503:
                self._total_503 += 1

            # Exponential backoff with jitter
            delay = min(
                self._backoff_base * (2 ** (ps.consecutive_failures - 1)),
                self._backoff_max,
            )
            jitter_amount = delay * self._jitter * random.uniform(-1.0, 1.0)
            delay = max(0.1, delay + jitter_amount)
            ps.cooldown_until = time.time() + delay
            ps.last_backoff = delay
            logger.warning(
                "[RateLimiter] %s: backoff %.1fs (failure %d)",
                provider, delay, ps.consecutive_failures,
            )

    def reset_provider(self, provider: str) -> None:
        """Reset all state for *provider* (e.g. after URL change)."""
        with self._lock:
            self._providers.pop(provider, None)

    def is_throttled(self, provider: str) -> bool:
        """Check if *provider* is currently in cooldown."""
        ps = self._ensure_provider(provider)
        return time.time() < ps.cooldown_until

    def get_stats(self, provider: str) -> RateLimitStats | None:
        """Return a stats snapshot for *provider*, or ``None``."""
        with self._lock:
            ps = self._providers.get(provider)
            if ps is None:
                return None
            now = time.time()
            cooldown_remaining = max(0.0, ps.cooldown_until - now)
            return RateLimitStats(
                provider=provider,
                tokens_remaining=ps.tokens,
                max_tokens=ps.max_tokens,
                cooldown_active=now < ps.cooldown_until,
                cooldown_remaining=cooldown_remaining,
                consecutive_failures=ps.consecutive_failures,
                total_throttled=self._total_throttled,
                total_429=self._total_429,
                total_503=self._total_503,
            )

    def get_all_stats(self) -> list[RateLimitStats]:
        """Return stats for all configured providers."""
        providers = list(self._providers.keys())
        return [s for p in providers if (s := self.get_stats(p)) is not None]

    @property
    def total_throttled(self) -> int:
        return self._total_throttled


class _RateLimitContext:
    """Context manager for rate-limited API calls."""

    def __init__(self, limiter: RateLimiter, provider: str) -> None:
        self._limiter = limiter
        self._provider = provider
        self._acquired = False

    def __enter__(self) -> _RateLimitContext:
        if not self._limiter.acquire(self._provider):
            logger.warning("[RateLimiter] %s: rate limited", self._provider)
        self._acquired = True
        return self

    def __exit__(self, *args: Any) -> None:
        pass
