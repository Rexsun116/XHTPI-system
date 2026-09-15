"""Agreed freight display uses disposable databases and persisted snapshots."""
from decimal import Decimal
from unittest import TestCase
from tests import test_v2_linked_shipment as fixture
from v2.models import OrderFreightAgreement, FreightSettlement, db


class FreightReferenceTest(TestCase):
    setUp = fixture.LinkedShipmentTest.setUp
    tearDown = fixture.LinkedShipmentTest.tearDown
    client = fixture.LinkedShipmentTest.client
    pi = fixture.LinkedShipmentTest.pi
    pair = fixture.LinkedShipmentTest.pair

    def setup_reference(self, pi, currency="USD", amount="677.50"):
        pi.status = "SHIPPED"
        agreement = OrderFreightAgreement(pi_id=pi.id, freight_forwarder_name_snapshot="Stored Forwarder",
                                          currency=currency, amount=Decimal(amount))
        settlement = FreightSettlement(pi_id=pi.id, usd_bill_required=True, cny_bill_required=True,
                                       usd_bill_amount=Decimal("700"), cny_bill_amount=Decimal("100"))
        db.session.add_all([agreement, settlement]); db.session.commit()
        return agreement, settlement

    def form(self, pi):
        response = self.client().get(f"/v2/orders/{pi.id}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True).split('<form id="freight-settlement"', 1)[1].split('</form>', 1)[0]

    def test_usd_cny_and_zero_snapshot_reference(self):
        for currency, amount, expected in (("USD", "677.50", "USD 677.50"),
                                            ("CNY", "1234.50", "CNY 1,234.50"),
                                            ("USD", "0", "USD 0.00")):
            pi = self.pi(f"REF-{currency}-{amount}")
            agreement, settlement = self.setup_reference(pi, currency, amount)
            before = list(db.session.execute(OrderFreightAgreement.__table__.select()).tuples())
            form = self.form(pi)
            self.assertIn(expected, form)
            self.assertEqual(form.count("Agreed Freight"), 1)
            self.assertIn('name="usd_bill_amount"', form)
            self.assertIn('name="cny_bill_amount"', form)
            self.assertNotIn('name="agreement_amount"', form)
            self.assertEqual(before, list(db.session.execute(OrderFreightAgreement.__table__.select()).tuples()))
            self.assertEqual(settlement.usd_bill_amount, Decimal("700"))

    def test_no_agreement_safe_unavailable_state(self):
        pi = self.pi("NO-AGREEMENT"); pi.status = "SHIPPED"; db.session.commit()
        form = self.form(pi)
        self.assertIn("Agreed freight not available", form)
        self.assertNotIn("USD 0.00", form)

    def test_actual_bill_edit_is_independent_and_current_snapshot_is_used(self):
        pi = self.pi("EDIT-BILL")
        agreement, settlement = self.setup_reference(pi)
        self.form(pi)
        response = self.client().post(f"/v2/orders/{pi.id}/facts", data={
            "_form_scope": "FREIGHT_SETTLEMENT", "usd_bill_amount": "725.25"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(settlement.usd_bill_amount, Decimal("725.25"))
        self.assertEqual(agreement.amount, Decimal("677.50"))
        # Display must follow the stored snapshot after an explicit amendment.
        agreement.amount = Decimal("650"); db.session.commit()
        self.assertIn("USD 650.00", self.form(pi))
        self.assertNotIn("USD 677.50", self.form(pi))

    def test_linked_customer_reference_and_export_has_no_editable_form(self):
        customer, export = self.pair()
        self.setup_reference(customer)
        export.status = "COMPLETED"; db.session.commit()
        self.assertIn("USD 677.50", self.form(customer))
        page = self.client().get(f"/v2/orders/{export.id}").get_data(as_text=True)
        self.assertNotIn('id="freight-settlement"', page)
        self.assertNotIn('name="usd_bill_amount"', page)
        self.assertNotIn('name="cny_bill_amount"', page)
