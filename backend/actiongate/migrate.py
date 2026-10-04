"""One-shot schema owner entrypoint. Runtime never receives this credential."""
from sqlalchemy import text
from pathlib import Path
from alembic import command
from alembic.config import Config
from .db import Base, engine


def main():
    with engine().connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(871220)"))
        conn.commit()
        try:
            configuration = Config()
            configuration.set_main_option("script_location", str(Path(__file__).resolve().parents[2]/"migrations"))
            configuration.attributes["connection"] = conn
            command.upgrade(configuration, "head")
            conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"))
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (1) ON CONFLICT DO NOTHING"))
            conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO actiongate"))
            conn.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO actiongate"))
            conn.execute(text("REVOKE UPDATE, DELETE, TRUNCATE ON audit_events, outbox FROM actiongate"))
            conn.commit()
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(871220)"))
            conn.commit()
    print("ActionGate schema version 1 ready; audit runtime permissions are append-only")


if __name__ == "__main__":
    main()
