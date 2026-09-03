from __future__ import annotations

import re
from pathlib import Path

from alembic import op


SQL_DIR = Path(__file__).resolve().parent / "sql"
GO_LINE = re.compile(r"^\s*GO\s*(?:--.*)?$", re.IGNORECASE | re.MULTILINE)


def run_sql_file(file_name: str) -> None:
    sql_path = SQL_DIR / file_name
    sql_text = sql_path.read_text(encoding="utf-8-sig")
    connection = op.get_bind()
    for batch in GO_LINE.split(sql_text):
        statement = batch.strip()
        if statement:
            connection.exec_driver_sql(statement)


def irreversible_downgrade() -> None:
    raise RuntimeError(
        "Automatic destructive downgrade is disabled. Restore a verified backup "
        "or apply an approved forward-fix migration."
    )
