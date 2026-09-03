from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

database_url = os.environ.get("TMS_DATABASE_URL")
if not database_url:
    raise RuntimeError("Set TMS_DATABASE_URL before running Alembic.")
config.set_main_option("sqlalchemy.url", database_url)

target_metadata = None


def run_migrations_offline() -> None:
    raise RuntimeError(
        "Offline SQL generation is not supported: TMS revisions execute native "
        "SQL Server batches split on GO. Run Alembic in online mode against an "
        "approved SQL Server database."
    )


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
