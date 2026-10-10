"""Read-only current USD/CNY reference-rate lookup.

This module is intentionally independent of management reporting.  It returns
current provider context only and must never be used to convert historical
business aggregates.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from typing import Callable
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)

USD_CNY_TTL = timedelta(minutes=20)
FRANKFURTER_USD_CNY_URL = "https://api.frankfurter.dev/v2/rate/usd/cny"


@dataclass(frozen=True)
class ProviderRate:
    """Provider-normalized USD/CNY data before local cache metadata is added."""

    rate: Decimal
    as_of: date | None


@dataclass(frozen=True)
class FXReference:
    """Current reference context; unavailable values intentionally have no rate."""

    base: str
    quote: str
    rate: Decimal | None
    as_of: date | None
    fetched_at: datetime
    source: str
    stale: bool
    available: bool


def _utcnow():
    return datetime.now(timezone.utc)


def _read_url(url: str, timeout: float) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "XHTPI-V2/2.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS provider URL
        return response.read()


class FrankfurterUsdCnyProvider:
    """Small adapter for Frankfurter v2's flat single-rate JSON response."""

    source = "frankfurter-v2"

    def __init__(self, *, transport: Callable[[str, float], bytes] | None = None,
                 endpoint: str = FRANKFURTER_USD_CNY_URL, timeout: float = 3.0):
        self.transport = transport or _read_url
        self.endpoint = endpoint
        self.timeout = timeout

    def fetch(self) -> ProviderRate:
        raw = self.transport(self.endpoint, self.timeout)
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        if not isinstance(payload, dict) or str(payload.get("base", "")).upper() != "USD":
            raise ValueError("FX provider response does not declare USD as its base currency")
        if str(payload.get("quote", "")).upper() != "CNY":
            raise ValueError("FX provider response does not declare CNY as its quote currency")
        try:
            rate = Decimal(str(payload["rate"]))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("FX provider returned an invalid USD/CNY rate") from error
        if not rate.is_finite() or rate <= 0:
            raise ValueError("FX provider returned a non-positive USD/CNY rate")
        raw_date = payload.get("date")
        try:
            as_of = date.fromisoformat(raw_date) if raw_date else None
        except (TypeError, ValueError) as error:
            raise ValueError("FX provider returned an invalid reference date") from error
        return ProviderRate(rate=rate, as_of=as_of)


class FXReferenceService:
    """In-process cached, fail-soft current USD/CNY reference service."""

    def __init__(self, provider: FrankfurterUsdCnyProvider | None = None, *,
                 ttl: timedelta = USD_CNY_TTL, clock: Callable[[], datetime] = _utcnow):
        self.provider = provider or FrankfurterUsdCnyProvider()
        self.ttl = ttl
        self.clock = clock
        self._cached: FXReference | None = None

    def current_usd_cny(self) -> FXReference:
        now = self.clock()
        cached = self._cached
        if cached is not None and now - cached.fetched_at < self.ttl:
            return replace(cached, stale=False)
        try:
            provider_rate = self.provider.fetch()
        except Exception as error:  # Provider/network/parse failures must not break a future dashboard.
            if cached is not None:
                logger.warning("USD/CNY reference refresh failed; returning stale cache: %s", error)
                return replace(cached, stale=True)
            logger.warning("USD/CNY reference is unavailable: %s", error)
            return FXReference(
                base="USD", quote="CNY", rate=None, as_of=None, fetched_at=now,
                source=getattr(self.provider, "source", "unknown"), stale=False, available=False,
            )
        reference = FXReference(
            base="USD", quote="CNY", rate=provider_rate.rate, as_of=provider_rate.as_of,
            fetched_at=now, source=self.provider.source, stale=False, available=True,
        )
        self._cached = reference
        return reference
