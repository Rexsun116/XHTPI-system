from datetime import datetime, timedelta
from decimal import Decimal
import unittest

from tests import test_support  # noqa: F401

from sqlalchemy import text

import app as app_module
from app import Customer, Exporter, Factory, PI, User, app, db
from reminders.dashboard import get_dashboard_tasks
from reminders.engine import reconcile_order_tasks
from reminders.enums import CompletionMode, TaskHealth, TaskStatus, WaitingOn
from reminders.freight import confirm_usd_freight_amount, update_freight_bill_amounts
from reminders.task_service import (
    add_follow_up,
    create_auto_task,
    create_manual_task,
    mark_done,
    move_to_waiting,
)
from task_models import OrderTask, TaskActivity, utc_now


REQUIRED_SCHEMA = {
    "fields": [
        {"key": "tracking_number", "label": "Tracking Number", "type": "text", "required": True},
        {"key": "carrier", "label": "物流公司", "type": "text", "required": False},
    ]
}


class DashboardIntegrationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)
        cls.original_rate_get = app_module.requests.get
        app_module.requests.get = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline"))

    @classmethod
    def tearDownClass(cls):
        app_module.requests.get = cls.original_rate_get

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        for table in (
            "task_activity", "order_task", "pi_item", "freight_quote", "pi", "product",
            "freight_forwarder", "factory", "customer", "exporter", "user",
        ):
            db.session.execute(text(f"DELETE FROM {table}"))
        db.session.commit()
        app._admin_created = True

        self.now = utc_now()
        user = User(username="dashboard-user")
        user.set_password("dashboard-password")
        customer = Customer(code="C-DASH", name="Rahul Minerals", address="Address", country="India")
        exporter = Exporter(code="E-DASH", name="AEA", address="Address", country="Hong Kong")
        factory = Factory(code="F-DASH", name="Factory A", address="Address", country="China")
        db.session.add_all([user, customer, exporter, factory])
        db.session.flush()
        self.pi = PI(
            pi_no="WU-DASH-001", pi_date=self.now.date(), customer_id=customer.id,
            exporter_id=exporter.id, status="新建",
        )
        self.commission_pi = PI(
            pi_no="Q-DASH-002", pi_date=self.now.date(), customer_id=customer.id,
            exporter_id=None, status="新建", commission_factory_id=factory.id,
        )
        db.session.add_all([self.pi, self.commission_pi])
        db.session.commit()
        self.user_id = user.id

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        self.context.pop()

    def client(self):
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(self.user_id)
            session["_fresh"] = True
        return client

    def headers(self, client):
        client.get("/")
        with client.session_transaction() as session:
            token = session["_task_csrf_token"]
        return {"X-CSRF-Token": token}

    def html(self, client=None):
        response = (client or self.client()).get("/")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def manual(self, title="Manual task", **values):
        task = create_manual_task(
            pi_id=values.pop("pi_id", self.pi.id), title=title, actor_id=self.user_id,
            now=values.pop("now", self.now), **values,
        )
        db.session.commit()
        return task

    def auto(self, title="Auto task", **values):
        code = values.pop("task_code", "AUTO_DASHBOARD")
        task = create_auto_task(
            pi_id=values.pop("pi_id", self.pi.id), task_code=code, title=title,
            rule_key=values.pop("rule_key", code.lower()), rule_version=1,
            instance_key="default", dedupe_key=f"dashboard:{self.pi.id}:{code}:{values.pop('dedupe_suffix', title)}",
            status=values.pop("status", TaskStatus.ACTION),
            health=values.pop("health", TaskHealth.NORMAL),
            completion_mode=values.pop("completion_mode", CompletionMode.RULE_DATA),
            now=values.pop("now", self.now), **values,
        )
        db.session.commit()
        return task

    def test_01_unauthenticated_dashboard_redirects(self):
        self.assertEqual(app.test_client().get("/").status_code, 302)

    def test_02_empty_task_dashboard(self):
        html = self.html()
        self.assertIn("No Action Required", html)
        self.assertNotIn('id="exceptionSection"', html)

    def test_03_exception_section_is_conditional(self):
        self.auto("实际到港日期未确认", health=TaskHealth.EXCEPTION, context_payload={"health_message": "ETA 已到，尚未记录实际日期"})
        html = self.html()
        self.assertIn('id="exceptionSection"', html)
        self.assertIn("ETA 已到，尚未记录实际日期", html)

    def test_04_action_task_renders(self):
        self.manual("办理 COO")
        self.assertIn("办理 COO", self.html())

    def test_05_waiting_task_renders(self):
        task = self.manual("等待客户确认")
        move_to_waiting(task, actor_id=self.user_id, waiting_on=WaitingOn.CUSTOMER, note="已发送客户")
        db.session.commit()
        html = self.html()
        self.assertIn("等待客户确认", html)
        self.assertIn("已发送客户", html)

    def test_06_upcoming_task_renders(self):
        self.manual("未来装柜", activation_at=self.now + timedelta(days=2))
        self.assertIn("未来装柜", self.html())

    def test_07_done_history_renders(self):
        task = self.manual("Booking confirmed")
        mark_done(task, actor_id=self.user_id, now=self.now)
        db.session.commit()
        html = self.html()
        self.assertIn("Done History", html)
        self.assertIn("Booking confirmed", html)

    def test_08_legacy_done_has_unknown_date(self):
        self.auto("历史 COO", status=TaskStatus.DONE, completion_mode=CompletionMode.MANUAL, resolution_code="LEGACY_DONE")
        html = self.html()
        self.assertIn("历史已完成", html)
        self.assertIn("完成时间未知", html)

    def test_09_payment_amount_formatting(self):
        self.auto("催客户付款", context_payload={"currency": "USD", "outstanding_amount": "49360.00"})
        self.assertIn("USD 49,360.00", self.html())

    def test_10_dual_currency_freight_formatting(self):
        self.auto("确认货代发票", context_payload={"usd_freight": "18650", "cny_charges": "12800"})
        html = self.html()
        self.assertIn("USD 18,650.00", html)
        self.assertIn("CNY 12,800.00", html)

    def test_11_dual_currency_is_not_summed(self):
        self.auto("确认两部分账单", context_payload={"usd_freight": "18650", "cny_charges": "12800"})
        self.assertNotIn("31,450.00", self.html())

    def test_12_missing_currency_is_safe(self):
        self.auto("待收款", context_payload={"outstanding_amount": "49360"})
        self.assertIn("49,360.00 · 币种未确认", self.html())

    def test_13_manual_done_post(self):
        task = self.manual("Complete me")
        client = self.client(); headers = self.headers(client)
        self.assertEqual(client.post(f"/tasks/{task.id}/done", json={"note": "Done"}, headers=headers).status_code, 200)
        self.assertEqual(db.session.get(OrderTask, task.id).status, TaskStatus.DONE)

    def test_14_rule_data_done_is_absent_and_rejected(self):
        task = self.auto("更新事实", context_payload={"action_target": "UPDATE_SHIPPING_INFO"})
        html = self.html()
        card = html[html.index("更新事实"):html.index("更新事实") + 1400]
        self.assertNotIn('data-action="done"', card)
        client = self.client(); headers = self.headers(client)
        self.assertEqual(client.post(f"/tasks/{task.id}/done", json={}, headers=headers).status_code, 400)

    def test_15_required_input_server_validation(self):
        task = self.manual("邮寄文件原件", completion_mode=CompletionMode.MANUAL_REQUIRED_INPUT, completion_schema=REQUIRED_SCHEMA)
        client = self.client(); headers = self.headers(client)
        self.assertEqual(client.post(f"/tasks/{task.id}/done", json={"completion_payload": {}}, headers=headers).status_code, 400)

    def test_16_tracking_payload_displays(self):
        task = self.manual("邮寄文件原件", completion_mode=CompletionMode.MANUAL_REQUIRED_INPUT, completion_schema=REQUIRED_SCHEMA)
        mark_done(task, actor_id=self.user_id, payload={"tracking_number": "DHL123456", "carrier": "DHL"}, now=self.now)
        db.session.commit()
        html = self.html()
        self.assertIn("DHL123456", html)
        self.assertIn("物流公司：DHL", html)

    def test_17_follow_up_post_from_action(self):
        task = self.auto("催客户付款", task_code="PAYMENT_BALANCE_FOLLOWUP")
        client = self.client(); headers = self.headers(client)
        response = client.post(f"/tasks/{task.id}/follow-up", json={"note": "已催 Rahul", "waiting_on": "CUSTOMER", "continue_waiting": True}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(task.activities[-1].event_type, "FOLLOW_UP")

    def test_18_follow_up_becomes_waiting(self):
        task = self.auto("催客户付款", task_code="PAYMENT_BALANCE_FOLLOWUP")
        add_follow_up(task, actor_id=self.user_id, note="已发邮件", waiting_on="CUSTOMER", continue_waiting=True)
        db.session.commit()
        self.assertEqual(task.status, TaskStatus.WAITING)

    def test_19_next_follow_up_displays(self):
        task = self.manual("等待回复")
        follow_at = self.now + timedelta(days=2)
        move_to_waiting(task, actor_id=self.user_id, waiting_on="CUSTOMER", next_follow_up_at=follow_at)
        db.session.commit()
        self.assertIn(follow_at.strftime("%Y-%m-%d %H:%M"), self.html())

    def test_20_overdue_display(self):
        task = self.manual("逾期跟进")
        move_to_waiting(task, actor_id=self.user_id, waiting_on="CUSTOMER", next_follow_up_at=self.now - timedelta(days=2))
        db.session.commit()
        self.assertIn("逾期 2 天", self.html())

    def test_21_due_waiting_projects_action_without_get_write(self):
        task = self.manual("到期跟进")
        move_to_waiting(task, actor_id=self.user_id, waiting_on="CUSTOMER", next_follow_up_at=self.now - timedelta(minutes=1))
        db.session.commit(); activity_count = len(task.activities)
        self.html(); db.session.expire_all(); stored = db.session.get(OrderTask, task.id)
        self.assertEqual(stored.status, TaskStatus.WAITING)
        self.assertEqual(len(stored.activities), activity_count)

    def test_22_reopen_requires_reason(self):
        task = self.manual("Reopen me"); mark_done(task, actor_id=self.user_id); db.session.commit()
        client = self.client(); headers = self.headers(client)
        self.assertEqual(client.post(f"/tasks/{task.id}/reopen", json={}, headers=headers).status_code, 400)
        self.assertEqual(client.post(f"/tasks/{task.id}/reopen", json={"reason": "客户要求修改"}, headers=headers).status_code, 200)

    def test_23_rule_data_reopen_unavailable(self):
        task = self.auto("Auto resolved", status=TaskStatus.DONE)
        html = self.html(); card = html[html.index("Auto resolved"):html.index("Auto resolved") + 1400]
        self.assertNotIn('data-action="reopen"', card)
        client = self.client(); headers = self.headers(client)
        self.assertEqual(client.post(f"/tasks/{task.id}/reopen", json={"reason": "No"}, headers=headers).status_code, 400)

    def test_24_activity_history_newest_first(self):
        task = self.manual("History task")
        move_to_waiting(task, actor_id=self.user_id, waiting_on="CUSTOMER", note="FIRST-UNIQUE", now=self.now)
        add_follow_up(task, actor_id=self.user_id, note="SECOND-UNIQUE", waiting_on="CUSTOMER", now=self.now + timedelta(hours=1))
        db.session.commit(); html = self.html()
        self.assertLess(html.index("SECOND-UNIQUE"), html.index("FIRST-UNIQUE"))

    def test_25_freight_confirmation_snapshot_displays(self):
        task = self.auto("美元账单已确认", status=TaskStatus.ACTION, completion_mode=CompletionMode.MANUAL, task_code="FREIGHT_USD_AMOUNT_CONFIRM")
        task.pi.freight_usd_bill_required = True; task.pi.freight_usd_amount = Decimal("18650")
        confirm_usd_freight_amount(task, actor_id=self.user_id, now=self.now); db.session.commit()
        self.assertIn("确认金额：USD 18,650.00", self.html())

    def test_26_amount_changed_exception_renders_old_and_current(self):
        self.pi.actual_departure_date = self.now.date() - timedelta(days=10)
        self.pi.freight_usd_bill_required = True; self.pi.freight_usd_amount = Decimal("18650")
        db.session.commit(); reconcile_order_tasks(self.pi, now=self.now, apply=True); db.session.commit()
        task = OrderTask.query.filter_by(pi_id=self.pi.id, task_code="FREIGHT_USD_AMOUNT_CONFIRM").one()
        confirm_usd_freight_amount(task, actor_id=self.user_id, now=self.now); db.session.commit()
        update_freight_bill_amounts(self.pi, usd_amount="19200", update_usd_amount=True)
        reconcile_order_tasks(self.pi, now=self.now, apply=True); db.session.commit()
        html = self.html()
        self.assertIn("USD 18,650.00", html)
        self.assertIn("USD 19,200.00", html)
        self.assertIn("金额在确认后发生变化", html)

    def test_27_action_target_maps_to_status_url(self):
        self.auto("更新到港", context_payload={"action_target": "UPDATE_ARRIVAL_INFO"})
        self.assertIn(f'/pi/{self.pi.id}/update-status', self.html())

    def test_28_missing_freight_forwarder_is_safe(self):
        self.auto("确认货代信息", context_payload={"action_target": "UPDATE_FREIGHT_INFO"})
        self.assertIn("确认货代信息", self.html())

    def test_29_commission_exporter_null_is_safe(self):
        self.manual("Commission task", pi_id=self.commission_pi.id)
        html = self.html()
        self.assertIn("Commission task", html)
        self.assertIn("Q-DASH-002", html)

    def test_30_raw_context_json_is_not_exposed(self):
        self.auto("Safe presenter", context_payload={"secret_internal_key": "SECRET_RAW_CONTEXT"})
        self.assertNotIn("SECRET_RAW_CONTEXT", self.html())

    def test_31_task_card_is_not_duplicated(self):
        task = self.manual("Only once")
        marker = f'<article class="occ-task-card task-action" data-task-id="{task.id}">'
        self.assertEqual(self.html().count(marker), 1)

    def test_32_selector_ordering_drives_next_action(self):
        low = self.manual("Lower priority action", priority=50)
        high = self.manual("Highest priority exception", priority=100)
        high.health = TaskHealth.EXCEPTION; db.session.commit()
        dashboard = get_dashboard_tasks(now=self.now)
        self.assertEqual(dashboard["next_actions"][self.pi.id]["id"], high.id)
        self.assertNotEqual(low.id, high.id)

    def test_33_simulated_reconcile_task_set_renders(self):
        self.pi.status = "已发运"
        self.pi.container_date = self.now.date() - timedelta(days=1)
        self.pi.eta = self.now.date() - timedelta(days=1)
        self.pi.coo_required = "已完成"; self.pi.coa_status = "已完成"
        self.pi.document_shipping_status = "已邮寄"; self.pi.tracking_number = "LEGACY-TRACK"
        db.session.commit(); reconcile_order_tasks(self.pi, now=self.now, apply=True); db.session.commit()
        html = self.html()
        self.assertIn("EMAIL发送付款文件给客户并请款", html)
        self.assertIn("确认司机信息", html)
        self.assertIn("确认实际到港日期", html)
        self.assertNotIn("录入货代美元", html)

    def test_34_csrf_rejects_authenticated_mutation_without_token(self):
        client = self.client()
        response = client.post("/tasks", json={"pi_id": self.pi.id, "title": "Blocked"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
