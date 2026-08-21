"""Phase 2D dual-currency freight settlement reminder rules."""

from datetime import datetime, time, timedelta

from reminders.definitions import ACTION, DONE, UPCOMING, RuleDefinition, cancel, ensure, ignore, resolve
from reminders.enums import CompletionMode, TaskHealth, TaskStatus
from reminders.freight import COMPONENTS, confirmation_snapshot_matches, freight_forwarder_name, money_text


def freight_trigger(pi):
    if pi.actual_departure_date is None:
        return None
    trigger_date = pi.actual_departure_date + timedelta(days=7)
    return trigger_date, datetime.combine(trigger_date, time.min)


def _base_context(pi):
    trigger = freight_trigger(pi)
    return {
        "freight_forwarder_name": freight_forwarder_name(pi),
        "actual_departure_date": (
            pi.actual_departure_date.isoformat() if pi.actual_departure_date else None
        ),
        "trigger_date": trigger[0].isoformat() if trigger else None,
        "legacy_freight_invoice_amount": money_text(pi.freight_invoice_amount),
        "legacy_amount_mapping": "UNKNOWN" if pi.freight_invoice_amount is not None else None,
        "action_target": "UPDATE_FREIGHT_INFO",
    }


def _required_state(pi, currency):
    return getattr(pi, COMPONENTS[currency]["required"])


def _component_context(pi, currency):
    fields = COMPONENTS[currency]
    context = _base_context(pi)
    context.update(
        {
            "currency": currency,
            "required": getattr(pi, fields["required"]),
            "amount": money_text(getattr(pi, fields["amount"])),
            "confirmed_fact": getattr(pi, fields["confirmed"]),
        }
    )
    return context


def _not_required_outcome(pi, currency, rule_context, task_code):
    required = _required_state(pi, currency)
    if required is False:
        return cancel("REQUIREMENT_REMOVED")
    if required is None:
        return cancel("REQUIREMENT_UNCONFIRMED") if task_code in rule_context.task_statuses else ignore()
    return None


def _capture_rule(currency, task_code):
    fields = COMPONENTS[currency]

    def evaluate(pi, now, rule_context):
        terminal = _not_required_outcome(pi, currency, rule_context, task_code)
        if terminal:
            return terminal
        context = _component_context(pi, currency)
        amount = getattr(pi, fields["amount"])
        if amount is not None:
            return resolve(f"FREIGHT_{currency}_AMOUNT_RECORDED", context=context)
        trigger = freight_trigger(pi)
        if trigger is None:
            if task_code in rule_context.task_statuses:
                return ensure(UPCOMING, reason_code="RULE_DEFERRED", context=context)
            return ignore()
        trigger_date, trigger_at = trigger
        if now.date() < trigger_date:
            return ensure(
                UPCOMING,
                reason_code="TRIGGER_IN_FUTURE",
                activation_at=trigger_at,
                context=context,
            )
        return ensure(
            ACTION,
            reason_code=f"FREIGHT_{currency}_AMOUNT_MISSING",
            activation_at=trigger_at,
            context=context,
        )

    return evaluate


def _confirm_rule(currency, task_code, changed_reason):
    fields = COMPONENTS[currency]

    def evaluate(pi, now, rule_context):
        terminal = _not_required_outcome(pi, currency, rule_context, task_code)
        if terminal:
            return terminal
        amount = getattr(pi, fields["amount"])
        if amount is None:
            return cancel("FREIGHT_AMOUNT_REMOVED") if task_code in rule_context.task_statuses else ignore()

        context = _component_context(pi, currency)
        task = rule_context.tasks.get(task_code)
        if task is not None and task.status == TaskStatus.DONE:
            if confirmation_snapshot_matches(task, currency, amount):
                return ensure(DONE, reason_code="CONFIRMED_AMOUNT_UNCHANGED", context=context)
            context.update(
                {
                    "health_reason_code": changed_reason,
                    "health_message": f"{currency} 货代账单金额在确认后发生变化，请重新确认",
                    "previous_confirmation": next(
                        (
                            activity.payload
                            for activity in reversed(task.activities)
                            if (activity.payload or {}).get("currency") == currency
                            and (activity.payload or {}).get("confirmed_amount") is not None
                        ),
                        None,
                    ),
                }
            )
            return ensure(
                ACTION,
                health=TaskHealth.EXCEPTION,
                reason_code=changed_reason,
                context=context,
                force_reactivate=True,
            )

        trigger = freight_trigger(pi)
        if trigger is None:
            if task_code in rule_context.task_statuses:
                return ensure(UPCOMING, reason_code="RULE_DEFERRED", context=context)
            return ignore()
        trigger_date, trigger_at = trigger
        return ensure(
            ACTION if now.date() >= trigger_date else UPCOMING,
            reason_code="FREIGHT_AMOUNT_READY" if now.date() >= trigger_date else "TRIGGER_IN_FUTURE",
            activation_at=trigger_at,
            context=context,
        )

    return evaluate


def _component_valid(pi, currency, rule_context):
    fields = COMPONENTS[currency]
    task = rule_context.tasks.get(fields["confirm_task"])
    return bool(
        getattr(pi, fields["confirmed"]) is True
        and task is not None
        and rule_context.task_statuses.get(fields["confirm_task"]) == TaskStatus.DONE
        and confirmation_snapshot_matches(task, currency, getattr(pi, fields["amount"]))
    )


def _required_components(pi):
    return [currency for currency in ("USD", "CNY") if _required_state(pi, currency) is True]


def _invoice_context(pi, rule_context):
    required = _required_components(pi)
    valid = {currency: _component_valid(pi, currency, rule_context) for currency in required}
    warnings = []
    if pi.freight_invoice_issued == "已开具" and any(not value for value in valid.values()):
        warnings.append("货代账单金额在发票流程后发生变化，请核实货代发票")
    context = _base_context(pi)
    context.update(
        {
            "required_components": required,
            "component_confirmations_valid": valid,
            "usd_freight": money_text(pi.freight_usd_amount) if "USD" in required else None,
            "cny_charges": money_text(pi.freight_cny_amount) if "CNY" in required else None,
            "warnings": warnings,
            "legacy_freight_invoice_issued": pi.freight_invoice_issued,
        }
    )
    return context


def _legacy_bill_confirmation(pi, now, rule_context):
    if pi.freight_invoice_confirmed != "已确认":
        return ignore()
    return ensure(
        DONE,
        reason_code="LEGACY_DONE",
        legacy_done=True,
        context={
            "legacy_field": "freight_invoice_confirmed",
            "legacy_value": pi.freight_invoice_confirmed,
            "compatibility_note": (
                "历史记录显示货代账单已确认，但无法区分美元/人民币账单确认情况"
            ),
        },
    )


def _invoice_issued(pi, now, rule_context):
    context = _invoice_context(pi, rule_context)
    existing = rule_context.tasks.get("FREIGHT_INVOICE_ISSUED")
    if pi.freight_invoice_issued == "已开具":
        return ensure(
            DONE,
            reason_code="LEGACY_DONE" if existing is None else "INVOICE_ALREADY_ISSUED",
            legacy_done=existing is None,
            context=context,
            refresh_done_context=existing is not None,
        )

    if freight_trigger(pi) is None:
        return cancel("ACTUAL_DEPARTURE_MISSING") if existing else ignore()

    required = context["required_components"]
    if not required:
        return cancel("NO_REQUIRED_FREIGHT_COMPONENTS") if existing else ignore()
    all_confirmed = all(context["component_confirmations_valid"].values())
    return ensure(
        ACTION if all_confirmed else UPCOMING,
        reason_code="ALL_FREIGHT_COMPONENTS_CONFIRMED" if all_confirmed else "WAITING_FOR_FREIGHT_CONFIRMATION",
        context=context,
    )


def _payment_confirm(pi, now, rule_context):
    task_code = "FREIGHT_PAYMENT_CONFIRM"
    existing = rule_context.tasks.get(task_code)
    invoice_done = (
        pi.freight_invoice_issued == "已开具"
        or rule_context.task_statuses.get("FREIGHT_INVOICE_ISSUED") == TaskStatus.DONE
    )
    context = _invoice_context(pi, rule_context)
    context.update(
        {
            "freight_payment_status": pi.freight_payment_status,
            "freight_paid_at": pi.freight_paid_at.isoformat() if pi.freight_paid_at else None,
            "action_target": "UPDATE_FREIGHT_INFO",
        }
    )
    if pi.freight_payment_status == "已付款":
        if pi.freight_paid_at is None:
            context.setdefault("warnings", []).append(
                "货代付款状态已标记为已付款，但付款日期未填写"
            )
        if existing is None:
            return ensure(
                DONE,
                reason_code="LEGACY_DONE",
                legacy_done=True,
                completed_at=pi.freight_paid_at,
                context=context,
            )
        return resolve("FREIGHT_PAYMENT_STATUS_PAID", context=context)
    if not invoice_done:
        return cancel("FREIGHT_INVOICE_NOT_ISSUED") if existing else ignore()
    return ensure(ACTION, reason_code="FREIGHT_PAYMENT_NOT_PAID", context=context)


FREIGHT_RULES = (
    RuleDefinition(
        "freight.usd.capture", 1, "FREIGHT_USD_AMOUNT_CAPTURE",
        "录入货代美元海运费账单金额", CompletionMode.RULE_DATA,
        _capture_rule("USD", "FREIGHT_USD_AMOUNT_CAPTURE"),
    ),
    RuleDefinition(
        "freight.usd.confirm", 1, "FREIGHT_USD_AMOUNT_CONFIRM",
        "确认货代美元海运费账单金额", CompletionMode.MANUAL,
        _confirm_rule(
            "USD", "FREIGHT_USD_AMOUNT_CONFIRM",
            "FREIGHT_USD_AMOUNT_CHANGED_AFTER_CONFIRMATION",
        ),
    ),
    RuleDefinition(
        "freight.cny.capture", 1, "FREIGHT_CNY_AMOUNT_CAPTURE",
        "录入货代人民币账单金额", CompletionMode.RULE_DATA,
        _capture_rule("CNY", "FREIGHT_CNY_AMOUNT_CAPTURE"),
    ),
    RuleDefinition(
        "freight.cny.confirm", 1, "FREIGHT_CNY_AMOUNT_CONFIRM",
        "确认货代人民币账单金额", CompletionMode.MANUAL,
        _confirm_rule(
            "CNY", "FREIGHT_CNY_AMOUNT_CONFIRM",
            "FREIGHT_CNY_AMOUNT_CHANGED_AFTER_CONFIRMATION",
        ),
    ),
    RuleDefinition(
        "freight.bill.legacy_confirm", 1, "LEGACY_FREIGHT_BILL_CONFIRM",
        "历史货代账单已确认", CompletionMode.MANUAL, _legacy_bill_confirmation,
    ),
    RuleDefinition(
        "freight.invoice.issued", 1, "FREIGHT_INVOICE_ISSUED",
        "确认货代发票已开具", CompletionMode.MANUAL, _invoice_issued,
    ),
    RuleDefinition(
        "freight.payment.confirm", 1, "FREIGHT_PAYMENT_CONFIRM",
        "确认是否已给货代付款", CompletionMode.RULE_DATA, _payment_confirm,
    ),
)
