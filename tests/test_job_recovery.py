"""Real database job ownership, crash recovery and preserved obligations."""
from datetime import timedelta
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from actiongate import policies, ledger
from actiongate.db import TestRun as Job, BudgetAccount, Reservation, transaction, uid
from actiongate import test_jobs as jobs


@pytest.mark.parametrize("expiry", ["heartbeat", "deadline"])
async def test_expired_worker_cannot_publish_pass_or_free_budget(workflow_run, expiry):
    configuration = policies.snapshot()["configuration"]
    test_id = jobs.create_job("recovery-test:" + uid(), "contract", configuration)
    reservation_id = ledger.reserve(workflow_run["id"], uid(), "execution", configuration, tokens=17)
    async def expired_worker(job_id):
        jobs.persist_results(job_id, [{"name": "completed_before_crash", "passed": True}])
        with transaction() as db:
            job = db.get(Job, job_id)
            current = db.scalar(select(func.clock_timestamp()))
            if expiry == "heartbeat":
                job.heartbeat_at = current - timedelta(seconds=jobs.LEASE_SECONDS + 1)
            else:
                job.deadline_at = current - timedelta(seconds=1)
        with transaction() as db:
            assert job_id in jobs.recover_expired(db)
        with pytest.raises(jobs.JobLost):
            jobs.persist_results(job_id, [{"name": "late_fake_pass", "passed": True}], "passed")
    await jobs.run_owned(test_id, expired_worker)
    with transaction() as db:
        job = db.get(Job, test_id)
        assert job.status == "failed" and job.owner is None
        assert [row["name"] for row in job.results] == ["completed_before_crash", "job.incomplete"]
        reservation = db.get(Reservation, reservation_id)
        assert reservation.status == "reserved"
        assert db.get(BudgetAccount, f"root:{workflow_run['id']}:tokens").reserved == 17
    # Explicit test cleanup has proof that this reservation never dispatched.
    ledger.settle(reservation_id, confirmed_not_started=True)


async def test_live_heartbeat_prevents_recovery_and_duplicate_owner(platform, monkeypatch):
    monkeypatch.setattr(jobs, "HEARTBEAT_SECONDS", .02)
    tenant = "lease-test:" + uid()
    configuration = policies.snapshot()["configuration"]
    test_id = jobs.create_job(tenant, "contract", configuration)
    began, finish = asyncio.Event(), asyncio.Event()
    async def worker(job_id):
        began.set()
        await finish.wait()
        jobs.persist_results(job_id, [{"name": "completed", "passed": True}], "passed")
    first = asyncio.create_task(jobs.run_owned(test_id, worker))
    await asyncio.wait_for(began.wait(), 5)
    with transaction() as db:
        before = db.get(Job, test_id).heartbeat_at
    await asyncio.sleep(.08)
    with transaction() as db:
        assert db.get(Job, test_id).heartbeat_at > before
        assert jobs.recover_expired(db, tenant) == []
    with pytest.raises(HTTPException) as conflict:
        jobs.create_job(tenant, "contract", configuration)
    assert conflict.value.status_code == 409
    async def duplicate(job_id):
        pytest.fail("A second owner ran the same interactive job")
    await jobs.run_owned(test_id, duplicate)
    finish.set()
    await first
    with transaction() as db:
        assert db.get(Job, test_id).status == "passed"
    replacement = jobs.create_job(tenant, "contract", configuration)
    assert replacement != test_id
    await jobs.run_owned(replacement, worker)
