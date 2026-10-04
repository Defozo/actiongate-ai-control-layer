"""Publish a validated policy or signed demo feed through the same operator API."""
import argparse
import json
import os
from pathlib import Path
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["policy", "feed"])
    parser.add_argument("input", type=Path)
    parser.add_argument("--url")
    args = parser.parse_args()
    port = os.getenv("ACTIONGATE_PORT", "8080")
    env = Path(__file__).resolve().parents[1]/".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("ACTIONGATE_PORT="):
                port = line.split("=", 1)[1]
    url = args.url or "http://127.0.0.1:"+port
    with httpx.Client(base_url=url, timeout=60, headers={"Origin": url}) as client:
        client.post("/api/demo/session", json={"role": "admin", "tenant": "synthetic_test_tenant"}).raise_for_status()
        if args.kind == "policy":
            body = {"yaml": args.input.read_text(encoding="utf-8")}
            valid = client.post("/api/policies/validate", json=body)
            valid.raise_for_status()
            if not valid.json()["valid"]:
                raise SystemExit(json.dumps(valid.json(), indent=2))
            response = client.post("/api/policies/activate", json=body)
        else:
            data = json.loads(args.input.read_text(encoding="utf-8"))
            response = client.post("/api/feed/publish", json={"rules": data["rules"]})
        response.raise_for_status()
        readback = client.get("/api/policies")
        readback.raise_for_status()
        assert readback.json()["generation"] == response.json()["generation"]
        print(json.dumps({"generation": response.json()["generation"], "status": "active", "read_back_verified": True}, indent=2))


if __name__ == "__main__":
    main()
