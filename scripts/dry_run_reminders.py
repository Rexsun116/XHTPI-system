#!/usr/bin/env python3
"""Plan AUTO reminder changes against an explicit non-production SQLite copy."""

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTANCE_DIR = (PROJECT_ROOT / "instance").resolve()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--pi", type=int, help="Limit the dry-run to one PI id")
    return parser.parse_args()


def main():
    args = parse_args()
    target = args.database.expanduser().resolve()
    print(f"requested_database={target}", file=sys.stderr)
    if not target.exists() or target.is_dir():
        raise SystemExit("Refusing dry-run: target must be an existing SQLite file.")
    if target.is_relative_to(INSTANCE_DIR):
        raise SystemExit(
            "Refusing dry-run against the real instance directory; use a verified file copy."
        )

    os.environ["XHTPI_DATABASE_URI"] = f"sqlite:///{target}"
    sys.path.insert(0, str(PROJECT_ROOT))

    from app import PI, app, db
    from reminders.engine import reconcile_order_tasks
    from reminders.payment import assess_payment
    from reminders.freight import money_text
    from reminders.rules.freight import freight_trigger

    with app.app_context():
        configured = Path(db.engine.url.database).expanduser().resolve()
        print(f"configured_database={configured}", file=sys.stderr)
        if configured != target:
            raise SystemExit("Refusing dry-run: configured database path mismatch.")
        query = PI.query
        if args.pi is not None:
            query = query.filter_by(id=args.pi)
        orders = []
        for pi in query.order_by(PI.id).all():
            actions = reconcile_order_tasks(pi, apply=False)
            payment = assess_payment(pi)
            action_dicts = [action.as_dict() for action in actions]
            trigger = freight_trigger(pi)
            freight_codes = {
                "FREIGHT_USD_AMOUNT_CAPTURE",
                "FREIGHT_USD_AMOUNT_CONFIRM",
                "FREIGHT_CNY_AMOUNT_CAPTURE",
                "FREIGHT_CNY_AMOUNT_CONFIRM",
                "LEGACY_FREIGHT_BILL_CONFIRM",
                "FREIGHT_INVOICE_ISSUED",
                "FREIGHT_PAYMENT_CONFIRM",
            }
            freight_actions = [
                action for action in actions if action.task_code in freight_codes
            ]
            orders.append(
                {
                    "pi_id": pi.id,
                    "pi_no": pi.pi_no,
                    "payment_plan": payment.context(),
                    "payment_rules": {
                        "PAYMENT_ADVANCE_WAITING": [a for a in action_dicts if a["task_code"] == "PAYMENT_ADVANCE_WAITING"],
                        "PAYMENT_EMAIL": [a for a in action_dicts if a["task_code"] == "PAYMENT_EMAIL"],
                        "PAYMENT_BALANCE_FOLLOWUP": [a for a in action_dicts if a["task_code"] == "PAYMENT_BALANCE_FOLLOWUP"],
                        "SETTLEMENT_DOCUMENT_ADVANCE": [a for a in action_dicts if a["task_code"] == "SETTLEMENT_DOCUMENT_ADVANCE"],
                        "SETTLEMENT_DOCUMENT_BALANCE": [a for a in action_dicts if a["task_code"] == "SETTLEMENT_DOCUMENT_BALANCE"],
                        "DOCUMENT_TELEX_RELEASE": [a for a in action_dicts if a["task_code"] == "DOCUMENT_TELEX_RELEASE"],
                    },
                    "freight_settlement": {
                        "actual_departure_date": pi.actual_departure_date.isoformat() if pi.actual_departure_date else None,
                        "trigger_date": trigger[0].isoformat() if trigger else None,
                        "usd": {
                            "required": pi.freight_usd_bill_required,
                            "amount": money_text(pi.freight_usd_amount),
                            "confirmed": pi.freight_usd_confirmed,
                        },
                        "cny": {
                            "required": pi.freight_cny_bill_required,
                            "amount": money_text(pi.freight_cny_amount),
                            "confirmed": pi.freight_cny_confirmed,
                        },
                        "invoice_issued": pi.freight_invoice_issued,
                        "payment_status": pi.freight_payment_status,
                        "paid_at": pi.freight_paid_at.isoformat() if pi.freight_paid_at else None,
                        "legacy": {
                            "amount": money_text(pi.freight_invoice_amount),
                            "bill_confirmed": pi.freight_invoice_confirmed,
                        },
                        "warnings": sorted(
                            {
                                warning
                                for action in freight_actions
                                for warning in action.outcome.context.get("warnings", [])
                            }
                        ),
                        "exceptions": [
                            action.as_dict()
                            for action in freight_actions
                            if action.health == "EXCEPTION"
                        ],
                        "actions": [action.as_dict() for action in freight_actions],
                    },
                    "actions": action_dicts,
                }
            )
        print(json.dumps({"mode": "dry-run", "orders": orders}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
