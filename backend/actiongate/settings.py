from functools import lru_cache
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[2]


def secret(name: str) -> str:
    value = os.getenv(name)
    path = os.getenv(name + "_FILE")
    if path:
        value = Path(path).read_text().strip()
    if not value or len(value) < 24:
        raise RuntimeError(f"Missing or invalid {name}; run the psst bootstrap")
    return value


@lru_cache
def settings() -> dict:
    return {
        "database_url": os.environ["DATABASE_URL"],
        "auth_key": secret("ACTIONGATE_AUTH_KEY"),
        "encryption_key": secret("ACTIONGATE_ENCRYPTION_KEY"),
        "audit_key": secret("ACTIONGATE_AUDIT_KEY"),
        "connector_key": secret("ACTIONGATE_CONNECTOR_KEY"),
        "issuer": os.getenv("AUTH_ISSUER", "actiongate-local"),
        "audience": os.getenv("AUTH_AUDIENCE", "actiongate"),
        "demo_auth": os.getenv("ACTIONGATE_DEMO_AUTH", "false").lower() == "true",
        "opa_url": os.getenv("OPA_URL", "http://opa:8181"),
        "demo_tools_url": os.getenv("DEMO_TOOLS_URL", "http://demo-tools:8010"),
        "policy_path": Path(os.getenv("POLICY_PATH", str(ROOT / "policy/control.yaml"))),
        "instance": os.getenv("HOSTNAME", "local"),
        "origins": os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8080,http://localhost:8080").split(","),
    }
