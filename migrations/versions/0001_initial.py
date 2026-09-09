"""Initial PostgreSQL evidence and work lifecycle; keep this DDL immutable."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE protocols (
      id text PRIMARY KEY, name text NOT NULL, profile text NOT NULL,
      analysis jsonb NOT NULL, enabled boolean NOT NULL, config_hash text NOT NULL
    );
    CREATE TABLE sources (
      id text PRIMARY KEY, spec jsonb NOT NULL, host text NOT NULL,
      sequence bigint NOT NULL DEFAULT 0, current_hash text, raw_hash text,
      representation_url text, etag text, last_modified text,
      interval_seconds integer NOT NULL, max_interval_seconds integer NOT NULL,
      pinned boolean NOT NULL DEFAULT false, next_check_at timestamptz NOT NULL DEFAULT now(),
      last_checked_at timestamptz, last_success_at timestamptz, last_changed_at timestamptz,
      stable_since timestamptz,
      failures integer NOT NULL DEFAULT 0, last_error text,
      lease_token text, lease_until timestamptz,
      CHECK (sequence >= 0), CHECK (interval_seconds > 0)
    );
    CREATE INDEX sources_due ON sources(next_check_at);
    CREATE TABLE subscriptions (
      protocol_id text REFERENCES protocols(id), source_id text REFERENCES sources(id),
      baseline_sequence bigint NOT NULL, active boolean NOT NULL DEFAULT true,
      PRIMARY KEY(protocol_id, source_id)
    );
    CREATE TABLE inventory_members (
      root_id text REFERENCES sources(id), child_id text REFERENCES sources(id),
      active boolean NOT NULL DEFAULT true, PRIMARY KEY(root_id, child_id)
    );
    CREATE TABLE versions (
      source_id text REFERENCES sources(id), sequence bigint NOT NULL,
      normalized_hash text NOT NULL, raw_hash text NOT NULL, observed_at timestamptz NOT NULL DEFAULT now(),
      metadata jsonb NOT NULL, PRIMARY KEY(source_id, sequence)
    );
    CREATE TABLE events (
      id text PRIMARY KEY, source_id text REFERENCES sources(id), sequence bigint NOT NULL,
      kind text NOT NULL, baseline boolean NOT NULL, old_hash text, new_hash text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(), metadata jsonb NOT NULL,
      UNIQUE(source_id, sequence)
    );
    CREATE INDEX events_time ON events(created_at);
    CREATE TABLE checks (
      id text PRIMARY KEY, source_id text REFERENCES sources(id), outcome text NOT NULL,
      checked_at timestamptz NOT NULL DEFAULT now(), error text, metadata jsonb NOT NULL DEFAULT '{}'
    );
    CREATE TABLE jobs (
      id text PRIMARY KEY, protocol_id text REFERENCES protocols(id), kind text NOT NULL,
      parent_id text REFERENCES jobs(id), payload jsonb NOT NULL,
      status text NOT NULL DEFAULT 'PENDING', created_at timestamptz NOT NULL DEFAULT now(),
      next_attempt_at timestamptz NOT NULL DEFAULT now(), attempts integer NOT NULL DEFAULT 0,
      lease_token text, lease_until timestamptz, last_error text
    );
    CREATE INDEX jobs_pending ON jobs(status, next_attempt_at);
    CREATE TABLE job_events (
      protocol_id text REFERENCES protocols(id), event_id text REFERENCES events(id),
      job_id text REFERENCES jobs(id), PRIMARY KEY(protocol_id, event_id)
    );
    CREATE TABLE analysis_chunks (
      job_id text REFERENCES jobs(id), ordinal integer NOT NULL, input_hash text NOT NULL,
      result jsonb NOT NULL, usage jsonb NOT NULL, PRIMARY KEY(job_id, ordinal)
    );
    CREATE TABLE reports (
      id text PRIMARY KEY, job_id text UNIQUE REFERENCES jobs(id), protocol_id text REFERENCES protocols(id),
      material boolean NOT NULL, result jsonb NOT NULL, blob_hash text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE outbox (
      id text PRIMARY KEY, report_id text REFERENCES reports(id), destination text NOT NULL,
      part text NOT NULL, status text NOT NULL DEFAULT 'PENDING', message_id bigint,
      attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT now(),
      sending_at timestamptz, last_error text, UNIQUE(report_id, destination, part)
    );
    CREATE TABLE origins (host text PRIMARY KEY, next_start_at timestamptz NOT NULL DEFAULT now());
    CREATE TABLE permits (id text PRIMARY KEY, host text REFERENCES origins(host), expires_at timestamptz NOT NULL);
    CREATE INDEX permits_host ON permits(host);
    """)


def downgrade():
    raise RuntimeError(
        "Restore a verified backup; destructive downgrades are intentionally disabled"
    )
