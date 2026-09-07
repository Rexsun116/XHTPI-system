"""PI-only document layout and safe local electronic-seal resolution."""
from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.documents import (exporter_seal_css_class, format_decimal_compact, format_document_multiline,
                          format_product_description, format_trade_term_for_document,
                          render_invoice_html, resolve_exporter_seal_uri)
from v2.models import BankAccount, Customer, Exporter, PI, PIItem, User, db


class ProformaDocumentTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'document.db'}", testing=True)
        self.app.config["DOCUMENT_ASSET_DIR"] = str(Path(self.tmp.name) / "private-stamps")
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="document", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="CUS-DOC", name="Customer <Unsafe>", address="A;B；C")
        self.exporter = Exporter(code="EXP-DOC", name="Historical Exporter", address="Exporter;Address")
        self.other_exporter = Exporter(code="EXP-OTHER", name="Other Exporter")
        self.bank = BankAccount(code="BNK-DOC", name="Live Account", beneficiary_name="Live beneficiary",
                                bank_name="Live Bank", account_number="LIVE", bank_address="Live address")
        db.session.add_all((self.user, self.customer, self.exporter, self.other_exporter, self.bank)); db.session.commit()

    def tearDown(self):
        db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def pi(self, *, exporter=None, pi_no="PI-DOC"):
        exporter = exporter or self.exporter
        pi = PI(pi_no=pi_no, pi_date=date(2026, 9, 6), order_type="SALES", status="NEW",
                customer_id=self.customer.id, exporter_id=exporter.id, customer_name_snapshot="Customer <Unsafe>",
                customer_address_snapshot="Line 1;Line 2；Line 3", customer_tax_code_snapshot="TAX",
                exporter_name_snapshot="Historical Exporter", exporter_address_snapshot="Top;Address",
                currency="USD", payment_terms="OA90", loading_port="SHANGHAI LONG PORT",
                destination_port="LOS ANGELES VERY LONG DESTINATION PORT", bank_account_id=self.bank.id,
                bank_name_snapshot="Snapshot Bank", bank_address_snapshot="Snapshot Bank;Address",
                bank_beneficiary_snapshot="Snapshot Beneficiary", bank_account_number_snapshot="SNAP-123",
                bank_swift_snapshot="SNAPSWIFT", bank_remittance_snapshot="Reference;Only")
        pi.items.append(PIItem(unit_price=Decimal("10"), quantity=Decimal("2"), quantity_unit="MT",
                               line_total=Decimal("20"), product_model_snapshot="A;B；<tag>"))
        db.session.add(pi); db.session.commit(); return pi

    def client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user.id); session["_fresh"] = True
        return client

    def test_multiline_is_safe_and_stored_values_are_unchanged(self):
        pi = self.pi(); pi.exporter_name_snapshot = "LINE1;LINE2；&unsafe"; db.session.commit()
        html = render_invoice_html(pi, "pi")
        self.assertEqual(format_document_multiline("A;B；C"), "A\nB\nC")
        self.assertEqual(pi.customer_address_snapshot, "Line 1;Line 2；Line 3")
        self.assertEqual(pi.exporter_name_snapshot, "LINE1;LINE2；&unsafe")
        self.assertIn("Line 1\nLine 2\nLine 3", html)
        self.assertGreaterEqual(html.count("LINE1\nLINE2\n&amp;unsafe"), 2)
        self.assertIn('class="company multiline"', html)
        self.assertIn('class="signature multiline"', html)
        self.assertIn("&lt;tag&gt;", html); self.assertNotIn("<tag>", html)

    def test_pi_layout_payment_route_note_and_snapshot_bank_mapping(self):
        pi = self.pi(); html = render_invoice_html(pi, "pi")
        for token in ('class="pi-to multiline"', 'class="pi-meta"', 'class="section pi-ports"',
                      'class="pi-port-destination"', "PAYMENT ROUTE", "**NOTE",
                      "DOCUMENTS ISSUED BY ELECTRONIC PROCESS. NO SIGN OR STAMP REQUIRED.",
                      "Historical Exporter"):
            self.assertIn(token, html)
        ordered = ["Account with Bank:", "Bank address:", "Beneficiary:",
                   "Account number: SNAP-123", "SWIFT Code: SNAPSWIFT"]
        positions = [html.index(value) for value in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("Live Bank", html); self.assertNotIn("LIVE", html)
        self.assertIn("Reference\nOnly", html)

    def test_commercial_invoice_reuses_approved_pi_body_with_only_title_changed(self):
        pi = self.pi(); item = pi.items[0]
        item.product_category_snapshot = "TITANIUM DIOXIDE"; item.product_brand_snapshot = None
        item.product_model_snapshot = "R-996"; item.trade_term = "FOB"
        pi.exporter_name_snapshot = "Exporter;中文公司"; pi.customer_address_snapshot = "Line 1；Line 2"
        pi.loading_port = "SHANGHAI"; db.session.commit()
        proforma = render_invoice_html(pi, "pi")
        invoice = render_invoice_html(pi, "invoice")
        self.assertIn(">INVOICE<", invoice)
        self.assertNotIn("PROFORMA INVOICE", invoice); self.assertNotIn("COMMERCIAL INVOICE", invoice)
        self.assertEqual(proforma.replace("PROFORMA INVOICE", "DOCUMENT-TITLE"),
                         invoice.replace("INVOICE", "DOCUMENT-TITLE"))
        for required in ("TITANIUM DIOXIDE R-996", "FOB SHANGHAI", "Account with Bank:",
                         "Bank address:", "Beneficiary:", "Account number:", "SWIFT Code:",
                         "Exporter\n中文公司", "Line 1\nLine 2"):
            self.assertIn(required, invoice)

    def test_structured_exporter_seal_isolated_by_code_and_missing_is_safe(self):
        pi = self.pi(); root = Path(self.app.config["DOCUMENT_ASSET_DIR"]); root.mkdir(parents=True)
        (root / "EXP-DOC.png").write_bytes(b"synthetic-test-image")
        self.assertTrue(resolve_exporter_seal_uri(pi).endswith("EXP-DOC.png"))
        html = render_invoice_html(pi, "pi"); self.assertIn('class="seal"', html)
        other = self.pi(exporter=self.other_exporter, pi_no="PI-OTHER")
        self.assertIsNone(resolve_exporter_seal_uri(other))
        self.assertNotIn('class="seal"', render_invoice_html(other, "pi"))
        (root / "EXP-OTHER.png").write_bytes(b"another-synthetic-test-image")
        self.assertTrue(resolve_exporter_seal_uri(other).endswith("EXP-OTHER.png"))
        self.assertNotEqual(resolve_exporter_seal_uri(pi), resolve_exporter_seal_uri(other))

    def test_missing_or_malformed_asset_and_no_exporter_relationship_do_not_break_pdf_route(self):
        pi = self.pi(); root = Path(self.app.config["DOCUMENT_ASSET_DIR"]); root.mkdir(parents=True)
        (root / "EXP-DOC.png").write_bytes(b"not-a-real-png")
        self.assertIn('class="seal"', render_invoice_html(pi, "pi"))
        response = self.client().get(f"/v2/orders/{pi.id}/documents/pi")
        self.assertEqual(response.status_code, 200); self.assertTrue(response.data.startswith(b"%PDF"))
        pi.exporter_id = None; db.session.commit()
        self.assertIsNone(resolve_exporter_seal_uri(pi))

    def test_seal_code_path_traversal_is_not_resolved(self):
        pi = self.pi(); self.exporter.code = "../not-a-seal"; db.session.commit()
        self.assertIsNone(resolve_exporter_seal_uri(pi))

    def test_pi_uses_compact_numbers_and_defined_trade_term_port_rules_only(self):
        self.assertEqual(format_decimal_compact(Decimal("1000.00")), "1000")
        self.assertEqual(format_decimal_compact(Decimal("1000.10")), "1000.1")
        self.assertEqual(format_decimal_compact(Decimal("1000.11")), "1000.11")
        pi = self.pi(); item = pi.items[0]
        item.unit_price = Decimal("1000.10"); item.quantity = Decimal("20.50"); item.line_total = Decimal("20000.50")
        item.trade_term = "fob"; pi.loading_port = "SHANGHAI"; pi.destination_port = "MOMBASA"; db.session.commit()
        html = render_invoice_html(pi, "pi")
        self.assertIn(">1000.1<", html); self.assertIn(">20.5 MT<", html)
        self.assertIn(">20000.5<", html); self.assertIn("USD 20000.5", html)
        self.assertNotIn("1000.1000", html); self.assertIn("FOB SHANGHAI", html)
        item.trade_term = "CIF"; pi.destination_port = "MOMBASA"; self.assertEqual(format_trade_term_for_document(item.trade_term, pi), "CIF MOMBASA")
        item.trade_term = "cfr"; pi.destination_port = "JEBEL ALI"; self.assertEqual(format_trade_term_for_document(item.trade_term, pi), "CFR JEBEL ALI")
        pi.loading_port = None; item.trade_term = "FOB"; self.assertEqual(format_trade_term_for_document(item.trade_term, pi), "FOB")
        self.assertEqual(format_trade_term_for_document("DAP", pi), "DAP")

    def test_seal_extension_whitelist_and_priority(self):
        pi = self.pi(); root = Path(self.app.config["DOCUMENT_ASSET_DIR"]); root.mkdir(parents=True)
        (root / "EXP-DOC.gif").write_bytes(b"not-whitelisted")
        self.assertIsNone(resolve_exporter_seal_uri(pi))
        (root / "EXP-DOC.jpeg").write_bytes(b"jpeg")
        self.assertTrue(resolve_exporter_seal_uri(pi).endswith("EXP-DOC.jpeg"))
        (root / "EXP-DOC.jpg").write_bytes(b"jpg")
        self.assertTrue(resolve_exporter_seal_uri(pi).endswith("EXP-DOC.jpg"))
        (root / "EXP-DOC.png").write_bytes(b"png")
        self.assertTrue(resolve_exporter_seal_uri(pi).endswith("EXP-DOC.png"))

    def test_pi_commodity_ignores_empty_null_like_brand_without_mutating_snapshots(self):
        pi = self.pi(); item = pi.items[0]
        item.product_category_snapshot = "TITANIUM DIOXIDE"; item.product_model_snapshot = "R-996"
        for brand in (None, "", "   ", "None", "null"):
            item.product_brand_snapshot = brand
            self.assertEqual(format_product_description(item.product_category_snapshot, item.product_brand_snapshot, item.product_model_snapshot), "TITANIUM DIOXIDE R-996")
            self.assertIn("TITANIUM DIOXIDE R-996", render_invoice_html(pi, "pi"))
        item.product_brand_snapshot = "TITAX"
        self.assertEqual(format_product_description(item.product_category_snapshot, item.product_brand_snapshot, item.product_model_snapshot), "TITANIUM DIOXIDE TITAX R-996")

    def test_round_seal_profile_is_code_keyed_for_aea_titax_only(self):
        pi = self.pi(); pi.exporter.code = "EXP0001"; db.session.commit()
        self.assertEqual(exporter_seal_css_class(pi), "seal seal-round")
        pi.exporter.code = "EXP0003"; db.session.commit()
        self.assertEqual(exporter_seal_css_class(pi), "seal seal-round seal-round-titax")
        pi.exporter.code = "EXP0002"; db.session.commit()
        self.assertEqual(exporter_seal_css_class(pi), "seal")
        html = render_invoice_html(pi, "pi")
        self.assertIn(".seal-round{width:150px;height:150px;object-fit:contain}", html)
        self.assertIn(".seal-round-titax{width:100px;height:100px;object-fit:contain}", html)

    def test_commercial_invoice_packing_and_booking_document_routes_remain_available(self):
        pi = self.pi()
        for kind, magic in (("invoice", b"%PDF"), ("packing", b"%PDF")):
            response = self.client().get(f"/v2/orders/{pi.id}/documents/{kind}")
            self.assertEqual(response.status_code, 200); self.assertTrue(response.data.startswith(magic))
