"""Small rule-definition types used by the Phase 2A reconcile engine."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from reminders.enums import CompletionMode, TaskHealth, TaskStatus


class RuleDecision:
    ENSURE = "ENSURE"
    RESOLVE = "RESOLVE"
    CANCEL = "CANCEL"
    IGNORE = "IGNORE"


@dataclass(frozen=True)
class RuleContext:
    facts: dict
    task_statuses: dict
    tasks: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RuleOutcome:
    decision: str
    status: str | None = None
    health: str = TaskHealth.NORMAL
    reason_code: str | None = None
    activation_at: datetime | None = None
    context: dict = field(default_factory=dict)
    legacy_done: bool = False
    completion_payload: dict | None = None
    waiting_on: str | None = None
    force_reactivate: bool = False
    refresh_done_context: bool = False
    completed_at: datetime | None = None


@dataclass(frozen=True)
class RuleDefinition:
    key: str
    version: int
    task_code: str
    title: str
    completion_mode: str
    evaluate: Callable
    description: str | None = None
    completion_schema: dict | None = None
    instance_key: str = "default"
    preserve_waiting: bool = False


def ignore():
    return RuleOutcome(RuleDecision.IGNORE)


def cancel(reason_code):
    return RuleOutcome(RuleDecision.CANCEL, reason_code=reason_code)


def resolve(reason_code, *, context=None, completion_payload=None):
    return RuleOutcome(
        RuleDecision.RESOLVE,
        status=TaskStatus.DONE,
        health=TaskHealth.NORMAL,
        reason_code=reason_code,
        context=context or {},
        completion_payload=completion_payload,
    )


def ensure(
    status,
    *,
    health=TaskHealth.NORMAL,
    reason_code=None,
    activation_at=None,
    context=None,
    legacy_done=False,
    completion_payload=None,
    waiting_on=None,
    force_reactivate=False,
    refresh_done_context=False,
    completed_at=None,
):
    return RuleOutcome(
        RuleDecision.ENSURE,
        status=status,
        health=health,
        reason_code=reason_code,
        activation_at=activation_at,
        context=context or {},
        legacy_done=legacy_done,
        completion_payload=completion_payload,
        waiting_on=waiting_on,
        force_reactivate=force_reactivate,
        refresh_done_context=refresh_done_context,
        completed_at=completed_at,
    )


MANUAL = CompletionMode.MANUAL
REQUIRED_INPUT = CompletionMode.MANUAL_REQUIRED_INPUT
UPCOMING = TaskStatus.UPCOMING
ACTION = TaskStatus.ACTION
DONE = TaskStatus.DONE
