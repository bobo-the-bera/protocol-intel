"""Run explicit migrations separately from worker startup."""

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(url, poolclass=NullPool, hide_parameters=True)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(617429103)")
        context.run_migrations()
