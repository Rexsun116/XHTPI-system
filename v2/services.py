"""V2 order mutations and targeted reconcile; no V1 adapters."""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from order_lifecycle import LifecyclePolicyError, OrderStage
from v2_domain import compare_freight_quote_to_bill

from .models import (
    BankAccount,
    FreightSettlement,
    OrderCorrectionSession,
    OrderFreightAgreement,
    OrderTask,
    PI,
    PIItem,
    ProductBatch,
    TaskActivity,
    db,
    utcnow,
)
from .business_time import arrival_schedule_projection, business_today
from .linked_trade import financial_owner_for, is_export_order
from .rules import DOCUMENT_RULES


CORRECTION_TO_POLICY_MODULE = {
    "COMMERCIAL": {"COMMERCIAL_CORE", "PI_ITEMS", "PAYMENT_PLAN"},
    "PAYMENT": {"PAYMENT_PLAN", "PAYMENT_RECEIPTS"},
    "DOCUMENTS": {"DOCUMENT_REQUIREMENTS", "POST_SHIPMENT"},
    "SHIPPING": {"SHIPPING_PREPARATION", "DRIVER_INFO", "ACTUAL_DEPARTURE"},
    "FREIGHT": {"FREIGHT_REQUIREMENTS", "FREIGHT_SETTLEMENT"},
    "ARRIVAL": {"ACTUAL_ARRIVAL"},
}


def apply_bank_snapshot(pi, bank_account):
    if bank_account is None:
        return
    pi.bank_account_id = bank_account.id
    pi.bank_beneficiary_snapshot = bank_account.beneficiary_name
    pi.bank_name_snapshot = bank_account.bank_name
    pi.bank_address_snapshot = bank_account.bank_address
    pi.bank_account_number_snapshot = bank_account.account_number
    pi.bank_swift_snapshot = bank_account.swift_code
    pi.bank_currency_snapshot = bank_account.currency
    pi.bank_remittance_snapshot = bank_account.remittance_information


def apply_product_snapshot(item, product):
    """Copy all document-facing product facts to an immutable PIItem snapshot."""
    item.product_category_snapshot = product.category
    item.product_brand_snapshot = product.brand
    item.product_model_snapshot = product.model
    item.product_packaging_snapshot = product.packaging
    item.product_hs_code_snapshot = product.hs_code


def create_freight_agreement(pi, quote, *, agreed_at=None, note=None):
    return OrderFreightAgreement(
        pi_id=pi.id,
        source_freight_quote_id=quote.id,
        freight_forwarder_id=quote.freight_forwarder_id,
        freight_forwarder_name_snapshot=quote.freight_forwarder.name,
        amount=quote.amount,
        currency=quote.currency.upper(),
        quote_date=quote.quote_date,
        agreed_at=agreed_at or utcnow(),
        note=note,
    )


def freight_agreement_difference(agreement, settlement):
    if agreement.currency.upper() == "USD":
        actual, currency = settlement.usd_bill_amount, "USD"
    elif agreement.currency.upper() == "CNY":
        actual, currency = settlement.cny_bill_amount, "CNY"
    else:
        return {"comparable": False, "reason": "UNSUPPORTED_AGREEMENT_CURRENCY", "difference": None}
    return compare_freight_quote_to_bill(
        quote_amount=agreement.amount,
        quote_currency=agreement.currency,
        bill_amount=actual,
        bill_currency=currency,
    )


def _activity(task, event, *, old=None, status=None, context=None, reason=None):
    db.session.add(TaskActivity(
        task_id=task.id, event_type=event, from_status=old, to_status=status or task.status,
        actor_type="SYSTEM", note=reason, payload=context or None,
    ))


def _find_task(pi, code):
    key = f"v2:order:{pi.id}:{code.lower()}"
    return db.session.scalar(select(OrderTask).where(OrderTask.dedupe_key == key))


def _upsert_task(pi, code, title, *, status, health="NORMAL", completion_mode="RULE_DATA", context=None,
                 activation_at=None, due_at=None, priority=100, force_reactivate=False):
    key = f"v2:order:{pi.id}:{code.lower()}"
    task = db.session.scalar(select(OrderTask).where(OrderTask.dedupe_key == key))
    if task is None:
        task = OrderTask(
            pi_id=pi.id, task_code=code, title=title, source="AUTO", status=status,
            health=health, completion_mode=completion_mode, context_payload=context or {}, priority=priority,
            activation_at=activation_at, due_at=due_at, dedupe_key=key,
        )
        db.session.add(task)
        db.session.flush()
        _activity(task, "CREATED", status=status, context=context)
        return task
    if task.status == "DONE" and task.completion_mode != "RULE_DATA" and not force_reactivate:
        return task
    old_status = task.status
    changed = (task.status != status or task.health != health or task.context_payload != (context or {})
               or task.activation_at != activation_at or task.due_at != due_at)
    if changed:
        event = ("REACTIVATED" if old_status == "CANCELLED"
                 else "RULE_REACTIVATED" if old_status == "DONE" and status in {"ACTION", "UPCOMING"}
                 else "RULE_DEFERRED" if old_status == "ACTION" and status == "UPCOMING"
                 else "STATUS_CHANGED")
        task.status, task.health, task.context_payload = status, health, context or {}
        task.activation_at, task.due_at, task.priority = activation_at, due_at, priority
        if status != "DONE":
            task.completed_at = task.completed_by_id = task.resolution_code = None
        _activity(task, event, old=old_status, status=status, context=context)
    return task


def _resolve_task(pi, code):
    key = f"v2:order:{pi.id}:{code.lower()}"
    task = db.session.scalar(select(OrderTask).where(OrderTask.dedupe_key == key))
    if task is not None and task.status not in {"DONE", "CANCELLED"}:
        old = task.status
        task.status, task.health = "DONE", "NORMAL"
        task.completed_at, task.resolution_code = utcnow(), "AUTO_RESOLVED"
        task.waiting_on = task.waiting_since = task.next_follow_up_at = None
        _activity(task, "AUTO_RESOLVED", old=old, status="DONE")
    return task


def _cancel_task(pi, code, reason="REQUIREMENT_REMOVED"):
    task = _find_task(pi, code)
    if task and task.status not in {"DONE", "CANCELLED"}:
        old = task.status
        task.status, task.health, task.resolution_code = "CANCELLED", "NORMAL", reason
        _activity(task, "CANCELLED", old=old, status="CANCELLED", reason=reason)
    return task


def _task_done(pi, code):
    task = _find_task(pi, code)
    return task if task and task.status == "DONE" else None


def _confirmed_snapshot(pi, code):
    task = _find_task(pi, code)
    if not task:
        return None
    activities = db.session.scalars(select(TaskActivity).where(
        TaskActivity.task_id == task.id, TaskActivity.event_type == "COMPLETED"
    ).order_by(TaskActivity.created_at.desc(), TaskActivity.id.desc())).all()
    return next((row.payload for row in activities if row.payload and row.payload.get("confirmed_amount")), None)


def _set_rule_data(pi, code, title, active, *, future=False, health="NORMAL", context=None,
                   activation_at=None, due_at=None, priority=100):
    if active:
        return _upsert_task(pi, code, title, status="ACTION", health=health, context=context,
                            activation_at=activation_at, due_at=due_at, priority=priority)
    task = _find_task(pi, code)
    if task is not None and task.status in {"ACTION", "WAITING", "UPCOMING"}:
        if future:
            return _upsert_task(pi, code, title, status="UPCOMING", context=context,
                                activation_at=activation_at, due_at=due_at, priority=priority)
        return _resolve_task(pi, code)
    if task is not None and task.status == "DONE" and future:
        return task
    return task


def required_freight_settlements_paid(settlement):
    """Whether every required currency branch has an explicit PAID fact."""
    if settlement is None:
        return True
    return (
        (settlement.usd_bill_required is not True or settlement.usd_payment_status == "PAID")
        and (settlement.cny_bill_required is not True or settlement.cny_payment_status == "PAID")
    )


EXPORT_FINANCIAL_TASK_CODES = (
    "PAYMENT_ADVANCE_WAITING", "PAYMENT_EMAIL", "PAYMENT_BALANCE_FOLLOWUP",
    "SETTLEMENT_DOCUMENT_ADVANCE", "SETTLEMENT_DOCUMENT_BALANCE",
    "FREIGHT_INVOICE_ISSUED", "FREIGHT_PAYMENT_CONFIRM", "FREIGHT_BILL_DIFFERS_FROM_AGREED_QUOTE",
    "FREIGHT_USD_AMOUNT_CAPTURE", "FREIGHT_USD_AMOUNT_CONFIRM", "FREIGHT_USD_INVOICE_ISSUED",
    "FREIGHT_USD_PAYMENT_CONFIRM", "FREIGHT_CNY_AMOUNT_CAPTURE", "FREIGHT_CNY_AMOUNT_CONFIRM",
    "FREIGHT_CNY_INVOICE_ISSUED", "FREIGHT_CNY_PAYMENT_CONFIRM",
)


def _cancel_export_financial_tasks(pi):
    for code in EXPORT_FINANCIAL_TASK_CODES:
        _cancel_task(pi, code, "LINKED_EXPORT_ORDER_FINANCIAL_OWNER")


def _fully_paid(pi):
    return bool(pi.contract_total and (pi.advance_received_amount or 0) + (pi.balance_received_amount or 0) >= pi.contract_total)


def _latest_completed_activity(task):
    """Return current-task completion evidence, newest first by durable order."""
    if task is None:
        return None
    return db.session.scalar(select(TaskActivity).where(
        TaskActivity.task_id == task.id,
        TaskActivity.event_type == "COMPLETED",
    ).order_by(TaskActivity.created_at.desc(), TaskActivity.id.desc()))


def completion_check(pi):
    """Read-only, authoritative ARRIVED -> COMPLETED gate evaluation."""
    resolution = financial_owner_for(pi)
    if is_export_order(pi):
        if resolution.valid:
            payment = {"complete": True, "managed_by": resolution.owner, "not_applicable": True}
            freight = {"complete": True, "managed_by": resolution.owner, "not_applicable": True,
                       "usd": {"required": False, "complete": True}, "cny": {"required": False, "complete": True}}
        else:
            payment = {"complete": False, "configuration_error": resolution.error, "not_applicable": True}
            freight = {"complete": False, "configuration_error": resolution.error, "not_applicable": True,
                       "usd": {"required": False, "complete": False}, "cny": {"required": False, "complete": False}}
    else:
        zero = Decimal("0.00")
        total = pi.contract_total
        received = (pi.advance_received_amount or zero) + (pi.balance_received_amount or zero)
        outstanding = max(total - received, zero)
        payment = {
            "total": total, "received": received, "outstanding": outstanding,
            "complete": outstanding == zero,
        }
        settlement = db.session.scalar(select(FreightSettlement).where(FreightSettlement.pi_id == pi.id))
        freight = {
            "usd": {"required": bool(settlement and settlement.usd_bill_required is True),
                    "status": settlement.usd_payment_status if settlement else None,
                    "complete": not settlement or settlement.usd_bill_required is not True or settlement.usd_payment_status == "PAID"},
            "cny": {"required": bool(settlement and settlement.cny_bill_required is True),
                    "status": settlement.cny_payment_status if settlement else None,
                    "complete": not settlement or settlement.cny_bill_required is not True or settlement.cny_payment_status == "PAID"},
            "complete": required_freight_settlements_paid(settlement),
        }

    telex_task = _find_task(pi, "DOCUMENT_TELEX_RELEASE")
    telex_required = pi.telex_release_required is True
    telex = {
        "required": telex_required,
        "complete": not telex_required or bool(telex_task and telex_task.status == "DONE"),
    }
    mail_task = _find_task(pi, "ORIGINAL_DOCUMENTS_MAIL")
    mail_required = pi.original_documents_mail_required is True
    mail_evidence = _latest_completed_activity(mail_task) if mail_task and mail_task.status == "DONE" else None
    tracking_number = str((mail_evidence.payload or {}).get("tracking_number") or "").strip() if mail_evidence else ""
    mail = {
        "required": mail_required,
        "complete": not mail_required or bool(mail_task and mail_task.status == "DONE" and tracking_number),
        "tracking_number": tracking_number or None,
    }
    documents = {"telex": telex, "mail": mail, "complete": telex["complete"] and mail["complete"]}
    return {
        "payment": payment,
        "freight": freight,
        "documents": documents,
        "overall_complete": payment["complete"] and freight["complete"] and documents["complete"],
    }


def _currency_settlement_fields(settlement, currency):
    prefix = currency.lower()
    return (
        getattr(settlement, f"{prefix}_bill_required"),
        getattr(settlement, f"{prefix}_bill_amount"),
        getattr(settlement, f"{prefix}_bill_confirmed"),
        getattr(settlement, f"{prefix}_invoice_issued"),
        getattr(settlement, f"{prefix}_payment_status"),
    )


def reconcile_order_tasks_for_pi(pi, *, now=None):
    """V2-only targeted rules. No legacy import or dashboard side effects."""
    now = now or utcnow()
    today = business_today(now)
    settlement = db.session.scalar(select(FreightSettlement).where(FreightSettlement.pi_id == pi.id))
    agreement = db.session.scalar(select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id == pi.id))
    export_order = is_export_order(pi)
    owner_resolution = financial_owner_for(pi)
    linked_agreement = (db.session.scalar(select(OrderFreightAgreement).where(
        OrderFreightAgreement.pi_id == owner_resolution.owner.id
    )) if export_order and owner_resolution.valid else None)
    if export_order:
        _cancel_export_financial_tasks(pi)
    loading_date = pi.container_loading_date or (pi.container_loading_at.date() if pi.container_loading_at else None)
    # These shared tasks represent the pre-v2_0004 model.  Preserve history but
    # prevent any active legacy task from competing with a currency branch.
    _cancel_task(pi, "FREIGHT_INVOICE_ISSUED", "Superseded by currency-specific freight settlement workflow.")
    _cancel_task(pi, "FREIGHT_PAYMENT_CONFIRM", "Superseded by currency-specific freight settlement workflow.")
    # A new sales order has one advance-payment business item, not separate
    # "wait" and "chase" tasks.  User follow-ups keep the same task in WAITING
    # until their requested follow-up time; the rule never creates a duplicate.
    planned_date = pi.planned_shipment_date or pi.etd
    advance_expected = pi.advance_payment_amount or Decimal("0")
    advance_received = pi.advance_received_amount or Decimal("0")
    advance_unpaid = not export_order and pi.order_type == "SALES" and advance_expected > 0 and advance_received < advance_expected
    advance_task = _find_task(pi, "PAYMENT_ADVANCE_WAITING")
    if export_order:
        _cancel_task(pi, "PAYMENT_ADVANCE_WAITING", "LINKED_EXPORT_ORDER_FINANCIAL_OWNER")
    elif advance_unpaid:
        outstanding = advance_expected - advance_received
        days_remaining = (planned_date - today).days if planned_date else None
        overdue = bool(planned_date and today > planned_date)
        chase_due = bool(planned_date and today >= planned_date - timedelta(days=10))
        context = {
            "currency": pi.currency,
            "customer_name": pi.customer_name_snapshot,
            "expected_amount": f"{advance_expected:.2f}",
            "received_amount": f"{advance_received:.2f}",
            "outstanding_amount": f"{outstanding:.2f}",
            "planned_shipment_date": planned_date.isoformat() if planned_date else None,
            "days_remaining": days_remaining,
            "action_target": "UPDATE_ADVANCE_RECEIPT",
            "shipping_preparation_blocked": True,
            "message": ("预付款未到账，计划发运日期已过" if overdue
                        else "发运准备暂未启动：等待预付款到账"),
        }
        if advance_task and advance_task.status == "WAITING" and advance_task.next_follow_up_at and advance_task.next_follow_up_at > now and not overdue:
            # Preserve an explicit customer-follow-up commitment.
            advance_task.context_payload = context
        else:
            _upsert_task(
                pi, "PAYMENT_ADVANCE_WAITING",
                "预付款未到账，计划发运日期已过" if overdue else ("催客户支付预付款" if chase_due else "等待客户支付预付款"),
                status="ACTION" if overdue or chase_due else "WAITING",
                health="EXCEPTION" if overdue else "NORMAL",
                context=context,
                activation_at=datetime.combine(planned_date - timedelta(days=10), datetime.min.time()) if planned_date else None,
                due_at=datetime.combine(planned_date, datetime.min.time()) if planned_date else None,
                priority=10,
            )
            task = _find_task(pi, "PAYMENT_ADVANCE_WAITING")
            if task and task.status == "WAITING" and not task.waiting_on:
                task.waiting_on, task.waiting_since = "CUSTOMER", now
    else:
        _resolve_task(pi, "PAYMENT_ADVANCE_WAITING")

    # NEW sales orders receive a gate, never PRE_SHIPMENT operational tasks.
    # The user explicitly confirms the lifecycle transition through the gate.
    prep_allowed = pi.order_type == "SALES" and not advance_unpaid
    prep_reached = bool(planned_date and today >= planned_date - timedelta(days=15))
    if pi.status == OrderStage.NEW:
        # Correct any work created by an older runtime without deleting its
        # append-only history.  NEW can expose only a stage gate.
        _cancel_task(pi, "SHIPPING_CONTAINER_LOADING", "ORDER_NOT_IN_PRE_SHIPMENT")
        _cancel_task(pi, "SHIPPING_FREIGHT_AGREEMENT", "ORDER_NOT_IN_PRE_SHIPMENT")
    if pi.status == OrderStage.NEW and prep_allowed and prep_reached:
        _upsert_task(
            pi, "STAGE_GATE_PRE_SHIPMENT", "开始发运准备",
            status="ACTION", health="NORMAL", completion_mode="RULE_DATA", priority=20,
            context={"planned_shipment_date": planned_date.isoformat(),
                     "days_remaining": (planned_date - today).days,
                     "message": "发运准备条件已具备，可以开始联系工厂和货代",
                     "action_target": "ENTER_PRE_SHIPMENT"},
        )
    elif pi.status == OrderStage.PRE_SHIPMENT and prep_allowed and prep_reached:
        _resolve_task(pi, "STAGE_GATE_PRE_SHIPMENT")
        _set_rule_data(
            pi, "SHIPPING_CONTAINER_LOADING", "确认工厂装柜日期",
            not bool(pi.container_loading_date or pi.container_loading_at),
            context={"planned_shipment_date": planned_date.isoformat(), "action_target": "UPDATE_LOADING_INFO"},
            activation_at=datetime.combine(planned_date - timedelta(days=15), datetime.min.time()), priority=30,
        )
        if export_order:
            _cancel_task(pi, "SHIPPING_FREIGHT_AGREEMENT", "LINKED_CUSTOMER_ORDER_OWNS_FREIGHT_AGREEMENT")
        else:
            _set_rule_data(
                pi, "SHIPPING_FREIGHT_AGREEMENT", "向货代询价并确认船期",
                agreement is None,
                context={"planned_shipment_date": planned_date.isoformat(), "action_target": "UPDATE_FREIGHT_AGREEMENT"},
                activation_at=datetime.combine(planned_date - timedelta(days=15), datetime.min.time()), priority=30,
            )
    elif advance_unpaid:
        # Do not present premature shipment-preparation work while the
        # commercial prerequisite is unmet.
        _cancel_task(pi, "SHIPPING_CONTAINER_LOADING", "ADVANCE_PAYMENT_PENDING")
        _cancel_task(pi, "SHIPPING_FREIGHT_AGREEMENT", "ADVANCE_PAYMENT_PENDING")
        _cancel_task(pi, "STAGE_GATE_PRE_SHIPMENT", "ADVANCE_PAYMENT_PENDING")
    elif pi.status != OrderStage.NEW:
        _resolve_task(pi, "STAGE_GATE_PRE_SHIPMENT")

    # PRE_SHIPMENT -> SHIPPED is also user-confirmed.  The two preparation
    # rules remain the stable workflow truth, but their source facts are
    # checked as well so an out-of-date DONE task cannot unlock the stage.
    if pi.status == OrderStage.PRE_SHIPMENT:
        loading_task = _find_task(pi, "SHIPPING_CONTAINER_LOADING")
        agreement_task = _find_task(pi, "SHIPPING_FREIGHT_AGREEMENT")
        loading_ready = bool(loading_task and loading_task.status == "DONE" and loading_date)
        stage_agreement = linked_agreement if export_order else agreement
        agreement_ready = (bool(owner_resolution.valid and linked_agreement) if export_order
                           else bool(agreement_task and agreement_task.status == "DONE" and agreement))
        missing = []
        if not loading_ready:
            missing.append("工厂装柜日期尚未确认")
        if not agreement_ready:
            missing.append(
                owner_resolution.error if export_order and not owner_resolution.valid
                else f"Linked customer order {owner_resolution.owner.pi_no} has no final accepted freight agreement."
                if export_order else "货代船期/最终报价尚未确认"
            )
        if planned_date and today >= planned_date:
            days_late = (today - planned_date).days
            if missing:
                _upsert_task(
                    pi, "STAGE_GATE_SHIPPED", "计划发运日期已到，发运准备尚未完成",
                    status="ACTION", health="EXCEPTION", completion_mode="RULE_DATA", priority=6,
                    context={"planned_shipment_date": planned_date.isoformat(), "days_overdue": days_late,
                             "missing_preparation": missing,
                             "message": "计划发运日期已到，但发运准备尚未完成。"},
                )
            else:
                _upsert_task(
                    pi, "STAGE_GATE_SHIPPED", "确认货物是否已发运", status="ACTION",
                    health="EXCEPTION" if days_late >= 3 else "NORMAL", completion_mode="RULE_DATA", priority=15,
                    context={"planned_shipment_date": planned_date.isoformat(),
                             "container_loading_date": loading_date.isoformat() if loading_date else None,
                             "freight_forwarder": stage_agreement.freight_forwarder_name_snapshot,
                             "days_overdue": days_late if days_late >= 3 else None,
                             "message": "计划发运日期已超过 3 天，订单仍处于待发运状态。请确认货物是否已经实际发运，或修改计划发运日期。" if days_late >= 3 else "确认货物是否已经实际发运"},
                )
        elif not missing and planned_date:
            _upsert_task(
                pi, "STAGE_GATE_SHIPPED", "发运准备已完成", status="UPCOMING",
                completion_mode="RULE_DATA", priority=15,
                activation_at=datetime.combine(planned_date, datetime.min.time()),
                context={"planned_shipment_date": planned_date.isoformat(),
                         "container_loading_date": loading_date.isoformat() if loading_date else None,
                         "freight_forwarder": stage_agreement.freight_forwarder_name_snapshot,
                         "message": "发运准备已完成，等待计划发运日期。"},
            )
        else:
            _cancel_task(pi, "STAGE_GATE_SHIPPED", "PREPARATION_NOT_READY")
    elif pi.status == OrderStage.SHIPPED:
        _resolve_task(pi, "STAGE_GATE_SHIPPED")
    else:
        _cancel_task(pi, "STAGE_GATE_SHIPPED", "ORDER_NOT_IN_PRE_SHIPMENT")

    # A generic overdue planned-shipment exception is useful outside the
    # advance-payment case.  The advance task itself carries the richer,
    # non-duplicated exception when payment is still outstanding.
    shipment_overdue = bool(planned_date and today > planned_date and not pi.actual_departure_date)
    if shipment_overdue and pi.status != OrderStage.PRE_SHIPMENT and not (pi.status == "NEW" and advance_unpaid):
        _upsert_task(
            pi, "SHIPPING_PLANNED_DATE_OVERDUE", "计划发运日期已过期",
            status="ACTION", health="EXCEPTION", priority=5,
            context={"planned_shipment_date": planned_date.isoformat(),
                     "days_overdue": (today - planned_date).days,
                     "message": "计划发运日期已过期，请确认订单是否延期或补充实际发运信息",
                     "action_target": "UPDATE_SHIPPING_INFO"},
        )
    else:
        _resolve_task(pi, "SHIPPING_PLANNED_DATE_OVERDUE")

    if loading_date and pi.status == OrderStage.PRE_SHIPMENT:
        complete = all((pi.driver_name, pi.driver_phone, pi.vehicle_number))
        activation_date = loading_date - timedelta(days=1)
        activation = datetime.combine(activation_date, datetime.min.time())
        _set_rule_data(pi, "SHIPPING_DRIVER_INFO", "确认司机信息",
                       not complete and today >= activation_date, future=not complete and today < activation_date,
                       health="EXCEPTION" if today >= loading_date and not complete else "NORMAL",
                       context={"container_loading_date": loading_date.isoformat(), "container_loading_period": pi.container_loading_period,
                                "message": "装柜日期已到，但司机信息尚未完整填写" if today >= loading_date and not complete else None},
                       activation_at=activation)
    elif pi.status in {OrderStage.SHIPPED, OrderStage.ARRIVED, OrderStage.COMPLETED}:
        _cancel_task(pi, "SHIPPING_DRIVER_INFO", "NO_LONGER_APPLICABLE_AFTER_SHIPMENT")

    if pi.status == OrderStage.PRE_SHIPMENT and pi.etd and not pi.actual_departure_date:
        reached = today >= pi.etd
        days = max((today - pi.etd).days, 0)
        _upsert_task(
            pi, "SHIPPING_ACTUAL_DEPARTURE", "确认船舶实际开航情况",
            status="ACTION" if reached else "UPCOMING", health="EXCEPTION" if reached else "NORMAL",
            context={"etd": pi.etd.isoformat(), "message": f"ETD 已过 {days} 天，尚未记录实际发运日期" if reached else None},
            activation_at=datetime.combine(pi.etd, datetime.min.time()),
        )
    elif pi.actual_departure_date:
        _resolve_task(pi, "SHIPPING_ACTUAL_DEPARTURE")
    else:
        _cancel_task(pi, "SHIPPING_ACTUAL_DEPARTURE", "NOT_APPLICABLE_OUTSIDE_PRE_SHIPMENT")

    arrival_task = _find_task(pi, "SHIPPING_ACTUAL_ARRIVAL")
    if pi.status == OrderStage.SHIPPED and pi.eta and not pi.actual_arrival_date:
        schedule = arrival_schedule_projection(pi.eta, today)
        context = {
            "eta": pi.eta.isoformat(), "trigger_date": schedule["trigger_date"].isoformat(),
            "action_target": "ENTER_ARRIVED",
            "message": schedule["message"],
        }
        if (arrival_task and arrival_task.status == "WAITING" and arrival_task.next_follow_up_at
                and arrival_task.next_follow_up_at.date() > today):
            arrival_task.health = schedule["health"]
            arrival_task.context_payload = context
            arrival_task.activation_at = datetime.combine(schedule["trigger_date"], datetime.min.time())
        else:
            desired_status = schedule["status"]
            if (arrival_task and arrival_task.status == "WAITING" and arrival_task.next_follow_up_at
                    and arrival_task.next_follow_up_at.date() <= today):
                desired_status = "ACTION"
            _upsert_task(
                pi, "SHIPPING_ACTUAL_ARRIVAL", "确认实际到港日期", status=desired_status,
                health=schedule["health"], context=context,
                activation_at=datetime.combine(schedule["trigger_date"], datetime.min.time()),
            )
    elif pi.actual_arrival_date or pi.status in {OrderStage.ARRIVED, OrderStage.COMPLETED}:
        _resolve_task(pi, "SHIPPING_ACTUAL_ARRIVAL")
    else:
        _cancel_task(pi, "SHIPPING_ACTUAL_ARRIVAL", "NOT_APPLICABLE_OUTSIDE_SHIPPED")

    for code, fact, title, trigger in DOCUMENT_RULES:
        if getattr(pi, fact) is not True:
            _cancel_task(pi, code)
            continue
        active = (
            trigger == "PRE_SHIPMENT" and pi.status in {"PRE_SHIPMENT","SHIPPED","ARRIVED","COMPLETED"}
            or trigger == "SHIPPED" and pi.status in {"SHIPPED","ARRIVED","COMPLETED"}
            or trigger == "LOADING" and loading_date and today >= loading_date
            or trigger == "LOADING_MINUS_5" and loading_date and today >= (loading_date - __import__('datetime').timedelta(days=5))
        )
        context = {"message": "APTA 日期需在提单开船日期三日内"} if code == "DOCUMENT_APTA" else None
        _upsert_task(pi, code, title, status="ACTION" if active else "UPCOMING", completion_mode="MANUAL", context=context)

    originals = [code for code, fact in (("DOCUMENT_ORIGINAL_BL", "original_bl_required"),
                                          ("DOCUMENT_INSURANCE_ORIGINAL", "insurance_original_required"))
                 if getattr(pi, fact) is True]
    if pi.original_documents_mail_required is not True or not originals:
        _cancel_task(pi, "ORIGINAL_DOCUMENTS_MAIL")
    else:
        ready = all(_task_done(pi, code) for code in originals)
        _upsert_task(pi, "ORIGINAL_DOCUMENTS_MAIL", "邮寄文件原件",
                     status="ACTION" if ready else "UPCOMING", completion_mode="MANUAL_REQUIRED_INPUT",
                     context={"required_inputs": ["tracking_number"], "prerequisites": originals})

    payment_readiness_pi = owner_resolution.owner if export_order and owner_resolution.valid else pi
    fully_paid = _fully_paid(payment_readiness_pi)
    if export_order:
        _cancel_task(pi, "PAYMENT_EMAIL", "LINKED_EXPORT_ORDER_FINANCIAL_OWNER")
        _cancel_task(pi, "SETTLEMENT_DOCUMENT_ADVANCE", "LINKED_EXPORT_ORDER_FINANCIAL_OWNER")
        _cancel_task(pi, "SETTLEMENT_DOCUMENT_BALANCE", "LINKED_EXPORT_ORDER_FINANCIAL_OWNER")
        _cancel_task(pi, "PAYMENT_BALANCE_FOLLOWUP", "LINKED_EXPORT_ORDER_FINANCIAL_OWNER")
    elif pi.status in {"SHIPPED","ARRIVED","COMPLETED"}:
        _upsert_task(pi,"PAYMENT_EMAIL","EMAIL发送付款文件给客户并请款",status="ACTION",completion_mode="MANUAL")
    else:
        _cancel_task(pi, "PAYMENT_EMAIL", "NOT_APPLICABLE")

    if pi.telex_release_required is True:
        if export_order and not owner_resolution.valid:
            _upsert_task(pi, "DOCUMENT_TELEX_RELEASE", "取得/发送提单电放件", status="ACTION",
                         health="EXCEPTION", completion_mode="MANUAL",
                         context={"message": owner_resolution.error})
        else:
            _upsert_task(pi,"DOCUMENT_TELEX_RELEASE","取得/发送提单电放件",status="ACTION" if fully_paid else "UPCOMING",completion_mode="MANUAL")
    else:
        _cancel_task(pi, "DOCUMENT_TELEX_RELEASE")
    if not export_order:
        if pi.settlement_documents_required is True:
            if (pi.advance_received_amount or 0)>0 and pi.advance_received_at:
                _upsert_task(pi,"SETTLEMENT_DOCUMENT_ADVANCE","准备预付款结汇文件",status="ACTION",completion_mode="MANUAL",context={"currency":pi.currency,"amount":str(pi.advance_received_amount)})
            if (pi.balance_received_amount or 0)>0 and pi.balance_received_at:
                _upsert_task(pi,"SETTLEMENT_DOCUMENT_BALANCE","准备尾款结汇文件",status="ACTION",completion_mode="MANUAL",context={"currency":pi.currency,"amount":str(pi.balance_received_amount)})
        else:
            _cancel_task(pi, "SETTLEMENT_DOCUMENT_ADVANCE")
            _cancel_task(pi, "SETTLEMENT_DOCUMENT_BALANCE")

        prerequisites = [task for task in (_task_done(pi, "PAYMENT_EMAIL"), _task_done(pi, "ORIGINAL_DOCUMENTS_MAIL")) if task and task.completed_at]
        followup = _find_task(pi, "PAYMENT_BALANCE_FOLLOWUP")
        if fully_paid:
            _resolve_task(pi, "PAYMENT_BALANCE_FOLLOWUP")
        elif prerequisites and (pi.balance_payment_amount or Decimal("0")) > Decimal("0"):
            base = min(task.completed_at for task in prerequisites)
            activation_date = base.date() + timedelta(days=3)
            activation = datetime.combine(activation_date, datetime.min.time())
            outstanding = (pi.balance_payment_amount or Decimal("0")) - (pi.balance_received_amount or Decimal("0"))
            if not (followup and followup.status == "WAITING" and followup.next_follow_up_at and followup.next_follow_up_at.date() > today):
                _upsert_task(pi, "PAYMENT_BALANCE_FOLLOWUP", "催客户付款",
                             status="ACTION" if today >= activation_date else "UPCOMING",
                             health="OVERDUE" if today > activation_date else "NORMAL",
                             context={"currency": pi.currency, "outstanding_amount": str(max(outstanding, Decimal('0'))),
                                      "base_completed_at": base.isoformat(), "trigger_date": activation_date.isoformat()},
                             activation_at=activation)
        else:
            _resolve_task(pi, "PAYMENT_BALANCE_FOLLOWUP")

    if pi.status == OrderStage.ARRIVED:
        _upsert_task(pi, "ARRIVAL_CUSTOMER_PICKUP", "提醒客户安排提货",
                     status="ACTION", completion_mode="MANUAL", priority=40,
                     context={"action_target": "VIEW_ORDER"})
    elif pi.status != OrderStage.COMPLETED:
        _cancel_task(pi, "ARRIVAL_CUSTOMER_PICKUP", "ORDER_NOT_ARRIVED")

    if not export_order and settlement and pi.actual_departure_date:
        trigger_date = pi.actual_departure_date + timedelta(days=7)
        reached = today >= trigger_date
        components = (
            ("USD", settlement.usd_bill_required, settlement.usd_bill_amount, settlement.usd_bill_confirmed),
            ("CNY", settlement.cny_bill_required, settlement.cny_bill_amount, settlement.cny_bill_confirmed),
        )
        for currency, required, amount, confirmed in components:
            invoice_code = f"FREIGHT_{currency}_INVOICE_ISSUED"
            payment_code = f"FREIGHT_{currency}_PAYMENT_CONFIRM"
            if required is not True:
                _cancel_task(pi, f"FREIGHT_{currency}_AMOUNT_CAPTURE")
                _cancel_task(pi, f"FREIGHT_{currency}_AMOUNT_CONFIRM")
                _cancel_task(pi, invoice_code)
                _cancel_task(pi, payment_code)
                continue
            capture_code = f"FREIGHT_{currency}_AMOUNT_CAPTURE"
            confirm_code = f"FREIGHT_{currency}_AMOUNT_CONFIRM"
            amount_changed = False
            if amount is None:
                _upsert_task(pi, capture_code, f"录入货代 {currency} 账单金额",
                             status="ACTION" if reached else "UPCOMING", activation_at=datetime.combine(trigger_date, datetime.min.time()),
                             context={"currency": currency, "trigger_date": trigger_date.isoformat()})
            else:
                _resolve_task(pi, capture_code)
                snapshot = _confirmed_snapshot(pi, confirm_code)
                amount_changed = bool(snapshot and Decimal(snapshot["confirmed_amount"]) != Decimal(amount))
                if confirmed is True and not amount_changed:
                    _resolve_task(pi, confirm_code)
                else:
                    _upsert_task(
                        pi, confirm_code, f"确认货代 {currency} 账单金额", status="ACTION" if reached else "UPCOMING",
                        health="EXCEPTION" if amount_changed else "NORMAL", completion_mode="MANUAL",
                        context={"currency": currency, "amount": f"{Decimal(amount):.2f}",
                                 "warning": f"{currency} 账单金额在确认后发生变化，请重新确认" if amount_changed else None,
                                 "previous_confirmed_amount": snapshot.get("confirmed_amount") if snapshot else None},
                        activation_at=datetime.combine(trigger_date, datetime.min.time()),
                        force_reactivate=amount_changed,
                    )
            invoice_issued = getattr(settlement, f"{currency.lower()}_invoice_issued")
            payment_status = getattr(settlement, f"{currency.lower()}_payment_status")
            prerequisites_met = amount is not None and confirmed is True and not amount_changed
            if not prerequisites_met:
                _cancel_task(pi, invoice_code, "PREREQUISITES_NOT_MET")
                _cancel_task(pi, payment_code, "PREREQUISITES_NOT_MET")
                continue
            if invoice_issued is not True:
                _upsert_task(pi, invoice_code, f"确认 {currency} 货代 Invoice 已开具",
                             status="ACTION", completion_mode="MANUAL",
                             context={"currency": currency})
                _cancel_task(pi, payment_code, "INVOICE_NOT_ISSUED")
                continue
            _resolve_task(pi, invoice_code)
            if payment_status == "PAID":
                _resolve_task(pi, payment_code)
            else:
                _upsert_task(pi, payment_code, f"确认 {currency} 货代付款",
                             status="ACTION", completion_mode="RULE_DATA",
                             context={"currency": currency, "action_target": f"UPDATE_{currency}_FREIGHT_PAYMENT"})

    if not export_order and settlement and agreement:
        comparison = freight_agreement_difference(agreement, settlement)
        if comparison.get("reason") == "AMOUNT_DIFFERENCE":
            _upsert_task(
                pi, "FREIGHT_BILL_DIFFERS_FROM_AGREED_QUOTE",
                "货代实际账单与约定报价不一致", status="ACTION", health="EXCEPTION",
                context={
                    "currency": agreement.currency,
                    "agreed_amount": f"{Decimal(agreement.amount):.2f}",
                    "actual_amount": f"{Decimal(
                        settlement.usd_bill_amount if agreement.currency == "USD" else settlement.cny_bill_amount
                    ):.2f}",
                    "difference": f"{Decimal(comparison['difference']):.2f}",
                },
            )
        else:
            _resolve_task(pi, "FREIGHT_BILL_DIFFERS_FROM_AGREED_QUOTE")
    return list(db.session.scalars(select(OrderTask).where(OrderTask.pi_id == pi.id)))


def save_order_with_reconcile(pi, *, now=None):
    try:
        db.session.flush()
        if pi.advance_payment_percent is not None and pi.advance_payment_amount is None:
            pi.advance_payment_amount = (
                pi.contract_total * Decimal(pi.advance_payment_percent) / Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if pi.balance_payment_amount is None:
            pi.balance_payment_amount = (pi.contract_total - (pi.advance_payment_amount or Decimal("0"))).quantize(Decimal("0.01"))
        pi.derive_commission()
        reconcile_order_tasks_for_pi(pi, now=now)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def apply_manual_task_business_fact(task):
    """Synchronize approved manual workflow facts before task completion."""
    settlement = db.session.scalar(select(FreightSettlement).where(FreightSettlement.pi_id == task.pi_id))
    payload = None
    if task.task_code in {"FREIGHT_USD_AMOUNT_CONFIRM", "FREIGHT_CNY_AMOUNT_CONFIRM"}:
        if settlement is None:
            raise ValueError("Freight settlement facts are missing.")
        currency = "USD" if "USD" in task.task_code else "CNY"
        amount = settlement.usd_bill_amount if currency == "USD" else settlement.cny_bill_amount
        if amount is None:
            raise ValueError(f"{currency} freight amount is required before confirmation.")
        if currency == "USD":
            settlement.usd_bill_confirmed = True
        else:
            settlement.cny_bill_confirmed = True
        payload = {"currency": currency, "confirmed_amount": f"{Decimal(amount):.2f}"}
    elif task.task_code in {"FREIGHT_USD_INVOICE_ISSUED", "FREIGHT_CNY_INVOICE_ISSUED"}:
        if settlement is None:
            raise ValueError("Freight settlement facts are missing.")
        currency = "USD" if "USD" in task.task_code else "CNY"
        required, amount, confirmed, _, _ = _currency_settlement_fields(settlement, currency)
        if required is not True or amount is None or confirmed is not True:
            raise ValueError(f"{currency} freight invoice prerequisites are not complete.")
        confirmed_at = utcnow()
        setattr(settlement, f"{currency.lower()}_invoice_issued", True)
        setattr(settlement, f"{currency.lower()}_invoice_issued_at", confirmed_at)
        payload = {"currency": currency, "confirmed_at": confirmed_at.isoformat()}
    return payload


def open_correction_session(pi, module, reason, actor_id):
    if is_export_order(pi) and module in {"PAYMENT", "FREIGHT"}:
        raise LifecyclePolicyError("Financial corrections are managed by the linked CUSTOMER_ORDER.")
    if pi.status != OrderStage.COMPLETED:
        raise LifecyclePolicyError("Correction sessions are only for completed orders.")
    if module not in CORRECTION_TO_POLICY_MODULE:
        raise LifecyclePolicyError("Unsupported correction module.")
    if not reason or not reason.strip():
        raise LifecyclePolicyError("Correction reason is required.")
    existing = db.session.scalar(select(OrderCorrectionSession).where(
        OrderCorrectionSession.pi_id == pi.id,
        OrderCorrectionSession.closed_at.is_(None),
    ))
    if existing:
        raise LifecyclePolicyError("An open correction session already exists for this order.")
    session = OrderCorrectionSession(
        pi_id=pi.id, module=module, reason=reason.strip(), opened_by_id=actor_id,
    )
    db.session.add(session)
    db.session.flush()
    return session


def assert_correction_allows(pi, module):
    if pi.status != OrderStage.COMPLETED:
        return
    session = db.session.scalar(select(OrderCorrectionSession).where(
        OrderCorrectionSession.pi_id == pi.id,
        OrderCorrectionSession.closed_at.is_(None),
    ))
    if session is None or module not in CORRECTION_TO_POLICY_MODULE[session.module]:
        raise LifecyclePolicyError("Completed order module is not unlocked for correction.")


def close_correction_session(session, actor_id, *, note=None, now=None):
    if session.closed_at is not None:
        raise LifecyclePolicyError("Correction session is already closed.")
    session.closed_by_id = actor_id
    session.closed_at = now or utcnow()
    session.close_note = note
    pi = db.session.get(PI, session.pi_id)
    reconcile_order_tasks_for_pi(pi, now=now)
    db.session.commit()
