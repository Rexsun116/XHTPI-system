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
        self.assertLess(html.index("PI Items"), html.index("Payment &amp; Commercial"))
        self.assertIn("remove-item", html)
        self.assertIn("recalculate", html)

    def test_03a_rendered_pi_validation_dom_and_initializer_contract(self):
        html = self.client().get("/v2/orders/new").get_data(as_text=True)
        self.assertIn('id="create-pi-form"', html)
        self.assertIn('id="pi_no" name="pi_no"', html)
        self.assertIn('id="pi-number-feedback" class="v2-pi-feedback" role="status" aria-live="polite"', html)
        self.assertIn('id="create-submit"', html)
        self.assertIn('name="csrf_token"', html)
        self.assertIn('id="contract-total"', html)
        self.assertIn("const feedback = document.getElementById('pi-number-feedback')", html)
        self.assertIn("feedback.textContent = result.available", html)
        self.assertLess(html.index('id="contract-total"'), html.index("<script>"))

    def test_03b_country_comboboxes_have_accessible_visible_names_and_hidden_codes(self):
        html = self.client().get("/v2/orders/new").get_data(as_text=True)
        for field in ("loading", "destination"):
            self.assertIn(f'id="{field}_country_name" name="{field}_country_name"', html)
            self.assertIn(f'name="{field}_country_code"', html)
            self.assertIn(f'aria-controls="{field}-country-options"', html)
        self.assertEqual(html.count('role="combobox"'), 2)
        self.assertEqual(html.count('role="listbox"'), 2)
        self.assertIn("v2_country_combobox.js", html)
        self.assertIn("window.V2_COUNTRIES", html)
        self.assertNotIn('name="loading_country_name" class="form-select"', html)
        self.assertNotIn('value="CN" data-country-code', html)

    def test_03c_country_combobox_client_contract_clears_stale_code_and_keeps_keyboard_support(self):
        script = (Path(__file__).parents[1] / "v2/static/v2_country_combobox.js").read_text()
        self.assertIn('code.value = "";', script)
        self.assertLess(script.index('code.value = "";'), script.index('if (exact) code.value = exact.code;'))
        for key in ("ArrowDown", "ArrowUp", "Enter", "Escape", "aria-expanded", "aria-activedescendant"):
            self.assertIn(key, script)
        self.assertIn('.slice(0, 10)', script)
        self.assertIn('country.aliases || []', script)
        self.assertIn('existing && existing.code !== country.code ? null : country', script)

    def test_04_multiple_items_contract_total_and_decimal_advance(self):
        form = self.form("MULTI", product_1=str(self.product.id), unit_price_1="20.25", quantity_1="4", quantity_unit_1="MT")
        response = self.client().post("/v2/orders/new", data=form)
        self.assertEqual(response.status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "MULTI"))
        self.assertEqual(len(pi.items), 2)
        self.assertEqual(pi.contract_total, Decimal("1081.00"))
        self.assertEqual(pi.advance_payment_amount, Decimal("216.20"))
        self.assertEqual(pi.balance_payment_amount, Decimal("864.80"))

    def test_04a_country_name_code_submission_persists_canonical_route_codes(self):
        form = self.form("COUNTRY-ROUTE", loading_port="Shanghai", loading_country_name="China",
                         loading_country_code="CN", destination_port="Mombasa",
                         destination_country_name="Kenya", destination_country_code="KE")
        self.assertEqual(self.client().post("/v2/orders/new", data=form).status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "COUNTRY-ROUTE"))
        self.assertEqual((pi.loading_port, pi.loading_country_code,
                          pi.destination_port, pi.destination_country_code),
                         ("Shanghai", "CN", "Mombasa", "KE"))

    def test_04b_full_exact_country_name_entry_resolves_without_hidden_code(self):
        form = self.form("COUNTRY-NAME", loading_country_name="china", destination_country_name="KENYA")
        self.assertEqual(self.client().post("/v2/orders/new", data=form).status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "COUNTRY-NAME"))
        self.assertEqual((pi.loading_country_code, pi.destination_country_code), ("CN", "KE"))

    def test_04ba_unambiguous_country_alias_entry_resolves_without_hidden_code(self):
        form = self.form("COUNTRY-ALIASES", loading_country_name="Republic of the Congo",
                         destination_country_name="DRC")
        self.assertEqual(self.client().post("/v2/orders/new", data=form).status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "COUNTRY-ALIASES"))
        self.assertEqual((pi.loading_country_code, pi.destination_country_code), ("CG", "CD"))

    def test_04c_country_validation_rejects_stale_or_forged_hidden_codes_without_partial_order(self):
        client = self.client()
        cases = (
            ("COUNTRY-MISSING", {"loading_port": "Shanghai"}, "Loading country is required"),
            ("COUNTRY-INVALID", {"loading_country_name": "Kenyaa", "loading_country_code": "KE"}, "Please select a valid loading country."),
            ("COUNTRY-AMBIGUOUS", {"loading_country_name": "Congo"}, "Please select a valid loading country."),
            ("COUNTRY-FORGED", {"loading_country_name": "Kenya", "loading_country_code": "XX"}, "Country code must be a valid ISO"),
            ("COUNTRY-MISMATCH", {"loading_country_name": "Kenya", "loading_country_code": "CN"}, "Loading country does not match"),
        )
        for pi_no, changes, message in cases:
            with self.subTest(pi_no=pi_no):
                response = client.post("/v2/orders/new", data=self.form(pi_no, **changes))
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.get_data(as_text=True))
                self.assertIsNone(db.session.scalar(db.select(PI).where(PI.pi_no == pi_no)))

    def test_04d_country_without_port_is_valid_and_failed_rerender_keeps_safe_country_state(self):
        client = self.client()
        country_only = self.form("COUNTRY-ONLY", destination_country_name="Kenya", destination_country_code="KE")
        self.assertEqual(client.post("/v2/orders/new", data=country_only).status_code, 302)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "COUNTRY-ONLY"))
        self.assertEqual((pi.destination_port, pi.destination_country_code), (None, "KE"))

        failed = self.form("COUNTRY-RERENDER", loading_country_name="Kenya", loading_country_code="KE",
                           unit_price_0="not-a-number")
        response = client.post("/v2/orders/new", data=failed)
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn('name="loading_country_name" value="Kenya"', html)
        self.assertIn('name="loading_country_code" value="KE"', html)
        self.assertIsNone(db.session.scalar(db.select(PI).where(PI.pi_no == "COUNTRY-RERENDER")))

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
        self.assertEqual(client.post("/v2/orders/new", data=self.form("WU1333")).status_code, 302)
        for pi_no in ("WU1333", "wu1333", "Wu1333", "wU1333"):
            with self.subTest(pi_no=pi_no):
                self.assertFalse(client.post("/v2/check-pi-number", data={"pi_no": pi_no}).get_json()["available"])
        self.assertTrue(client.post("/v2/check-pi-number", data={"pi_no": "WU1334"}).get_json()["available"])

    def test_07_duplicate_is_rejected_when_async_check_is_bypassed(self):
        client = self.client(); client.post("/v2/orders/new", data=self.form("WU1333"))
        for pi_no in ("WU1333", "wu1333"):
            with self.subTest(pi_no=pi_no):
                response = client.post("/v2/orders/new", data=self.form(pi_no))
                self.assertEqual(response.status_code, 400)
                self.assertIn("PI Number already exists.", response.get_data(as_text=True))
                self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(PI)), 1)

    def test_07a_api_rejects_case_only_duplicate(self):
        client = self.client(); self.assertEqual(client.post("/v2/orders/new", data=self.form("WU1333")).status_code, 302)
        response = client.post("/orders", json={"pi_no": "wu1333"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "PI Number already exists."})

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
