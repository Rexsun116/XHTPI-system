"""Focused isolated tests for V2 Create PI UAT Fix #1."""

from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import Customer, Exporter, Factory, PI, PIItem, Product, User, db


class V2CreatePIFormTest(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.temp.name) / 'v2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="create", password_hash=generate_password_hash("password"))
        self.customer = Customer(code="C", name="Customer")
        self.exporter = Exporter(code="E", name="Exporter")
        self.factory = Factory(code="F", name="Factory")
        self.product = Product(code="P", category="TITANIUM DIOXIDE", brand="BLR", model="R504")
        db.session.add_all([self.user, self.customer, self.exporter, self.factory, self.product]); db.session.commit()

    def tearDown(self):
        db.session.remove(); self.ctx.pop(); self.temp.cleanup()

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def form(self, pi_no="NEW-1", **changes):
        data = {
            "pi_no": pi_no, "pi_date": "2026-08-22", "order_type": "SALES", "currency": "USD",
            "customer_id": str(self.customer.id), "exporter_id": str(self.exporter.id),
            "payment_terms": "20% advance", "advance_payment_percent": "20", "planned_shipment_date": "2026-09-20",
            "product_0": str(self.product.id), "factory_0": str(self.factory.id), "unit_price_0": "100",
            "quantity_0": "10", "quantity_unit_0": "MT",
        }
        data.update(changes); return data

    def test_01_form_defaults_currency_and_empty_party_placeholders(self):
        html = self.client().get("/v2/orders/new").get_data(as_text=True)
        self.assertIn('<option value="USD" selected>', html)
        self.assertIn('<option value="">Select Customer</option>', html)
        self.assertIn('<option value="">Select Exporter</option>', html)

    def test_02_sales_initially_hides_commission_and_has_toggle_marker(self):
        html = self.client().get("/v2/orders/new").get_data(as_text=True)
        self.assertIn('id="commission-section"', html)
        self.assertIn('hidden', html)
        self.assertIn("toggleCommission", html)

    def test_03_items_precede_payment_and_dynamic_remove_exists(self):
        html = self.client().get("/v2/orders/new").get_data(as_text=True)
        self.assertLess(html.index("2. PI Items"), html.index("3. Structured Payment Plan"))
        self.assertIn("remove-item", html)
        self.assertIn("recalculate", html)

    def test_04_multiple_items_contract_total_and_decimal_advance(self):
        form = self.form("MULTI", product_1=str(self.product.id), unit_price_1="20.25", quantity_1="4", quantity_unit_1="MT")
        response = self.client().post("/v2/orders/new", data=form)
        self.assertEqual(response.status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "MULTI"))
        self.assertEqual(len(pi.items), 2)
        self.assertEqual(pi.contract_total, Decimal("1081.00"))
        self.assertEqual(pi.advance_payment_amount, Decimal("216.20"))
        self.assertEqual(pi.balance_payment_amount, Decimal("864.80"))

    def test_05_removed_item_gap_does_not_break_server_parsing(self):
        form = self.form("GAP", product_3=str(self.product.id), unit_price_3="10", quantity_3="2")
        del form["product_0"]; del form["unit_price_0"]; del form["quantity_0"]
        response = self.client().post("/v2/orders/new", data=form)
        self.assertEqual(response.status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "GAP"))
        self.assertEqual(len(pi.items), 1)
        self.assertEqual(pi.contract_total, Decimal("20.00"))

    def test_06_unique_pi_check_available_and_duplicate(self):
        client = self.client()
        self.assertTrue(client.post("/v2/check-pi-number", data={"pi_no": "UNIQUE"}).get_json()["available"])
        self.assertEqual(client.post("/v2/orders/new", data=self.form("UNIQUE")).status_code, 302)
        self.assertFalse(client.post("/v2/check-pi-number", data={"pi_no": "UNIQUE"}).get_json()["available"])

    def test_07_duplicate_is_rejected_when_async_check_is_bypassed(self):
        client = self.client(); client.post("/v2/orders/new", data=self.form("DUP"))
        response = client.post("/v2/orders/new", data=self.form("DUP"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("PI Number already exists.", response.get_data(as_text=True))
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(PI)), 1)

    def test_08_required_fields_are_server_validated_and_preserved(self):
        client = self.client()
        for field, label in (("pi_no", "PI Number"), ("pi_date", "PI Date"), ("payment_terms", "Payment Terms"),
                             ("customer_id", "Customer"), ("exporter_id", "Exporter")):
            form = self.form("KEEP-ME"); form[field] = ""
            response = client.post("/v2/orders/new", data=form)
            self.assertEqual(response.status_code, 400)
            html = response.get_data(as_text=True)
            self.assertIn(f"{label} is required.", html)
            if field != "pi_no":
                self.assertIn("KEEP-ME", html)
            if field != "payment_terms":
                self.assertIn("20% advance", html)

    def test_09_sales_ignores_crafted_commission_facts(self):
        response = self.client().post("/v2/orders/new", data=self.form("SALES-C", commission_rate="9", commission_amount="99", commission_currency="USD"))
        self.assertEqual(response.status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "SALES-C"))
        self.assertIsNone(pi.commission_rate); self.assertIsNone(pi.commission_amount)

    def test_10_commission_stores_commission_facts(self):
        response = self.client().post("/v2/orders/new", data=self.form("COMM", order_type="COMMISSION", commission_factory_id=str(self.factory.id), commission_rate="2.5", commission_currency="USD"))
        self.assertEqual(response.status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "COMM"))
        self.assertEqual(pi.commission_amount, Decimal("25.00"))

    def test_11_product_category_brand_model_are_snapshotted_for_sales_and_commission(self):
        client = self.client()
        self.assertEqual(client.post("/v2/orders/new", data=self.form("SNAP-SALES")).status_code, 302)
        self.assertEqual(client.post("/v2/orders/new", data=self.form(
            "SNAP-COMMISSION", order_type="COMMISSION", commission_factory_id=str(self.factory.id),
        )).status_code, 302)
        sales = db.session.scalar(db.select(PI).where(PI.pi_no == "SNAP-SALES"))
        commission = db.session.scalar(db.select(PI).where(PI.pi_no == "SNAP-COMMISSION"))
        for pi in (sales, commission):
            self.assertEqual(
                (pi.items[0].product_category_snapshot, pi.items[0].product_brand_snapshot,
                 pi.items[0].product_model_snapshot),
                ("TITANIUM DIOXIDE", "BLR", "R504"),
            )
        self.product.brand = "NEWBRAND"; db.session.commit()
        self.assertEqual(sales.items[0].product_brand_snapshot, "BLR")
