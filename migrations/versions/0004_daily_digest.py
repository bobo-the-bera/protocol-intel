"""Persist digest progress and cycle outcomes across disposable scheduled runners."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE daily_digest_state (
      singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
      next_day date NOT NULL
    );
    CREATE TABLE monitor_cycles (
      id text PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT now(),
      completed_at timestamptz, status text NOT NULL DEFAULT 'RUNNING',
      result jsonb NOT NULL DEFAULT '{}'
    );
    CREATE INDEX monitor_cycles_time ON monitor_cycles(started_at);
    CREATE INDEX reports_time ON reports(created_at);
    CREATE INDEX checks_time ON checks(checked_at);
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; digest progress must not be discarded")
