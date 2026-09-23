"""Presentation contracts for the completed-order correction workspace."""
from datetime import datetime
from pathlib import Path
from unittest import TestCase

from tests import test_v2_order_delete as fixture
from v2.models import FreightSettlement, OrderCorrectionSession, db

ROOT = Path(__file__).parents[1]


class V2CorrectionPresentationTest(TestCase):
    def setUp(self):
        self.editor = (ROOT / "v2/templates/v2/correction.html").read_text()
        self.secondary = (ROOT / "v2/templates/v2/_order_secondary.html").read_text()
        self.css = (ROOT / "v2/static/v2_design_system.css").read_text()

    def test_order_detail_keeps_separate_open_save_and_close_contracts(self):
        for token in (
            'id="correction-workflow"', 'CONTROLLED CORRECTION',
            "url_for('v2.correction_open'", "url_for('v2.order_facts'",
            "url_for('v2.correction_close'", "url_for('v2.correction_edit'",
            'name="module"', 'name="reason"', 'name="note"',
            'SESSION OPEN', 'Open Full Correction Editor',
            'Saving a correction keeps this session open.', 'Close Correction',
        ):
            self.assertIn(token, self.secondary)
        self.assertIn('{{ csrf() }}', self.secondary)
        self.assertIn('method="post" action="{{ url_for(\'v2.correction_close\'', self.secondary)

    def test_editor_is_a_completed_module_scoped_workspace(self):
        for token in (
            "{% block body_class %}v2-modern{% endblock %}", 'v2-correction-page',
            'Correction context', 'COMPLETED', 'Editable scope · {{ correction.module }}',
            'Saving persists only this module’s correction.', 'Save Correction',
            'Back to Order', 'name="csrf_token"', 'method="post"',
        ):
            self.assertIn(token, self.editor)
        self.assertNotIn('Back to order and close session', self.editor)
        self.assertNotIn('v2-new-order-form', self.editor)

    def test_all_existing_module_field_contracts_remain_in_the_editor(self):
        for token in (
            "'COMMERCIAL'", "'PAYMENT'", "'DOCUMENTS'", "'SHIPPING'", "'FREIGHT'", "'ARRIVAL'",
            "'pi_no'", 'name="customer_id"', 'name="bank_account_id"', "'item_' ~ item.id ~ '_quantity'",
            "'advance_payment_percent'", "'balance_received_at'", 'document_facts',
            'name="other_document_notes"', "'container_loading_at'", "'actual_departure_date'",
            "'usd_bill_required'", "'agreement_amount'", "'actual_arrival_date'",
        ):
            self.assertIn(token, self.editor)

    def test_correction_styles_keep_scope_and_close_controls_responsive(self):
        for token in (
            '.v2-correction-page', '.v2-correction-context', '.v2-correction-grid',
            '.v2-correction-session-summary', '.v2-correction-close-form',
            '@media(max-width:575.98px)',
        ):
            self.assertIn(token, self.css)
        self.assertNotIn('overflow-x:hidden', self.css)


class V2CorrectionEditorRenderTest(TestCase):
    setUp = fixture.V2OrderDeleteTest.setUp
    tearDown = fixture.V2OrderDeleteTest.tearDown
    client = fixture.V2OrderDeleteTest.client
    make_order = fixture.V2OrderDeleteTest.make_order

    def test_every_existing_module_renders_the_completed_correction_workspace(self):
        pi = self.make_order(pi_no="CORRECTION-PRESENTATION", status="COMPLETED")
        for module in ("COMMERCIAL", "PAYMENT", "DOCUMENTS", "SHIPPING", "FREIGHT", "ARRIVAL"):
            with self.subTest(module=module):
                session = OrderCorrectionSession(pi_id=pi.id, module=module,
                                                  reason="Presentation review", opened_by_id=self.user.id)
                db.session.add(session)
                db.session.commit()
                page = self.client().get(f"/v2/corrections/{session.id}/edit")
                self.assertEqual(page.status_code, 200)
                text = page.get_data(as_text=True)
                self.assertIn("v2-correction-page", text)
                self.assertIn(f"Editable scope · {module}", text)
                self.assertIn('name="csrf_token"', text)
                self.assertIn("Back to Order", text)
                db.session.delete(session)
                db.session.commit()

    def test_order_detail_payment_correction_serializes_calendar_dates_for_facts_save(self):
        pi = self.make_order(pi_no="CORRECTION-DATE", status="COMPLETED")
        pi.advance_received_at = datetime(2026, 5, 1)
        pi.balance_received_at = None
        correction = OrderCorrectionSession(pi_id=pi.id, module="PAYMENT",
                                            reason="Correct receipt date", opened_by_id=self.user.id)
        db.session.add(correction)
        db.session.commit()

        secondary = (ROOT / "v2/templates/v2/_order_secondary.html").read_text()
        self.assertIn("value.strftime('%Y-%m-%d') if value else ''", secondary)

        page = self.client().get(f"/v2/orders/{pi.id}")
        text = page.get_data(as_text=True)
        self.assertIn('name="advance_received_at" type="date" value="2026-05-01"', text)
        self.assertNotIn('name="advance_received_at" type="date" value="2026-05-01 00:00:00"', text)
        self.assertIn('name="balance_received_at" type="date" value=""', text)

        response = self.client().post(f"/v2/orders/{pi.id}/facts", data={
            "advance_received_at": "2026-05-01",
        })
        self.assertEqual(response.status_code, 302)
        db.session.refresh(pi)
        self.assertEqual(pi.advance_received_at, datetime(2026, 5, 1))
        self.assertIsNone(db.session.get(OrderCorrectionSession, correction.id).closed_at)

    def test_order_detail_freight_correction_serializes_paid_at_dates_for_facts_save(self):
        pi = self.make_order(pi_no="CORRECTION-FREIGHT-DATE", status="COMPLETED",
                             with_all_children=True)
        settlement = db.session.scalar(db.select(FreightSettlement).where(FreightSettlement.pi_id == pi.id))
        existing_correction = db.session.scalar(db.select(OrderCorrectionSession).where(
            OrderCorrectionSession.pi_id == pi.id,
        ))
        db.session.delete(existing_correction)
        settlement.usd_paid_at = datetime(2026, 5, 3)
        settlement.cny_paid_at = None
        correction = OrderCorrectionSession(pi_id=pi.id, module="FREIGHT",
                                            reason="Correct freight payment date", opened_by_id=self.user.id)
        db.session.add(correction)
        db.session.commit()

        page = self.client().get(f"/v2/orders/{pi.id}")
        text = page.get_data(as_text=True)
        self.assertIn('name="usd_paid_at" type="date" value="2026-05-03"', text)
        self.assertNotIn('name="usd_paid_at" type="date" value="2026-05-03 00:00:00"', text)
        self.assertIn('name="cny_paid_at" type="date" value=""', text)

        response = self.client().post(f"/v2/orders/{pi.id}/facts", data={
            "usd_payment_status": "PAID", "usd_paid_at": "2026-05-03",
        })
        self.assertEqual(response.status_code, 302)
        db.session.refresh(settlement)
        self.assertEqual(settlement.usd_paid_at, datetime(2026, 5, 3))
        self.assertIsNone(db.session.get(OrderCorrectionSession, correction.id).closed_at)
