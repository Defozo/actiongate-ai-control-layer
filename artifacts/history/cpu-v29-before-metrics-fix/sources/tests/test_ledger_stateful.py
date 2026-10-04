"""Stateful lifecycle properties against PostgreSQL and the real HTTP sink.

Admission and metered amounts are explicitly controlled contract fixtures, not
model inference or a provider invoice. The sink still requires its actual
connector identity and durable grant. A fresh Python process verifies recovery;
clean-install/offline acceptance separately covers complete service restarts.
"""
import copy
import json
import subprocess
import sys
from collections import defaultdict
from datetime import timedelta

import httpx
import pytest
from hypothesis import settings as property_settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule, run_state_machine_as_test
from sqlalchemy import func, select, text

from actiongate import ledger, policies
from actiongate.broker import create_run
from actiongate.contracts import RunRequest
from actiongate.db import (BudgetAccount, ConnectorReceipt, ExecutionGrant, Operation, Principal,
    Reservation, RunContext, State, UsageEntry, transaction, uid)
from actiongate.security import decrypt, digest, encrypt
from actiongate.settings import settings

UNITS = ("tokens", "usd_micros", "slot_millis")


class LifecycleMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.actor = {"sub": "stateful:" + uid(), "tenant": "synthetic-stateful-" + uid(), "role": "analyst"}
        with transaction() as db:
            db.add(Principal(id=self.actor["sub"], tenant=self.actor["tenant"], role="analyst"))
        self.root = create_run(RunRequest(document_ids=[], allow_publish=True), self.actor)["id"]
        self.child = create_run(RunRequest(document_ids=[], parent_id=self.root, allow_publish=True), self.actor)["id"]
        self.snapshot = policies.snapshot()
        self.cfg = copy.deepcopy(self.snapshot["configuration"])
        self.cfg["budgets"].update(run_total_tokens=120, run_usd_micros=120,
            tenant_daily_tokens=12000, tenant_daily_usd_micros=12000)
        self.cfg["local_resources"]["run_slot_seconds"] = .120
        self.attempts = []
        self.restarts = 0
        self.client = self.new_client()

    def new_client(self):
        return httpx.Client(base_url=settings()["demo_tools_url"],
            headers={"X-Connector-Key": settings()["connector_key"]}, timeout=20, follow_redirects=False, trust_env=False)

    def expected_accounts(self, run_id, amounts):
        with transaction() as db:
            period = str(db.scalar(text("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date")))
        scopes = [f"tenant:{self.actor['tenant']}:{period}", f"user:{self.actor['tenant']}:{self.actor['sub']}:{period}", f"root:{self.root}"]
        if run_id != self.root:
            scopes.append(f"run:{run_id}")
        return {scope + ":" + unit: {"unit": unit, "amount": amount}
            for scope in scopes for unit, amount in amounts.items()}

    def expected_balances(self):
        expected = defaultdict(lambda: [0, 0])
        for attempt in self.attempts:
            for account_id, entry in attempt["accounts"].items():
                if attempt["status"] == "settled":
                    expected[account_id][0] += attempt["actual"][entry["unit"]]
                if attempt["status"] in {"reserved", "dispatched", "usage_unknown"}:
                    expected[account_id][1] += entry["amount"]
                expected[account_id]  # Retain the zero balance of released attempts.
        return dict(expected)

    def reserve_attempt(self, amounts, *, child=False, intent=None):
        run_id, operation_id = (self.child if child else self.root), uid()
        balances = self.expected_balances()
        should_accept = all(sum(balances.get(f"root:{self.root}:{unit}", (0, 0))) + amount <= 120
            for unit, amount in amounts.items())
        before = ledger.balances(self.actor["tenant"])
        if not should_accept:
            with pytest.raises(ledger.BudgetDenied):
                ledger.reserve(run_id, operation_id, "execution", self.cfg,
                    generation=self.snapshot["generation"], **amounts)
            assert ledger.balances(self.actor["tenant"]) == before
            with transaction() as db:
                assert db.scalar(select(Reservation).where(Reservation.operation_id == operation_id)) is None
                assert db.get(ConnectorReceipt, operation_id) is None
            return None
        reservation_id = ledger.reserve(run_id, operation_id, "execution", self.cfg,
            generation=self.snapshot["generation"], **amounts)
        intent = intent or uid()
        args = {"content": "State machine controlled effect " + intent, "recipient": "internal_demo_sink"}
        attempt = {"id": operation_id, "reservation": reservation_id, "run": run_id, "intent": intent,
            "amounts": amounts, "accounts": self.expected_accounts(run_id, amounts), "args": args,
            "status": "reserved", "actual": dict.fromkeys(UNITS, 0), "effect": False, "retried": False}
        with transaction() as db:
            ctx = db.get(RunContext, self.root)
            state = db.get(State, 1)
            db.add(Operation(id=operation_id, tenant=self.actor["tenant"], actor=self.actor["sub"], run_id=run_id,
                root_id=self.root, tool="reports.publish_demo", idempotency_key=operation_id,
                payload_hash=digest(args), input_hash=digest(args), encrypted_payload=encrypt(args),
                policy_generation=state.generation, revocation_epoch=state.revocation_epoch,
                label=ctx.label, label_version=ctx.label_version,
                metadata_={"execution_mode": "contract-state-machine-controlled-admission", "owner_fence": ctx.fence}))
            assert db.get(Reservation, reservation_id).accounts == attempt["accounts"]
        self.attempts.append(attempt)
        return attempt

    def dispatch_attempt(self, attempt):
        assert attempt["status"] == "reserved"
        with transaction() as db:
            state = db.scalar(select(State).where(State.id == 1).with_for_update())
            op, ctx = db.get(Operation, attempt["id"]), db.get(RunContext, self.root)
            op.status = "dispatched"
            op.decision = "allow"
            op.stage = "dispatch"
            db.add(ExecutionGrant(operation_id=op.id, tenant=op.tenant, payload_hash=op.payload_hash,
                generation=state.generation, label_version=ctx.label_version, revocation_epoch=state.revocation_epoch,
                connector="actiongate-demo-sink", status="consumed",
                expires_at=db.scalar(select(func.clock_timestamp())) + timedelta(minutes=5)))
        response = self.client.post("/execute", json={"operation_id": attempt["id"]})
        assert response.status_code == 200, response.text
        assert response.json()["content"] == attempt["args"]["content"]
        assert response.json()["delivered"] is True
        attempt.update(status="dispatched", effect=True)

    def cancel_attempt(self, attempt):
        assert attempt["status"] == "reserved" and not attempt["effect"]
        assert ledger.settle(attempt["reservation"], confirmed_not_started=True) == "released"
        attempt["status"] = "released"
        with transaction() as db:
            db.get(Operation, attempt["id"]).status = "cancelled_before_dispatch"
        response = self.client.post("/execute", json={"operation_id": attempt["id"]})
        assert response.status_code == 409

    def timeout_attempt(self, attempt):
        assert attempt["status"] in {"dispatched", "usage_unknown"}
        before = ledger.balances(self.actor["tenant"])
        assert ledger.settle(attempt["reservation"], None) == "usage_unknown"
        attempt["status"] = "usage_unknown"
        assert ledger.balances(self.actor["tenant"]) == before

    def settle_attempt(self, attempt, ratio):
        assert attempt["status"] in {"dispatched", "usage_unknown"}
        # Multiples of five milliseconds avoid a binary-float ceil ambiguity;
        # this fixture defines the usage, rather than copying ledger's rounding.
        actual = {unit: amount * ratio // 100 for unit, amount in attempt["amounts"].items()}
        actual["slot_millis"] = actual["slot_millis"] // 5 * 5
        usage = {"total_tokens": actual["tokens"], "usd_micros": actual["usd_micros"],
            "inference_slot_seconds": actual["slot_millis"] / 1000}
        assert ledger.settle(attempt["reservation"], usage) == "settled"
        attempt.update(status="settled", actual=actual)
        with transaction() as db:
            op = db.get(Operation, attempt["id"])
            op.status, op.settlement_status = "completed", "settled"

    def restart_client(self):
        self.client.close()
        # No server or publisher mutation: a genuinely fresh process reads the
        # durable state and replays terminal settlement. No credentials printed.
        script = """
import json, sys
from sqlalchemy import select
from actiongate import ledger
from actiongate.db import ConnectorReceipt, Reservation, transaction
tenant = sys.argv[1]
with transaction() as db:
    rows = list(db.scalars(select(Reservation).where(Reservation.tenant == tenant)))
    statuses = {row.id: row.status for row in rows}
    receipts = sorted(db.scalars(select(ConnectorReceipt.operation_id).where(ConnectorReceipt.tenant == tenant)))
for row in rows:
    if row.status in ('settled', 'released'):
        assert ledger.settle(row.id, {'total_tokens': 999999, 'usd_micros': 999999}) == row.status
print(json.dumps({'balances': ledger.balances(tenant), 'statuses': statuses, 'receipts': receipts}))
"""
        result = subprocess.run([sys.executable, "-c", script, self.actor["tenant"]],
            text=True, capture_output=True, timeout=30, check=True)
        restored = json.loads(result.stdout)
        assert restored["statuses"] == {item["reservation"]: self.db_status(item) for item in self.attempts}
        assert restored["receipts"] == sorted(item["id"] for item in self.attempts if item["effect"])
        assert {row["id"]: [row["spent"], row["reserved"]] for row in restored["balances"]} == self.expected_balances()
        self.client = self.new_client()
        self.restarts += 1

    @staticmethod
    def db_status(attempt):
        return "reserved" if attempt["status"] == "dispatched" else attempt["status"]

    def choices(self, *statuses):
        return [item for item in self.attempts if item["status"] in statuses]

    @initialize()
    def required_lifecycle(self):
        # Every generated trace contains all required lifecycle transitions;
        # generated suffixes vary ordering, interleaving, costs and root/child.
        first = self.reserve_attempt(dict.fromkeys(UNITS, 30), child=True)
        self.cancel_attempt(first)
        first["retried"] = True
        retry = self.reserve_attempt(dict.fromkeys(UNITS, 30), intent=first["intent"])
        self.dispatch_attempt(retry)
        self.timeout_attempt(retry)
        self.assert_invariants()
        self.restart_client()
        self.settle_attempt(retry, 34)  # Ten units in each dimension.
        boundary = self.reserve_attempt(dict.fromkeys(UNITS, 110))
        assert boundary is not None
        assert self.reserve_attempt(dict.fromkeys(UNITS, 1)) is None
        self.cancel_attempt(boundary)

    @rule(tokens=st.integers(0, 80), usd=st.integers(0, 80), slots=st.integers(0, 16), child=st.booleans())
    def reserve(self, tokens, usd, slots, child):
        self.reserve_attempt({"tokens": tokens, "usd_micros": usd, "slot_millis": slots * 5}, child=child)

    @precondition(lambda self: bool(self.choices("reserved")))
    @rule(data=st.data())
    def dispatch(self, data):
        self.dispatch_attempt(data.draw(st.sampled_from(self.choices("reserved"))))

    @precondition(lambda self: bool(self.choices("reserved")))
    @rule(data=st.data())
    def cancel_before_start(self, data):
        self.cancel_attempt(data.draw(st.sampled_from(self.choices("reserved"))))

    @precondition(lambda self: bool(self.choices("dispatched", "usage_unknown")))
    @rule(data=st.data())
    def lost_usage_retains_full_obligation(self, data):
        self.timeout_attempt(data.draw(st.sampled_from(self.choices("dispatched", "usage_unknown"))))

    @precondition(lambda self: bool(self.choices("dispatched", "usage_unknown")))
    @rule(data=st.data(), ratio=st.integers(0, 100))
    def reconcile_known_usage(self, data, ratio):
        self.settle_attempt(data.draw(st.sampled_from(self.choices("dispatched", "usage_unknown"))), ratio)

    @precondition(lambda self: bool(self.choices("dispatched", "usage_unknown")))
    @rule(data=st.data())
    def replay_same_admitted_effect(self, data):
        attempt = data.draw(st.sampled_from(self.choices("dispatched", "usage_unknown")))
        response = self.client.post("/execute", json={"operation_id": attempt["id"]})
        assert response.status_code == 200 and response.json()["receipt_id"] == attempt["id"]

    @precondition(lambda self: any(item["status"] == "released" and not item["retried"] for item in self.attempts))
    @rule(data=st.data(), child=st.booleans())
    def retry_only_confirmed_not_started(self, data, child):
        prior = data.draw(st.sampled_from([item for item in self.attempts if item["status"] == "released" and not item["retried"]]))
        retried = self.reserve_attempt(prior["amounts"], child=child, intent=prior["intent"])
        if retried:
            prior["retried"] = True
            assert retried["id"] != prior["id"] and retried["reservation"] != prior["reservation"]

    @precondition(lambda self: bool(self.choices("settled", "released")))
    @rule(data=st.data())
    def replay_terminal_settlement_is_a_noop(self, data):
        attempt = data.draw(st.sampled_from(self.choices("settled", "released")))
        assert ledger.settle(attempt["reservation"], {"total_tokens": 999999, "usd_micros": 999999}) == attempt["status"]

    @precondition(lambda self: self.restarts < 2)
    @rule()
    def restart_and_recover_from_postgres(self):
        self.restart_client()

    @invariant()
    def assert_invariants(self):
        expected = self.expected_balances()
        with transaction() as db:
            balances = list(db.scalars(select(BudgetAccount).where(BudgetAccount.tenant == self.actor["tenant"])))
            assert {row.id: [row.spent, row.reserved] for row in balances} == expected
            for account in balances:
                assert 0 <= account.spent <= account.limit and 0 <= account.reserved <= account.limit - account.spent
            receipts = list(db.scalars(select(ConnectorReceipt).where(ConnectorReceipt.tenant == self.actor["tenant"])))
            assert {receipt.operation_id for receipt in receipts} == {item["id"] for item in self.attempts if item["effect"]}
            effects_by_intent = defaultdict(int)
            for attempt in self.attempts:
                reservation = db.get(Reservation, attempt["reservation"])
                assert reservation.status == self.db_status(attempt)
                assert reservation.accounts == attempt["accounts"]
                entry = db.get(UsageEntry, attempt["reservation"])
                assert (entry is not None) == (attempt["status"] in {"settled", "released"})
                if attempt["effect"]:
                    receipt = db.get(ConnectorReceipt, attempt["id"])
                    assert receipt.payload_hash == digest(attempt["args"])
                    assert decrypt(receipt.encrypted_result)["content"] == attempt["args"]["content"]
                    effects_by_intent[attempt["intent"]] += 1
            assert all(count == 1 for count in effects_by_intent.values())

    def teardown(self):
        self.client.close()
        # Outstanding effects remain charged in their isolated synthetic
        # namespace. Cleanup never releases an unknown financial obligation.


@pytest.mark.integration
def test_stateful_reserve_dispatch_settle_cancel_retry_restart_preserves_money_and_effects(platform):
    run_state_machine_as_test(LifecycleMachine,
        settings=property_settings(max_examples=12, stateful_step_count=28, deadline=None, database=None))
