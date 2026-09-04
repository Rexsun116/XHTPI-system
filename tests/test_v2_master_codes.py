"""V2 master-code generation and immutable-code browser behavior."""

from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.master_codes import create_master_record, generate_master_code
from v2.models import (
    BankAccount,
    Customer,
    Exporter,
    Factory,
    FreightForwarder,
    PI,
    Product,
    User,
    db,
)


class V2MasterCodeTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database = Path(self.tmp.name) / "master-codes.db"
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(root / "scripts" / "init_v2_test_db.py"), str(database)],
                       cwd=root, check=True, capture_output=True, text=True)
        self.app = create_app(f"sqlite:///{database}", testing=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.user = User(username="master", password_hash=generate_password_hash("password"))
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

    def create_payload(self, kind, *, name="Test Master", code=None):
        payloads = {
            "customers": {"name": name},
            "exporters": {"name": name},
            "factories": {"name": name},
            "forwarders": {"name": name},
            "products": {"model": name},
            "banks": {"name": name, "beneficiary_name": "Beneficiary",
                      "bank_name": "Bank", "account_number": "123"},
        }
        payload = payloads[kind]
        if code is not None:
            payload["code"] = code
        return payload

    def test_create_all_six_master_types_without_code(self):
        client = self.client()
        cases = (
            ("exporters", Exporter, "EXP0001"),
            ("customers", Customer, "CUS0001"),
            ("factories", Factory, "FAC0001"),
            ("forwarders", FreightForwarder, "FF0001"),
            ("products", Product, "PRD0001"),
            ("banks", BankAccount, "BNK0001"),
        )
        for kind, model, expected in cases:
            with self.subTest(kind=kind):
                response = client.post(f"/v2/master/{kind}", data=self.create_payload(kind))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(db.session.scalar(db.select(model.code)), expected)

    def test_sequences_are_consecutive_independent_and_ignore_legacy_codes(self):
        db.session.add(Customer(code="C001", name="Legacy"))
        db.session.commit()
        client = self.client()
        client.post("/v2/master/customers", data={"name": "First"})
        client.post("/v2/master/customers", data={"name": "Second"})
        client.post("/v2/master/exporters", data={"name": "Exporter"})
        self.assertEqual(list(db.session.scalars(db.select(Customer.code).order_by(Customer.id))),
                         ["C001", "CUS0001", "CUS0002"])
        self.assertEqual(db.session.scalar(db.select(Exporter.code)), "EXP0001")

    def test_forged_create_code_cannot_control_generated_code(self):
        self.client().post("/v2/master/customers",
                           data=self.create_payload("customers", code="CUS9999"))
        self.assertEqual(db.session.scalar(db.select(Customer.code)), "CUS0001")

    def test_edit_displays_readonly_code_and_forged_post_cannot_change_it(self):
        client = self.client()
        client.post("/v2/master/products", data={"model": "Original"})
        product = db.session.scalar(db.select(Product))
        page = client.get(f"/v2/master/products/{product.id}/edit").get_data(as_text=True)
        self.assertIn('value="PRD0001" readonly data-master-code', page)
        self.assertNotIn('name="code"', page)
        response = client.post(f"/v2/master/products/{product.id}/edit",
                               data={"model": "Updated", "code": "PRD9999"})
        self.assertEqual(response.status_code, 302)
        db.session.refresh(product)
        self.assertEqual((product.code, product.model), ("PRD0001", "Updated"))

    def test_create_form_has_no_code_input(self):
        page = self.client().get("/v2/master/customers").get_data(as_text=True)
        self.assertNotIn('name="code"', page)
        self.assertIn("Code will be generated automatically.", page)

    def test_unique_collision_is_rolled_back_and_retried(self):
        db.session.add(Customer(code="CUS0001", name="Existing"))
        db.session.commit()
        with patch("v2.master_codes.generate_master_code",
                   side_effect=["CUS0001", "CUS0002"]):
            created = create_master_record(Customer, "CUS", {"name": "Concurrent"})
        self.assertEqual(created.code, "CUS0002")
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(Customer)), 2)

    def test_edit_toggle_and_existing_code_regression(self):
        customer = Customer(code="KEEP-ME", name="Original")
        db.session.add(customer)
        db.session.commit()
        client = self.client()
        client.post(f"/v2/master/customers/{customer.id}/edit", data={"name": "Updated"})
        client.post(f"/v2/master/customers/{customer.id}/toggle")
        db.session.refresh(customer)
        self.assertEqual((customer.code, customer.name, customer.active),
                         ("KEEP-ME", "Updated", False))

    def test_generated_masters_continue_to_support_pi_and_product_snapshot(self):
        client = self.client()
        for kind in ("customers", "exporters", "products", "banks"):
            client.post(f"/v2/master/{kind}", data=self.create_payload(kind, name=kind))
        customer = db.session.scalar(db.select(Customer))
        exporter = db.session.scalar(db.select(Exporter))
        product = db.session.scalar(db.select(Product))
        bank = db.session.scalar(db.select(BankAccount))
        response = client.post("/orders", json={
            "pi_no": "AUTO-CODE-PI", "pi_date": "2026-09-04", "order_type": "SALES",
            "customer_id": customer.id, "exporter_id": exporter.id,
            "bank_account_id": bank.id, "currency": "USD", "payment_terms": "20% advance",
            "planned_shipment_date": "2026-10-01", "advance_payment_percent": "20",
            "items": [{"product_id": product.id, "unit_price": "10", "quantity": "2"}],
        })
        self.assertEqual(response.status_code, 201)
        pi = db.session.scalar(db.select(PI).where(PI.pi_no == "AUTO-CODE-PI"))
        self.assertEqual(pi.items[0].product_model_snapshot, "products")

    def test_generator_uses_largest_valid_sequence_and_expands_past_four_digits(self):
        db.session.add_all([
            Customer(code="CUS0009", name="Nine"),
            Customer(code="CUS10000", name="Ten Thousand"),
            Customer(code="CUS-BAD", name="Malformed"),
        ])
        db.session.commit()
        self.assertEqual(generate_master_code(Customer, "CUS"), "CUS10001")
