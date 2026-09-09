"""Keep setup evidence separate from protocol observations and analysis work."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE storage_probes (
      id text PRIMARY KEY,
      backend_identity text NOT NULL,
      blob_hash text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX storage_probes_backend ON storage_probes(backend_identity,created_at DESC);
    """)


def downgrade():
    raise RuntimeError(
        "Restore a verified backup; destructive downgrades are intentionally disabled"
    )
