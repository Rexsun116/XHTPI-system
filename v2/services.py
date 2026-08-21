"""V2 order mutations and targeted reconcile; no V1 adapters."""

from datetime import datetime
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


def _upsert_task(pi, code, title, *, status, health="NORMAL", completion_mode="RULE_DATA", context=None):
    key = f"v2:order:{pi.id}:{code.lower()}"
    task = db.session.scalar(select(OrderTask).where(OrderTask.dedupe_key == key))
    if task is None:
        task = OrderTask(
            pi_id=pi.id, task_code=code, title=title, source="AUTO", status=status,
            health=health, completion_mode=completion_mode, context_payload=context or {},
            dedupe_key=key,
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(TaskActivity(
            task_id=task.id, event_type="CREATED", to_status=status, actor_type="SYSTEM",
            payload=context or {},
        ))
        return task
    changed = task.status != status or task.health != health or task.context_payload != (context or {})
    if changed:
        old = task.status
        task.status, task.health, task.context_payload = status, health, context or {}
        db.session.add(TaskActivity(
            task_id=task.id, event_type="STATUS_CHANGED", from_status=old,
            to_status=status, actor_type="SYSTEM", payload=context or {},
        ))
    return task


def _resolve_task(pi, code):
    key = f"v2:order:{pi.id}:{code.lower()}"
    task = db.session.scalar(select(OrderTask).where(OrderTask.dedupe_key == key))
    if task is not None and task.status not in {"DONE", "CANCELLED"}:
        old = task.status
        task.status, task.health = "DONE", "NORMAL"
        task.completed_at, task.resolution_code = utcnow(), "AUTO_RESOLVED"
        db.session.add(TaskActivity(
            task_id=task.id, event_type="AUTO_RESOLVED", from_status=old,
            to_status="DONE", actor_type="SYSTEM",
        ))
    return task


def reconcile_order_tasks_for_pi(pi, *, now=None):
    """V2-only targeted rules. No legacy import or dashboard side effects."""
    now = now or utcnow()
    settlement = db.session.scalar(select(FreightSettlement).where(FreightSettlement.pi_id == pi.id))
    agreement = db.session.scalar(select(OrderFreightAgreement).where(OrderFreightAgreement.pi_id == pi.id))
    if pi.advance_payment_amount and (pi.advance_received_amount or Decimal("0")) < pi.advance_payment_amount:
        outstanding = pi.advance_payment_amount - (pi.advance_received_amount or Decimal("0"))
        _upsert_task(
            pi, "PAYMENT_ADVANCE_WAITING", "等待客户支付预付款", status="WAITING",
            context={"currency": pi.currency, "outstanding_amount": str(outstanding)},
        )
    else:
        _resolve_task(pi, "PAYMENT_ADVANCE_WAITING")

    if pi.container_loading_at and pi.status in {OrderStage.PRE_SHIPMENT, OrderStage.SHIPPED}:
        complete = all((pi.driver_name, pi.driver_phone, pi.vehicle_number))
        if not complete:
            reached = now >= pi.container_loading_at
            _upsert_task(
                pi, "SHIPPING_DRIVER_INFO", "确认司机信息", status="ACTION",
                health="EXCEPTION" if reached else "NORMAL",
                context={"container_loading_at": pi.container_loading_at.isoformat()},
            )
        else:
            _resolve_task(pi, "SHIPPING_DRIVER_INFO")

    if pi.etd and pi.etd <= now.date() and not pi.actual_departure_date:
        _upsert_task(
            pi, "SHIPPING_ACTUAL_DEPARTURE", "确认船舶实际开航情况",
            status="ACTION", health="EXCEPTION", context={"etd": pi.etd.isoformat()},
        )
    elif pi.actual_departure_date:
        _resolve_task(pi, "SHIPPING_ACTUAL_DEPARTURE")

    if pi.eta and pi.eta <= now.date() and not pi.actual_arrival_date:
        _upsert_task(
            pi, "SHIPPING_ACTUAL_ARRIVAL", "确认实际到港日期",
            status="ACTION", health="EXCEPTION", context={"eta": pi.eta.isoformat()},
        )
    elif pi.actual_arrival_date:
        _resolve_task(pi, "SHIPPING_ACTUAL_ARRIVAL")

    if pi.coo_required is True and pi.container_loading_at:
        if now >= pi.container_loading_at:
            _upsert_task(
                pi, "DOCUMENT_COO", "办理 COO", status="ACTION",
                completion_mode="MANUAL",
            )

    if settlement and pi.actual_departure_date:
        components = (
            ("USD", settlement.usd_bill_required, settlement.usd_bill_amount, settlement.usd_bill_confirmed),
            ("CNY", settlement.cny_bill_required, settlement.cny_bill_amount, settlement.cny_bill_confirmed),
        )
        required_confirmations = []
        for currency, required, amount, confirmed in components:
            if required is not True:
                continue
            capture_code = f"FREIGHT_{currency}_AMOUNT_CAPTURE"
            confirm_code = f"FREIGHT_{currency}_AMOUNT_CONFIRM"
            if amount is None:
                _upsert_task(pi, capture_code, f"录入货代 {currency} 账单金额", status="ACTION")
            else:
                _resolve_task(pi, capture_code)
                if confirmed is True:
                    _resolve_task(pi, confirm_code)
                else:
                    _upsert_task(
                        pi, confirm_code, f"确认货代 {currency} 账单金额", status="ACTION",
                        completion_mode="MANUAL", context={"currency": currency, "amount": f"{Decimal(amount):.2f}"},
                    )
            required_confirmations.append(confirmed is True)
        if required_confirmations and all(required_confirmations):
            if settlement.invoice_issued is True:
                _resolve_task(pi, "FREIGHT_INVOICE_ISSUED")
                if settlement.payment_status == "PAID":
                    _resolve_task(pi, "FREIGHT_PAYMENT_CONFIRM")
                else:
                    _upsert_task(pi, "FREIGHT_PAYMENT_CONFIRM", "确认是否已给货代付款", status="ACTION")
            else:
                _upsert_task(
                    pi, "FREIGHT_INVOICE_ISSUED", "确认货代发票已开具",
                    status="ACTION", completion_mode="MANUAL",
                )

    if settlement and agreement:
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


def open_correction_session(pi, module, reason, actor_id):
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
