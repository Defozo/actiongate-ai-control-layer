"""Actual shared-GPU inference and independent supervised cancellation proof."""
import asyncio
import json
import time

from actiongate.policies import snapshot, verify_stored_snapshot
from actiongate.runtime import RuntimeFailure, WorkerClient


async def main():
    active = snapshot()
    signed = verify_stored_snapshot(active)
    guard, business = WorkerClient('guard'), WorkerClient('business')
    before = await business.status()
    artifact = signed['model_artifacts']['local-business']
    task = asyncio.create_task(business.infer(
        [{'role': 'user', 'content': 'List the integers from 1 through 150, separated by spaces.'}],
        512, deadline_seconds=120, generation=active['generation'],
        expected_model_digest=artifact['manifest']['digest'], expected_manifest_sha256=artifact['sha256']))
    until = time.monotonic()+5
    concurrent = False
    while time.monotonic()<until and not task.done():
        if (await business.status())['active']:
            concurrent = True
            break
        await asyncio.sleep(.01)
    report = {'passed':False, 'business_observed_active_before_guard_stop':concurrent,
              'gpu_isolation_scope':'separate process groups and queues on shared physical GPU; no VRAM partition'}
    try:
        await guard.infer([{'role':'user','content':'List all integers from 1 through 1000.'}],
                          256, deadline_seconds=.05, generation=active['generation'])
        report['guard_stop'] = {'confirmed':False, 'reason':'unexpected_completion'}
    except RuntimeFailure as exc:
        report['guard_stop'] = exc.stop
        report['guard_usage'] = exc.usage
    report['business_active_after_guard_stop'] = bool((await business.status())['active'])
    result = await task
    after = await business.status()
    report['business_usage'] = result['usage']
    report['business_epoch_unchanged'] = before['epoch']==after['epoch']
    report['business_completed'] = result['usage']['completion_tokens']>0
    report['passed'] = bool(concurrent and report['business_active_after_guard_stop'] and report['guard_stop'] and report['guard_stop'].get('confirmed')
                            and report['business_epoch_unchanged'] and report['business_completed'])
    print(json.dumps(report,indent=2))
    return report['passed']


if __name__=='__main__':
    raise SystemExit(0 if asyncio.run(main()) else 1)
