"""Connect control requirements to actual named test executions."""
import html
import json
from pathlib import Path


def build(report, root: Path):
    registry = json.loads((root/"tests/control_registry.json").read_text(encoding="utf-8"))
    rows = []
    for control in registry["controls"]:
        evidence = {}
        for polarity in ("positive", "negative"):
            evidence[polarity] = []
            for test_name in control[polarity]:
                matches = [test for test in report["tests"] if test["name"].split("[", 1)[0].rsplit(".", 1)[-1] == test_name]
                evidence[polarity].append({"test": test_name, "executions": len(matches),
                    "status": "passed" if matches and all(test["status"] == "passed" for test in matches) else "failed" if matches else "not_run"})
        rows.append({**control, "evidence": evidence, "status": "covered" if all(test["status"] == "passed"
            for tests in evidence.values() for test in tests) else "missing_or_failed_evidence"})
    result = {"suite": report["suite"], "created_at": report["created_at"], "scope": registry["scope"],
        "controls": rows, "covered": sum(row["status"] == "covered" for row in rows), "total": len(rows)}
    out = root/"artifacts"
    (out/f"{report['suite']}-coverage.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    markup = "<!doctype html><meta charset=utf-8><title>ActionGate control coverage</title><style>body{font:16px system-ui;max-width:1200px;margin:3rem auto}td,th{padding:12px;text-align:left;vertical-align:top;border-bottom:1px solid #ddd}</style>"
    markup += "<h1>Control, requirement and executed evidence</h1><p>"+html.escape(registry["scope"])+"</p><table><tr><th>Control</th><th>Requirement</th><th>Status</th><th>Evidence</th></tr>"
    for row in rows:
        names = "<br>".join(html.escape(test["test"]+": "+test["status"]) for tests in row["evidence"].values() for test in tests)
        markup += "<tr>"+"".join("<td>"+html.escape(row[key])+"</td>" for key in ("control_id", "requirement", "status"))+"<td>"+names+"</td></tr>"
    (out/f"{report['suite']}-coverage.html").write_text(markup+"</table>", encoding="utf-8")
    return result
