"""Verify actual runtime format enforcement with think=false and no JSON prompt."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from actiongate import policies
from actiongate.runtime import WorkerClient


async def run():
    active = policies.snapshot()
    signed = policies.verify_stored_snapshot(active)
    artifact = signed['model_artifacts']['local-guard']
    worker = WorkerClient('guard')
    schema = {'type': 'object', 'properties': {'format_check': {'type': 'string', 'enum': ['native-json-ok']}},
              'required': ['format_check'], 'additionalProperties': False}
    messages = [{'role': 'user', 'content': 'Reply with one short sentence in ordinary English prose saying that you are ready.'}]
    report = {'suite': 'native-json-format', 'created_at': datetime.now(timezone.utc).isoformat(),
              'generation': active['generation'], 'model_artifact': artifact,
              'worker': await worker.ready(), 'messages': messages, 'schema': schema,
              'think': False, 'temperature': 0, 'seed': 42, 'passed': False}
    try:
        response = await worker.infer(messages, 64, schema=schema, generation=active['generation'],
            expected_model_digest=artifact['manifest']['digest'], expected_manifest_sha256=artifact['sha256'])
        report['response'] = response
        raw = response['message']['content']
        report['passed'] = json.loads(raw) == {'format_check': 'native-json-ok'}
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'reason': str(error)}
    Path('/app/artifacts/native-json-format.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(run()))
