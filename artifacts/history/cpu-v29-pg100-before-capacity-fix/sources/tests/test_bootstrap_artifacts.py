"""Preparation must follow the approved artifact, including alternate models."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest


@pytest.fixture
def preparation(tmp_path):
    source = Path(__file__).resolve().parents[1]/'scripts/runtime_bootstrap.py'
    spec = importlib.util.spec_from_file_location('bootstrap_contract', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    (tmp_path/'models').mkdir()
    manifest = {'model': 'qwen3.5:4b', 'digest': 'sha256:'+'a'*64,
        'tokenizer_commit': 'b'*40,
        'tokenizer_source': 'https://huggingface.co/Qwen/Qwen3.5-4B/resolve/'+'b'*40+'/tokenizer.json',
        'tokenizer_sha256': hashlib.sha256(b'approved artifact').hexdigest()}
    (tmp_path/'models/model-manifest.json').write_text(json.dumps(manifest))
    return module, manifest


def test_bootstrap_downloads_exact_manifest_source_without_rewriting_it(preparation, monkeypatch):
    module, manifest = preparation
    before = (module.ROOT/'models/model-manifest.json').read_bytes()
    seen = []
    def download(url, timeout):
        seen.append(url)
        return io.BytesIO(b'approved artifact')
    monkeypatch.setattr(module.urllib.request, 'urlopen', download)
    assert module.pinned_model() == 'qwen3.5:4b'
    module.prepare_tokenizer()
    assert seen == [manifest['tokenizer_source']]
    assert (module.ROOT/'models/model-manifest.json').read_bytes() == before


def test_verified_cached_tokenizer_needs_no_network(preparation, monkeypatch):
    module, _ = preparation
    (module.ROOT/'models/tokenizer.json').write_bytes(b'approved artifact')
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: pytest.fail('Unexpected download'))
    module.prepare_tokenizer()


def test_mismatched_download_does_not_replace_local_artifact(preparation, monkeypatch):
    module, _ = preparation
    target = module.ROOT/'models/tokenizer.json'
    target.write_bytes(b'previous bytes')
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'changed upstream'))
    with pytest.raises(RuntimeError, match='digest mismatch'):
        module.prepare_tokenizer()
    assert target.read_bytes() == b'previous bytes'


@pytest.mark.parametrize('name', ['../escape:tag', 'name:tag/escape', 'name:tag;command'])
def test_registry_and_shell_paths_are_bounded(preparation, name):
    module, manifest = preparation
    manifest['model'] = name
    (module.ROOT/'models/model-manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match='bounded'):
        module.pinned_model()
