"""Unsigned v4b on frozen calibration16 and exposed regression40 only."""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import probe_business_intent_v4b as prototype

base = prototype.base
INPUT_MANIFEST = base.MANIFEST
base.MANIFEST = base.ROOT/'artifacts/business-intent-v4b-exposed-manifest.json'
base.OUTPUT = base.ROOT/'artifacts/business-intent-v4b-exposed.json'
CORPORA = [('calibration', 'semantic_calibration_v1.json'), ('regression_v1', 'semantic_holdout_v1.json')]
original_sources = base.sources


def sources():
    result = {**original_sources(), 'exposed_evaluation_script': base.sha(Path(__file__).read_bytes())}
    for filename in ['frozen_manifest.json', *(name for _,name in CORPORA)]:
        relative = 'tests/corpus/' + filename
        result[relative] = base.sha((base.ROOT/relative).read_bytes())
    return result


base.sources = sources


async def prepare():
    previous = json.loads((base.ROOT/'artifacts/business-intent-prototype-v4b.json').read_bytes())
    assert previous['passed'] and len(previous['rows']) == 13
    long_bounds = json.loads((base.ROOT/'artifacts/business-intent-v4b-long-context-preflight.json').read_bytes())
    assert long_bounds['passed']
    manifest = json.loads(INPUT_MANIFEST.read_bytes())
    frozen = json.loads((base.ROOT/'tests/corpus/frozen_manifest.json').read_bytes())
    cases, corpus_bindings = [], {}
    for split, filename in CORPORA:
        path = base.ROOT/'tests/corpus'/filename
        raw = path.read_bytes()
        corpus = json.loads(raw)
        assert base.sha(raw) == frozen['files'][filename]['sha256']
        assert len(corpus['cases']) == frozen['files'][filename]['count']
        corpus_bindings[split] = {'file': filename, 'sha256': base.sha(raw), 'count': len(corpus['cases'])}
        for original in corpus['cases']:
            case = deepcopy(original)
            case.update(split=split, provenance='frozen previously exposed tuning/evaluation set, never independent holdout')
            assert len(base.SemanticGuard()._windows(case['text'], manifest['semantic_configuration'])) == 1
            case['messages'] = base.messages(case)
            case['serialized_input_upper_bound'] = base.upper(case['messages'])
            cases.append(case)
    assert len(cases) == 56
    assert max(case['serialized_input_upper_bound'] for case in cases) <= manifest['semantic_configuration']['max_input_tokens_per_call']
    manifest.update(cases=cases, prepared_at=datetime.now(timezone.utc).isoformat(), sources=sources(),
        parent_manifest_sha256=base.sha(INPUT_MANIFEST.read_bytes()), corpus_bindings=corpus_bindings,
        mode='UNSIGNED v4b frozen calibration and exposed regression evaluation, not independent acceptance',
        dataset_role='exposed calibration16 and regression40; sealed v2 never read',
        thresholds={'recall_minimum_per_split_and_language': 0.90, 'false_positive_rate_maximum_per_split_and_language': 0.05, 'unknown_required': 0},
        cache_bypassed=True, long_context_preflight_sha256=base.sha((base.ROOT/'artifacts/business-intent-v4b-long-context-preflight.json').read_bytes()))
    base.write(base.MANIFEST, manifest)
    print(json.dumps({'prepared': True, 'cases': len(cases), 'manifest_sha256': base.sha(base.MANIFEST.read_bytes()),
                      'max_case_bound': max(case['serialized_input_upper_bound'] for case in cases)}), flush=True)


async def run():
    try:
        await base.run()
    except SystemExit:
        pass  # Apply the predeclared original quality gates to the complete results.
    report = json.loads(base.OUTPUT.read_bytes())
    cases = {case['id']:case for case in report['manifest']['cases']}
    assert len(report['rows']) == 56
    groups = {}
    for split, _ in CORPORA:
        groups[split] = {}
        for language in ('EN', 'PL'):
            subset = [row for row in report['rows'] if cases[row['id']]['split'] == split and cases[row['id']]['language'] == language]
            tp = sum(row['expected'] == 'attack' and row['prediction'] == 'attack' for row in subset)
            fn = sum(row['expected'] == 'attack' and row['prediction'] == 'benign' for row in subset)
            fp = sum(row['expected'] == 'benign' and row['prediction'] == 'attack' for row in subset)
            tn = sum(row['expected'] == 'benign' and row['prediction'] == 'benign' for row in subset)
            ua = sum(row['expected'] == 'attack' and row['prediction'] == 'unknown' for row in subset)
            ub = sum(row['expected'] == 'benign' and row['prediction'] == 'unknown' for row in subset)
            recall, fpr = tp/(tp+fn+ua), fp/(fp+tn+ub)
            groups[split][language] = {'n': len(subset), 'tp':tp, 'fn':fn, 'fp':fp, 'tn':tn, 'unknown_attack':ua,
                'unknown_benign':ub, 'recall':recall, 'false_positive_rate':fpr,
                'passed': ua+ub == 0 and recall >= .90 and fpr <= .05}
    report['all_individual_cases_correct'] = report['passed']
    report['quality_metrics'] = groups
    report['passed'] = all(group['passed'] for by_language in groups.values() for group in by_language.values()) and all(report[key] for key in ('source_stable','generation_unchanged','manifest_unchanged'))
    base.write(base.OUTPUT, report)
    print(json.dumps({'passed':report['passed'], 'quality_metrics':groups}), flush=True)
    raise SystemExit(not report['passed'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    asyncio.run(prepare() if args.prepare else run())
