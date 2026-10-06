"""Isolated presentation coverage for the read-only V2 Business Overview."""
from datetime import date, datetime, timezone
from pathlib import Path
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from tests import test_v2_new_order_edit as fixture
from v2.fx_reference import FXReference
from v2.management_dashboard import format_compact_amount, format_count
from v2.models import Customer, FreightSettlement, PI, PIItem, TradeGroup, db


class BusinessDashboardTest(TestCase):
    setUp = fixture.NewOrderEditTest.setUp
    tearDown = fixture.NewOrderEditTest.tearDown
    client = fixture.NewOrderEditTest.client
    form = fixture.NewOrderEditTest.form
    order = fixture.NewOrderEditTest.order

    today = date(2026, 9, 20)

    def fx(self, *, available=True, stale=False):
        return FXReference(
            base="USD", quote="CNY", rate=Decimal("7.1234") if available else None,
            as_of=date(2026, 9, 19) if available else None,
            fetched_at=datetime(2026, 9, 20, 1, tzinfo=timezone.utc),
            source="test", stale=stale, available=available,
        )

    def get(self, url="/v2/business", *, fx=None):
        with patch("v2.web.business_today", return_value=self.today), patch(
            "v2.management_dashboard.fx_reference_service.current_usd_cny",
            return_value=fx or self.fx(),
        ):
            return self.client().get(url)

    def create_order(self, number, **changes):
        self.assertEqual(self.client().post("/v2/orders/new", data=self.form(number, **changes)).status_code, 302)
        return db.session.scalar(db.select(PI).where(PI.pi_no == number))

    def test_requires_authentication(self):
        response = self.app.test_client().get("/v2/business")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_operations_dashboard_route_remains_reachable(self):
        response = self.client().get("/v2/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Operations Dashboard", response.get_data(as_text=True))

    def test_authenticated_page_uses_real_reporting_data_and_preserves_operations_entry(self):
        self.create_order("BUS-USD", loading_port="Shanghai", loading_country_name="China",
                          loading_country_code="CN", destination_port="Mombasa",
                          destination_country_name="Kenya", destination_country_code="KE")
        html = self.get().get_data(as_text=True)
        for label in (
            "MANAGEMENT REPORTING", "Business Overview", "Booked revenue · YTD", "YTD orders", "Open order volume", "Commercial scale", "Monthly booked revenue", "Volume", "Monthly orders",
            "Customer analysis", "Top customers", "Collection position", "Product analysis",
            "Products", "Geography", "Destination markets", "Business movement",
            "Confirmed freight bills", "Data coverage",
        ):
            self.assertIn(label, html)
        self.assertIn("$1,000.00", html)
        self.assertIn("USD/CNY Reference</b> 1 USD = ¥7.1234", html)
        self.assertIn("Source date · 2026-09-19", html)
        self.assertIn('href="/v2/"', html)
        self.assertIn('href="/v2/business"', html)
        self.assertNotIn("Gross Margin", html)
        self.assertIn("Profitability analysis requires purchase-cost and historical FX contracts.", html)

    def test_valid_linked_trade_counts_customer_owner_once_in_the_page(self):
        customer = self.create_order("BUS-CUSTOMER")
        export = self.create_order("BUS-EXPORT", unit_price_0="999", quantity_0="10")
        group = TradeGroup(group_no="BUSINESS-PAIR")
        db.session.add(group)
        customer.trade_group = export.trade_group = group
        customer.trade_role, export.trade_role = "CUSTOMER_ORDER", "EXPORT_ORDER"
        db.session.commit()

        html = self.get().get_data(as_text=True)
        self.assertIn("$1,000.00", html)
        self.assertNotIn("USD 9,990.00", html)

    def test_year_query_uses_full_requested_reporting_year_for_ytd_kpis(self):
        historical = self.create_order("BUS-2025")
        current = self.create_order("BUS-2026", unit_price_0="20", quantity_0="10")
        historical.pi_date = date(2025, 8, 22)
        current.pi_date = date(2026, 8, 22)
        db.session.commit()

        html = self.get("/v2/business?year=2025").get_data(as_text=True)
        self.assertIn("Management reporting · 2025 full reporting year", html)
        self.assertIn("$1,000.00", html)
        self.assertNotIn("$200.00", html)

    def test_year_selector_and_all_analytical_sections_use_the_selected_owner_population(self):
        historical = self.create_order("BUS-YEAR-2025", destination_port="Mombasa",
                                       destination_country_name="Kenya", destination_country_code="KE")
        current = self.create_order("BUS-YEAR-2026", unit_price_0="90", quantity_0="10",
                                    destination_port="Shanghai", destination_country_name="China",
                                    destination_country_code="CN")
        historical.pi_date = date(2025, 8, 22)
        current.pi_date = date(2026, 8, 22)
        historical.customer_name_snapshot = "Historical Customer"
        current.customer_name_snapshot = "Current Customer"
        historical.items[0].product_model_snapshot = "Historical Product"
        current.items[0].product_model_snapshot = "Current Product"
        db.session.add_all((
            FreightSettlement(pi_id=historical.id, usd_bill_required=True, usd_bill_amount=Decimal("10"),
                             usd_bill_confirmed=True, usd_payment_status="UNPAID", cny_bill_required=False),
            FreightSettlement(pi_id=current.id, usd_bill_required=True, usd_bill_amount=Decimal("90"),
                             usd_bill_confirmed=True, usd_payment_status="UNPAID", cny_bill_required=False),
        ))
        db.session.commit()

        html = self.get("/v2/business?year=2025").get_data(as_text=True)
        self.assertIn('name="year"', html)
        self.assertIn('value="2025" selected', html)
        self.assertIn('value="2026"', html)
        for historical_label in ("Historical Customer", "Historical Product", "Kenya", "$10.00"):
            self.assertIn(historical_label, html)
        for current_label in ("Current Customer", "Current Product", "China", "$90.00"):
            self.assertNotIn(current_label, html)

        empty = self.get("/v2/business?year=2024").get_data(as_text=True)
        self.assertIn('value="2024" selected', empty)
        self.assertIn("No booked revenue for this reporting period.", empty)

    def test_currency_sections_remain_separate_and_empty_state_is_honest(self):
        self.create_order("BUS-EUR", currency="EUR", unit_price_0="20", quantity_0="10")
        html = self.get().get_data(as_text=True)
        self.assertIn("€200.00", html)
        self.assertNotIn("$200.00", html)

    def test_weight_and_freight_context_render_from_reporting_without_unit_or_currency_merging(self):
        pi = self.create_order("BUS-WEIGHT", quantity_0="1500", quantity_unit_0="KGS")
        pi.items.append(PIItem(unit_price=Decimal("1"), quantity=Decimal("12"),
                               quantity_unit="BAGS", line_total=Decimal("12"),
                               product_model_snapshot="Unsupported unit"))
        db.session.add(FreightSettlement(
            pi_id=pi.id, usd_bill_required=True, usd_bill_amount=Decimal("100"),
            usd_bill_confirmed=True, usd_payment_status="UNPAID",
            cny_bill_required=True, cny_bill_amount=Decimal("50"),
            cny_bill_confirmed=True, cny_payment_status="PAID",
        ))
        db.session.commit()

        html = self.get().get_data(as_text=True)
        self.assertIn("1.50 MT", html)
        self.assertIn("Unsupported weight lines: <strong>1", html)
        self.assertIn("Total billed</span><strong>$100.00", html)
        self.assertIn("Unpaid</span><strong>$100.00", html)
        self.assertIn("Total billed</span><strong>¥50.00", html)
        self.assertIn("Unpaid</span><strong>¥0.00", html)

    def test_missing_historical_snapshots_render_honestly(self):
        pi = self.create_order("BUS-MISSING")
        pi.customer_name_snapshot = ""
        pi.destination_country_code = None
        pi.items[0].product_model_snapshot = ""
        db.session.commit()

        html = self.get().get_data(as_text=True)
        self.assertIn("Customer not recorded", html)
        self.assertIn("Product not recorded", html)
        self.assertIn("Country not recorded", html)

    def test_route_uses_the_fail_soft_fx_service_via_a_mocked_provider_result(self):
        with patch("v2.web.business_today", return_value=self.today), patch(
            "v2.management_dashboard.fx_reference_service.current_usd_cny",
            return_value=self.fx(),
        ) as reference:
            response = self.client().get("/v2/business")
        self.assertEqual(response.status_code, 200)
        reference.assert_called_once_with()

    def test_fx_fresh_stale_and_unavailable_states_are_explicit(self):
        self.create_order("BUS-FX")
        with self.subTest("fresh"):
            html = self.get(fx=self.fx()).get_data(as_text=True)
            self.assertIn("Source date · 2026-09-19", html)
            self.assertNotIn("Reference · stale", html)
        with self.subTest("stale"):
            html = self.get(fx=self.fx(stale=True)).get_data(as_text=True)
            self.assertIn("USD/CNY Reference</b> 1 USD = ¥7.1234 · stale", html)
        with self.subTest("unavailable"):
            html = self.get(fx=self.fx(available=False)).get_data(as_text=True)
            self.assertIn("USD/CNY reference unavailable", html)

    def test_empty_reporting_state_and_invalid_year_are_safe(self):
        html = self.get().get_data(as_text=True)
        self.assertIn("No booked revenue for this reporting period.", html)
        self.assertIn("USD primary management position", html)
        self.assertIn("USD contracted", html)
        self.assertEqual(self.get("/v2/business?year=not-a-year").status_code, 400)
        self.assertEqual(self.get("/v2/business?year=2027").status_code, 400)

    def test_chart_tabs_and_narrow_layout_use_accessible_scoped_contracts(self):
        self.create_order("BUS-TABS")
        html = self.get().get_data(as_text=True)
        self.assertIn('role="tablist"', html)
        self.assertIn('aria-selected="true"', html)
        self.assertIn('data-business-dashboard', html)
        self.assertIn('data-chart="USD"', html)
        self.assertIn('biz-order-bars', html)
        self.assertIn('biz-executive-grid', html)
        self.assertIn('biz-revenue-hero', html)
        self.assertIn('biz-metric-card--orders', html)
        self.assertIn('biz-metric-card--volume', html)
        self.assertIn('biz-metric-card--receivable', html)
        self.assertIn('biz-commercial-layout', html)
        self.assertIn('biz-chart-line', html)
        for structure in (
            'biz-customer-panel', 'biz-currency-caption', 'biz-share-track',
            'biz-concentration', 'biz-receivables-panel', 'biz-progress',
            'biz-outstanding-list', 'biz-products-panel', 'biz-product-list',
            'biz-market-panel', 'biz-market-track', 'biz-context-grid',
            'biz-shipment-stat', 'biz-freight-amounts', 'biz-coverage-list',
            'biz-quality-foot', 'biz-profit-note',
        ):
            self.assertIn(structure, html)
        self.assertIn("v2_business_dashboard.css", html)
        stylesheet = Path("v2/static/v2_business_dashboard.css").read_text()
        foundation = Path("v2/static/v2_design_system.css").read_text()
        self.assertIn(".v2-business-page", stylesheet)
        for alias in (
            "--biz-bg: var(--v2-color-canvas)",
            "--biz-surface: var(--v2-color-surface)",
            "--biz-border: var(--v2-color-border)",
            "--biz-text: var(--v2-color-text)",
            "--biz-primary: var(--v2-color-primary)",
            "--biz-revenue: var(--v2-color-accent)",
            "--biz-shadow: var(--v2-shadow-card)",
        ):
            self.assertIn(alias, stylesheet)
        self.assertIn(".biz-revenue-hero", stylesheet)
        self.assertIn(".biz-chart-line { fill: none; stroke: #168d80", stylesheet)
        self.assertIn("--v2-color-primary:#153b64", foundation)
        self.assertIn(".biz-table-scroll", stylesheet)
        self.assertIn("overflow-x: auto", stylesheet)
        self.assertIn(".biz-context-grid", stylesheet)
        self.assertIn(".biz-share-track", stylesheet)
        self.assertIn(".biz-coverage-track", stylesheet)
        self.assertNotIn("overflow-x:hidden", stylesheet)

    def test_usd_remains_primary_when_cny_is_larger_or_inserted_first(self):
        self.create_order("BUS-CNY-FIRST", currency="CNY", unit_price_0="2000", quantity_0="100")
        self.create_order("BUS-USD-PRIMARY", currency="USD", unit_price_0="86", quantity_0="1000")

        html = self.get().get_data(as_text=True)
        usd_position = html.index('class="is-primary"><span>USD · primary</span>')
        cny_position = html.index('class="is-secondary"><span>CNY</span>')
        self.assertLess(usd_position, cny_position)
        self.assertIn('title="$86,000.00"', html)
        self.assertIn('>$86K</strong>', html)
        self.assertIn('title="¥200,000.00"', html)
        self.assertIn('>¥200K</strong>', html)
        self.assertIn('USD outstanding receivables: $86,000.00', html)

    def test_usd_primary_zero_is_not_replaced_by_cny(self):
        self.create_order("BUS-CNY-ONLY", currency="CNY", unit_price_0="1200", quantity_0="1")
        html = self.get().get_data(as_text=True)
        self.assertIn('USD booked revenue: $0.00', html)
        self.assertIn('USD outstanding receivables: $0.00', html)
        self.assertIn('USD primary management position', html)
        self.assertLess(html.index('USD · primary'), html.index('CNY</span>'))

    def test_analytical_currency_views_are_card_local_and_prepared_without_a_query_state(self):
        usd = self.create_order("BUS-USD-ANALYSIS", currency="USD", unit_price_0="300", quantity_0="1",
                                destination_port="Mombasa", destination_country_name="Kenya",
                                destination_country_code="KE")
        cny = self.create_order("BUS-CNY-ANALYSIS", currency="CNY", unit_price_0="900", quantity_0="1",
                                destination_port="Shanghai", destination_country_name="China",
                                destination_country_code="CN")
        usd.customer_name_snapshot, cny.customer_name_snapshot = "USD Customer", "CNY Customer"
        other_customer = Customer(code="BUS-CNY", name="CNY Customer")
        db.session.add(other_customer)
        db.session.flush()
        cny.customer_id = other_customer.id
        usd.items[0].product_model_snapshot, cny.items[0].product_model_snapshot = "USD Product", "CNY Product"
        db.session.commit()

        html = self.get("/v2/business?year=2026").get_data(as_text=True)
        self.assertNotIn('name="currency"', html)
        self.assertNotIn('currency=CNY', html)
        for card in ("customer", "receivables", "products", "markets"):
            self.assertIn(f'data-analysis-button="{card}" data-analysis-currency="USD" aria-pressed="true"', html)
            self.assertIn(f'data-analysis-button="{card}" data-analysis-currency="CNY" aria-pressed="false"', html)
            self.assertIn(f'data-analysis-view="{card}" data-analysis-currency="USD"', html)
            self.assertIn(f'data-analysis-view="{card}" data-analysis-currency="CNY" hidden', html)
        for label in ("USD Customer", "CNY Customer", "USD Product", "CNY Product", "Kenya", "China"):
            self.assertIn(label, html)
        self.assertNotIn("biz-no-fx", html)
        self.assertIn("data-analysis-button", html)
        self.assertIn("view.hidden=view.dataset.analysisCurrency!==currency", html)
        self.assertIn('id="business-reporting-year" name="year"', html)
        stylesheet = Path("v2/static/v2_business_dashboard.css").read_text()
        self.assertIn("appearance: none", stylesheet)
        self.assertIn("background-image: url", stylesheet)
        self.assertIn(".biz-metric-card__value-row", stylesheet)

    def test_count_formatter_is_integer_only_and_kpi_cards_share_rows(self):
        self.create_order("BUS-INTEGER", currency="USD", unit_price_0="1", quantity_0="1")
        html = self.get().get_data(as_text=True)
        self.assertEqual(format_count(Decimal("1.00")), "1")
        self.assertEqual(format_count(Decimal("12000.00")), "12,000")
        self.assertIn('class="biz-metric-card__icon-row"', html)
        self.assertIn('class="biz-metric-card__title-row"', html)
        self.assertIn('class="biz-metric-card__value-row"', html)
        self.assertIn('class="biz-metric-card__support-row"', html)
        self.assertIn('>1</div>', html)
        self.assertNotIn('>1.00</td>', html)

    def test_year_without_cny_keeps_usd_as_the_only_local_analysis_view(self):
        historical = self.create_order("BUS-2025-USD", currency="USD", unit_price_0="100", quantity_0="1")
        current = self.create_order("BUS-2026-CNY", currency="CNY", unit_price_0="900", quantity_0="1")
        historical.pi_date = date(2025, 8, 22)
        current.pi_date = date(2026, 8, 22)
        db.session.commit()

        html = self.get("/v2/business?year=2025").get_data(as_text=True)
        self.assertIn('data-analysis-view="customer" data-analysis-currency="USD"', html)
        self.assertNotIn('data-analysis-button="customer"', html)
        self.assertNotIn('data-analysis-currency="CNY"', html)
        self.assertIn('$100.00', html)

    def test_other_currency_remains_separate_without_becoming_an_analysis_mode(self):
        self.create_order("BUS-EUR-OTHER", currency="EUR", unit_price_0="12", quantity_0="100")
        html = self.get().get_data(as_text=True)
        self.assertIn('Other currencies remain separately reported in Commercial Scale: EUR.', html)
        self.assertIn('data-chart="EUR"', html)
        self.assertNotIn('>EUR</a>', html)

    def test_compact_number_format_is_deterministic_and_keeps_small_values_exact(self):
        self.assertEqual(format_compact_amount(Decimal("86")), "86.00")
        self.assertEqual(format_compact_amount(Decimal("286500")), "286.5K")
        self.assertEqual(format_compact_amount(Decimal("1286000")), "1.29M")
        self.assertEqual(format_compact_amount(Decimal("12800000")), "12.8M")


if __name__ == "__main__":
    import unittest
    unittest.main()
