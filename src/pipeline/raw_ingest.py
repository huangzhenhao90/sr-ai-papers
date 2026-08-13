"""Transaction-safe persistence helpers for source records."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from src.db.schema import RawRecord


class RawRecordWriteError(RuntimeError):
    """Raised when a raw record fails for a reason other than duplication."""


def _is_duplicate(session: Session, source: str, source_record_id: str) -> bool:
    existing_id = session.execute(
        select(RawRecord.id).where(
            RawRecord.source == source,
            RawRecord.source_record_id == source_record_id,
        )
    ).scalar_one_or_none()
    return existing_id is not None


def insert_raw_record(
    session: Session,
    *,
    run_id: int,
    source: str,
    source_record_id: str,
    payload: dict[str, Any],
) -> bool:
    """Insert one source record without endangering the surrounding transaction.

    Returns ``False`` only when the source record already exists. Every record is
    flushed inside a savepoint so a duplicate cannot roll back earlier inserts.
    """
    try:
        with session.begin_nested():
            session.add(
                RawRecord(
                    run_id=run_id,
                    source=source,
                    source_record_id=source_record_id,
                    payload=payload,
                )
            )
            session.flush()
    except IntegrityError as exc:
        if _is_duplicate(session, source, source_record_id):
            return False
        raise RawRecordWriteError(
            f"Failed to persist raw record: source={source} "
            f"record_id={source_record_id}"
        ) from exc
    except SQLAlchemyError as exc:
        raise RawRecordWriteError(
            f"Failed to persist raw record: source={source} "
            f"record_id={source_record_id}"
        ) from exc
    return True


def count_run_records(session: Session, run_id: int) -> int:
    return int(
        session.execute(
            select(func.count(RawRecord.id)).where(RawRecord.run_id == run_id)
        ).scalar_one()
    )


def assert_run_record_count(session: Session, run_id: int, *, expected: int) -> None:
    persisted = count_run_records(session, run_id)
    if persisted != expected:
        raise RuntimeError(
            f"Source run {run_id} persisted {persisted} raw records; expected {expected}"
        )
