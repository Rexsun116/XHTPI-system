"""Safety and ownership coverage for permanent deletion of NEW V2 orders."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import (
    BankAccount,
    Customer,
    Exporter,
    Factory,
    FreightForwarder,
    FreightQuote,
    FreightSettlement,
    OrderCorrectionSession,
    OrderFreightAgreement,
    OrderTask,
    PI,
    PIItem,
    Product,
    ProductBatch,
    TaskActivity,
    User,
    db,
)
from v2.order_deletion import (
    OrderDeletionConfirmationError,
    OrderDeletionNotAllowed,
    delete_new_order,
)


class V2OrderDeleteTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database = Path(self.tmp.name) / "delete.db"
        root = Path(__file__).resolve().parents[1]
        subprocess.run([sys.executable, str(root / "scripts" / "init_v2_test_db.py"), str(database)],
                       cwd=root, check=True, capture_output=True, text=True)
        self.app = create_app(f"sqlite:///{database}", testing=True)
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.user = User(username="deleter", password_hash=generate_password_hash("password"))
        self.customer = Customer(code="C", name="Customer")
        self.exporter = Exporter(code="E", name="Exporter")
        self.factory = Factory(code="F", name="Factory")
        self.forwarder = FreightForwarder(code="FF", name="Forwarder")
        self.product = Product(code="P", model="Product")
        self.bank = BankAccount(code="B", name="Bank", beneficiary_name="Beneficiary",
                                bank_name="Bank", account_number="123")
        db.session.add_all([self.user, self.customer, self.exporter, self.factory,
                            self.forwarder, self.product, self.bank])
        db.session.flush()
        self.quote = FreightQuote(freight_forwarder_id=self.forwarder.id,
                                  departure_port="Shanghai", destination_port="Lagos",
                                  amount=Decimal("500"), currency="USD")
        db.session.add(self.quote)
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

    def make_order(self, pi_no="DELETE-ME", status="NEW", with_all_children=False):
        pi = PI(pi_no=pi_no, pi_date=date(2026, 9, 3), order_type="SALES", status=status,
                customer_id=self.customer.id, exporter_id=self.exporter.id,
                bank_account_id=self.bank.id, freight_forwarder_id=self.forwarder.id,
                customer_name_snapshot=self.customer.name,
                exporter_name_snapshot=self.exporter.name, currency="USD")
        item = PIItem(product_id=self.product.id, factory_id=self.factory.id,
                      unit_price=Decimal("10"), quantity=Decimal("2"), quantity_unit="MT",
                      line_total=Decimal("20"), product_model_snapshot="Product")
        item.batches.append(ProductBatch(batch_number="BATCH-1"))
        pi.items.append(item)
        db.session.add(pi)
        db.session.flush()
        task = OrderTask(pi_id=pi.id, task_code="DELETE_TEST", title="Delete test",
                         source="AUTO", status="ACTION", health="NORMAL",
                         completion_mode="MANUAL", dedupe_key=f"delete:{pi.id}")
        db.session.add(task)
        db.session.flush()
        db.session.add(TaskActivity(task_id=task.id, event_type="CREATED", to_status="ACTION",
                                    actor_type="SYSTEM"))
        if with_all_children:
            db.session.add_all([
                OrderFreightAgreement(pi_id=pi.id, source_freight_quote_id=self.quote.id,
                                      freight_forwarder_id=self.forwarder.id,
                                      freight_forwarder_name_snapshot=self.forwarder.name,
                                      amount=Decimal("500"), currency="USD"),
                FreightSettlement(pi_id=pi.id, usd_bill_required=True),
                OrderCorrectionSession(pi_id=pi.id, module="COMMERCIAL", reason="test",
                                       opened_by_id=self.user.id),
            ])
        db.session.commit()
        return pi

    def test_new_order_and_every_owned_dependency_are_deleted_but_masters_remain(self):
        pi = self.make_order(with_all_children=True)
        ids = {model: db.session.scalar(db.select(model.id)) for model in
               (PIItem, ProductBatch, OrderTask, TaskActivity, OrderFreightAgreement,
                FreightSettlement, OrderCorrectionSession)}

        self.assertEqual(delete_new_order(pi, pi.pi_no), "DELETE-ME")

        self.assertIsNone(db.session.get(PI, pi.id))
        for model, row_id in ids.items():
            self.assertIsNone(db.session.get(model, row_id), model.__name__)
        for model, row_id in ((Customer, self.customer.id), (Exporter, self.exporter.id),
                              (Factory, self.factory.id), (FreightForwarder, self.forwarder.id),
                              (FreightQuote, self.quote.id), (BankAccount, self.bank.id),
                              (Product, self.product.id), (User, self.user.id)):
            self.assertIsNotNone(db.session.get(model, row_id), model.__name__)
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(TaskActivity)), 0)

    def test_wrong_and_blank_confirmation_are_rejected_and_order_remains(self):
        for confirmation in ("WRONG", ""):
            with self.subTest(confirmation=confirmation):
                pi = self.make_order(pi_no=f"KEEP-{confirmation or 'BLANK'}")
                with self.assertRaises(OrderDeletionConfirmationError):
                    delete_new_order(pi, confirmation)
                self.assertIsNotNone(db.session.get(PI, pi.id))

    def test_allowlist_rejects_every_non_new_status(self):
        for status in ("PRE_SHIPMENT", "SHIPPED", "ARRIVED", "COMPLETED"):
            with self.subTest(status=status):
                pi = self.make_order(pi_no=f"KEEP-{status}", status=status)
                with self.assertRaises(OrderDeletionNotAllowed):
                    delete_new_order(pi, pi.pi_no)
                self.assertIsNotNone(db.session.get(PI, pi.id))

    def test_direct_post_cannot_bypass_status_or_confirmation_guards(self):
        client = self.client()
        new = self.make_order(pi_no="KEEP-NEW")
        response = client.post(f"/v2/orders/{new.id}/delete", data={"confirmation": "WRONG"})
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(db.session.get(PI, new.id))
        shipped = self.make_order(pi_no="KEEP-SHIPPED", status="SHIPPED")
        response = client.post(f"/v2/orders/{shipped.id}/delete",
                               data={"confirmation": shipped.pi_no})
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(db.session.get(PI, shipped.id))

    def test_ui_only_offers_delete_for_new_and_success_redirects_with_flash(self):
        client = self.client()
        pi = self.make_order(pi_no="UI-DELETE")
        page = client.get(f"/v2/orders/{pi.id}")
        self.assertIn("Delete Order", page.get_data(as_text=True))
        response = client.post(f"/v2/orders/{pi.id}/delete",
                               data={"confirmation": pi.pi_no}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("Order UI-DELETE permanently deleted.", text)
        self.assertNotIn("UI-DELETE", text.replace("Order UI-DELETE permanently deleted.", ""))

        later = self.make_order(pi_no="NO-DELETE", status="PRE_SHIPMENT")
        self.assertNotIn("Delete Order", client.get(f"/v2/orders/{later.id}").get_data(as_text=True))
        self.assertEqual(client.get(f"/v2/orders/{later.id}/delete").status_code, 409)

    def test_delete_requires_authentication_and_csrf(self):
        pi = self.make_order(pi_no="SECURE")
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get(f"/v2/orders/{pi.id}/delete").status_code, 302)
        self.assertEqual(anonymous.post(f"/v2/orders/{pi.id}/delete",
                                        data={"confirmation": pi.pi_no}).status_code, 302)
        client = self.client()
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(client.post(f"/v2/orders/{pi.id}/delete",
                                     data={"confirmation": pi.pi_no}).status_code, 400)
        self.assertIsNotNone(db.session.get(PI, pi.id))

    def test_commit_failure_rolls_back_order_and_children(self):
        pi = self.make_order(pi_no="ROLLBACK", with_all_children=True)
        pi_id = pi.id
        with patch.object(db.session, "commit", side_effect=RuntimeError("simulated commit failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated commit failure"):
                delete_new_order(pi, pi.pi_no)

        self.assertIsNotNone(db.session.get(PI, pi_id))
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(PIItem)
                                           .where(PIItem.pi_id == pi_id)), 1)
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(OrderTask)
                                           .where(OrderTask.pi_id == pi_id)), 1)
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(TaskActivity)), 1)

    def test_deleted_order_and_tasks_disappear_from_dashboard_queries(self):
        pi = self.make_order(pi_no="DASHBOARD-DELETE")
        pi_id = pi.id
        delete_new_order(pi, pi.pi_no)
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(PI)
                                           .where(PI.id == pi_id)), 0)
        self.assertEqual(db.session.scalar(db.select(db.func.count()).select_from(OrderTask)
                                           .where(OrderTask.pi_id == pi_id)), 0)
        self.assertNotIn("DASHBOARD-DELETE", self.client().get("/v2/").get_data(as_text=True))
