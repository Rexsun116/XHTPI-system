"""Read-only Task History presentation coverage on disposable V2 databases."""

from datetime import date, datetime
from pathlib import Path
import tempfile
from unittest import TestCase

from werkzeug.security import generate_password_hash

from v2.app import create_app
from v2.models import Customer, Exporter, OrderTask, PI, PIItem, TaskActivity, User, db


class TaskHistoryPresentationTest(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(f"sqlite:///{Path(self.tmp.name) / 'v2.db'}", testing=True)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.user = User(username="history", password_hash=generate_password_hash("pw"))
        self.customer = Customer(code="C", name="Customer")
        self.exporter = Exporter(code="E", name="Exporter")
        db.session.add_all([self.user, self.customer, self.exporter]); db.session.commit(); self.user_id = self.user.id

    def tearDown(self):
        db.session.remove(); self.ctx.pop(); self.tmp.cleanup()

    def task(self, *, status="WAITING", pi_status="ARRIVED"):
        pi = PI(pi_no="HISTORY-001", pi_date=date(2026, 9, 1), order_type="SALES", status=pi_status,
                customer_id=self.customer.id, exporter_id=self.exporter.id, customer_name_snapshot="Customer",
                exporter_name_snapshot="Exporter", currency="USD")
        pi.items.append(PIItem(unit_price=10, quantity=1, quantity_unit="KGS", line_total=10))
        db.session.add(pi); db.session.flush()
        task = OrderTask(pi_id=pi.id, task_code="HISTORY", title="Confirm a deliberately long task title",
                         source="MANUAL", status=status, health="NORMAL", completion_mode="MANUAL",
                         dedupe_key=f"v2:order:{pi.id}:history")
        db.session.add(task); db.session.commit(); return pi, task

    def client(self, authenticated=True):
        client = self.app.test_client()
        if authenticated:
            with client.session_transaction() as session:
                session["_user_id"] = str(self.user_id); session["_fresh"] = True
        return client

    def test_populated_history_uses_newest_tie_order_and_existing_activity_details(self):
        pi, task = self.task()
        stamp = datetime(2026, 9, 20, 9, 0)
        older = TaskActivity(task_id=task.id, event_type="CREATED", actor_type="SYSTEM", created_at=datetime(2026, 9, 19, 9))
        first = TaskActivity(task_id=task.id, event_type="FOLLOW_UP", actor_type="USER", actor_id=self.user.id,
                             note="First long operational note", created_at=stamp)
        newest = TaskActivity(task_id=task.id, event_type="ADVANCE_RECEIPT_RECORDED", actor_type="USER", actor_id=self.user.id,
                              note="A deliberately long note that must wrap instead of widening the page", created_at=stamp,
                              payload={"currency": "USD", "confirmed_amount": "10.00", "previous_amount": "0.00",
                                       "received_at": "2026-09-20", "waiting_on": "CUSTOMER", "next_follow_up_at": "2026-09-25"})
        db.session.add_all([older, first, newest]); db.session.commit()
        page = self.client().get(f"/v2/tasks/{task.id}/history").get_data(as_text=True)
        for text in ("v2-modern", "Task History", task.title, pi.pi_no, "WAITING", "Activity History",
                     "Advance receipt confirmation", "User #", "System", newest.note,
                     "Confirmed cumulative advance: USD 10.00", "Waiting on: CUSTOMER", "Next follow-up: 2026-09-25"):
            self.assertIn(text, page)
        self.assertLess(page.index("Advance receipt confirmation"), page.index("First long operational note"))
        self.assertLess(page.index("First long operational note"), page.index("Created"))
        self.assertIn(f'href="/v2/orders/{pi.id}?open_task={task.id}#task-{task.id}"', page)
        self.assertEqual(page.count("<form"), 1)
        self.assertIn('class="v2-shell-logout-form"', page)

    def test_empty_completed_task_history_remains_accessible_without_fabricated_events(self):
        pi, task = self.task(status="DONE", pi_status="COMPLETED")
        response = self.client().get(f"/v2/tasks/{task.id}/history")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("No activity recorded for this task.", page)
        self.assertIn("The current task state is not treated as a historical event.", page)
        self.assertIn("DONE", page)
        self.assertEqual(page.count("<form"), 1)
        self.assertIn('class="v2-shell-logout-form"', page)

    def test_history_requires_login(self):
        _, task = self.task()
        self.assertEqual(self.client(False).get(f"/v2/tasks/{task.id}/history").status_code, 302)

    def test_missing_task_history_is_not_found(self):
        _, task = self.task()
        client = self.client()
        self.assertEqual(client.get(f"/v2/tasks/{task.id}/history").status_code, 200)
        self.assertEqual(client.get("/v2/tasks/999999/history").status_code, 404)
