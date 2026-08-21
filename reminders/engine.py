"""Idempotent AUTO task planning and reconcile execution."""

from dataclasses import dataclass
from datetime import datetime

from reminders.adapters import document_facts
from reminders.definitions import RuleContext, RuleDecision
from reminders.enums import CompletionMode, TaskHealth, TaskSource, TaskStatus
from reminders.rules.documents import DOCUMENT_RULES
from reminders.rules.freight import FREIGHT_RULES
from reminders.rules.payments import PAYMENT_RULES
from reminders.rules.shipping import SHIPPING_RULES
from reminders.task_service import (
    cancel_auto_task,
    create_auto_task,
    legacy_complete_auto_task,
    reactivate_auto_task,
    reactivate_completed_auto_task,
    reactivate_rule_data_task,
    refresh_completed_auto_task_context,
    resolve_from_data,
    update_auto_task,
)
from task_models import OrderTask, utc_now


class ReconcileActionType:
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    CANCEL = "CANCEL"
    REACTIVATE = "REACTIVATE"
    LEGACY_COMPLETE = "LEGACY_COMPLETE"
    AUTO_RESOLVE = "AUTO_RESOLVE"
    RULE_REACTIVATE = "RULE_REACTIVATE"
    DEFER = "DEFER"
    CONFIRMATION_REACTIVATE = "CONFIRMATION_REACTIVATE"
    UPDATE_DONE_CONTEXT = "UPDATE_DONE_CONTEXT"


@dataclass(frozen=True)
class ReconcileAction:
    action: str
    pi_id: int
    rule_key: str
    task_code: str
    title: str
    status: str | None
    health: str | None
    reason_code: str | None
    existing_task_id: int | None
    dedupe_key: str
    definition: object
    outcome: object

    def as_dict(self):
        return {
            "action": self.action,
            "pi_id": self.pi_id,
            "rule_key": self.rule_key,
            "task_code": self.task_code,
            "title": self.title,
            "status": self.status,
            "health": self.health,
            "reason_code": self.reason_code,
            "existing_task_id": self.existing_task_id,
            "dedupe_key": self.dedupe_key,
        }


def task_dedupe_key(pi_id, rule_key, instance_key="default"):
    return f"ORDER:{pi_id}:{rule_key}:{instance_key}"


def _effective_existing_status(existing, outcome):
    if existing is None:
        return outcome.status
    if existing.status == TaskStatus.DONE:
        return TaskStatus.DONE
    if outcome.legacy_done:
        return TaskStatus.DONE
    if existing.status == TaskStatus.CANCELLED:
        return outcome.status
    if existing.status == TaskStatus.UPCOMING and outcome.status == TaskStatus.ACTION:
        return TaskStatus.ACTION
    return existing.status


def _effective_health(existing, outcome):
    if outcome.health == TaskHealth.EXCEPTION:
        return TaskHealth.EXCEPTION
    if existing is not None and existing.health == TaskHealth.OVERDUE:
        return TaskHealth.OVERDUE
    return TaskHealth.NORMAL


def _needs_update(existing, definition, outcome, target_status, target_health):
    return any(
        (
            existing.status != target_status,
            existing.health != target_health,
            existing.title != definition.title,
            existing.description != definition.description,
            existing.completion_mode != definition.completion_mode,
            existing.completion_schema != definition.completion_schema,
            existing.context_payload != outcome.context,
            existing.activation_at != outcome.activation_at,
            (
                existing.waiting_on != outcome.waiting_on
                and not (definition.preserve_waiting and target_status == TaskStatus.WAITING)
            ),
            existing.rule_version != definition.version,
        )
    )


def plan_order_tasks(pi, now=None, *, rule_definitions=None):
    """Return a no-write reconcile plan for one PI."""
    now = now or datetime.now()
    existing_tasks = OrderTask.query.filter_by(pi_id=pi.id, source=TaskSource.AUTO).all()
    existing_by_dedupe = {task.dedupe_key: task for task in existing_tasks if task.dedupe_key}
    task_statuses = {task.task_code: task.status for task in existing_tasks}
    tasks_by_code = {task.task_code: task for task in existing_tasks}
    facts = document_facts(pi)
    actions = []

    rules = rule_definitions or (DOCUMENT_RULES + SHIPPING_RULES + PAYMENT_RULES + FREIGHT_RULES)
    for definition in rules:
        dedupe_key = task_dedupe_key(pi.id, definition.key, definition.instance_key)
        existing = existing_by_dedupe.get(dedupe_key)
        outcome = definition.evaluate(
            pi,
            now,
            RuleContext(facts=facts, task_statuses=task_statuses, tasks=tasks_by_code),
        )

        if outcome.decision == RuleDecision.IGNORE:
            continue
        if outcome.decision == RuleDecision.RESOLVE:
            if existing is not None and existing.status in TaskStatus.ACTIVE:
                actions.append(
                    ReconcileAction(
                        ReconcileActionType.AUTO_RESOLVE,
                        pi.id,
                        definition.key,
                        definition.task_code,
                        definition.title,
                        TaskStatus.DONE,
                        TaskHealth.NORMAL,
                        outcome.reason_code,
                        existing.id,
                        dedupe_key,
                        definition,
                        outcome,
                    )
                )
                task_statuses[definition.task_code] = TaskStatus.DONE
            continue
        if outcome.decision == RuleDecision.CANCEL:
            if existing is not None and existing.status in TaskStatus.ACTIVE:
                actions.append(
                    ReconcileAction(
                        ReconcileActionType.CANCEL,
                        pi.id,
                        definition.key,
                        definition.task_code,
                        definition.title,
                        TaskStatus.CANCELLED,
                        TaskHealth.NORMAL,
                        outcome.reason_code,
                        existing.id,
                        dedupe_key,
                        definition,
                        outcome,
                    )
                )
                task_statuses[definition.task_code] = TaskStatus.CANCELLED
            continue

        if existing is None:
            action_type = (
                ReconcileActionType.LEGACY_COMPLETE
                if outcome.legacy_done
                else ReconcileActionType.CREATE
            )
            actions.append(
                ReconcileAction(
                    action_type,
                    pi.id,
                    definition.key,
                    definition.task_code,
                    definition.title,
                    outcome.status,
                    outcome.health,
                    outcome.reason_code,
                    None,
                    dedupe_key,
                    definition,
                    outcome,
                )
            )
            task_statuses[definition.task_code] = outcome.status
            continue

        if existing.status == TaskStatus.DONE:
            if outcome.force_reactivate:
                actions.append(
                    ReconcileAction(
                        ReconcileActionType.CONFIRMATION_REACTIVATE,
                        pi.id,
                        definition.key,
                        definition.task_code,
                        definition.title,
                        outcome.status,
                        outcome.health,
                        outcome.reason_code or "CONFIRMED_FACT_CHANGED",
                        existing.id,
                        dedupe_key,
                        definition,
                        outcome,
                    )
                )
                task_statuses[definition.task_code] = outcome.status
            elif outcome.refresh_done_context and existing.context_payload != outcome.context:
                actions.append(
                    ReconcileAction(
                        ReconcileActionType.UPDATE_DONE_CONTEXT,
                        pi.id,
                        definition.key,
                        definition.task_code,
                        definition.title,
                        TaskStatus.DONE,
                        existing.health,
                        outcome.reason_code or "DONE_CONTEXT_REFRESHED",
                        existing.id,
                        dedupe_key,
                        definition,
                        outcome,
                    )
                )
                task_statuses[definition.task_code] = TaskStatus.DONE
            elif definition.completion_mode == CompletionMode.RULE_DATA:
                actions.append(
                    ReconcileAction(
                        ReconcileActionType.RULE_REACTIVATE,
                        pi.id,
                        definition.key,
                        definition.task_code,
                        definition.title,
                        outcome.status,
                        outcome.health,
                        "RULE_REACTIVATED",
                        existing.id,
                        dedupe_key,
                        definition,
                        outcome,
                    )
                )
                task_statuses[definition.task_code] = outcome.status
            else:
                task_statuses[definition.task_code] = TaskStatus.DONE
            continue
        if outcome.legacy_done:
            actions.append(
                ReconcileAction(
                    ReconcileActionType.LEGACY_COMPLETE,
                    pi.id,
                    definition.key,
                    definition.task_code,
                    definition.title,
                    TaskStatus.DONE,
                    TaskHealth.NORMAL,
                    "LEGACY_DONE",
                    existing.id,
                    dedupe_key,
                    definition,
                    outcome,
                )
            )
            task_statuses[definition.task_code] = TaskStatus.DONE
            continue
        if existing.status == TaskStatus.CANCELLED:
            actions.append(
                ReconcileAction(
                    ReconcileActionType.REACTIVATE,
                    pi.id,
                    definition.key,
                    definition.task_code,
                    definition.title,
                    outcome.status,
                    outcome.health,
                    "REQUIREMENT_RESTORED",
                    existing.id,
                    dedupe_key,
                    definition,
                    outcome,
                )
            )
            task_statuses[definition.task_code] = outcome.status
            continue

        if definition.completion_mode == CompletionMode.RULE_DATA:
            if definition.preserve_waiting and existing.status == TaskStatus.WAITING:
                follow_up = existing.next_follow_up_at
                if follow_up is None or follow_up > now:
                    target_status = TaskStatus.WAITING
                    target_health = TaskHealth.NORMAL
                else:
                    target_status = TaskStatus.ACTION
                    target_health = (
                        TaskHealth.OVERDUE if follow_up < now else TaskHealth.NORMAL
                    )
            else:
                target_status = outcome.status
                target_health = outcome.health
        else:
            target_status = _effective_existing_status(existing, outcome)
            target_health = _effective_health(existing, outcome)
        if _needs_update(existing, definition, outcome, target_status, target_health):
            action_type = (
                ReconcileActionType.DEFER
                if definition.completion_mode == CompletionMode.RULE_DATA
                and target_status == TaskStatus.UPCOMING
                and (existing.status != target_status or existing.health != target_health)
                else ReconcileActionType.UPDATE
            )
            update_reason = outcome.reason_code or "RULE_RECONCILED"
            if definition.preserve_waiting and existing.status == TaskStatus.WAITING:
                if target_status == TaskStatus.WAITING:
                    update_reason = "FOLLOW_UP_WAITING"
                elif target_health == TaskHealth.OVERDUE:
                    update_reason = "FOLLOW_UP_OVERDUE"
                else:
                    update_reason = "FOLLOW_UP_DUE"
            actions.append(
                ReconcileAction(
                    action_type,
                    pi.id,
                    definition.key,
                    definition.task_code,
                    definition.title,
                    target_status,
                    target_health,
                    (
                        "RULE_DEFERRED"
                        if action_type == ReconcileActionType.DEFER
                        else update_reason
                    ),
                    existing.id,
                    dedupe_key,
                    definition,
                    outcome,
                )
            )
        task_statuses[definition.task_code] = target_status

    return actions


def _apply_action(action, now):
    definition = action.definition
    outcome = action.outcome
    common = {
        "title": definition.title,
        "description": definition.description,
        "completion_mode": definition.completion_mode,
        "completion_schema": definition.completion_schema,
        "context_payload": outcome.context,
        "activation_at": outcome.activation_at,
        "waiting_on": outcome.waiting_on,
        "rule_version": definition.version,
        "now": now,
    }
    if action.action in (ReconcileActionType.CREATE, ReconcileActionType.LEGACY_COMPLETE) and action.existing_task_id is None:
        return create_auto_task(
            pi_id=action.pi_id,
            task_code=definition.task_code,
            rule_key=definition.key,
            instance_key=definition.instance_key,
            dedupe_key=action.dedupe_key,
            status=action.status,
            health=action.health,
            resolution_code="LEGACY_DONE" if action.action == ReconcileActionType.LEGACY_COMPLETE else None,
            completion_payload=outcome.completion_payload,
            completed_at=outcome.completed_at,
            **common,
        )
    task = OrderTask.query.session.get(OrderTask, action.existing_task_id)
    if action.action == ReconcileActionType.CANCEL:
        return cancel_auto_task(task, reason_code=action.reason_code, now=now)
    if action.action == ReconcileActionType.REACTIVATE:
        return reactivate_auto_task(task, status=action.status, health=action.health, **common)
    if action.action == ReconcileActionType.RULE_REACTIVATE:
        return reactivate_rule_data_task(task, status=action.status, health=action.health, **common)
    if action.action == ReconcileActionType.CONFIRMATION_REACTIVATE:
        return reactivate_completed_auto_task(
            task,
            status=action.status,
            health=action.health,
            reason_code=action.reason_code,
            **common,
        )
    if action.action == ReconcileActionType.UPDATE_DONE_CONTEXT:
        return refresh_completed_auto_task_context(
            task,
            context_payload=outcome.context,
            reason_code=action.reason_code,
            now=now,
        )
    if action.action == ReconcileActionType.AUTO_RESOLVE:
        return resolve_from_data(
            task,
            reason_code=action.reason_code,
            context_payload=outcome.context,
            payload=outcome.completion_payload,
            now=now,
        )
    if action.action == ReconcileActionType.LEGACY_COMPLETE:
        return legacy_complete_auto_task(
            task,
            context_payload=outcome.context,
            completion_payload=outcome.completion_payload,
            now=now,
        )
    if action.action in (ReconcileActionType.UPDATE, ReconcileActionType.DEFER):
        return update_auto_task(
            task,
            status=action.status,
            health=action.health,
            reason_code=action.reason_code,
            **common,
        )
    raise ValueError(f"Unsupported reconcile action: {action.action}")


def reconcile_order_tasks(pi, now=None, *, apply=False, rule_definitions=None):
    """Plan one PI and optionally apply the exact plan in the caller's transaction."""
    evaluation_now = now or datetime.now()
    persistence_now = now or utc_now()
    actions = plan_order_tasks(pi, now=evaluation_now, rule_definitions=rule_definitions)
    if apply:
        for action in actions:
            _apply_action(action, persistence_now)
    return actions


def reconcile_order_tasks_for_pi(pi, now=None):
    """Apply all unified rules for one PI inside the caller's transaction."""
    if pi is None or pi.id is None:
        raise ValueError("A persisted PI is required for targeted reconcile.")
    return reconcile_order_tasks(pi, now=now, apply=True)
