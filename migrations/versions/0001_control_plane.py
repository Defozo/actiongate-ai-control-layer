"""Initial tenant-scoped control, ledger, immutable snapshots and audit schema."""
from alembic import op
from actiongate.db import Base

revision = "0001_control_plane"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent adoption also covers installations made before version stamping.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    raise RuntimeError("Destructive audit/ledger rollback requires restoring an explicitly selected backup")
