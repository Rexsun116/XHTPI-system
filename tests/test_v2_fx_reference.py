"""Isolated contract tests for the fail-soft USD/CNY reference service."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from unittest import TestCase
from types import SimpleNamespace

from v2.fx_reference import FXReferenceService, FrankfurterUsdCnyProvider
from v2.reporting import build_management_report


class FXReferenceTest(TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 25, 13, 55, tzinfo=timezone.utc)
        self.calls = []

    def transport(self, payload):
        def fetch(url, timeout):
            self.calls.append((url, timeout))
            if isinstance(payload, Exception):
                raise payload
            return json.dumps(payload).encode("utf-8")
        return fetch

    @staticmethod
    def valid_payload(rate="7.123456789", source_date="2026-09-24"):
        return {"base": "USD", "quote": "CNY", "date": source_date, "rate": rate}

    def service(self, payload, *, ttl=timedelta(minutes=20)):
        provider = FrankfurterUsdCnyProvider(transport=self.transport(payload))
        return FXReferenceService(provider, ttl=ttl, clock=lambda: self.now)

    def test_usd_cny_is_normalized_as_cny_per_one_usd_with_decimal_precision(self):
        reference = self.service(self.valid_payload()).current_usd_cny()
        self.assertEqual((reference.base, reference.quote), ("USD", "CNY"))
        self.assertEqual(reference.rate, Decimal("7.123456789"))
        self.assertEqual(reference.as_of.isoformat(), "2026-09-24")
        self.assertEqual(reference.fetched_at, self.now)
        self.assertNotEqual(reference.fetched_at, reference.as_of)
        self.assertTrue(reference.available)
        self.assertFalse(reference.stale)
        self.assertTrue(self.calls[0][0].endswith("/v2/rate/usd/cny"))
        self.assertEqual(self.calls[0][1], 3.0)

    def test_inverted_source_payload_is_rejected_instead_of_being_misread(self):
        reference = self.service({"base": "CNY", "quote": "USD", "date": "2026-09-24", "rate": "0.14"}).current_usd_cny()
        self.assertFalse(reference.available)
        self.assertIsNone(reference.rate)

    def test_fresh_cache_prevents_duplicate_provider_requests(self):
        service = self.service(self.valid_payload())
        first = service.current_usd_cny()
        self.now += timedelta(minutes=19)
        second = service.current_usd_cny()
        self.assertEqual(first.rate, second.rate)
        self.assertEqual(len(self.calls), 1)

    def test_expired_cache_refreshes_from_provider(self):
        payloads = [self.valid_payload("7.10"), self.valid_payload("7.20")]

        def transport(url, timeout):
            self.calls.append((url, timeout))
            return json.dumps(payloads.pop(0)).encode("utf-8")

        service = FXReferenceService(FrankfurterUsdCnyProvider(transport=transport), clock=lambda: self.now)
        self.assertEqual(service.current_usd_cny().rate, Decimal("7.10"))
        self.now += timedelta(minutes=20)
        self.assertEqual(service.current_usd_cny().rate, Decimal("7.20"))
        self.assertEqual(len(self.calls), 2)

    def test_failed_refresh_returns_stale_cached_reference(self):
        payloads = [self.valid_payload("7.12"), TimeoutError("slow provider")]

        def transport(url, timeout):
            self.calls.append((url, timeout))
            item = payloads.pop(0)
            if isinstance(item, Exception):
                raise item
            return json.dumps(item).encode("utf-8")

        service = FXReferenceService(FrankfurterUsdCnyProvider(transport=transport), clock=lambda: self.now)
        original = service.current_usd_cny()
        self.now += timedelta(minutes=20)
        stale = service.current_usd_cny()
        self.assertEqual(stale.rate, original.rate)
        self.assertEqual(stale.as_of, original.as_of)
        self.assertTrue(stale.stale)
        self.assertTrue(stale.available)

    def test_initial_provider_failure_is_an_explicit_unavailable_reference(self):
        reference = self.service(TimeoutError("offline")).current_usd_cny()
        self.assertFalse(reference.available)
        self.assertFalse(reference.stale)
        self.assertIsNone(reference.rate)
        self.assertIsNone(reference.as_of)

    def test_malformed_response_is_fail_soft(self):
        reference = self.service({"base": "USD", "quote": "CNY", "rate": "not-a-number"}).current_usd_cny()
        self.assertFalse(reference.available)
        self.assertIsNone(reference.rate)

    def test_reference_lookup_does_not_modify_currency_separated_management_reporting(self):
        order = SimpleNamespace(
            id=1, include_in_business_stats=True, trade_group_id=None, trade_role=None,
            status="NEW", currency="USD", contract_total=Decimal("100"),
            advance_received_amount=None, balance_received_amount=None, items=(),
        )
        before = build_management_report([order], as_of=self.now.date())
        self.service(self.valid_payload()).current_usd_cny()
        after = build_management_report([order], as_of=self.now.date())
        self.assertEqual(after, before)
        self.assertEqual(after["kpis"]["contracted_by_currency"], {"USD": Decimal("100")})
