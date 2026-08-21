from datetime import datetime, timedelta
import unittest


from tests import test_support  # noqa: F401  # Configure isolated DB before importing app.

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import Customer, Exporter, PI, User, app, db
from reminders.enums import (
    ActivityEvent,
    ActorType,
    CompletionMode,
    TaskHealth,
    TaskSource,
    TaskStatus,
    WaitingOn,
)
from reminders.selector import select_next_action
from reminders.task_service import (
    CompletionValidationError,
    InvalidTransitionError,
    add_follow_up,
    add_note,
    cancel_task,
    create_manual_task,
    mark_done,
    move_to_waiting,
    reconcile_task_timing,
    reopen_task,
    resolve_from_data,
)
from task_models import OrderTask, TaskActivity


BASE_NOW = datetime(2026, 8, 21, 10, 0, 0)
REQUIRED_SCHEMA = {
    "fields": [
        {
            "key": "tracking_number",
            "label": "Tracking Number",
            "type": "text",
            "required": True,
        },
        {"key": "carrier", "label": "物流公司", "type": "text", "required": False},
    ]
}


class TaskFoundationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        for table in [
            "task_activity",
            "order_task",
            "pi_item",
            "freight_quote",
            "pi",
            "product",
            "freight_forwarder",
            "factory",
            "customer",
            "exporter",
            "user",
        ]:
            db.session.execute(text(f"DELETE FROM {table}"))
        db.session.commit()

        user = User(username="task-tester", role="admin")
        user.set_password("test-password")
        customer = Customer(
            code="C-TASK",
            name="Task Test Customer",
            address="Test Address",
            country="Test Country",
        )
        exporter = Exporter(
            code="E-TASK",
            name="Task Test Exporter",
            address="Test Address",
            country="Test Country",
        )
        db.session.add_all([user, customer, exporter])
        db.session.flush()
        pi = PI(
            pi_no="TASK-TEST-001",
            pi_date=BASE_NOW.date(),
            customer_id=customer.id,
            exporter_id=exporter.id,
        )
        db.session.add(pi)
        db.session.commit()
        self.user_id = user.id
        self.pi_id = pi.id

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.context.pop()

    def create_task(self, **overrides):
        values = {
            "pi_id": self.pi_id,
            "title": "Manual task",
            "actor_id": self.user_id,
            "now": BASE_NOW,
        }
        values.update(overrides)
        task = create_manual_task(**values)
        db.session.commit()
        return task

    def create_rule_data_task(self, **overrides):
        values = {
            "pi_id": self.pi_id,
            "scope": "ORDER",
            "task_code": "DATA_TEST",
            "title": "Update order data",
            "source": TaskSource.AUTO,
            "status": TaskStatus.ACTION,
            "health": TaskHealth.NORMAL,
            "completion_mode": CompletionMode.RULE_DATA,
            "priority": 100,
            "created_at": BASE_NOW,
            "updated_at": BASE_NOW,
        }
        values.update(overrides)
        task = OrderTask(**values)
        db.session.add(task)
        db.session.commit()
        return task

    def test_01_manual_task_creation(self):
        task = self.create_task(description="Call customer", priority=20)
        self.assertEqual(task.source, TaskSource.MANUAL)
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.task_code, "MANUAL_GENERAL")
        self.assertEqual(task.priority, 20)

    def test_02_created_activity(self):
        task = self.create_task()
        activity = task.activities[0]
        self.assertEqual(activity.event_type, ActivityEvent.CREATED)
        self.assertEqual(activity.actor_type, ActorType.USER)
        self.assertEqual(activity.actor_id, self.user_id)

    def test_03_action_to_waiting(self):
        task = self.create_task()
        move_to_waiting(task, actor_id=self.user_id, waiting_on=WaitingOn.CUSTOMER, now=BASE_NOW)
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.WAITING)
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.WAITING_STARTED)

    def test_04_waiting_on_is_saved(self):
        task = self.create_task()
        follow_at = BASE_NOW + timedelta(days=2)
        move_to_waiting(
            task,
            actor_id=self.user_id,
            waiting_on=WaitingOn.FREIGHT_FORWARDER,
            next_follow_up_at=follow_at,
            now=BASE_NOW,
        )
        db.session.commit()
        self.assertEqual(task.waiting_on, WaitingOn.FREIGHT_FORWARDER)
        self.assertEqual(task.next_follow_up_at, follow_at)

    def test_05_follow_up_activity_is_appended(self):
        task = self.create_task()
        move_to_waiting(task, actor_id=self.user_id, waiting_on=WaitingOn.CUSTOMER, now=BASE_NOW)
        next_at = BASE_NOW + timedelta(days=3)
        add_follow_up(
            task,
            actor_id=self.user_id,
            note="Customer asked for more time",
            next_follow_up_at=next_at,
            now=BASE_NOW + timedelta(hours=1),
        )
        db.session.commit()
        activity = task.activities[-1]
        self.assertEqual(activity.event_type, ActivityEvent.FOLLOW_UP)
        self.assertEqual(activity.note, "Customer asked for more time")
        self.assertEqual(task.status, TaskStatus.WAITING)

    def test_06_waiting_becomes_action_when_follow_up_is_due_idempotently(self):
        task = self.create_task()
        due = BASE_NOW + timedelta(days=1)
        move_to_waiting(
            task,
            actor_id=self.user_id,
            waiting_on=WaitingOn.CUSTOMER,
            next_follow_up_at=due,
            now=BASE_NOW,
        )
        first = reconcile_task_timing(tasks=[task], now=due + timedelta(minutes=1))
        activity_count = len(task.activities)
        second = reconcile_task_timing(tasks=[task], now=due + timedelta(minutes=2))
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.health, TaskHealth.OVERDUE)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(task.activities), activity_count)

    def test_07_upcoming_becomes_action_on_activation_idempotently(self):
        activation = BASE_NOW + timedelta(days=1)
        task = self.create_task(activation_at=activation)
        first = reconcile_task_timing(tasks=[task], now=activation + timedelta(seconds=1))
        activity_count = len(task.activities)
        second = reconcile_task_timing(tasks=[task], now=activation + timedelta(seconds=2))
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.health, TaskHealth.OVERDUE)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(task.activities), activity_count)

    def test_08_due_at_sets_overdue_health(self):
        task = self.create_task(due_at=BASE_NOW - timedelta(minutes=1))
        self.assertEqual(task.health, TaskHealth.OVERDUE)

    def test_09_manual_done(self):
        task = self.create_task()
        completed = BASE_NOW + timedelta(hours=1)
        mark_done(task, actor_id=self.user_id, note="Finished", now=completed)
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.completed_at, completed)
        self.assertEqual(task.completed_by_id, self.user_id)
        self.assertEqual(task.resolution_code, "MANUAL_DONE")

    def test_10_completed_activity(self):
        task = self.create_task()
        mark_done(task, actor_id=self.user_id, now=BASE_NOW + timedelta(hours=1))
        db.session.commit()
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.COMPLETED)
        self.assertEqual(task.activities[-1].to_status, TaskStatus.DONE)

    def test_11_required_input_rejects_missing_field(self):
        task = self.create_task(
            completion_mode=CompletionMode.MANUAL_REQUIRED_INPUT,
            completion_schema=REQUIRED_SCHEMA,
        )
        with self.assertRaises(CompletionValidationError):
            mark_done(task, actor_id=self.user_id, payload={"carrier": "DHL"}, now=BASE_NOW)

    def test_12_required_input_allows_valid_payload(self):
        task = self.create_task(
            completion_mode=CompletionMode.MANUAL_REQUIRED_INPUT,
            completion_schema=REQUIRED_SCHEMA,
        )
        mark_done(
            task,
            actor_id=self.user_id,
            payload={"tracking_number": "TRACK-001", "carrier": "DHL"},
            now=BASE_NOW,
        )
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_13_completion_payload_is_kept_on_activity(self):
        task = self.create_task(
            completion_mode=CompletionMode.MANUAL_REQUIRED_INPUT,
            completion_schema=REQUIRED_SCHEMA,
        )
        payload = {"tracking_number": "TRACK-002", "carrier": "FedEx"}
        mark_done(task, actor_id=self.user_id, payload=payload, now=BASE_NOW)
        db.session.commit()
        self.assertEqual(task.activities[-1].payload, payload)

    def test_14_rule_data_rejects_manual_done(self):
        task = self.create_rule_data_task()
        with self.assertRaisesRegex(InvalidTransitionError, "resolved by order data"):
            mark_done(task, actor_id=self.user_id, now=BASE_NOW)

    def test_15_reopen_done_task(self):
        task = self.create_task()
        mark_done(task, actor_id=self.user_id, now=BASE_NOW)
        reopen_task(task, actor_id=self.user_id, reason="Customer requested a change", now=BASE_NOW)
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertIsNone(task.completed_at)
        self.assertIsNone(task.completed_by_id)
        self.assertIsNone(task.resolution_code)

    def test_16_reopen_preserves_completed_activity(self):
        task = self.create_task()
        mark_done(task, actor_id=self.user_id, now=BASE_NOW)
        reopen_task(task, actor_id=self.user_id, reason="Needs revision", now=BASE_NOW)
        db.session.commit()
        events = [activity.event_type for activity in task.activities]
        self.assertIn(ActivityEvent.COMPLETED, events)
        self.assertEqual(events[-1], ActivityEvent.REOPENED)

    def test_17_cancel_manual_task(self):
        task = self.create_task()
        cancel_task(task, actor_id=self.user_id, reason="No longer needed", now=BASE_NOW)
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.CANCELLED)

    def test_18_next_action_priority(self):
        action = self.create_task(title="Normal action", priority=1)
        exception = self.create_task(title="Exception", priority=100)
        exception.health = TaskHealth.EXCEPTION
        waiting = self.create_task(title="Waiting", priority=1)
        move_to_waiting(waiting, actor_id=self.user_id, waiting_on=WaitingOn.BANK, now=BASE_NOW)
        db.session.commit()
        result = select_next_action([action, waiting, exception], now=BASE_NOW)
        self.assertEqual(result.task.id, exception.id)
        self.assertEqual(result.display_priority, 0)
        self.assertEqual(result.reason, "EXCEPTION")

    def test_19_done_and_cancelled_are_not_next_action(self):
        done = self.create_task(title="Done")
        cancelled = self.create_task(title="Cancelled")
        mark_done(done, actor_id=self.user_id, now=BASE_NOW)
        cancel_task(cancelled, actor_id=self.user_id, reason="No longer needed", now=BASE_NOW)
        db.session.commit()
        self.assertIsNone(select_next_action([done, cancelled], now=BASE_NOW))

    def test_20_duplicate_manual_titles_are_allowed(self):
        first = self.create_task(title="Same title")
        second = self.create_task(title="Same title")
        self.assertNotEqual(first.id, second.id)
        self.assertIsNone(first.dedupe_key)
        self.assertIsNone(second.dedupe_key)

    def test_21_auto_dedupe_key_is_unique(self):
        self.create_rule_data_task(dedupe_key="AUTO:TEST:1")
        duplicate = OrderTask(
            pi_id=self.pi_id,
            scope="ORDER",
            task_code="DATA_TEST",
            title="Duplicate auto task",
            source=TaskSource.AUTO,
            status=TaskStatus.ACTION,
            health=TaskHealth.NORMAL,
            completion_mode=CompletionMode.RULE_DATA,
            priority=100,
            dedupe_key="AUTO:TEST:1",
            created_at=BASE_NOW,
            updated_at=BASE_NOW,
        )
        db.session.add(duplicate)
        with self.assertRaises(IntegrityError):
            db.session.commit()

    def test_22_rule_data_can_only_use_system_resolution(self):
        task = self.create_rule_data_task()
        resolve_from_data(task, reason_code="ORDER_DATA_UPDATED", now=BASE_NOW)
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.activities[-1].event_type, ActivityEvent.AUTO_RESOLVED)
        self.assertEqual(task.activities[-1].actor_type, ActorType.SYSTEM)

    def test_23_mutation_route_requires_login_and_uses_current_user(self):
        client = app.test_client()
        response = client.post("/tasks", json={"pi_id": self.pi_id, "title": "Route task"})
        self.assertEqual(response.status_code, 302)
        response = client.post(
            "/login",
            data={"username": "task-tester", "password": "test-password"},
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            session["_task_csrf_token"] = "test-task-csrf"
        headers = {"X-CSRF-Token": "test-task-csrf"}
        response = client.post(
            "/tasks",
            json={"pi_id": self.pi_id, "title": "Route task", "actor_id": 999999},
            headers=headers,
        )
        self.assertEqual(response.status_code, 201)
        task = db.session.get(OrderTask, response.get_json()["task"]["id"])
        self.assertEqual(task.created_by_id, self.user_id)

    def test_24_follow_up_can_return_waiting_task_to_action(self):
        task = self.create_task()
        move_to_waiting(task, actor_id=self.user_id, waiting_on=WaitingOn.CUSTOMER, now=BASE_NOW)
        add_follow_up(
            task,
            actor_id=self.user_id,
            note="Need to prepare a revised document",
            continue_waiting=False,
            now=BASE_NOW + timedelta(hours=1),
        )
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.ACTION)
        self.assertEqual(task.activities[-1].from_status, TaskStatus.WAITING)
        self.assertEqual(task.activities[-1].to_status, TaskStatus.ACTION)

    def test_25_activity_is_append_only(self):
        task = self.create_task()
        add_note(task, actor_id=self.user_id, note="Original note", now=BASE_NOW)
        db.session.commit()
        activity = task.activities[-1]
        activity.note = "Changed note"
        with self.assertRaisesRegex(ValueError, "append-only"):
            db.session.commit()

    def test_26_next_action_uses_priority_then_relevant_date(self):
        later = self.create_task(
            title="Later",
            priority=20,
            due_at=BASE_NOW + timedelta(days=2),
        )
        higher_priority = self.create_task(
            title="Higher priority",
            priority=10,
            due_at=BASE_NOW + timedelta(days=3),
        )
        same_priority_earlier = self.create_task(
            title="Earlier",
            priority=20,
            due_at=BASE_NOW + timedelta(days=1),
        )
        result = select_next_action([later, same_priority_earlier, higher_priority], now=BASE_NOW)
        self.assertEqual(result.task.id, higher_priority.id)

        result = select_next_action([later, same_priority_earlier], now=BASE_NOW)
        self.assertEqual(result.task.id, same_priority_earlier.id)

    def test_27_mutation_routes_delegate_full_manual_lifecycle(self):
        client = app.test_client()
        login = client.post(
            "/login",
            data={"username": "task-tester", "password": "test-password"},
        )
        self.assertEqual(login.status_code, 302)
        with client.session_transaction() as session:
            session["_task_csrf_token"] = "test-task-csrf"
        headers = {"X-CSRF-Token": "test-task-csrf"}

        created = client.post("/tasks", json={"pi_id": self.pi_id, "title": "Route lifecycle"}, headers=headers)
        self.assertEqual(created.status_code, 201)
        task_id = created.get_json()["task"]["id"]
        waiting = client.post(
            f"/tasks/{task_id}/waiting",
            json={"waiting_on": WaitingOn.CUSTOMER, "note": "Sent to customer"},
            headers=headers,
        )
        self.assertEqual(waiting.status_code, 200)
        follow_up = client.post(
            f"/tasks/{task_id}/follow-up",
            json={"note": "Customer replied", "continue_waiting": False},
            headers=headers,
        )
        self.assertEqual(follow_up.status_code, 200)
        self.assertEqual(follow_up.get_json()["task"]["status"], TaskStatus.ACTION)
        self.assertEqual(client.post(f"/tasks/{task_id}/done", json={"note": "Done"}, headers=headers).status_code, 200)
        self.assertEqual(
            client.post(f"/tasks/{task_id}/reopen", json={"reason": "Needs revision"}, headers=headers).status_code,
            200,
        )
        cancelled = client.post(
            f"/tasks/{task_id}/cancel",
            json={"reason": "No longer required"},
            headers=headers,
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["task"]["status"], TaskStatus.CANCELLED)

        required = client.post(
            "/tasks",
            json={
                "pi_id": self.pi_id,
                "title": "Mail originals",
                "completion_mode": CompletionMode.MANUAL_REQUIRED_INPUT,
                "completion_schema": REQUIRED_SCHEMA,
            },
            headers=headers,
        )
        required_id = required.get_json()["task"]["id"]
        missing = client.post(f"/tasks/{required_id}/done", json={"completion_payload": {}}, headers=headers)
        self.assertEqual(missing.status_code, 400)
        completed = client.post(
            f"/tasks/{required_id}/done",
            json={"completion_payload": {"tracking_number": "ROUTE-TRACK-001"}},
            headers=headers,
        )
        self.assertEqual(completed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
