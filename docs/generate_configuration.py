"""Generate field documentation from the implemented Pydantic contract."""
import json
from pathlib import Path
import yaml
from actiongate.controls.schema import PolicyConfig

root = Path(__file__).resolve().parents[1]
schema = PolicyConfig.model_json_schema()
configured = yaml.safe_load((root / 'policy/control.yaml').read_text())
rows = []

def render(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('|', '\\|')

def visit(node, path='', values=None):
    if '$ref' in node:
        node = {**schema['$defs'][node['$ref'].split('/')[-1]], **{k:v for k,v in node.items() if k != '$ref'}}
    if node.get('properties'):
        for name, child in node['properties'].items():
            value = (values or {}).get(name) if isinstance(values, dict) else None
            visit(child, f'{path}.{name}'.strip('.'), value)
        return
    if path == 'flow.sinks':
        for sink, value in values.items():
            visit(node['additionalProperties'], f'{path}.{sink}', value)
        return
    constraints = {k:node[k] for k in ('type','enum','const','minimum','maximum','minLength','maxLength','pattern') if k in node}
    if 'anyOf' in node:
        constraints['allowed'] = node['anyOf']
    rows.append((path, render(values if values is not None else node.get('default','required')), render(constraints), node.get('description','Defined by the policy schema.')))

visit(schema, values=configured)
header = '# Configuration field reference\n\nGenerated from `backend/actiongate/controls/schema.py` and the checked-in `policy/control.yaml`. The configured values are defaults for this checkout, not measured performance results. Unknown fields are rejected. Cross-field validation also requires ordered confidentiality, public-only public/cloud sinks, valid registry filenames, lower review than block threshold, overlapping windows smaller than the window, a context that fits input plus output, and guard sublimits inside root limits.\n\n| Field | Checkout value / schema default | Allowed values or bounds | Meaning |\n| --- | --- | --- | --- |\n'
text = header + '\n'.join('| `' + field + '` | `' + default + '` | `' + constraints + '` | ' + description + ' |' for field, default, constraints, description in rows) + '\n'
(root/'docs/configuration-reference.md').write_text(text, encoding='utf-8')
(root/'docs/policy.schema.json').write_text(json.dumps(schema, indent=2)+'\n', encoding='utf-8')
(root/'policy/schema.json').write_text(json.dumps(schema, indent=2)+'\n', encoding='utf-8')
