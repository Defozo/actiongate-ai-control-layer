"""Render the separate jury proxy without exposing its password in argv/logs.

Inject ACTIONGATE_JURY_PASSWORD through psst. Output belongs in .state, never
in the public repository. The upstream must be the isolated synthetic GPU demo.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", required=True)
    parser.add_argument("--local-port", required=True, type=int)
    parser.add_argument("--output", type=Path, default=Path(".state/public-demo"))
    args = parser.parse_args()
    origin = urlparse(args.origin)
    if (origin.scheme != "https" or not origin.hostname or origin.port is not None
            or origin.username or origin.password or origin.path not in ("", "/")
            or origin.query or origin.fragment
            or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for ch in origin.hostname)):
        raise SystemExit("Expected a plain HTTPS origin with a DNS hostname")
    if not 1024 <= args.local_port <= 65535:
        raise SystemExit("Expected an unprivileged local demo port")
    password = os.environ["ACTIONGATE_JURY_PASSWORD"]
    if len(password) < 24 or any(ch.isspace() for ch in password):
        raise SystemExit("Jury credential must be a generated secret of at least 24 characters")
    root = Path(__file__).resolve().parents[2]
    state = (root / ".state").resolve()
    output = args.output.resolve()
    if output == state or state not in output.parents:
        raise SystemExit("Private proxy output must be below this project's .state directory")
    output.mkdir(parents=True, exist_ok=True)
    config = Path(__file__).with_name("nginx.conf.template").read_text(encoding="utf-8")
    config = config.replace("__PUBLIC_ORIGIN__", "https://" + origin.hostname)
    config = config.replace("__PUBLIC_HOST__", origin.hostname)
    config = config.replace("__LOCAL_ORIGIN__", f"http://127.0.0.1:{args.local_port}")
    (output / "nginx.conf").write_text(config, encoding="utf-8")
    # nginx's RFC 2307 salted SHA format. High-entropy generated password,
    # separate salt for each render; neither value appears in public artifacts.
    salt = secrets.token_bytes(20)
    encoded = base64.b64encode(hashlib.sha1(password.encode() + salt).digest() + salt).decode()
    (output / "jury.htpasswd").write_text("juror:{SSHA}" + encoded + "\n", encoding="ascii")
    print(json.dumps({"configured": True, "origin": "https://" + origin.hostname,
                      "local_origin": f"http://127.0.0.1:{args.local_port}",
                      "credential_source": "psst:ACTIONGATE_JURY_PASSWORD"}))


if __name__ == "__main__":
    main()
