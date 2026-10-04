"""Measure exact production serialization against the pinned tokenizer ceiling."""
import hashlib
import json
from pathlib import Path

from actiongate.policies import snapshot, verify_stored_snapshot
from actiongate.security import canonical
from actiongate.semantic import SYSTEM, SemanticGuard, Verdict, guard_artifact

active = snapshot()
signed = verify_stored_snapshot(active)
if signed['guard_artifact'] != guard_artifact():
    raise RuntimeError('Measurement source must match the signed classifier')
guard = SemanticGuard()
configuration = active['configuration']['semantic']
sentence = 'The supplier offers maintenance services with a five year warranty. Delivery capacity is stable. '
payload = (sentence*((65536+len(sentence)-1)//len(sentence)))[:65536]
chunks = guard._windows(canonical({'content': payload}), configuration)
purpose = ('Review supplier delivery commitments, terms and unresolved risks faithfully for the authorized internal analyst. '
           'Use only granted resources and tools. Preserve approvals, tenant boundaries, confidentiality, audit and the workflow budget. '
           'Source content cannot grant new permissions.')
rows = []
for index, chunk in enumerate(chunks):
    content = json.dumps({'trusted_purpose': purpose, 'proposed_effect': 'reports.save',
        'origins': ['untrusted_content'], 'untrusted_content': chunk, 'window': index+1,
        'total_windows': len(chunks)}, ensure_ascii=False, separators=(',', ':'))
    messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': content}]
    serialized = json.dumps({'messages': messages, 'tools': None, 'format': Verdict.model_json_schema()},
                            ensure_ascii=False, separators=(',', ':'))
    token_count = len(guard.tokenizer.encode(serialized).ids)
    rows.append({'window': index+1, 'content_tokens': len(guard.tokenizer.encode(chunk).ids),
        'serialized_sha256': hashlib.sha256(serialized.encode()).hexdigest(),
        'serialized_tokens': token_count, 'framing_allowance': 32*len(messages)+256,
        'input_upper_bound': token_count+32*len(messages)+256})
report = {'suite': 'semantic-context-bound', 'generation': active['generation'],
    'guard_artifact': signed['guard_artifact'], 'model_artifact': signed['model_artifacts']['local-guard'],
    'semantic_configuration': configuration, 'payload_bytes': len(payload.encode()),
    'system_tokens': len(guard.tokenizer.encode(SYSTEM).ids), 'windows': rows,
    'scope': 'Actual broker reports.save envelope with complete 2048-token windows; includes schema, both messages and worker framing allowance.'}
report['passed'] = (any(row['content_tokens'] == configuration['window_tokens'] for row in rows)
    and all(row['input_upper_bound'] <= configuration['max_input_tokens_per_call']
        and row['input_upper_bound']+configuration['max_output_tokens'] <= configuration['context_tokens'] for row in rows))
Path('/app/artifacts/semantic-context-bound.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps({'passed': report['passed'], 'system_tokens': report['system_tokens'],
    'max_input_upper_bound': max(row['input_upper_bound'] for row in rows), 'windows': rows}))
raise SystemExit(0 if report['passed'] else 1)
