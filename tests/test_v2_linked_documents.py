"""Batch 3B document ownership/gates, exclusively on temporary SQLite databases."""
from datetime import date, datetime
from decimal import Decimal
from unittest import TestCase
from unittest.mock import patch

from sqlalchemy import event, update

from tests import test_v2_linked_shipment as shipment_fixture
from v2.linked_shipment import (_claim_pair, LinkedShipmentError,
                                record_linked_actual_departure)
from v2.models import OrderFreightAgreement, OrderTask, PI, TaskActivity, db
from v2.presenter import task_actions
from v2.rules import CUSTOMER_DOCUMENT_TASK_CODES, LINKED_EXPORT_SETTLEMENT
from v2.selector import projected
from v2.services import reconcile_order_tasks_for_pi


class LinkedDocumentsTest(TestCase):
    # Reuse only fixture helpers, not inherited test methods/counts.
    setUp = shipment_fixture.LinkedShipmentTest.setUp
    tearDown = shipment_fixture.LinkedShipmentTest.tearDown
    client = shipment_fixture.LinkedShipmentTest.client
    pi = shipment_fixture.LinkedShipmentTest.pi
    pair = shipment_fixture.LinkedShipmentTest.pair
    snapshot = shipment_fixture.LinkedShipmentTest.snapshot
    departure = shipment_fixture.LinkedShipmentTest.departure
    assert_departed = shipment_fixture.LinkedShipmentTest.assert_departed

    codes = ("DOCUMENT_EXPORT_LICENSE", "DOCUMENT_CUSTOMS", LINKED_EXPORT_SETTLEMENT)
    flags = ("export_license_required", "customs_docs_required", "settlement_documents_required")

    def task(self, pi, code):
        return db.session.scalar(db.select(OrderTask).where(
            OrderTask.dedupe_key == f"v2:order:{pi.id}:{code.lower()}"))

    def ready_pair(self):
        customer, export = self.pair()
        customer.container_loading_date = date(2099, 12, 1)
        for flag in self.flags:
            setattr(export, flag, True)
        reconcile_order_tasks_for_pi(export, now=datetime(2026, 9, 20))
        db.session.commit()
        return customer, export

    def complete(self, export, codes=None):
        for code in codes or self.codes[:2]:
            response = self.client().post(f"/v2/tasks/{self.task(export, code).id}/done")
            self.assertEqual(response.status_code, 302)

    def test_future_customer_loading_activates_all_three_without_export_loading(self):
        customer, export = self.ready_pair()
        self.assertIsNone(export.container_loading_date)
        for code in self.codes:
            task = self.task(export, code)
            self.assertEqual((task.status, task.completion_mode), ("ACTION", "MANUAL"))
            self.assertEqual(projected(task)[0], "ACTION")
            self.assertIn("done", [action["kind"] for action in task_actions(task)])
            self.assertIsNone(self.task(customer, code))

    def test_export_loading_or_legacy_datetime_cannot_replace_customer_loading_date(self):
        customer, export = self.pair()
        export.container_loading_date = date(2000, 1, 1)
        customer.container_loading_at = datetime(2000, 1, 1)
        for flag in self.flags:
            setattr(export, flag, True)
        reconcile_order_tasks_for_pi(export); db.session.commit()
        for code in self.codes:
            self.assertIsNone(self.task(export, code))

    def test_customer_loading_route_enter_change_clear_and_restore_reuses_rows(self):
        customer, export = self.pair()
        for flag in self.flags:
            setattr(export, flag, True)
        db.session.commit()
        ids = None
        for value, expected in (("2099-12-01", "ACTION"), ("2099-12-02", "ACTION"),
                                ("", "CANCELLED"), ("2099-12-03", "ACTION")):
            response = self.client().post(f"/v2/orders/{customer.id}/facts",
                                          data={"container_loading_date": value})
            self.assertEqual(response.status_code, 302)
            tasks = [self.task(export, code) for code in self.codes]
            self.assertEqual([t.status for t in tasks], [expected] * 3)
            if ids is None:
                ids = [t.id for t in tasks]
            self.assertEqual([t.id for t in tasks], ids)
        for code in self.codes:
            self.assertEqual(OrderTask.query.filter_by(pi_id=export.id, task_code=code).count(), 1)

    def test_done_tasks_survive_loading_changes_clear_and_requirement_removal(self):
        customer, export = self.ready_pair()
        self.complete(export, self.codes)
        before = [(self.task(export, code).id, self.task(export, code).completed_at) for code in self.codes]
        for value in ("2099-12-02", "", "2099-12-03"):
            self.assertEqual(self.client().post(f"/v2/orders/{customer.id}/facts",
                data={"container_loading_date": value}).status_code, 302)
            self.assertEqual([self.task(export, code).status for code in self.codes], ["DONE"] * 3)
        self.assertEqual(self.client().post(f"/v2/orders/{export.id}/document-requirements",
            data={flag: "false" for flag in self.flags}).status_code, 302)
        self.assertEqual([(self.task(export, code).id, self.task(export, code).completed_at)
                          for code in self.codes], before)
        self.assertEqual([self.task(export, code).status for code in self.codes], ["DONE"] * 3)

    def test_flags_are_export_local_not_copied_from_customer(self):
        customer, export = self.pair()
        customer.container_loading_date = date(2099, 1, 1)
        for flag in self.flags:
            setattr(customer, flag, True)
            setattr(export, flag, False)
        db.session.commit()
        self.assertEqual(self.client().post(f"/v2/orders/{customer.id}/facts",
            data={"container_loading_date": "2099-01-02"}).status_code, 302)
        for flag, code in zip(self.flags, self.codes):
            self.assertIs(getattr(export, flag), False)
            self.assertIsNone(self.task(export, code))

    def test_independent_document_windows_and_receipt_settlement_unchanged(self):
        pi = self.pi("XHT-independent")  # Prefix must not activate linked behavior.
        pi.container_loading_date = date(2026, 10, 1)
        for flag in self.flags:
            setattr(pi, flag, True)
        for day, expected in ((25, "UPCOMING"), (26, "ACTION")):
            reconcile_order_tasks_for_pi(pi, now=datetime(2026, 9, day))
            for code in self.codes[:2]:
                self.assertEqual(self.task(pi, code).status, expected)
        self.assertIsNone(self.task(pi, LINKED_EXPORT_SETTLEMENT))
        self.assertIsNone(self.task(pi, "SETTLEMENT_DOCUMENT_ADVANCE"))
        pi.advance_received_amount = Decimal("10"); pi.advance_received_at = datetime(2026, 9, 20)
        pi.balance_received_amount = Decimal("90"); pi.balance_received_at = datetime(2026, 9, 21)
        reconcile_order_tasks_for_pi(pi)
        for code in ("SETTLEMENT_DOCUMENT_ADVANCE", "SETTLEMENT_DOCUMENT_BALANCE"):
            self.assertEqual(self.task(pi, code).status, "ACTION")

    def test_each_hard_prerequisite_blocks_both_order_sides_without_partial_writes(self):
        for required_flag, label in zip(self.flags[:2], ("Export License", "Customs Documents")):
            for side in (0, 1):
                with self.subTest(flag=required_flag, side=side):
                    customer, export = self.pair()
                    setattr(export, required_flag, True)
                    db.session.commit()
                    before = self.snapshot()
                    response = self.departure((customer, export)[side], shipping_company="DO NOT SAVE",
                                              bill_of_lading_number="DO NOT SAVE")
                    self.assertEqual(response.status_code, 409)
                    self.assertIn(label, response.get_data(as_text=True))
                    self.assertEqual(self.snapshot(), before)
                    self.assertTrue(db.session.is_active)

    def test_both_missing_reported_on_usable_form_with_export_link(self):
        customer, export = self.ready_pair()
        before = self.snapshot()
        response = self.departure(customer)
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 409)
        for text in (export.pi_no, "Export License", "Customs Documents", "<form", 'name="actual_departure_date"',
                     f'href="/v2/orders/{export.id}"'):
            self.assertIn(text, page)
        self.assertNotIn("DOCUMENT_EXPORT_LICENSE", page)
        self.assertNotIn("DOCUMENT_CUSTOMS", page)
        self.assertEqual(self.snapshot(), before)

    def test_only_done_satisfies_gate_not_waiting_upcoming_or_cancelled(self):
        customer, export = self.ready_pair()
        self.complete(export, ("DOCUMENT_CUSTOMS",))
        license_task = self.task(export, "DOCUMENT_EXPORT_LICENSE")
        for status in ("ACTION", "WAITING", "UPCOMING", "CANCELLED"):
            license_task.status = status; db.session.commit()
            before = self.snapshot()
            self.assertEqual(self.departure(customer).status_code, 409)
            self.assertEqual(self.snapshot(), before)

    def test_false_unknown_hard_requirements_do_not_block(self):
        for required in (False, None):
            customer, export = self.pair()
            export.export_license_required = export.customs_docs_required = required
            db.session.commit()
            self.assertEqual(self.departure(customer).status_code, 302)
            self.assert_departed(customer, export)

    def test_required_done_allows_atomic_departure_and_cancels_advisory_with_history(self):
        customer, export = self.ready_pair()
        self.complete(export)
        settlement = self.task(export, LINKED_EXPORT_SETTLEMENT)
        history = {a.id for a in settlement.activities}
        self.assertTrue(history)
        self.assertEqual(settlement.status, "ACTION")
        self.assertEqual(self.departure(customer).status_code, 302)
        self.assert_departed(customer, export)
        self.assertEqual(settlement.status, "CANCELLED")
        self.assertIsNone(settlement.completed_at)
        self.assertNotEqual(settlement.resolution_code, "MANUAL_DONE")
        db.session.expire(settlement, ["activities"])
        self.assertTrue(history <= {a.id for a in settlement.activities})
        self.assertIn("CANCELLED", [a.event_type for a in settlement.activities])
        self.assertNotIn("COMPLETED", [a.event_type for a in settlement.activities])
        for code in self.codes[:2]:
            self.assertEqual(self.task(export, code).status, "DONE")
        self.assertFalse(OrderTask.query.filter(OrderTask.pi_id == export.id,
            OrderTask.status.in_(("ACTION", "WAITING", "UPCOMING"))).all())

    def test_completed_settlement_stays_done_and_history_is_visible(self):
        customer, export = self.ready_pair()
        self.complete(export, self.codes)
        task = self.task(export, LINKED_EXPORT_SETTLEMENT)
        completed_at = task.completed_at
        self.assertEqual(self.departure(customer).status_code, 302)
        self.assertEqual((task.status, task.completed_at), ("DONE", completed_at))
        response = self.client().get(f"/v2/tasks/{task.id}/history")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Completed", response.get_data(as_text=True))

    def test_settlement_ignores_receipt_amounts_and_dates_without_customer_duplicate(self):
        customer, export = self.ready_pair()
        task = self.task(export, LINKED_EXPORT_SETTLEMENT)
        task_id = task.id
        for amount, received_at in ((0, None), (999, datetime(2026, 9, 1))):
            for row in (customer, export):
                row.advance_received_amount = row.balance_received_amount = Decimal(amount)
                row.advance_received_at = row.balance_received_at = received_at
            reconcile_order_tasks_for_pi(export); reconcile_order_tasks_for_pi(customer)
            db.session.commit()
            self.assertEqual((task.id, task.status), (task_id, "ACTION"))
            self.assertNotIn("amount", task.context_payload)
            self.assertIsNone(self.task(customer, LINKED_EXPORT_SETTLEMENT))
            self.assertIsNone(self.task(export, "SETTLEMENT_DOCUMENT_ADVANCE"))
            self.assertIsNone(self.task(export, "SETTLEMENT_DOCUMENT_BALANCE"))

    def test_removing_requirements_false_or_unknown_retires_active_tasks(self):
        for value in ("false", ""):
            customer, export = self.ready_pair()
            ids = [self.task(export, code).id for code in self.codes]
            response = self.client().post(f"/v2/orders/{export.id}/document-requirements",
                                          data={flag: value for flag in self.flags})
            self.assertEqual(response.status_code, 302)
            for code, ident in zip(self.codes, ids):
                task = self.task(export, code)
                self.assertEqual((task.id, task.status), (ident, "CANCELLED"))

    def test_false_unknown_settlement_flag_does_not_create_task(self):
        for value in (False, None):
            customer, export = self.pair()
            customer.container_loading_date = date(2099, 1, 1)
            export.settlement_documents_required = value
            reconcile_order_tasks_for_pi(export); db.session.commit()
            self.assertIsNone(self.task(export, LINKED_EXPORT_SETTLEMENT))

    def test_customer_delivery_tasks_suppressed_without_suppressing_china_export_tasks(self):
        customer, export = self.ready_pair()
        export.coo_required = export.apta_required = export.coa_required = export.coc_required = True
        export.obd_electronic_required = export.insurance_electronic_required = True
        for stage in ("PRE_SHIPMENT", "SHIPPED", "ARRIVED", "COMPLETED"):
            export.status = stage
            reconcile_order_tasks_for_pi(export); db.session.commit()
            for code in CUSTOMER_DOCUMENT_TASK_CODES:
                self.assertIsNone(self.task(export, code))
            for code in ("DOCUMENT_COO", "DOCUMENT_APTA", "DOCUMENT_COA", "DOCUMENT_COC"):
                self.assertIsNotNone(self.task(export, code))

    def test_existing_customer_delivery_tasks_hidden_and_retired_preserving_done(self):
        customer, export = self.pair()
        tasks = []
        for index, code in enumerate(sorted(CUSTOMER_DOCUMENT_TASK_CODES)):
            task = OrderTask(pi_id=export.id, task_code=code, title=code, source="AUTO",
                status="DONE" if index == 0 else "ACTION", health="NORMAL", completion_mode="MANUAL",
                dedupe_key=f"v2:order:{export.id}:{code.lower()}")
            db.session.add(task); db.session.flush()
            db.session.add(TaskActivity(task_id=task.id, event_type="CREATED", actor_type="SYSTEM"))
            tasks.append(task)
        db.session.commit()
        history = {a.id for a in TaskActivity.query.all()}
        for task in tasks[1:]:
            self.assertEqual(projected(task)[0], "CANCELLED")
            self.assertEqual(task_actions(task), [{"kind": "history", "label": "History"}])
            self.assertEqual(self.client().post(f"/v2/tasks/{task.id}/done").status_code, 409)
        reconcile_order_tasks_for_pi(export); db.session.commit()
        self.assertEqual(tasks[0].status, "DONE")
        self.assertEqual([t.status for t in tasks[1:]], ["CANCELLED"] * (len(tasks) - 1))
        self.assertTrue(history <= {a.id for a in TaskActivity.query.all()})

    def test_dashboard_and_order_get_do_not_reconcile_or_write(self):
        customer, export = self.ready_pair()
        before = self.snapshot()
        with patch("v2.web.reconcile_order_tasks_for_pi", side_effect=AssertionError("GET reconciled")):
            for url in ("/v2/", f"/v2/orders/{export.id}", f"/v2/orders/{customer.id}/enter-shipped"):
                self.assertEqual(self.client().get(url).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_departure_refreshes_new_requirement_committed_before_claim(self):
        customer, export = self.pair()
        export_id = export.id
        self.assertIsNone(export.export_license_required)
        def claim(c, e):
            with db.engine.begin() as connection:
                connection.execute(update(PI).where(PI.id == export_id).values(export_license_required=True))
            self.assertIsNone(e.export_license_required)
            _claim_pair(c, e)
        with patch("v2.linked_shipment._claim_pair", side_effect=claim):
            with self.assertRaisesRegex(LinkedShipmentError, "Export License"):
                record_linked_actual_departure(customer, date(2026, 9, 18))
        self.assertEqual((customer.status, export.status), ("PRE_SHIPMENT", "PRE_SHIPMENT"))
        self.assertIsNone(customer.actual_departure_date)
        self.assertIsNone(export.actual_departure_date)
        self.assertEqual((OrderTask.query.count(), TaskActivity.query.count()), (0, 0))

    def test_departure_reads_new_task_state_not_preloaded_done(self):
        customer, export = self.ready_pair()
        self.complete(export)
        task = self.task(export, "DOCUMENT_CUSTOMS")
        task_id = task.id
        self.assertEqual(task.status, "DONE")
        def claim(c, e):
            with db.engine.begin() as connection:
                connection.execute(update(OrderTask).where(OrderTask.id == task_id).values(status="ACTION"))
            self.assertEqual(task.status, "DONE")
            _claim_pair(c, e)
        with patch("v2.linked_shipment._claim_pair", side_effect=claim):
            with self.assertRaisesRegex(LinkedShipmentError, "Customs Documents"):
                record_linked_actual_departure(customer, date(2026, 9, 18))
        self.assertEqual((customer.status, export.status), ("PRE_SHIPMENT", "PRE_SHIPMENT"))
        self.assertIsNone(customer.actual_departure_date)
        self.assertIsNone(export.actual_departure_date)

    def test_concurrently_completed_task_is_refreshed_and_preserved_during_reconcile(self):
        customer, export = self.ready_pair()
        self.complete(export, ("DOCUMENT_CUSTOMS",))
        task = self.task(export, "DOCUMENT_EXPORT_LICENSE")
        task_id = task.id
        self.assertEqual(task.status, "ACTION")
        def claim(c, e):
            with db.engine.begin() as connection:
                connection.execute(update(OrderTask).where(OrderTask.id == task_id).values(
                    status="DONE", completed_at=datetime(2026, 9, 18), resolution_code="MANUAL_DONE"))
            self.assertEqual(task.status, "ACTION")
            _claim_pair(c, e)
        with patch("v2.linked_shipment._claim_pair", side_effect=claim):
            record_linked_actual_departure(customer, date(2026, 9, 18))
        self.assert_departed(customer, export)
        self.assertEqual((task.status, task.resolution_code), ("DONE", "MANUAL_DONE"))
        self.assertEqual(task.completed_at, datetime(2026, 9, 18))

    def test_independent_departure_does_not_gain_linked_document_gate(self):
        pi = self.pi("XHT-INDEPENDENT-GATE")
        pi.container_loading_date = date(2026, 9, 17)
        pi.export_license_required = pi.customs_docs_required = True
        for code in ("SHIPPING_CONTAINER_LOADING", "SHIPPING_FREIGHT_AGREEMENT"):
            db.session.add(OrderTask(pi_id=pi.id, task_code=code, title=code, source="AUTO",
                status="DONE", health="NORMAL", completion_mode="RULE_DATA",
                dedupe_key=f"v2:order:{pi.id}:{code.lower()}"))
        db.session.add(OrderFreightAgreement(pi_id=pi.id, freight_forwarder_name_snapshot="FF",
                                            amount=Decimal("1"), currency="USD"))
        db.session.commit()
        self.assertEqual(self.departure(pi, shipping_company="Carrier", bill_of_lading_number="BL").status_code, 302)
        self.assertEqual(pi.status, "SHIPPED")

    def test_loading_activation_failure_rolls_back_both_dates_and_document_tasks(self):
        customer, export = self.pair()
        for flag in self.flags:
            setattr(export, flag, True)
        db.session.commit()
        before = self.snapshot()
        def reconcile(row):
            reconcile_order_tasks_for_pi(row)
            db.session.flush()
            if row is customer:
                raise RuntimeError("activation failure")
        with patch("v2.linked_shipment.reconcile_order_tasks_for_pi", side_effect=reconcile):
            with self.assertRaisesRegex(RuntimeError, "activation failure"):
                self.client().post(f"/v2/orders/{customer.id}/facts", data={"container_loading_date": "2099-01-01"})
        self.assertEqual(self.snapshot(), before)

    def test_departure_revalidates_status_after_claim_before_document_check(self):
        customer, export = self.ready_pair()
        before = self.snapshot()
        def claim(c, e):
            _claim_pair(c, e)
            db.session.execute(update(PI).where(PI.id == e.id).values(status="COMPLETED")
                               .execution_options(synchronize_session=False))
        with patch("v2.linked_shipment._claim_pair", side_effect=claim), \
             patch("v2.linked_shipment._validate_export_documents") as validate:
            with self.assertRaises(LinkedShipmentError):
                record_linked_actual_departure(customer, date(2026, 9, 18))
        validate.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_claims_and_refresh_precede_document_validation_and_mutation(self):
        customer, export = self.ready_pair()
        self.complete(export)
        claims = []
        refreshes = []
        refresh = db.session.refresh
        def claim(c, e):
            _claim_pair(c, e)
            claims.extend(sorted((c.id, e.id)))
        def refreshed(row, **kwargs):
            self.assertEqual(claims, sorted((customer.id, export.id)))
            refresh(row, **kwargs)
            refreshes.append(row.id)
        def validate(e):
            self.assertEqual(refreshes, sorted((customer.id, export.id)))
            self.assertEqual((customer.status, export.status), ("PRE_SHIPMENT", "PRE_SHIPMENT"))
            self.assertIsNone(customer.actual_departure_date)
            self.assertIsNone(export.actual_departure_date)
        with patch("v2.linked_shipment._claim_pair", side_effect=claim), \
             patch.object(db.session, "refresh", side_effect=refreshed), \
             patch("v2.linked_shipment._validate_export_documents", side_effect=validate):
            record_linked_actual_departure(export, date(2026, 9, 18))
        self.assert_departed(customer, export)

    def test_failure_after_advisory_retirement_rolls_back_tasks_and_history(self):
        customer, export = self.ready_pair()
        self.complete(export)
        before = self.snapshot()
        def reconcile(row):
            reconcile_order_tasks_for_pi(row)
            db.session.flush()
            if row is customer:
                raise RuntimeError("after export retirement")
        with patch("v2.linked_shipment.reconcile_order_tasks_for_pi", side_effect=reconcile):
            with self.assertRaisesRegex(RuntimeError, "retirement"):
                record_linked_actual_departure(customer, date(2026, 9, 18))
        self.assertEqual(self.snapshot(), before)

    def test_success_has_one_commit_after_document_validation_and_retirement(self):
        customer, export = self.ready_pair()
        self.complete(export)
        commits = []
        def committed(session):
            with db.engine.connect() as connection:
                commits.append(connection.execute(db.select(PI.status).order_by(PI.id)).scalars().all())
        session = db.session()
        event.listen(session, "after_commit", committed)
        try:
            record_linked_actual_departure(customer, date(2026, 9, 18))
        finally:
            event.remove(session, "after_commit", committed)
        self.assertEqual(commits, [["SHIPPED", "COMPLETED"]])
