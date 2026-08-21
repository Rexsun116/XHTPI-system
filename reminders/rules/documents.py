"""Phase 2A document and payment-email AUTO rule definitions."""

from datetime import datetime, time, timedelta

from reminders.definitions import (
    ACTION,
    DONE,
    MANUAL,
    REQUIRED_INPUT,
    UPCOMING,
    RuleDefinition,
    cancel,
    ensure,
    ignore,
)
from reminders.enums import TaskHealth, TaskStatus
from reminders.rules.shipping import loading_moment


STATUS_ORDER = ("新建", "待发运", "已发运", "已到港", "已完成")
MAIL_COMPLETION_SCHEMA = {
    "fields": [
        {
            "key": "tracking_number",
            "label": "Tracking Number",
            "type": "text",
            "required": True,
        },
        {"key": "carrier", "label": "物流公司", "type": "text", "required": False},
        {"key": "note", "label": "备注", "type": "text", "required": False},
    ]
}


def _at_start_of_day(value):
    return datetime.combine(value, time.min) if value else None


def _status_at_least(pi, status):
    try:
        return STATUS_ORDER.index(pi.status) >= STATUS_ORDER.index(status)
    except ValueError:
        return False


def _fact_context(fact, **values):
    context = {
        "required_source": fact.source,
        "legacy_value": fact.raw_value,
    }
    context.update(values)
    return context


def _required_outcome(fact):
    if fact.required is False:
        return cancel("REQUIREMENT_REMOVED")
    if fact.required is None:
        return ignore()
    if fact.legacy_done:
        return ensure(
            DONE,
            reason_code="LEGACY_DONE",
            context=_fact_context(fact),
            legacy_done=True,
        )
    return None


def _container_date_rule(fact_key, label, offset_days=0, extra_context=None):
    def evaluate(pi, now, rule_context):
        fact = rule_context.facts[fact_key]
        terminal = _required_outcome(fact)
        if terminal:
            return terminal
        loading_at, loading_precision = loading_moment(pi)
        if loading_at is None:
            return ensure(
                UPCOMING,
                health=TaskHealth.EXCEPTION,
                reason_code="MISSING_TRIGGER_DATE",
                context=_fact_context(
                    fact,
                    health_reason_code="MISSING_TRIGGER_DATE",
                    health_message=f"{label}已标记为需要，但尚未填写装柜日期",
                    **(extra_context or {}),
                ),
            )
        if offset_days:
            trigger_date = loading_at.date() + timedelta(days=offset_days)
            trigger_at = _at_start_of_day(trigger_date)
            reached = now.date() >= trigger_date
        else:
            trigger_date = loading_at.date()
            trigger_at = loading_at
            reached = now >= trigger_at
        status = ACTION if reached else UPCOMING
        return ensure(
            status,
            activation_at=trigger_at,
            context=_fact_context(
                fact,
                trigger_date=trigger_date.isoformat(),
                trigger_at=trigger_at.isoformat(),
                loading_precision=loading_precision,
                **(extra_context or {}),
            ),
        )

    return evaluate


def _coc(pi, _now, rule_context):
    fact = rule_context.facts["COC"]
    terminal = _required_outcome(fact)
    if terminal:
        return terminal
    status = ACTION if _status_at_least(pi, "待发运") else UPCOMING
    return ensure(status, context=_fact_context(fact, trigger_status="待发运"))


def _shipped_or_etd_rule(fact_key):
    def evaluate(pi, now, rule_context):
        fact = rule_context.facts[fact_key]
        terminal = _required_outcome(fact)
        if terminal:
            return terminal
        reached = _status_at_least(pi, "已发运") or (pi.etd is not None and now.date() >= pi.etd)
        return ensure(
            ACTION if reached else UPCOMING,
            activation_at=_at_start_of_day(pi.etd),
            context=_fact_context(
                fact,
                trigger_status="已发运",
                etd=pi.etd.isoformat() if pi.etd else None,
            ),
        )

    return evaluate


def _mail(pi, _now, rule_context):
    fact = rule_context.facts["ORIGINAL_DOCUMENTS_MAIL"]
    terminal = _required_outcome(fact)
    if terminal and fact.legacy_done:
        payload = {"tracking_number": pi.tracking_number} if pi.tracking_number else None
        return ensure(
            DONE,
            reason_code="LEGACY_DONE",
            context=_fact_context(fact, legacy_tracking_number=pi.tracking_number),
            legacy_done=True,
            completion_payload=payload,
        )
    if terminal:
        return terminal

    originals = {
        "DOCUMENT_ORIGINAL_BL": rule_context.facts["ORIGINAL_BL"],
        "DOCUMENT_INSURANCE_ORIGINAL": rule_context.facts["INSURANCE_ORIGINAL"],
    }
    required_codes = [code for code, original_fact in originals.items() if original_fact.required is True]
    if not required_codes:
        return cancel("NO_REQUIRED_ORIGINALS")
    all_done = all(rule_context.task_statuses.get(code) == TaskStatus.DONE for code in required_codes)
    return ensure(
        ACTION if all_done else UPCOMING,
        context=_fact_context(fact, required_original_task_codes=required_codes),
    )


def _payment_email(pi, _now, _rule_context):
    if _status_at_least(pi, "已发运"):
        return ensure(ACTION, context={"trigger_status": "已发运"})
    return cancel("NOT_APPLICABLE")


DOCUMENT_RULES = (
    RuleDefinition("document.coo", 2, "DOCUMENT_COO", "办理 COO", MANUAL, _container_date_rule("COO", "COO")),
    RuleDefinition(
        "document.apta",
        2,
        "DOCUMENT_APTA",
        "办理 APTA",
        MANUAL,
        _container_date_rule(
            "APTA",
            "APTA",
            extra_context={"special_notice": "APTA 日期需在提单开船日期三日内"},
        ),
        description="APTA 日期需在提单开船日期三日内",
    ),
    RuleDefinition(
        "document.export_license",
        2,
        "DOCUMENT_EXPORT_LICENSE",
        "办理出口许可证",
        MANUAL,
        _container_date_rule("EXPORT_LICENSE", "出口许可证", offset_days=-5),
    ),
    RuleDefinition(
        "document.customs",
        2,
        "DOCUMENT_CUSTOMS",
        "准备报关文件",
        MANUAL,
        _container_date_rule("CUSTOMS", "报关文件", offset_days=-5),
    ),
    RuleDefinition("document.coc", 1, "DOCUMENT_COC", "办理 COC", MANUAL, _coc),
    RuleDefinition(
        "document.original_bl",
        1,
        "DOCUMENT_ORIGINAL_BL",
        "取得/处理提单原件",
        MANUAL,
        _shipped_or_etd_rule("ORIGINAL_BL"),
    ),
    RuleDefinition(
        "document.obd_bl",
        1,
        "DOCUMENT_OBD_BL",
        "取得 OBD 提单电子版",
        MANUAL,
        _shipped_or_etd_rule("OBD_BL"),
    ),
    RuleDefinition(
        "document.coa",
        2,
        "DOCUMENT_COA",
        "取得并准备 COA 质检单",
        MANUAL,
        _container_date_rule("COA", "COA"),
    ),
    RuleDefinition(
        "document.insurance_original",
        1,
        "DOCUMENT_INSURANCE_ORIGINAL",
        "取得保单原件",
        MANUAL,
        _shipped_or_etd_rule("INSURANCE_ORIGINAL"),
    ),
    RuleDefinition(
        "document.insurance_electronic",
        1,
        "DOCUMENT_INSURANCE_ELECTRONIC",
        "取得保单电子版",
        MANUAL,
        _shipped_or_etd_rule("INSURANCE_ELECTRONIC"),
    ),
    RuleDefinition(
        "document.original_documents_mail",
        1,
        "DOCUMENT_ORIGINALS_MAIL",
        "邮寄文件原件",
        REQUIRED_INPUT,
        _mail,
        completion_schema=MAIL_COMPLETION_SCHEMA,
    ),
    RuleDefinition(
        "payment.email_documents",
        1,
        "PAYMENT_EMAIL",
        "EMAIL发送付款文件给客户并请款",
        MANUAL,
        _payment_email,
    ),
)
