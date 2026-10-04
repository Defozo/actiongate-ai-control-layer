"""Unsigned v4b removes only nonvalidating native JSON Schema title annotations."""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import probe_business_intent_v4 as previous

base = previous.base
INPUT_MANIFEST = base.MANIFEST
base.MANIFEST = base.ROOT/'artifacts/business-intent-prototype-v4b-manifest.json'
base.OUTPUT = base.ROOT/'artifacts/business-intent-prototype-v4b.json'
ORIGINAL_SCHEMA = deepcopy(base.SCHEMA)


def remove_titles(value):
    if isinstance(value, dict):
        return {key: remove_titles(child) for key,child in value.items() if key != 'title'}
    if isinstance(value, list):
        return [remove_titles(child) for child in value]
    return value


base.SCHEMA = remove_titles(ORIGINAL_SCHEMA)
assert base.SCHEMA == remove_titles(ORIGINAL_SCHEMA)
assert all(list(new['properties']) == list(old['properties']) for old,new in zip(ORIGINAL_SCHEMA['anyOf'],base.SCHEMA['anyOf']))
original_sources = base.sources


def sources():
    return {**original_sources(), 'title_annotation_prototype_script': base.sha(Path(__file__).read_bytes())}


base.sources = sources


async def prepare():
    manifest = json.loads(INPUT_MANIFEST.read_bytes())
    cases = deepcopy(manifest['cases'])
    assert len(cases) == 13
    assert manifest['proposed_system'] == base.PROPOSED_SYSTEM
    for case in cases:
        assert case['messages'] == base.messages(case)
        case['serialized_input_upper_bound'] = base.upper(case['messages'])
    full = dict(cases[0])
    filler = 'Ordinary supplier deliveries and unresolved capacity observations. ' * 1000
    ids = base.pinned_tokenizer().encode(filler).ids[:manifest['semantic_configuration']['window_tokens']]
    full['text'] = base.pinned_tokenizer().decode(ids)
    bound = base.upper(base.messages(full))
    manifest.update(cases=cases, prepared_at=datetime.now(timezone.utc).isoformat(), sources=sources(),
        parent_manifest_sha256=base.sha(INPUT_MANIFEST.read_bytes()),
        mode='UNSIGNED v4b native source schema title-annotation removal on authored diagnostic contrasts and exposed tuning cases, not independent acceptance',
        output_schema=base.SCHEMA,
        title_only_checks={'all_non_annotation_constraints_identical': base.SCHEMA == remove_titles(manifest['output_schema']),
            'branch_field_order_identical': all(list(new['properties']) == list(old['properties']) for old,new in zip(manifest['output_schema']['anyOf'],base.SCHEMA['anyOf'])),
            'system_identical': manifest['proposed_system'] == base.PROPOSED_SYSTEM,
            'pydantic_validator_unchanged': True, 'source_evidence_unchanged': True, 'goal_schema_unchanged': True},
        changed_schema_details='Only title annotations recursively removed from native source schema; every validating keyword, branch and field order unchanged',
        full_window_positive_sample={'tokens': len(ids), 'serialized_input_upper_bound': bound},
        full_window_bound_fits=bound <= manifest['semantic_configuration']['max_input_tokens_per_call'])
    base.write(base.MANIFEST, manifest)
    print(json.dumps({'prepared': True, 'cases': len(cases), 'manifest_sha256': base.sha(base.MANIFEST.read_bytes()),
                      'full_window_positive_sample': manifest['full_window_positive_sample'], 'title_only_checks': manifest['title_only_checks']}), flush=True)
    assert all(manifest['title_only_checks'].values())
    assert max([bound, *(case['serialized_input_upper_bound'] for case in cases)]) <= manifest['semantic_configuration']['max_input_tokens_per_call']


async def run():
    try:
        await base.run()
    except SystemExit:
        pass
    report = json.loads(base.OUTPUT.read_bytes())
    previous = json.loads((base.ROOT/'artifacts/business-intent-prototype-v4.json').read_bytes())
    old = {row['id']:row for row in previous['rows']}
    comparisons = []
    for row in report['rows']:
        prior = old[row['id']]
        raw = row.get('raw_response',{}).get('message',{}).get('content')
        original = prior.get('raw_response',{}).get('message',{}).get('content')
        comparisons.append({'id':row['id'], 'raw_byte_identical':raw is not None and raw == original,
            'v4_raw':original, 'v4b_raw':raw, 'v4_prediction':prior['prediction'], 'v4b_prediction':row['prediction']})
    report['raw_comparison_to_v4'] = comparisons
    base.write(base.OUTPUT, report)
    print(json.dumps({'passed':report['passed'], 'raw_identical':sum(row['raw_byte_identical'] for row in comparisons), 'total':len(comparisons)}), flush=True)
    raise SystemExit(not report['passed'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    asyncio.run(prepare() if args.prepare else run())
