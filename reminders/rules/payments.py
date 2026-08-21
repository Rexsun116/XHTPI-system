"""Phase 2C payment, settlement-document, and telex-release rules."""

from datetime import datetime, time, timedelta
from decimal import Decimal

from reminders.adapters import legacy_settlement_fact, legacy_telex_done
from reminders.definitions import ACTION, DONE, UPCOMING, RuleDefinition, cancel, ensure, ignore, resolve
from reminders.enums import CompletionMode, TaskHealth, TaskStatus, WaitingOn
from reminders.payment import ZERO, assess_payment, stage_received


def _customer_name(pi):
    return pi.customer_name_snapshot or (pi.customer.name if pi.customer else None) or "未知客户"


def _payment_context(pi, assessment, *, stage=None):
    context = assessment.context()
    context.update(
        {
            "customer_name": _customer_name(pi),
            "payment_stage": stage,
            "action_target": "UPDATE_PAYMENT_INFO",
        }
    )
    if stage == "advance":
        context.update(
            {
                "percentage": context["advance_percent"],
                "expected_amount": context["advance_expected"],
                "received_amount": context["advance_received"],
                "outstanding_amount": context["advance_outstanding"],
            }
        )
    elif stage == "balance":
        balance_percent = (
            (Decimal("100") - assessment.advance_percent).quantize(Decimal("0.01"))
            if assessment.advance_percent is not None
            else None
        )
        context.update(
            {
                "percentage": format(balance_percent, ".2f") if balance_percent is not None else None,
                "expected_amount": context["balance_expected"],
                "received_amount": context["balance_received"],
                "outstanding_amount": context["balance_outstanding"],
            }
        )
    return context


def _completed_candidate(rule_context, task_code):
    task = rule_context.tasks.get(task_code)
    if task is None or task.status != TaskStatus.DONE or task.completed_at is None:
        return None
    return task.completed_at


def _balance_follow_up(pi, now, rule_context):
    assessment = assess_payment(pi)
    context = _payment_context(pi, assessment, stage="balance")
    candidates = [
        value
        for value in (
            _completed_candidate(rule_context, "PAYMENT_EMAIL"),
            _completed_candidate(rule_context, "DOCUMENT_ORIGINALS_MAIL"),
        )
        if value is not None
    ]
    if assessment.fully_received:
        return resolve("PAYMENT_FULLY_RECEIVED", context=context)
    if assessment.balance_expected is not None and assessment.balance_expected <= ZERO:
        return cancel("NO_BALANCE_PAYMENT_REQUIRED")
    if not candidates:
        return ignore()

    base_date = min(value.date() for value in candidates)
    trigger_date = base_date + timedelta(days=3)
    activation_at = datetime.combine(trigger_date, time.min)
    context.update(
        {
            "base_date": base_date.isoformat(),
            "trigger_date": trigger_date.isoformat(),
            "eligible_completed_dates": sorted(value.isoformat() for value in candidates),
            "calendar_day_offset": 3,
        }
    )
    if now.date() < trigger_date:
        return ensure(
            UPCOMING,
            reason_code="PAYMENT_FOLLOW_UP_NOT_DUE",
            activation_at=activation_at,
            context=context,
        )
    return ensure(
        ACTION,
        reason_code="PAYMENT_BALANCE_OUTSTANDING",
        activation_at=activation_at,
        context=context,
    )


def _advance_waiting(pi, now, rule_context):
    assessment = assess_payment(pi)
    context = _payment_context(pi, assessment, stage="advance")
    expected = assessment.advance_expected
    received = assessment.advance_received or ZERO
    if (
        assessment.advance_percent is not None
        and assessment.advance_percent > ZERO
        and assessment.contract_total <= ZERO
        and getattr(pi, "advance_payment_amount", None) is None
    ):
        warnings = list(context.get("warnings") or [])
        if "CONTRACT_TOTAL_MISSING" not in warnings:
            warnings.append("CONTRACT_TOTAL_MISSING")
        context.update(
            {
                "warnings": warnings,
                "health_reason_code": "PAYMENT_CONTRACT_TOTAL_MISSING",
                "health_message": "已填写预付款比例，但订单合同总额无法计算，请补充有效产品金额或预付款应收金额",
            }
        )
        return ensure(
            UPCOMING,
            health=TaskHealth.EXCEPTION,
            reason_code="PAYMENT_CONTRACT_TOTAL_MISSING",
            context=context,
        )
    if expected is None or expected <= ZERO:
        if "PAYMENT_ADVANCE_WAITING" in rule_context.task_statuses:
            return cancel("NO_ADVANCE_PAYMENT_REQUIRED")
        return ignore()
    if received >= expected:
        return resolve("ADVANCE_PAYMENT_RECEIVED", context=context)
    context["waiting_on"] = WaitingOn.CUSTOMER
    return ensure(
        TaskStatus.WAITING,
        reason_code="ADVANCE_PAYMENT_OUTSTANDING",
        context=context,
        waiting_on=WaitingOn.CUSTOMER,
    )


def _settlement_legacy(pi, now, rule_context):
    fact = legacy_settlement_fact(pi.settlement_documents_required)
    if not fact.legacy_done:
        return ignore()
    return ensure(
        DONE,
        reason_code="LEGACY_DONE",
        legacy_done=True,
        context={
            "legacy_field": "settlement_documents_required",
            "legacy_value": fact.raw_value,
            "compatibility_note": (
                "旧字段无法区分预付款或尾款结汇文件；仅保留一条历史完成记录，完成时间未知"
            ),
        },
    )


def _settlement_stage(stage):
    def evaluate(pi, now, rule_context):
        fact = legacy_settlement_fact(pi.settlement_documents_required)
        if fact.required is False:
            return cancel("REQUIREMENT_REMOVED")
        if fact.legacy_done or fact.required is not True:
            return ignore()

        assessment = assess_payment(pi)
        context = _payment_context(pi, assessment, stage=stage)
        expected = (
            assessment.advance_expected if stage == "advance" else assessment.balance_expected
        )
        received_at = getattr(pi, f"{stage}_received_at")
        context["received_at"] = received_at.isoformat() if received_at else None
        if stage == "balance" and expected is not None and expected <= ZERO:
            return cancel("NO_BALANCE_PAYMENT_REQUIRED")
        if stage_received(pi, stage):
            return ensure(ACTION, reason_code=f"{stage.upper()}_PAYMENT_RECEIVED", context=context)
        return ensure(UPCOMING, reason_code=f"{stage.upper()}_PAYMENT_NOT_RECEIVED", context=context)

    return evaluate


def _telex_release(pi, now, rule_context):
    required = pi.telex_release_required
    if required is False:
        return cancel("REQUIREMENT_REMOVED")
    if required is not True:
        return ignore()

    assessment = assess_payment(pi)
    context = _payment_context(pi, assessment)
    context.update(
        {
            "legacy_field": "telex_release",
            "legacy_value": pi.telex_release,
            "action_target": "UPDATE_PAYMENT_INFO",
        }
    )
    if legacy_telex_done(pi.telex_release):
        return ensure(
            DONE,
            reason_code="LEGACY_DONE",
            legacy_done=True,
            context=context,
        )
    if assessment.fully_received:
        return ensure(ACTION, reason_code="PAYMENT_FULLY_RECEIVED", context=context)
    return ensure(UPCOMING, reason_code="PAYMENT_NOT_FULLY_RECEIVED", context=context)


PAYMENT_RULES = (
    RuleDefinition(
        "payment.advance_waiting",
        1,
        "PAYMENT_ADVANCE_WAITING",
        "等待客户支付预付款",
        CompletionMode.RULE_DATA,
        _advance_waiting,
    ),
    RuleDefinition(
        "payment.balance_follow_up",
        1,
        "PAYMENT_BALANCE_FOLLOWUP",
        "催客户付款",
        CompletionMode.RULE_DATA,
        _balance_follow_up,
        preserve_waiting=True,
    ),
    RuleDefinition(
        "settlement.document.legacy",
        1,
        "LEGACY_SETTLEMENT_DOCUMENT",
        "历史结汇文件已完成",
        CompletionMode.MANUAL,
        _settlement_legacy,
    ),
    RuleDefinition(
        "settlement.document",
        1,
        "SETTLEMENT_DOCUMENT_ADVANCE",
        "准备预付款结汇文件",
        CompletionMode.MANUAL,
        _settlement_stage("advance"),
        instance_key="advance",
    ),
    RuleDefinition(
        "settlement.document",
        1,
        "SETTLEMENT_DOCUMENT_BALANCE",
        "准备尾款结汇文件",
        CompletionMode.MANUAL,
        _settlement_stage("balance"),
        instance_key="balance",
    ),
    RuleDefinition(
        "document.telex_release",
        1,
        "DOCUMENT_TELEX_RELEASE",
        "取得/发送提单电放件",
        CompletionMode.MANUAL,
        _telex_release,
    ),
)
