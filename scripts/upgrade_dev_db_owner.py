"""One-time upgrade of an existing local development volume to separated roles.

Run with only ACTIONGATE_DB_OWNER_PASSWORD injected from psst. This script never
prints SQL, passwords or connection strings and only touches the ActionGate DB.
"""
import os
import re
import subprocess

password = os.environ["ACTIONGATE_DB_OWNER_PASSWORD"]
runtime_password = os.environ["ACTIONGATE_DB_PASSWORD"]
if not all(re.fullmatch("[a-f0-9]{64}", value) for value in (password,runtime_password)):
    raise RuntimeError("Unexpected generated password format")
setup = subprocess.run(["docker", "exec", "-i", "actiongate-postgres-1", "psql", "-v", "ON_ERROR_STOP=1", "-U", "actiongate", "-d", "actiongate"], input="CREATE ROLE actiongate_maintenance LOGIN SUPERUSER;", text=True,capture_output=True)
if setup.returncode:
    raise RuntimeError("Cannot open temporary local-only maintenance role")
sql = "\n".join([
    "BEGIN;",
    "ALTER ROLE actiongate RENAME TO actiongate_owner;",
    "ALTER ROLE actiongate_owner PASSWORD '" + password + "';",
    "CREATE ROLE actiongate LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '" + runtime_password + "';",
    "ALTER DATABASE actiongate OWNER TO actiongate_owner;",
    "ALTER SCHEMA public OWNER TO actiongate_owner;",
    "DO $$ DECLARE item record; BEGIN FOR item IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP EXECUTE format('ALTER TABLE public.%I OWNER TO actiongate_owner',item.tablename); END LOOP; END $$;",
    "REVOKE ALL ON SCHEMA public FROM PUBLIC;",
    "GRANT USAGE ON SCHEMA public TO actiongate;",
    "GRANT CONNECT ON DATABASE actiongate TO actiongate;",
    "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO actiongate;",
    "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO actiongate;",
    "ALTER DEFAULT PRIVILEGES FOR ROLE actiongate_owner IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO actiongate;",
    "ALTER DEFAULT PRIVILEGES FOR ROLE actiongate_owner IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO actiongate;",
    "COMMIT;",
])
result = subprocess.run(["docker", "exec", "-i", "actiongate-postgres-1", "psql", "-v", "ON_ERROR_STOP=1", "-U", "actiongate_maintenance", "-d", "actiongate"], input=sql, text=True, capture_output=True)
if result.returncode:
    raise RuntimeError("Development role migration failed: " + result.stderr.replace(password, "[redacted]").replace(runtime_password,"[redacted]"))
cleanup = subprocess.run(["docker", "exec", "-i", "actiongate-postgres-1", "psql", "-v", "ON_ERROR_STOP=1", "-U", "actiongate_owner", "-d", "actiongate"], input="DROP ROLE actiongate_maintenance;", text=True,capture_output=True,check=True)
print("ActionGate database owner/runtime roles separated")
