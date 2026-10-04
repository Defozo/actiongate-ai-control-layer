"""Unsigned native consistency schema with the unchanged v3 primary prompt."""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import probe_business_intent_v3 as previous

base = previous.base
INPUT_MANIFEST = base.MANIFEST
base.MANIFEST = base.ROOT/'artifacts/business-intent-prototype-v4-manifest.json'
base.OUTPUT = base.ROOT/'artifacts/business-intent-prototype-v4.json'
branches = []
for categories, risks, minimum, maximum in [
    (['none'], [0], 0, 0),
    (['prompt_injection', 'goal_drift', 'data_exfiltration', 'memory_poisoning', 'code_execution'], [1, 2, 3], 1, 2),
    (['uncertain', 'unsupported_language'], [3], 0, 2),
]:
    branch = deepcopy(base.SCHEMA)
    branch['properties']['category'] = {'type': 'string', 'enum': categories}
    branch['properties']['risk_level'] = {'type': 'integer', 'enum': risks}
    branch['properties']['evidence'] = {'type': 'array', 'minItems': minimum, 'maxItems': maximum, 'items': {'type': 'string'}}
    assert list(branch['properties']) == previous.ORDER
    branches.append(branch)
base.SCHEMA = {'title': 'SourceAssessment', 'anyOf': branches}
original_sources = base.sources


def sources():
    return {**original_sources(), 'native_consistency_prototype_script': base.sha(Path(__file__).read_bytes())}


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
        mode='UNSIGNED decoding-order and native consistency hypothesis on authored diagnostic contrasts and exposed tuning cases, not independent acceptance',
        output_schema=base.SCHEMA, schema_semantics_unchanged=True,
        changed_schema_details='Native anyOf mirrors existing Verdict category/risk/evidence invariants, with independent production parser and exact source evidence validation unchanged',
        full_window_positive_sample={'tokens': len(ids), 'serialized_input_upper_bound': bound},
        full_window_bound_fits=bound <= manifest['semantic_configuration']['max_input_tokens_per_call'])
    base.write(base.MANIFEST, manifest)
    print(json.dumps({'prepared': True, 'cases': len(cases), 'manifest_sha256': base.sha(base.MANIFEST.read_bytes()),
                      'full_window_positive_sample': manifest['full_window_positive_sample'], 'full_window_bound_fits': manifest['full_window_bound_fits']}), flush=True)
    assert max([bound, *(case['serialized_input_upper_bound'] for case in cases)]) <= manifest['semantic_configuration']['max_input_tokens_per_call'], 'Full-window bound exceeds signed input limit; no inference authorized'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    asyncio.run(prepare() if args.prepare else base.run())
