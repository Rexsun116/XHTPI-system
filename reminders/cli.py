"""Explicit dry-run-first CLI entry points for AUTO task reconciliation."""

import json
from pathlib import Path

import click
from flask import current_app
from flask.cli import AppGroup

from app import PI, db
from reminders.engine import reconcile_order_tasks


reminders_cli = AppGroup("reminders")


def _guard_production_apply(apply, allow_production):
    if not apply:
        return
    database = db.engine.url.database
    if not database:
        return
    configured = Path(database).expanduser().resolve()
    instance_dir = Path(current_app.instance_path).resolve()
    if configured.is_relative_to(instance_dir) and not allow_production:
        raise click.ClickException(
            "Refusing reconcile apply in the real instance directory without "
            "--allow-production. Run a verified file-copy dry-run first."
        )


def _report(pi, actions):
    return {
        "pi_id": pi.id,
        "pi_no": pi.pi_no,
        "actions": [action.as_dict() for action in actions],
    }


@reminders_cli.command("reconcile")
@click.option("--pi", "pi_id", type=int, required=True, help="PI database ID")
@click.option("--apply", is_flag=True, help="Apply the plan; default is dry-run only")
@click.option("--allow-production", is_flag=True, help="Explicitly allow writes in the instance directory")
def reconcile_command(pi_id, apply, allow_production):
    _guard_production_apply(apply, allow_production)
    pi = db.session.get(PI, pi_id)
    if pi is None:
        raise click.ClickException("PI does not exist.")
    actions = reconcile_order_tasks(pi, apply=apply)
    if apply:
        db.session.commit()
    click.echo(json.dumps({"mode": "apply" if apply else "dry-run", **_report(pi, actions)}, ensure_ascii=False, indent=2))


@reminders_cli.command("reconcile-all")
@click.option("--apply", is_flag=True, help="Apply all plans; default is dry-run only")
@click.option("--allow-production", is_flag=True, help="Explicitly allow writes in the instance directory")
def reconcile_all_command(apply, allow_production):
    _guard_production_apply(apply, allow_production)
    reports = []
    for pi in PI.query.order_by(PI.id).all():
        actions = reconcile_order_tasks(pi, apply=apply)
        reports.append(_report(pi, actions))
    if apply:
        db.session.commit()
    click.echo(json.dumps({"mode": "apply" if apply else "dry-run", "orders": reports}, ensure_ascii=False, indent=2))
