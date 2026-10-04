"""Capture a synthetic case from the already exposed calibration/regression corpus.

The independent v2 holdout is deliberately not an accepted input here.
Run inside the isolated GPU gateway with the desired case ID as an argument.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from actiongate.policies import snapshot, verify_stored_snapshot
from actiongate.runtime import pinned_tokenizer
from actiongate.semantic import PROMPT_VERSION, SYSTEM, SemanticGuard, Verdict, guard_artifact, source_evidence


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('case_id')
    parser.add_argument('--corpus', choices=('semantic_calibration_v1.json', 'semantic_holdout_v1.json'),
                        default='semantic_holdout_v1.json')
    args = parser.parse_args()
    case = next(value for value in json.loads((Path('/app/tests/corpus')/args.corpus).read_text())['cases']
                if value['id'] == args.case_id)
    active = snapshot()
    signed = verify_stored_snapshot(active)
    if signed['guard_artifact'] != guard_artifact() or active['configuration']['semantic']['prompt_version'] != PROMPT_VERSION:
        raise RuntimeError('Active signed artifact must match the diagnostic process')
    model = signed['model_artifacts']['local-guard']
    guard = SemanticGuard()
    original = guard.worker.infer
    captured = []
    input_hashes = []

    async def capture(*values, **kwargs):
        serialized = json.dumps({'messages': values[0], 'format': kwargs.get('schema'), 'tools': kwargs.get('tools')},
                                ensure_ascii=False, separators=(',', ':'))
        input_hashes.append(hashlib.sha256(serialized.encode()).hexdigest())
        response = await original(*values, **kwargs)
        captured.append(response)
        return response

    guard.worker.infer = capture
    result = await guard.scan(case['text'], case['purpose'], case['effect'], ['untrusted_content'], active['generation'],
        config=active['configuration']['semantic'], expected_model_digest=model['manifest']['digest'],
        expected_manifest_sha256=model['sha256'])
    validation = []
    for response in captured:
        try:
            verdict = Verdict.model_validate_json(response['message']['content'])
            item = {'schema_valid': True, 'reason_length': len(verdict.reason),
                    'inconsistent_verdict': (verdict.verdict == 'benign' and verdict.risk_level != 0)
                       or (verdict.verdict != 'benign' and verdict.risk_level == 0)}
            try:
                item['source_evidence'] = [source_evidence(value, case['text']) for value in verdict.evidence]
            except ValueError as error:
                item['evidence_error'] = str(error)
            validation.append(item)
        except Exception as error:
            validation.append({'schema_valid': False, 'error': str(error)})
    report = {'suite': 'exposed_synthetic_semantic_diagnostic', 'corpus': args.corpus, 'case': case,
        'runtime_profile': 'isolated-gpu', 'active_generation': active['generation'],
        'guard_artifact': guard_artifact(), 'signed_guard_artifact': signed['guard_artifact'],
        'process_prompt_version': PROMPT_VERSION, 'process_system_tokens': len(pinned_tokenizer().encode(SYSTEM).ids),
        'origins': ['untrusted_content'], 'serialized_worker_input_sha256': input_hashes,
        'semantic_configuration': active['configuration']['semantic'], 'model_artifact': model,
        'raw_worker_results': captured, 'validation': validation, 'scan_result': result}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
