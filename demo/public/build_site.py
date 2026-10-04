"""Publish the reviewed presentation bytes from the verified release ZIP."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
ASSETS = {
    'ActionGate.pdf': 'ActionGate.pdf',
    'ActionGate.pptx': 'ActionGate.pptx',
    'ActionGate-pitch.mp4': 'ActionGate-demo.mp4',
    'ActionGate-cover.png': 'ActionGate-cover.png',
    'ActionGate-poster.png': 'ActionGate-poster.png',
    'ActionGate-demo.en.vtt': 'ActionGate-demo.vtt',
    'ActionGate-transcript.txt': 'ActionGate-transcript.txt',
    'ActionGate-song-pl-2026-10-04-v2.mp4': 'ActionGate-song-pl-2026-10-04-v2.mp4',
    'ActionGate-song-pl-2026-10-04-v2.en.vtt': 'ActionGate-song-pl-2026-10-04-v2.en.vtt',
    'ActionGate-song-pl-2026-10-04-v2-transcript.txt': 'ActionGate-song-pl-2026-10-04-v2-transcript.txt',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--origin', required=True)
    args = parser.parse_args()
    origin = args.origin.rstrip('/')
    parsed = urlparse(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path
            or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789.-' for c in parsed.hostname)):
        raise SystemExit('Expected a plain HTTPS demo origin')
    public_proof = json.loads((ROOT / 'artifacts/public-proxy-external.json').read_text())
    if public_proof['status'] != 'passed' or public_proof['mode'] != 'application' or public_proof['url'] != origin:
        raise SystemExit('The public application access proof is missing or belongs to another origin')
    archive_path = ROOT / 'artifacts/actiongate-submission.zip'
    archive_sha256 = sha(archive_path)
    release_scan = json.loads((ROOT / '.state/release-secret-scan.json').read_text())
    if (release_scan.get('status') != 'passed' or not release_scan.get('archive_stable')
            or release_scan.get('archive_sha256') != archive_sha256):
        raise SystemExit('The final release ZIP needs its matching successful core credential scan')
    output = ROOT / '.state/gh-pages'
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(archive_path) as archive:
        release_manifest = json.loads(archive.read('artifacts/release-manifest.json'))
        accepted_bytes = archive.read('artifacts/acceptance-report.json')
        accepted = json.loads(accepted_bytes)
        binding = release_manifest.get('acceptance', {})
        if (release_manifest.get('release_state') != 'submission-prepared'
                or binding.get('status') != 'passed' or binding.get('fresh') is not True
                or binding.get('sha256') != hashlib.sha256(accepted_bytes).hexdigest()
                or accepted.get('status') != 'passed' or accepted.get('required_verification_status') != 'passed'
                or not accepted.get('evidence') or not release_manifest.get('git_commit')
                or release_manifest.get('source_changes_after_commit')):
            raise SystemExit('The release ZIP is not an accepted committed submission package')
        for member, expected in accepted['evidence'].items():
            if hashlib.sha256(archive.read(member)).hexdigest() != expected['sha256']:
                raise SystemExit('Archived acceptance evidence differs: ' + member)
        release_files = {item['path']: item for item in release_manifest['files']}
        for source, target in ASSETS.items():
            member = 'artifacts/submission/' + source
            path = ROOT / member
            data = archive.read(member)
            digest = hashlib.sha256(data).hexdigest()
            if (not data or not path.is_file() or sha(path) != digest
                    or release_files.get(member, {}).get('sha256') != digest):
                raise SystemExit('The reviewed presentation and release ZIP differ: ' + source)
            (output / target).write_bytes(data)
            manifest.append({'source': member, 'published_name': target,
                             'sha256': digest, 'size_bytes': len(data)})
    if sha(archive_path) != archive_sha256:
        raise SystemExit('Release ZIP changed during the material build')
    html = Path(__file__).with_name('index.html').read_text(encoding='utf-8').replace('__DEMO_URL__', origin)
    (output / 'index.html').write_text(html, encoding='utf-8')
    shutil.copy2(Path(__file__).with_name('styles.css'), output / 'styles.css')
    (output / '.nojekyll').write_text('', encoding='ascii')
    site_paths = sorted(path for path in output.rglob('*') if path.is_file() and '.git' not in path.relative_to(output).parts)
    if {path.relative_to(output).as_posix() for path in site_paths} != set(ASSETS.values()) | {'index.html', 'styles.css', '.nojekyll'}:
        raise SystemExit('The publishing directory contains unexpected files')
    record = {'built_at': datetime.now(timezone.utc).isoformat(), 'demo_url': origin,
              'release_archive_sha256': archive_sha256,
              'public_application_proof_sha256': sha(ROOT / 'artifacts/public-proxy-external.json'),
              'assets': manifest,
              'site_files': [{'path': path.relative_to(output).as_posix(), 'sha256': sha(path), 'size_bytes': path.stat().st_size}
                             for path in site_paths]}
    (ROOT / '.state/public-demo/site-build.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps({'built': True, 'files': len(ASSETS) + 3, 'output': '.state/gh-pages'}))


if __name__ == '__main__':
    main()
