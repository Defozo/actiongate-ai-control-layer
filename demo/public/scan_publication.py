"""Check the exact committed source, release ZIP and static-site bytes.

This supplements the required core release scan with the credentials used for
the authorized public demonstration and audio production. Values and snippets
are never printed or recorded. Inject only the named values with psst.
"""
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from scan_release_secrets import scan_stream

NAMES = ('ACTIONGATE_JURY_PASSWORD', 'ACTIONGATE_NGROK_AUTHTOKEN',
         'HACKTRIBE_PASSWORD', 'ELEVENLABS_API_KEY', 'GOOGLE_AI_STUDIO_API_KEY')


def git(*args):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, check=True)
    return result.stdout


def main():
    patterns = {}
    for name in NAMES:
        value = os.environ.get(name, '')
        if len(value) < 8:
            raise SystemExit('Required publication credential is missing or too short: ' + name)
        raw = value.encode()
        patterns[name] = {raw, value.encode('utf-16-le'), value.encode('utf-16-be'),
                          base64.b64encode(raw), base64.urlsafe_b64encode(raw),
                          quote(value, safe='').encode(), json.dumps(value)[1:-1].encode()}
    commit = git('rev-parse', 'HEAD').decode().strip()
    entries = []
    offending = []

    def inspect(name, data, scope):
        entries.append({'path': name, 'scope': scope, 'size_bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest()})
        offending.extend({'path': name, 'scope': scope, 'key_name': key}
                         for key in scan_stream(io.BytesIO(data), patterns))

    tracked = git('ls-tree', '-rz', '--name-only', commit).decode('utf-8').split('\0')
    for name in filter(None, tracked):
        inspect(name, git('show', commit + ':' + name), 'committed_source')
    # A fast-forward preserves the earlier public source export. Inspect its
    # reachable historical bytes too, even when a file was later replaced.
    history = git('rev-list', commit).decode('ascii').splitlines()[1:]
    inspected_blobs = set()
    for revision in history:
        for entry in filter(None, git('ls-tree', '-rz', revision).split(b'\0')):
            metadata, name = entry.split(b'\t', 1)
            _, kind, object_id = metadata.split()
            if kind != b'blob' or object_id in inspected_blobs:
                continue
            inspected_blobs.add(object_id)
            inspect(revision + ':' + name.decode('utf-8'),
                    git('cat-file', 'blob', object_id.decode('ascii')), 'committed_history')
    archive_path = ROOT / 'artifacts/actiongate-submission.zip'
    archive_before = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    inspect(archive_path.name, archive_path.read_bytes(), 'release_zip_raw')
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            if not entry.is_dir():
                inspect(entry.filename, archive.read(entry), 'release_zip_member')
    site = ROOT / '.state/gh-pages'
    if not (site / 'index.html').is_file():
        raise SystemExit('The final static publishing directory is missing')
    def site_paths():
        return sorted(path.relative_to(site).as_posix() for path in site.rglob('*')
                      if path.is_file() and '.git' not in path.relative_to(site).parts)
    site_before = site_paths()
    for name in site_before:
        inspect(name, (site / name).read_bytes(), 'static_site')
    stable = commit == git('rev-parse', 'HEAD').decode().strip()
    stable = stable and archive_before == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    stable = stable and site_before == site_paths()
    stable = stable and all(hashlib.sha256((site / item['path']).read_bytes()).hexdigest() == item['sha256']
                            for item in entries if item['scope'] == 'static_site')
    result = {'status': 'passed' if not offending and stable else 'failed',
              'checked_at': datetime.now(timezone.utc).isoformat(), 'git_commit': commit,
              'archive_sha256': archive_before, 'secret_names': list(NAMES), 'stable': stable,
              'historical_commits': history,
              'entries': entries, 'offending': offending}
    (ROOT / '.state/publication-secret-scan.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'git_commit': commit, 'checked_files': len(entries),
                      'secret_names': list(NAMES), 'offending': offending, 'stable': stable}))
    raise SystemExit(result['status'] != 'passed')


if __name__ == '__main__':
    main()
