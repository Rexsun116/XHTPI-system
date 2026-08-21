#!/usr/bin/env python3
"""Apply reminders and render Dashboard against an explicit disposable DB copy."""

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTANCE_DIR = (PROJECT_ROOT / "instance").resolve()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="Existing disposable SQLite copy")
    parser.add_argument(
        "--apply-copy",
        action="store_true",
        help="Required acknowledgement that the disposable copy will be mutated",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    target = args.database.expanduser().resolve()
    print(f"requested_database={target}", file=sys.stderr)
    if not args.apply_copy:
        raise SystemExit("Refusing simulation without --apply-copy.")
    if not target.exists() or target.is_dir():
        raise SystemExit("Refusing simulation: target must be an existing SQLite file.")
    if target.is_relative_to(INSTANCE_DIR):
        raise SystemExit("Refusing simulation against the real instance directory.")

    os.environ["XHTPI_DATABASE_URI"] = f"sqlite:///{target}"
    sys.path.insert(0, str(PROJECT_ROOT))

    from app import PI, User, app, db
    from reminders.dashboard import get_dashboard_tasks
    from reminders.engine import reconcile_order_tasks
    from task_models import OrderTask, TaskActivity

    with app.app_context():
        configured = Path(db.engine.url.database).expanduser().resolve()
        with db.engine.connect() as connection:
            actual = Path(connection.engine.url.database).expanduser().resolve()
        print(f"configured_database={configured}", file=sys.stderr)
        print(f"actual_orm_database={actual}", file=sys.stderr)
        if configured != target or actual != target:
            raise SystemExit("Refusing simulation: database path mismatch.")

        plans = []
        for pi in PI.query.order_by(PI.id).all():
            actions = reconcile_order_tasks(pi, apply=True)
            plans.append(
                {
                    "pi_id": pi.id,
                    "pi_no": pi.pi_no,
                    "actions": [action.as_dict() for action in actions],
                }
            )
        db.session.commit()

        dashboard = get_dashboard_tasks()
        groups = {
            key: [
                {
                    "task_id": task["id"],
                    "pi_no": task["pi_no"],
                    "task_code": task["task_code"],
                    "title": task["title"],
                    "status": task["status"],
                    "health": task["health"],
                }
                for task in dashboard[key]
            ]
            for key in ("exception", "action", "waiting", "upcoming", "done")
        }

        render = {"status_code": None, "contains_control_center": False}
        user = User.query.order_by(User.id).first()
        if user is not None:
            client = app.test_client()
            with client.session_transaction() as session:
                session["_user_id"] = str(user.id)
                session["_fresh"] = True
            response = client.get("/")
            html = response.get_data(as_text=True)
            render = {
                "status_code": response.status_code,
                "contains_control_center": "Order Control Center" in html,
            }

        print(
            json.dumps(
                {
                    "database": str(target),
                    "reconcile": plans,
                    "counts": {
                        "order_task": OrderTask.query.count(),
                        "task_activity": TaskActivity.query.count(),
                    },
                    "groups": groups,
                    "dashboard_render": render,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
