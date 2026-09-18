"""Focused Generated Files presentation coverage for Order Detail."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import Customer, Exporter, PI, PIItem, User, db


class GeneratedFilesPresentationTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'v2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="generated-files", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="CUS", name="Customer")
        self.exporter = Exporter(code="EXP", name="Exporter")
        db.session.add_all((self.user, self.customer, self.exporter)); db.session.commit()

    def tearDown(self):
        db.session.rollback(); db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def order(self):
        pi = PI(pi_no="GENERATED-FILES", pi_date=date(2026, 9, 1), order_type="SALES", status="NEW",
                customer_id=self.customer.id, exporter_id=self.exporter.id,
                customer_name_snapshot="Customer", exporter_name_snapshot="Exporter", currency="USD",
                payment_terms="T/T", planned_shipment_date=date(2026, 9, 30))
        pi.items.append(PIItem(unit_price=Decimal("10"), quantity=Decimal("1"), quantity_unit="MT",
                               line_total=Decimal("10"), product_model_snapshot="R-996"))
        db.session.add(pi); db.session.commit()
        return pi

    def test_generated_files_use_authoritative_routes_and_approved_format_assets(self):
        pi = self.order()
        page = self.client().get(f"/v2/orders/{pi.id}").get_data(as_text=True)

        for kind, label in (("pi", "PI"), ("invoice", "Commercial Invoice"),
                            ("packing", "Packing List"), ("contract", "Contract")):
            self.assertIn(label, page)
            self.assertIn(f'href="/v2/orders/{pi.id}/documents/{kind}"', page)
            self.assertIn(f'aria-label="Download {label} PDF"', page)
        self.assertEqual(page.count("/v2/static/icons/file-pdf-download.png"), 4)
        self.assertIn(f'href="/v2/orders/{pi.id}/documents/booking"', page)
        self.assertIn('aria-label="Download Booking Word document"', page)
        self.assertIn("/v2/static/icons/file-word-download.png", page)
        self.assertNotIn("file-excel-download.png", page)
        self.assertNotIn("order-file-icon", page)
        self.assertNotIn("Download PDF</a>", page)
        self.assertNotIn("Download Word</a>", page)

