"""Centralized, stable code allocation for V2 master records."""

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .models import db


class MasterCodeAllocationError(RuntimeError):
    pass


def generate_master_code(model, prefix):
    """Return the next code based on the largest valid code for one model."""
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{4,}})$")
    largest = 0
    for code in db.session.scalars(select(model.code)):
        match = pattern.fullmatch(code or "")
        if match:
            largest = max(largest, int(match.group(1)))
    return f"{prefix}{largest + 1:04d}"


def create_master_record(model, prefix, attributes, *, max_attempts=3):
    """Allocate and commit a master record, retrying only code collisions."""
    last_collision = None
    for _attempt in range(max_attempts):
        code = generate_master_code(model, prefix)
        row = model(code=code, **attributes)
        db.session.add(row)
        try:
            db.session.commit()
            return row
        except IntegrityError as exc:
            db.session.rollback()
            collision = db.session.scalar(select(model.id).where(model.code == code))
            if collision is None:
                raise
            last_collision = exc
    raise MasterCodeAllocationError(
        f"Could not allocate a unique {prefix} master code after {max_attempts} attempts."
    ) from last_collision
