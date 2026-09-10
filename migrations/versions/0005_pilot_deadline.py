"""A durable pilot deadline survives retries, redeployments and new workflow runs."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE pilot_window (
      singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
      started_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
      CHECK(ends_at > started_at)
    );
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; never reset the pilot deadline implicitly")
