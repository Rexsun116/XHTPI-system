"""Presentation and browser-contract coverage for the shared V2 Master Data page."""

from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import Customer, Product, User, db


class _FormNestingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form_depth = 0
        self.nested_form_found = False

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            if self.form_depth:
                self.nested_form_found = True
            self.form_depth += 1

    def handle_endtag(self, tag):
        if tag == "form" and self.form_depth:
            self.form_depth -= 1


class V2MasterPresentationTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database = Path(self.tmp.name) / "master-presentation.db"
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(root / "scripts" / "init_v2_test_db.py"), str(database)],
                       cwd=root, check=True, capture_output=True, text=True)
        self.app = create_app(f"sqlite:///{database}", testing=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.user = User(username="master-presentation", password_hash=generate_password_hash("password"))
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()
        self.tmp.cleanup()

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id)
            session["_fresh"] = True
        return client

    def test_all_master_kinds_render_shared_workspace_without_delete(self):
        client = self.client()
        for kind in ("customers", "exporters", "factories", "forwarders", "products", "banks"):
            with self.subTest(kind=kind):
                page = client.get(f"/v2/master/{kind}").get_data(as_text=True)
                self.assertIn('class="v2-master-page"', page)
                self.assertIn('class="v2-master-form"', page)
                self.assertIn("v2-master-list-panel", page)
                self.assertNotIn(">Delete<", page)

    def test_party_create_and_edit_keep_fields_codes_and_post_contracts(self):
        client = self.client()
        party_fields = ("name", "address", "country", "contact_person", "phone", "email")
        for kind in ("customers", "exporters", "factories", "forwarders"):
            with self.subTest(kind=kind):
                page = client.get(f"/v2/master/{kind}").get_data(as_text=True)
                for field in party_fields:
                    self.assertIn(f'name="{field}"', page)
                self.assertNotIn('name="tax_code"', page)
                self.assertNotIn('name="code"', page)
                self.assertIn("Code will be generated automatically", page)

        response = client.post("/v2/master/customers", data={
            "name": "Customer detail", "address": "Address", "country": "CN",
            "contact_person": "Contact", "phone": "100", "email": "contact@example.test",
        })
        self.assertEqual(response.status_code, 302)
        created = db.session.scalar(db.select(Customer).where(Customer.name == "Customer detail"))
        self.assertEqual((created.address, created.country, created.contact_person, created.phone, created.email),
                         ("Address", "CN", "Contact", "100", "contact@example.test"))

        customer = Customer(code="CUS0002", name="Existing")
        db.session.add(customer)
        db.session.commit()
        page = client.get(f"/v2/master/customers/{customer.id}/edit").get_data(as_text=True)
        self.assertIn(f'action="/v2/master/customers/{customer.id}/edit"', page)
        self.assertIn('value="CUS0002" readonly data-master-code', page)
        self.assertIn('href="/v2/master/customers">Cancel</a>', page)
        for field in party_fields:
            self.assertIn(f'name="{field}"', page)

    def test_product_and_bank_fields_and_existing_controls_remain_intact(self):
        client = self.client()
        cases = {
            "products": ("category", "brand", "model", "packaging", "hs_code"),
            "banks": ("name", "beneficiary_name", "bank_name", "bank_address", "account_number", "swift_code", "currency", "remittance_information"),
        }
        for kind, fields in cases.items():
            with self.subTest(kind=kind):
                page = client.get(f"/v2/master/{kind}").get_data(as_text=True)
                for field in fields:
                    self.assertIn(f'name="{field}"', page)

    def test_product_uses_dedicated_editor_and_preserves_existing_browser_contract(self):
        product = Product(code="PRD0001", category="Pigment", brand="Acme", model="R-996",
                          packaging="25 kg bag", hs_code="320611")
        db.session.add(product)
        db.session.commit()
        client = self.client()

        page = client.get("/v2/master/products").get_data(as_text=True)
        self.assertIn('class="v2-master-fieldset v2-product-details"', page)
        self.assertIn('class="v2-master-fieldset v2-product-trade"', page)
        self.assertIn('class="v2-table v2-master-table v2-product-table"', page)
        for field in ("category", "brand", "model", "packaging", "hs_code"):
            self.assertIn(f'name="{field}"', page)
        self.assertNotIn("product_hs_code_snapshot", page)
        self.assertNotIn(">Delete<", page)
        self.assertIn(f'<form method="post" action="/v2/master/products/{product.id}/toggle">', page)
        self.assertIn("Set Inactive", page)

        edit_page = client.get(f"/v2/master/products/{product.id}/edit").get_data(as_text=True)
        self.assertIn(f'action="/v2/master/products/{product.id}/edit"', edit_page)
        self.assertIn('value="PRD0001" readonly data-master-code', edit_page)
        self.assertIn('href="/v2/master/products">Cancel</a>', edit_page)

    def test_toggle_remains_post_with_csrf_and_rows_do_not_nest_forms(self):
        customer = Customer(code="CUS0001", name="Existing")
        db.session.add(customer)
        db.session.commit()
        page = self.client().get("/v2/master/customers").get_data(as_text=True)
        self.assertIn(f'<form method="post" action="/v2/master/customers/{customer.id}/toggle">', page)
        self.assertIn('name="csrf_token"', page)
        self.assertIn("Set Inactive", page)
        parser = _FormNestingParser()
        parser.feed(page)
        self.assertFalse(parser.nested_form_found)

    def test_quotes_navigation_stays_hidden_while_quotes_route_remains_available(self):
        client = self.client()
        master_page = client.get("/v2/master/forwarders").get_data(as_text=True)
        self.assertNotIn('href="/v2/quotes"', master_page)
        self.assertEqual(client.get("/v2/quotes").status_code, 200)
