"""Persist paid submissions before parsing so retries cannot silently rebill them."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE model_calls (
      job_id text NOT NULL REFERENCES jobs(id), call_key text NOT NULL,
      input_hash text NOT NULL, status text NOT NULL,
      response_hash text, usage jsonb NOT NULL DEFAULT '{}',
      created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
      PRIMARY KEY(job_id,call_key)
    );
    CREATE INDEX model_calls_time ON model_calls(created_at);
    """)


def downgrade():
    raise RuntimeError("Restore a verified backup; paid-call receipts must not be discarded")
