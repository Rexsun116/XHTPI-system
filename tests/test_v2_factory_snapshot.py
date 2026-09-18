"""Focused coverage for PIItem Factory snapshot compatibility and creation."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import re
import tempfile
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import Customer, Exporter, Factory, PI, PIItem, Product, User, db


class FactorySnapshotTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'v2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="factory-snapshot", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="CUS", name="Customer")
        self.exporter = Exporter(code="EXP", name="Exporter")
        self.product = Product(code="PRD", category="Pigment", brand="Brand", model="R-996")
        self.factory_a = Factory(code="FAC-A", name="Factory A", address="Address A", tax_code="TAX-A",
                                 country="CN", contact_person="Alice", phone="100", email="factory@example.test")
        self.factory_b = Factory(code="FAC-B", name="Factory B", address="Address B", tax_code="TAX-B",
                                 country="CN", contact_person="Bob", phone="200")
        db.session.add_all((self.user, self.customer, self.exporter, self.product, self.factory_a, self.factory_b))
        db.session.commit()

    def tearDown(self):
        db.session.rollback(); db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def order(self, number, items):
        pi = PI(pi_no=number, pi_date=date(2026, 9, 1), order_type="SALES", status="NEW",
                customer_id=self.customer.id, exporter_id=self.exporter.id,
                customer_name_snapshot="Customer", exporter_name_snapshot="Exporter", currency="USD",
                planned_shipment_date=date(2026, 9, 30), payment_terms="T/T")
        for factory_id, snapshot in items:
            pi.items.append(PIItem(product_id=self.product.id, factory_id=factory_id,
                                   factory_name_snapshot=snapshot, unit_price=Decimal("10"),
                                   quantity=Decimal("1"), quantity_unit="MT", line_total=Decimal("10"),
                                   product_model_snapshot="R-996"))
        db.session.add(pi); db.session.commit()
        return pi

    def factory_display(self, pi):
        response = self.client().get(f"/v2/orders/{pi.id}")
        self.assertEqual(response.status_code, 200)
        match = re.search(r"<dt>Factory</dt><dd>(.*?)</dd>", response.get_data(as_text=True))
        self.assertIsNotNone(match)
        return match.group(1)

    def test_snapshot_name_wins_over_current_master_name(self):
        pi = self.order("FACTORY-SNAPSHOT", [(self.factory_a.id, "Historical Factory")])
        self.factory_a.name = "Renamed Factory"; db.session.commit()

        self.assertEqual(self.factory_display(pi), "Historical Factory")

    def test_blank_snapshot_uses_factory_master_name(self):
        pi = self.order("FACTORY-FALLBACK", [(self.factory_a.id, None)])

        self.assertEqual(self.factory_display(pi), "Factory A")

    def test_no_factory_data_renders_dash(self):
        pi = self.order("FACTORY-NONE", [(None, None)])

        self.assertEqual(self.factory_display(pi), "—")

    def test_same_resolved_factory_is_shown_once(self):
        pi = self.order("FACTORY-DEDUP", [(self.factory_a.id, None), (self.factory_a.id, "Factory A")])

        self.assertEqual(self.factory_display(pi), "Factory A")

    def test_distinct_resolved_factories_preserve_item_order(self):
        pi = self.order("FACTORY-MULTI", [(self.factory_b.id, None), (self.factory_a.id, "Historical A")])

        self.assertEqual(self.factory_display(pi), "Factory B / Historical A")

    def test_mixed_snapshot_and_master_fallback_resolve_independently(self):
        pi = self.order("FACTORY-MIXED", [(self.factory_a.id, "Historical A"), (self.factory_b.id, None)])

        self.assertEqual(self.factory_display(pi), "Historical A / Factory B")

    def new_order_form(self, pi_no, *, factory_id=None):
        data = {
            "pi_no": pi_no, "pi_date": "2026-09-01", "order_type": "SALES", "currency": "USD",
            "customer_id": str(self.customer.id), "exporter_id": str(self.exporter.id),
            "payment_terms": "T/T", "advance_payment_percent": "20", "planned_shipment_date": "2026-09-30",
            "product_0": str(self.product.id), "trade_term_0": "FOB", "unit_price_0": "10",
            "quantity_0": "2", "quantity_unit_0": "MT",
        }
        if factory_id is not None:
            data["factory_0"] = str(factory_id)
        return data

    def test_standard_new_order_copies_existing_factory_snapshot_contract(self):
        response = self.client().post("/v2/orders/new", data=self.new_order_form("FACTORY-WRITE", factory_id=self.factory_a.id))
        self.assertEqual(response.status_code, 302)
        item = db.session.scalar(db.select(PIItem).join(PI).where(PI.pi_no == "FACTORY-WRITE"))

        self.assertEqual(item.factory_id, self.factory_a.id)
        self.assertEqual(
            (item.factory_name_snapshot, item.factory_address_snapshot, item.factory_tax_code_snapshot,
             item.factory_country_snapshot, item.factory_contact_snapshot, item.factory_phone_snapshot),
            ("Factory A", "Address A", "TAX-A", "CN", "Alice", "100"),
        )
        self.assertIsNone(item.factory_email_snapshot)
        self.assertEqual((item.product_model_snapshot, item.quantity, item.quantity_unit, item.trade_term),
                         ("R-996", Decimal("2"), "MT", "FOB"))

    def test_standard_new_order_without_factory_keeps_factory_facts_empty(self):
        response = self.client().post("/v2/orders/new", data=self.new_order_form("FACTORY-WRITE-NONE"))
        self.assertEqual(response.status_code, 302)
        item = db.session.scalar(db.select(PIItem).join(PI).where(PI.pi_no == "FACTORY-WRITE-NONE"))

        self.assertIsNone(item.factory_id)
        self.assertEqual(
            (item.factory_name_snapshot, item.factory_address_snapshot, item.factory_tax_code_snapshot,
             item.factory_country_snapshot, item.factory_contact_snapshot, item.factory_phone_snapshot),
            (None, None, None, None, None, None),
        )
