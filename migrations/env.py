"""Alembic runs only through the owner-scoped migration command."""
from alembic import context
from actiongate.db import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use python -m actiongate.migrate with the schema-owner credential")
context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
