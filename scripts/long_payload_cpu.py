"""Real 64 KiB broker workflow under the unmodified reference CPU policy.

Run in the trusted test-runner after the deployed source/artifact checks pass.
The scan observer records actual calls; it neither substitutes verdicts nor
changes cache, resource, deadline, reservation, or authorization settings.
"""
from __future__ import annotations

import asyncio
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from sqlalchemy import select

from actiongate import broker, ledger, policies
from actiongate.contracts import ActionRequest, RunRequest
from actiongate.db import DataObject, Principal, Reservation, transaction, uid
from actiongate.runtime import WorkerClient
from actiongate.security import canonical, decrypt
from actiongate.semantic import GOAL_ACTION_SYSTEM, SemanticGuard, guard_artifact


async def run(runtime_profile='cpu'):
    active = policies.snapshot()
    signed = policies.verify_stored_snapshot(active)
    configuration = active['configuration']
    if signed['guard_artifact'] != guard_artifact():
        raise RuntimeError('Classifier source differs from the deployed signed generation')
    if not configuration['controls']['semantic']['enabled']:
        raise RuntimeError('This proof requires full semantic protection')
    tenant = runtime_profile+'-long-payload-'+uid()
    actor = {'sub': uid(), 'tenant': tenant, 'role': 'analyst'}
    with transaction() as db:
        db.add(Principal(id=actor['sub'], tenant=tenant, role=actor['role']))
    sentence = 'The supplier offers maintenance services with a five year warranty. Delivery capacity is stable. '
    payload = (sentence*((65536+len(sentence)-1)//len(sentence)))[:65536]
    arguments = {'content': payload}
    scans = []
    inference_calls = []
    real_scan = SemanticGuard.scan
    real_infer = WorkerClient.infer

    async def observe_infer(self, messages, *args, **kwargs):
        response = await real_infer(self, messages, *args, **kwargs)
        inference_calls.append({'role': self.role,
            'assessment': 'goal_action' if messages[0]['content'] == GOAL_ACTION_SYSTEM else 'source_window',
            'context_preflight': response['context_preflight'], 'usage': response['usage'],
            'generation': response['generation'], 'model_manifest_sha256': response['model_manifest_sha256']})
        return response

    async def observe_scan(self, text, *args, **kwargs):
        began = time.monotonic()
        outcome = await real_scan(self, text, *args, **kwargs)
        scans.append({'input_bytes': len(text.encode('utf-8')),
            'input_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'seconds': time.monotonic()-began, 'result': outcome})
        return outcome

    guard = SemanticGuard()
    estimated_input = guard.estimate(canonical(arguments), configuration['semantic'])
    report = {'suite': f'reference-{runtime_profile}-64kib', 'runtime_profile': runtime_profile, 'mode': 'real-local-model',
        'started_at': datetime.now(timezone.utc).isoformat(), 'generation': active['generation'],
        'guard_artifact': signed['guard_artifact'], 'model_artifacts': signed['model_artifacts'],
        'semantic_configuration': configuration['semantic'],
        'local_resource_configuration': configuration['local_resources'],
        'budget_configuration': configuration['budgets'],
        'payload_bytes': len(payload.encode('utf-8')),
        'payload_sha256': hashlib.sha256(payload.encode('utf-8')).hexdigest(),
        'input_estimate': estimated_input, 'checks': {}, 'passed': False,
        'cache_isolation': 'New unique tenant and operation; both actual scans must be observed. No cache configuration is changed.'}
    began = time.monotonic()
    try:
        report['workers'] = {role: await WorkerClient(role).ready() for role in ('guard', 'business')}
        run_info = await asyncio.to_thread(broker.create_run, RunRequest(document_ids=[]), actor)
        report['run_id'] = run_info['id']
        SemanticGuard.scan = observe_scan
        WorkerClient.infer = observe_infer
        operation = await broker.execute(ActionRequest(run_id=run_info['id'], tool='reports.save',
            arguments=arguments, idempotency_key=uid()), actor)
        report['operation'] = operation
        with transaction() as db:
            stored = db.scalar(select(DataObject).where(DataObject.tenant == tenant,
                DataObject.name == 'report-'+operation['id']))
            stored_content = decrypt(stored.encrypted) if stored else None
            reservations = list(db.scalars(select(Reservation).where(Reservation.operation_id == operation['id'])))
            report['reservations'] = [{'id': row.id, 'kind': row.kind, 'status': row.status,
                                      'accounts': row.accounts, 'usage': row.usage} for row in reservations]
        report['balances'] = ledger.balances(tenant)
        input_scans = [scan for scan in scans if scan['input_sha256'] == hashlib.sha256(canonical(arguments).encode('utf-8')).hexdigest()]
        output_scans = [scan for scan in scans if scan not in input_scans]
        guard_reservations = [row for row in report['reservations'] if row['kind'].startswith('guard.')]
        report['checks'] = {
            'exact_64kib_input': len(payload.encode('utf-8')) == 65536,
            'normal_broker_completed': operation['status'] == 'completed',
            'saved_content_exact_without_truncation': stored_content == payload,
            'actual_multiwindow_input': len(input_scans) == 1 and estimated_input['windows'] > 1
                and len(input_scans[0]['result'].get('windows', [])) == estimated_input['content_windows'],
            'separate_goal_action_assessment': len(input_scans) == 1
                and isinstance(input_scans[0]['result'].get('goal_action'), dict)
                and input_scans[0]['result']['goal_action'].get('evidence_scope') == 'derived_goal_action_context'
                and input_scans[0]['result'].get('inspection_calls') == estimated_input['windows'],
            'actual_output_guard': len(output_scans) == 1 and len(output_scans[0]['result'].get('windows', [])) >= 1,
            'all_actual_serialized_contexts_within_ceiling': len(inference_calls) == estimated_input['windows']+1
                and all(call['context_preflight']['upper_bound'] <= configuration['semantic']['max_input_tokens_per_call']
                        for call in inference_calls),
            'all_semantic_windows_complete_benign': len(scans) == 2 and all(scan['result'].get('complete') is True
                and scan['result'].get('verdict') == 'benign' and scan['result'].get('risk_level') == 0 for scan in scans),
            'positive_known_guard_usage': len(guard_reservations) == 2 and all(row['status'] == 'settled'
                and row['usage'].get('total_tokens', 0) > 0 and not row['usage'].get('usage_unknown') for row in guard_reservations),
            'all_budgets_reconciled_without_overrun': bool(report['balances']) and all(not row['overcommitted']
                and row['reserved'] == 0 for row in report['balances']),
            'generation_unchanged': policies.snapshot()['generation'] == active['generation'],
            'root_deadline_preserved': time.monotonic()-began < configuration['budgets']['run_deadline_seconds'],
        }
        report['passed'] = all(report['checks'].values())
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'reason': str(error)}
    finally:
        SemanticGuard.scan = real_scan
        WorkerClient.infer = real_infer
        report['scans'] = scans
        report['inference_calls'] = inference_calls
        report['seconds'] = time.monotonic()-began
        path = Path(f'/app/artifacts/long-payload-{runtime_profile}.json')
        path.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': report['checks'], 'seconds': report['seconds']}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-profile', choices=('cpu', 'gpu'), default='cpu')
    raise SystemExit(asyncio.run(run(parser.parse_args().runtime_profile)))
