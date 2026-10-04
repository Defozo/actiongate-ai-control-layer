import json
import pytest
from pathlib import Path
from actiongate import recovery


def test_journal_encrypts_only_metadata_and_respects_capacity(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("ACTIONGATE_SPOOL_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ACTIONGATE_SPOOL_DIRECTORY", str(tmp_path))
    entry = {"operation_id": "test-id", "run_id": "test-run", "tenant": "synthetic_test_tenant",
             "dispatched": True, "reservation_id": "reservation", "usage": None, "status": "outcome_unknown"}
    assert recovery.spool(entry)
    saved = next(tmp_path.glob("*.sealed")).read_bytes()
    assert b"test-id" not in saved
    monkeypatch.setattr(recovery, "MAX_JOURNAL_BYTES", len(saved))
    assert recovery.spool(entry) is False
    with pytest.raises(ValueError):
        recovery.spool({**entry, "raw_prompt": "must never be written"})


def test_journal_tampering_cannot_be_reconciled(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet, InvalidToken
    monkeypatch.setenv("ACTIONGATE_SPOOL_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ACTIONGATE_SPOOL_DIRECTORY", str(tmp_path))
    recovery.spool({"operation_id": "test-id", "usage": None})
    target = next(tmp_path.glob("*.sealed"))
    target.write_bytes(target.read_bytes()[:-10]+b"tamperedxx")
    with pytest.raises(InvalidToken):
        recovery.recover_spool()
    assert target.exists()
