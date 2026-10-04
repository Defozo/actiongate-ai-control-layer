"""Recover lost interactive workers without retrying effects or freeing budgets."""
import asyncio
from contextvars import ContextVar
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, func

from .db import TestRun as Job, State, transaction, uid

__test__ = False
HEARTBEAT_SECONDS = 10
LEASE_SECONDS = 90
_owner = ContextVar("test_job_owner", default=None)


class JobLost(RuntimeError):
    pass


def recover_expired(db, tenant=None):
    current = db.scalar(select(func.clock_timestamp()))
    query = select(Job).where(Job.status == "running").with_for_update()
    if tenant is not None:
        query = query.where(Job.tenant == tenant)
    recovered = []
    for job in db.scalars(query):
        heartbeat = job.heartbeat_at or job.created_at
        reason = "deadline_elapsed" if job.deadline_at and current >= job.deadline_at else (
            "worker_lease_expired" if current - heartbeat >= timedelta(seconds=LEASE_SECONDS) else None)
        if reason:
            job.status, job.owner = "failed", None
            job.results = [*job.results, {"name": "job.incomplete", "passed": False,
                "details": {"reason": reason, "automatic_retry": False, "budget_reservations_preserved": True}}]
            recovered.append(job.id)
    return recovered


def create_job(tenant, suite, configuration):
    # A job has a finite deadline derived from the allowed workflow lifetime.
    # The heartbeat detects a process crash sooner, including during model calls.
    workflows = 16 if suite == "synthetic-policy-replay" else 4 if suite == "all-local" else 1
    duration = workflows * configuration["budgets"]["run_deadline_seconds"] + LEASE_SECONDS
    with transaction() as db:
        db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        recover_expired(db, tenant)
        if db.scalar(select(Job).where(Job.tenant == tenant, Job.status == "running")):
            raise HTTPException(409, "A test job is already running for this tenant")
        current = db.scalar(select(func.clock_timestamp()))
        job = Job(tenant=tenant, suite=suite, heartbeat_at=current,
                  deadline_at=current + timedelta(seconds=duration))
        db.add(job)
        db.flush()
        return job.id


def persist_results(test_id, records, status="running"):
    with transaction() as db:
        job = db.scalar(select(Job).where(Job.id == test_id).with_for_update())
        current = db.scalar(select(func.clock_timestamp()))
        if (not job or job.status != "running" or job.owner != _owner.get() or not job.owner
                or not job.deadline_at or current >= job.deadline_at
                or not job.heartbeat_at or current - job.heartbeat_at >= timedelta(seconds=LEASE_SECONDS)):
            raise JobLost("Interactive test ownership expired")
        job.results, job.status = list(records), status
        if status != "running":
            job.owner = None


async def run_owned(test_id, worker, *args):
    owner = uid()
    with transaction() as db:
        recover_expired(db)
        job = db.scalar(select(Job).where(Job.id == test_id).with_for_update())
        if not job or job.status != "running" or job.owner:
            return
        current = db.scalar(select(func.clock_timestamp()))
        job.owner, job.heartbeat_at = owner, current
    token = _owner.set(owner)
    def interrupted(reason):
        with transaction() as db:
            recover_expired(db)
            job = db.scalar(select(Job).where(Job.id == test_id).with_for_update())
            if job and job.status == "running" and job.owner == owner:
                job.status, job.owner = "failed", None
                job.results = [*job.results, {"name": "job.incomplete", "passed": False,
                    "details": {"reason": reason, "automatic_retry": False, "budget_reservations_preserved": True}}]
    async def heartbeat():
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            with transaction() as db:
                recover_expired(db)
                job = db.scalar(select(Job).where(Job.id == test_id).with_for_update())
                if not job or job.status != "running" or job.owner != owner:
                    return
                job.heartbeat_at = db.scalar(select(func.clock_timestamp()))
    work, keepalive = asyncio.create_task(worker(test_id, *args)), asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait((work, keepalive), return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            await work
        else:
            # Let admitted work settle. Workers recheck their persisted lease
            # before every next action and cannot overwrite an expired result.
            await work
    except asyncio.CancelledError:
        interrupted("worker_cancelled")
        raise
    except Exception:
        interrupted("worker_failed")
    finally:
        keepalive.cancel()
        if not work.done():
            work.cancel()
        await asyncio.gather(work, keepalive, return_exceptions=True)
        _owner.reset(token)


async def recover_jobs_forever():
    while True:
        try:
            with transaction() as db:
                recover_expired(db)
        except Exception:
            # An unavailable authority cannot authorize retries or free resources.
            pass
        await asyncio.sleep(HEARTBEAT_SECONDS)
