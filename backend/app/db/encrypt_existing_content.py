"""Seal rows written before DATA_ENCRYPTION_KEY was set.

    python -m app.db.encrypt_existing_content            # count what would be sealed
    python -m app.db.encrypt_existing_content --apply    # seal it

Rows are read and written back through the ORM, whose column types do the sealing, in
batches that each commit on their own; running it again skips what is already sealed.
"""

from __future__ import annotations

import sys

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import sync_engine
from app.core.encryption import SEALED_PREFIX, cipher
from app.models.models import ChatMessage, ContractReview

BATCH = 200
TARGETS = (
    (ChatMessage, ("content",), f"content NOT LIKE '{SEALED_PREFIX}%'"),
    (
        ContractReview,
        ("contract_text", "result"),
        f"(contract_text NOT LIKE '{SEALED_PREFIX}%' OR jsonb_typeof(result) <> 'string')",
    ),
)


def seal_existing(session: Session, *, apply: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    for model, columns, plain in TARGETS:
        query = session.query(model).filter(text(plain)).order_by(model.id)
        counts[model.__tablename__] = query.count()
        if not apply:
            continue
        while True:
            rows = query.limit(BATCH).all()
            if not rows:
                break
            for row in rows:
                for column in columns:
                    flag_modified(row, column)
            session.commit()
    return counts


def main() -> None:
    apply = "--apply" in sys.argv
    if cipher() is None:
        raise SystemExit("DATA_ENCRYPTION_KEY is not set; nothing can be sealed.")
    with Session(sync_engine) as session:
        counts = seal_existing(session, apply=apply)
    verb = "sealed" if apply else "would seal (run with --apply)"
    for table, count in counts.items():
        print(f"{table}: {verb} {count} rows")


if __name__ == "__main__":
    main()
