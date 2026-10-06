"""Isolated route, projection, filter, and pagination coverage for All Orders."""
from datetime import date
from decimal import Decimal
from unittest import TestCase

from tests import test_v2_new_order_edit as fixture
from v2.models import Customer, FreightSettlement, PI, PIItem, Product, TradeGroup, db


class AllOrdersTest(TestCase):
    setUp = fixture.NewOrderEditTest.setUp
    tearDown = fixture.NewOrderEditTest.tearDown
    client = fixture.NewOrderEditTest.client

    def make_order(self, number, *, status="NEW", pi_date=date(2026, 9, 1), customer=None,
                   product=None, currency="USD", destination="KE"):
        customer = customer or self.customer
        product = product or self.product
        pi = PI(
            pi_no=number, pi_date=pi_date, order_type="SALES", status=status,
            customer_id=customer.id, exporter_id=self.exporter.id,
            customer_name_snapshot=customer.name, exporter_name_snapshot=self.exporter.name,
            currency=currency, advance_payment_amount=Decimal("200"),
            balance_payment_amount=Decimal("800"), advance_received_amount=Decimal("0"),
            balance_received_amount=Decimal("0"), destination_country_code=destination,
            destination_port="Mombasa" if destination == "KE" else "Bangkok",
        )
        pi.items.append(PIItem(
            product_id=product.id, unit_price=Decimal("100"), quantity=Decimal("10"),
            quantity_unit="MT", line_total=Decimal("1000"),
            product_category_snapshot=product.category, product_model_snapshot=product.model,
        ))
        db.session.add(pi); db.session.flush()
        return pi

    def test_route_requires_authentication(self):
        response = self.app.test_client().get("/v2/orders")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_population_payment_freight_and_read_only_links(self):
        standalone = self.make_order("ALL-STANDALONE", status="COMPLETED", pi_date=date(2026, 8, 1))
        standalone.advance_received_amount = Decimal("200")
        standalone.balance_received_amount = Decimal("800")
        active = self.make_order("ALL-ACTIVE", pi_date=date(2026, 9, 2))
        db.session.add(FreightSettlement(pi_id=active.id, usd_bill_required=True, usd_payment_status="UNPAID"))
        db.session.commit()
        html = self.client().get("/v2/orders").get_data(as_text=True)
        self.assertIn('href="/v2/orders/%s"' % active.id, html)
        self.assertIn("ALL-ACTIVE", html)
        self.assertIn("ALL-STANDALONE", html)
        self.assertIn("Outstanding", html)
        self.assertIn("Not required", html)
        self.assertLess(html.index("ALL-ACTIVE"), html.index("ALL-STANDALONE"))
        self.assertIn('method="get"', html)
        self.assertNotIn('method="post" action="/v2/orders"', html)

    def test_filters_use_pi_date_status_customer_product_country_and_type(self):
        second_customer = Customer(code="C2", name="Second Customer")
        second_product = Product(code="P2", category="Clay", model="KL-5")
        db.session.add_all((second_customer, second_product)); db.session.flush()
        target = self.make_order("ALL-TARGET", pi_date=date(2025, 5, 2), customer=second_customer,
                                 product=second_product, destination="TH")
        other = self.make_order("ALL-OTHER", pi_date=date(2026, 5, 2))
        target.trade_role = "CUSTOMER_ORDER"; target.trade_group = TradeGroup(group_no="ALL-FILTER")
        db.session.commit()
        url = (f"/v2/orders?year=2025&status=ACTIVE&customer={second_customer.id}&product={second_product.id}"
               "&destination_country=TH&order_type=customer_order")
        html = self.client().get(url).get_data(as_text=True)
        self.assertIn("ALL-TARGET", html)
        self.assertNotIn("ALL-OTHER", html)
        self.assertIn('value="2025" selected', html)
        self.assertIn('value="ACTIVE" selected', html)
        self.assertIn('value="customer_order" selected', html)

    def test_linked_export_uses_shipment_and_financial_owner_projection(self):
        customer = self.make_order("ALL-CUSTOMER", pi_date=date(2026, 9, 9), destination="TH")
        export = self.make_order("ALL-EXPORT", pi_date=date(2026, 9, 9), destination=None)
        group = TradeGroup(group_no="ALL-PAIR")
        customer.trade_group = export.trade_group = group
        customer.trade_role, export.trade_role = "CUSTOMER_ORDER", "EXPORT_ORDER"
        customer.etd = date(2026, 10, 1); customer.eta = date(2026, 10, 15)
        customer.advance_received_amount = Decimal("200"); customer.balance_received_amount = Decimal("800")
        db.session.add(FreightSettlement(pi_id=customer.id, usd_bill_required=True, usd_payment_status="PAID"))
        db.session.commit()
        html = self.client().get("/v2/orders").get_data(as_text=True)
        export_at = html.index("ALL-EXPORT")
        self.assertIn("Thailand", html[export_at:export_at + 2200])
        self.assertIn("2026-10-01", html[export_at:export_at + 2200])
        self.assertIn("Paid", html[export_at:export_at + 2600])
        self.assertIn("Linked · export side", html)
        for parameter in ("customer_payment=paid", "freight_payment=paid"):
            with self.subTest(parameter=parameter):
                filtered = self.client().get(f"/v2/orders?{parameter}").get_data(as_text=True)
                self.assertIn("ALL-CUSTOMER", filtered)
                self.assertIn("ALL-EXPORT", filtered)

    def test_payment_and_freight_filters_share_row_contract_and_fail_closed(self):
        paid = self.make_order("ALL-PAID")
        paid.advance_received_amount = Decimal("200"); paid.balance_received_amount = Decimal("800")
        freight_paid = self.make_order("ALL-FREIGHT-PAID")
        db.session.add(FreightSettlement(pi_id=freight_paid.id, usd_bill_required=True, usd_payment_status="PAID"))
        freight_pending = self.make_order("ALL-FREIGHT-PENDING")
        db.session.add(FreightSettlement(pi_id=freight_pending.id, usd_bill_required=True, usd_payment_status="UNPAID"))
        not_required = self.make_order("ALL-NOT-REQUIRED")
        db.session.add(FreightSettlement(pi_id=not_required.id, usd_bill_required=False, cny_bill_required=False))
        malformed = self.make_order("ALL-MALFORMED")
        malformed.trade_group = TradeGroup(group_no="ALL-BROKEN"); malformed.trade_role = "EXPORT_ORDER"
        db.session.commit()
        client = self.client()
        outstanding = client.get("/v2/orders?customer_payment=outstanding").get_data(as_text=True)
        self.assertNotIn("ALL-PAID", outstanding)
        self.assertIn("ALL-FREIGHT-PENDING", outstanding)
        freight = client.get("/v2/orders?freight_payment=outstanding").get_data(as_text=True)
        self.assertIn("ALL-FREIGHT-PENDING", freight)
        self.assertNotIn("ALL-FREIGHT-PAID", freight)
        self.assertNotIn("ALL-NOT-REQUIRED", freight)
        self.assertNotIn("ALL-MALFORMED", freight)

    def test_shell_active_state_is_limited_to_the_all_orders_route(self):
        pi = self.make_order("ALL-NAV")
        db.session.commit()
        client = self.client()
        listing = client.get("/v2/orders").get_data(as_text=True)
        self.assertIn('href="/v2/orders" aria-label="All Orders" aria-current="page"', listing)
        new_order = client.get("/v2/orders/new").get_data(as_text=True)
        detail = client.get(f"/v2/orders/{pi.id}").get_data(as_text=True)
        self.assertNotIn('href="/v2/orders" aria-label="All Orders" aria-current="page"', new_order)
        self.assertNotIn('href="/v2/orders" aria-label="All Orders" aria-current="page"', detail)

    def test_default_and_explicit_date_ordering_are_distinct(self):
        completed = self.make_order("ALL-COMPLETE-NEWEST", status="COMPLETED", pi_date=date(2026, 9, 30))
        active_old = self.make_order("ALL-ACTIVE-OLD", pi_date=date(2026, 1, 1))
        active_new = self.make_order("ALL-ACTIVE-NEW", pi_date=date(2026, 9, 1))
        db.session.commit()
        client = self.client()
        default = client.get("/v2/orders").get_data(as_text=True)
        self.assertLess(default.index(active_new.pi_no), default.index(active_old.pi_no))
        self.assertLess(default.index(active_old.pi_no), default.index(completed.pi_no))
        desc = client.get("/v2/orders?sort=order_date&direction=desc").get_data(as_text=True)
        self.assertLess(desc.index(completed.pi_no), desc.index(active_new.pi_no))
        asc = client.get("/v2/orders?sort=order_date&direction=asc").get_data(as_text=True)
        self.assertLess(asc.index(active_old.pi_no), asc.index(completed.pi_no))
        self.assertIn('aria-sort="ascending"', asc)

    def test_pagination_has_stable_boundaries_and_preserves_filters(self):
        for number in range(27):
            self.make_order(f"ALL-PAGE-{number:02d}", pi_date=date(2026, 1, 1))
        db.session.commit()
        first = self.client().get("/v2/orders?status=ACTIVE").get_data(as_text=True)
        self.assertIn("Showing 1–25 of 27", first)
        self.assertIn("status=ACTIVE", first)
        self.assertIn("page=2", first)
        second = self.client().get("/v2/orders?status=ACTIVE&page=2").get_data(as_text=True)
        self.assertIn("Showing 26–27 of 27", second)
        self.assertIn("ALL-PAGE-26", first)
        self.assertNotIn("ALL-PAGE-00", first)
        self.assertIn("ALL-PAGE-00", second)

    def test_empty_state_and_presentation_hooks_render(self):
        self.make_order("ALL-RENDER")
        html = self.client().get("/v2/orders?q=NO-SUCH-PI").get_data(as_text=True)
        self.assertIn("No orders match the current filters.", html)
        self.assertIn("v2-orders-table", self.client().get("/v2/orders").get_data(as_text=True))
        self.assertIn('aria-label="All Orders"', self.client().get("/v2/orders").get_data(as_text=True))
