"""Local trusted-app bootstrap and example. Not an isolated agent runtime."""
from http.cookiejar import CookieJar
import json
import os
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

from actiongate_client import ActionGate, NoRedirect

base = os.getenv("ACTIONGATE_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
if urlsplit(base).hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise SystemExit("This demo identity bootstrap is restricted to loopback hosts.")
opener = build_opener(NoRedirect(), HTTPCookieProcessor(CookieJar()))


def post(path, value):
    with opener.open(Request(base + path, data=json.dumps(value).encode(), headers={"Content-Type": "application/json", "Origin": base}), timeout=300) as response:
        return json.load(response)


post("/api/demo/session", {"role": "analyst", "tenant": "acme"})
run = post("/runs", {"purpose": "supplier_review", "document_ids": ["supplier-acme-1"], "allow_publish": False})
# The token remains in memory. Never print it, write it into the audit, or expose
# this trusted-app session to a model. Production uses a real trusted issuer.
client = ActionGate(base, run["workload_token"], run["id"])
result = client.action("documents.read", {"document_id": "supplier-acme-1"})
print(json.dumps({key: result.get(key) for key in ("id", "decision", "status", "label", "rule_ids")}, indent=2))
if result["status"] != "completed":
    raise SystemExit(1)
