"""Run inside the prepared worker image with no network and a synthetic token.

These supervisor invariants do not start or substitute for real model inference.
The functional preflight separately exercises the actual process watchdog.
"""
import asyncio
import copy
import json
import time
from unittest.mock import patch
from types import SimpleNamespace

import httpx
from fastapi import HTTPException
import worker


async def main():
    auth = "Bearer " + worker.TOKEN
    tool_message = worker.Message(role="tool", content="5", tool_call_id="call_1", tool_name="calculator")
    assert tool_message.model_dump(exclude_none=True)["tool_call_id"] == "call_1"
    configuration = {
        "semantic": {"deadline_seconds": 90, "context_tokens": 8192, "max_input_tokens_per_call": 4096, "max_output_tokens": 256, "max_windows": 8},
        "local_resources": {"business_slots": 1, "guard_slots": 1, "require_confirmed_stop": True, "max_waiting_jobs": 1, "queue_wait_seconds": 10, "business_call_deadline_seconds": 120},
    }
    result = await worker.configure(worker.Configuration(generation=1, config=configuration), auth)
    assert result["prepared"]
    changed = copy.deepcopy(configuration)
    changed["local_resources"]["max_waiting_jobs"] = 2
    try:
        await worker.configure(worker.Configuration(generation=1, config=changed), auth)
        raise AssertionError("Immutable generation changed")
    except HTTPException as error:
        assert error.status_code == 409
    changed["local_resources"]["guard_slots"] = True
    try:
        await worker.configure(worker.Configuration(generation=2, config=changed), auth)
        raise AssertionError("Boolean slot configuration accepted")
    except HTTPException as error:
        assert error.status_code == 422

    ticket = await worker.reserve_ticket(worker.TicketRequest(windows=2, generation=1), auth)
    try:
        await worker.reserve_ticket(worker.TicketRequest(windows=1, generation=1), auth)
        raise AssertionError("Full inspection queue accepted more work")
    except HTTPException as error:
        assert error.status_code == 429
    worker.supervisor.tickets[ticket["ticket_id"]]["expires_monotonic"] = time.monotonic() - 1
    worker.supervisor.active = {"ticket_id": ticket["ticket_id"]}
    worker.supervisor.purge_tickets()
    assert ticket["ticket_id"] in worker.supervisor.tickets
    worker.supervisor.active = None
    replacement = await worker.reserve_ticket(worker.TicketRequest(windows=1, generation=1), auth)
    assert ticket["ticket_id"] not in worker.supervisor.tickets
    assert replacement["ticket_id"] in worker.supervisor.tickets
    worker.supervisor.epoch += 1
    worker.supervisor.purge_tickets()
    assert not worker.supervisor.tickets
    assert 1 in worker.supervisor.configurations
    long_ticket = await worker.reserve_ticket(worker.TicketRequest(windows=9, generation=1), auth)
    assert worker.supervisor.tickets[long_ticket['ticket_id']]['remaining'] == 9
    try:
        await worker.reserve_ticket(worker.TicketRequest(windows=10, generation=1), auth)
        raise AssertionError('More than source windows plus one goal assessment admitted')
    except HTTPException as error:
        assert error.status_code == 422
    worker.supervisor.tickets.clear()
    events = []
    async def stop(reason):
        assert worker.supervisor.lock.locked()
        events.append(reason)
        return {"confirmed": True, "reason": reason}
    async def start():
        assert worker.supervisor.lock.locked()
        worker.supervisor.epoch += 1
    client_class = httpx.AsyncClient
    def incomplete_client(**kwargs):
        return client_class(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"done": True})), **kwargs)
    with patch.object(worker.httpx, "AsyncClient", incomplete_client), patch.object(worker.supervisor, "stop", stop), patch.object(worker.supervisor, "start", start):
        try:
            await worker.infer(worker.Inference(messages=[worker.Message(role="user", content="Synthetic")], max_tokens=16), auth)
            raise AssertionError("Missing usage accepted")
        except HTTPException as error:
            assert error.status_code == 502
            assert error.detail["stop"]["confirmed"]
            assert error.detail["usage"]["usage_unknown"]
    assert events == ["incomplete_result_or_usage"]
    assert not worker.supervisor.lock.locked()
    assert worker.supervisor.active is None
    observations = []
    def delayed_unload(request):
        assert worker.supervisor.lock.locked()
        if request.url.path == '/api/ps':
            observations.append(request.url.path)
            return httpx.Response(200,json={'models':[{'name':worker.MODEL}] if len(observations)<3 else []})
        return httpx.Response(200,json={'done':True})
    def unload_client(**kwargs):
        return client_class(transport=httpx.MockTransport(delayed_unload),**kwargs)
    with patch.object(worker.httpx,'AsyncClient',unload_client):
        unloaded = await worker.unload(auth)
    assert unloaded['confirmed'] and len(observations)==3
    assert not worker.supervisor.lock.locked()
    captured_env = {}
    def launch(*args, **kwargs):
        captured_env.update(kwargs['env'])
        return SimpleNamespace(poll=lambda: None, pid=123456)
    version = worker.MANIFEST['runtime'].split(':', 1)[1]
    def runtime_response(request):
        return httpx.Response(200, json={'version': version} if request.url.path == '/api/version'
            else {'models': [{'name': worker.MODEL, 'digest': worker.MANIFEST['digest']}]})
    def runtime_client(**kwargs):
        return client_class(transport=httpx.MockTransport(runtime_response), **kwargs)
    with patch.object(worker.subprocess, 'Popen', launch), patch.object(worker.httpx, 'AsyncClient', runtime_client):
        supervisor = worker.Supervisor()
        await supervisor.start()
        assert supervisor.runtime == worker.MANIFEST['runtime']
        assert captured_env['LLAMA_ARG_CACHE_RAM'] == '512'
        assert 'WORKER_TOKEN' not in captured_env
        version = 'unsupported-version'
        try:
            await worker.Supervisor().start()
            raise AssertionError('Runtime mismatch was admitted')
        except RuntimeError as error:
            assert 'Runtime version differs' in str(error)
    print(json.dumps({"passed": True, "checks": ["tool_call_id_preserved", "immutable_generation", "strict_resource_types", "queue_capacity", "active_ticket_protected", "expired_ticket_reclaims_capacity", "epoch_invalidates_tickets", "configuration_survives_process_epoch", "incomplete_result_confirmed_stop_before_release", "missing_usage_not_invented", "asynchronous_unload_holds_slot_until_absence", "multiwindow_ticket_includes_one_bounded_goal_assessment", "child_cache_bound_and_no_auth_secret", "actual_runtime_version_matches_manifest_before_ready"]}))


asyncio.run(main())
