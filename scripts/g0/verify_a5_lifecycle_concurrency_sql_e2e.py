from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.infrastructure.database import check_database, get_engine

_LOCK_SQL = text(
    "SET NOCOUNT ON; "
    "DECLARE @result int; "
    "EXEC @result=sys.sp_getapplock "
    "@Resource=:resource,@LockMode='Exclusive',"
    "@LockOwner='Transaction',@LockTimeout=:timeout_ms; "
    "SELECT @result"
)


def main() -> None:
    if not os.getenv("TMS_DATABASE_URL"):
        raise RuntimeError("TMS_DATABASE_URL is required")
    identity = check_database()
    if identity["database"] != "TMS_G0_DEV":
        raise RuntimeError("This concurrency verification is restricted to TMS_G0_DEV")
    if identity["schema_revision"] != "sql2014_0024":
        raise RuntimeError("sql2014_0024 is required")

    engine = get_engine()
    resource = "TMS:LIFECYCLE:DATASET:46"
    started = threading.Event()
    outcome: dict[str, object] = {}

    with engine.connect() as first:
        first_transaction = first.begin()
        first_result = int(
            first.execute(
                _LOCK_SQL,
                {"resource": resource, "timeout_ms": 0},
            ).scalar_one()
        )
        if first_result < 0:
            raise RuntimeError("First lifecycle AppLock could not be acquired")

        def acquire_second() -> None:
            try:
                with engine.connect() as second:
                    second_transaction = second.begin()
                    started.set()
                    began = time.perf_counter()
                    result = int(
                        second.execute(
                            _LOCK_SQL,
                            {"resource": resource, "timeout_ms": 5000},
                        ).scalar_one()
                    )
                    outcome["elapsed"] = time.perf_counter() - began
                    outcome["result"] = result
                    second_transaction.rollback()
            except SQLAlchemyError as exc:  # surfaced on the main test thread
                outcome["error"] = exc

        thread = threading.Thread(target=acquire_second, daemon=True)
        thread.start()
        if not started.wait(timeout=2):
            first_transaction.rollback()
            raise RuntimeError("Second lifecycle lock session did not start")
        time.sleep(0.75)
        if not thread.is_alive():
            first_transaction.rollback()
            raise RuntimeError("Second session bypassed the Dataset lifecycle lock")
        first_transaction.rollback()
        thread.join(timeout=8)

    if thread.is_alive():
        raise RuntimeError("Second lifecycle lock session did not finish")
    if "error" in outcome:
        raise RuntimeError("Second lifecycle lock session failed") from outcome["error"]
    elapsed = float(outcome.get("elapsed", 0.0))
    result = int(outcome.get("result", -999))
    if result < 0 or elapsed < 0.65 or elapsed > 5.5:
        raise RuntimeError(
            f"Unexpected lifecycle lock result: result={result}, elapsed={elapsed:.3f}"
        )
    print(
        "lifecycle_applock_concurrency=PASS "
        f"second_session_wait_seconds={elapsed:.3f} data_mutation=false"
    )


if __name__ == "__main__":
    main()
