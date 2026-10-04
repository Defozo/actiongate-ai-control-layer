"""Inventory the prepared GPU image without starting models or accessing a network."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'actiongate-worker-gpu:0.1.0'
INSPECT = '''import hashlib,json,pathlib
root=pathlib.Path('/usr/lib/ollama')
libraries=[]
for path in sorted(root.glob('cuda_*/*')):
    if path.is_file() and not path.is_symlink():
        digest=hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda:source.read(8*1024*1024),b''):
                digest.update(chunk)
        libraries.append({'path':str(path),'name':path.name,'size_bytes':path.stat().st_size,'sha256':digest.hexdigest()})
print(json.dumps({'libraries':libraries,'backend_directories':[p.name for p in sorted(root.glob('cuda_*'))],
                  'license_files':[str(p) for p in pathlib.Path('/usr/share/licenses/actiongate-gpu').glob('*')]}))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument('--dockerfile', default='runtime/Dockerfile')
    parser.add_argument('--output', default='artifacts/gpu-library-inventory.json')
    args = parser.parse_args()
    source_image = re.search(r'^FROM (ollama/ollama@sha256:[0-9a-f]{64}) AS ollama$', (ROOT/args.dockerfile).read_text(), re.M)
    if source_image is None:
        raise RuntimeError('GPU runtime source image must be pinned in its Dockerfile')
    identity = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.image], text=True))[0]
    value = json.loads(subprocess.check_output([
        'docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--entrypoint', 'python', args.image, '-c', INSPECT,
    ], text=True))
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'Actual CUDA backend files in the final optional GPU image. CPU image contains no CUDA backend.',
        'source_image': source_image.group(1),
        'observed_image_id': identity['Id'],
        'image': args.image,
        'runtime_backend': 'cuda_v12',
        **value,
    }
    if value['backend_directories'] != ['cuda_v12'] or len(value['libraries']) != 4:
        raise RuntimeError('Prepared image contains unexpected CUDA backends or libraries')
    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'image_id': report['observed_image_id'], 'libraries': len(value['libraries']),
                      'backend': report['runtime_backend']}))


if __name__ == '__main__':
    main()
