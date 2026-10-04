"""Exercise real staged worker generations without executing an inference."""
import asyncio
import copy
import json
from pathlib import Path
import time

import httpx
import yaml

from actiongate.runtime import RuntimeFailure, WorkerClient


async def run():
    from actiongate.policies import snapshot, verify_stored_snapshot
    active = snapshot()
    signed = verify_stored_snapshot(active)
    configuration = yaml.safe_load(Path('/app/policy/control.yaml').read_text())
    configuration['local_resources']['max_waiting_jobs'] = 2
    guard = WorkerClient('guard')
    generation = int(time.time_ns() // 1000)
    report = {'suite': 'runtime-configuration', 'mode': 'real_worker_api', 'checks': {},
        'generation': active['generation'], 'guard_artifact': signed['guard_artifact'],
        'model_artifacts': signed['model_artifacts'], 'semantic_configuration': active['configuration']['semantic']}
    staged = await guard.configure(generation, configuration)
    report['checks']['dynamic_queue_staged'] = staged['effective']['max_waiting_jobs'] == 2
    report['checks']['immutable_idempotent'] = staged == await guard.configure(generation, configuration)
    altered = copy.deepcopy(configuration)
    altered['local_resources']['max_waiting_jobs'] = 3
    try:
        await guard.configure(generation, altered)
        report['checks']['same_generation_change_denied'] = False
    except httpx.HTTPStatusError as exc:
        report['checks']['same_generation_change_denied'] = exc.response.status_code == 409
    altered['local_resources']['business_slots'] = 2
    try:
        await guard.configure(generation+1, altered)
        report['checks']['unsupported_slot_count_denied'] = False
    except httpx.HTTPStatusError as exc:
        report['checks']['unsupported_slot_count_denied'] = exc.response.status_code == 422
    tickets = []
    try:
        tickets = [await guard.reserve(1, generation), await guard.reserve(1, generation)]
        try:
            await guard.reserve(1, generation)
            report['checks']['configured_queue_enforced'] = False
        except httpx.HTTPStatusError as exc:
            report['checks']['configured_queue_enforced'] = exc.response.status_code == 429
    finally:
        for ticket in tickets:
            await guard.release(ticket['ticket_id'])
    before = await guard.status()
    for name, requested_generation, max_tokens, expected in (
        ('unknown_generation_denied', generation+9, 16, 'worker_http_409'),
        ('configured_output_ceiling_denied', generation, configuration['semantic']['max_output_tokens']+1, 'worker_http_422')):
        try:
            await guard.infer([{'role': 'user', 'content': 'Synthetic configuration test'}], max_tokens, generation=requested_generation,
                              deadline_seconds=configuration['semantic']['deadline_seconds'])
            report['checks'][name] = False
        except RuntimeFailure as exc:
            report['checks'][name] = str(exc) == expected and exc.usage['usage_unknown'] is False
    after = await guard.status()
    report['checks']['rejected_requests_never_started_inference'] = before['completed'] == after['completed']
    report['passed'] = all(report['checks'].values())
    print(json.dumps(report, indent=2))
    return report['passed']


if __name__ == '__main__':
    raise SystemExit(0 if asyncio.run(run()) else 1)
