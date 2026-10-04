"""Durable ownership and recovery deadlines for interactive test jobs."""
from alembic import op
from sqlalchemy import inspect, text

revision = "0002_test_job_leases"
down_revision = "0001_control_plane"
branch_labels = None
depends_on = None


def upgrade():
    # The initial migration adopts Base.metadata on a clean installation.
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("test_runs")}
    for name, sql_type in [("owner", "varchar"), ("heartbeat_at", "timestamptz"), ("deadline_at", "timestamptz")]:
        if name not in columns:
            op.execute(text(f"ALTER TABLE test_runs ADD COLUMN {name} {sql_type}"))


def downgrade():
    raise RuntimeError("Removing job ownership requires an explicitly selected backup")
