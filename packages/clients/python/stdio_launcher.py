"""Launch only the checked-in, digest-pinned ActionGate stdio adapter."""
import hashlib
import json
from pathlib import Path
import sys

from mcp import StdioServerParameters

ROOT = Path(__file__).resolve().parents[3]


def registered_stdio(workload_token: str, base_url: str = "http://127.0.0.1:8080") -> StdioServerParameters:
    source = ROOT / "backend/actiongate/mcp_stdio.py"
    registry = json.loads((ROOT / "packages/clients/stdio-wrapper.json").read_text(encoding="utf-8"))
    if (registry["module"] != "actiongate.mcp_stdio" or registry["version"] != "1.0.0"
            or hashlib.sha256(source.read_bytes()).hexdigest() != registry["sha256"]):
        raise ValueError("The registered stdio wrapper definition changed")
    # The official SDK inherits its own small platform environment allowlist.
    # Do not copy os.environ, which could contain operator or provider secrets.
    return StdioServerParameters(command=sys.executable, args=["-m", registry["module"]],
        env={"PYTHONPATH": str(ROOT / "backend"), "ACTIONGATE_WORKLOAD_TOKEN": workload_token,
             "ACTIONGATE_BASE_URL": base_url}, cwd=str(ROOT))
