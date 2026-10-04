"""Atomic multi-scope reservations. Unknown work never expires into free money."""
from math import ceil
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from .db import BudgetAccount, Reservation, UsageEntry, Run, State, transaction
from .security import audit
from .telemetry import measured


class BudgetDenied(Exception):
    pass


class GenerationChanged(BudgetDenied):
    pass


def _accounts(db, run, cfg, kind, amounts):
    budgets = cfg["budgets"]
    local = cfg["local_resources"]
    billing_actor = db.get(Run, run.root_id).actor
    period = str(db.execute(text("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')::date")).scalar_one())
    rows = []
    for unit, amount in amounts.items():
        run_limit = {"usd_micros": budgets["run_usd_micros"],
                     "tokens": budgets["run_total_tokens"],
                     "slot_millis": int(local["run_slot_seconds"] * 1000)}[unit]
        tenant_limit = {"usd_micros": budgets["tenant_daily_usd_micros"],
                        "tokens": budgets.get("tenant_daily_tokens", budgets["run_total_tokens"] * 100),
                        "slot_millis": int(local["run_slot_seconds"] * 1000 * 100)}[unit]
        scopes = [(f"tenant:{run.tenant}:{period}", tenant_limit, "tenant"),
                  (f"user:{run.tenant}:{billing_actor}:{period}", tenant_limit, "user"),
                  (f"root:{run.root_id}", run_limit, "root")]
        if run.id != run.root_id:
            scopes.append((f"run:{run.id}", run_limit, "run"))
        if kind.startswith("guard"):
            guard_limit = {"usd_micros": run_limit,
                           "tokens": budgets["guard_subbudget_tokens"],
                           "slot_millis": int(local["guard_subbudget_slot_seconds"] * 1000)}[unit]
            scopes.append((f"guard:{run.root_id}", guard_limit, "guard"))
        for scope_id, limit, scope in scopes:
            rows.append((f"{scope_id}:{unit}", unit, int(amount), limit, scope, period))
    return sorted(rows)


@measured("ledger_reserve_ms")
def reserve(run_id, operation_id, kind, cfg, *, tokens=0, usd_micros=0, slot_millis=0, generation=None):
    amounts = {"tokens": int(tokens), "usd_micros": int(usd_micros), "slot_millis": int(slot_millis)}
    if any(v < 0 for v in amounts.values()):
        raise ValueError("Reservations cannot be negative")
    with transaction() as db:
        # This lock also fences an operator's limit reduction or kill switch.
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        if generation is not None and generation != state.generation:
            raise GenerationChanged("Control generation changed before reservation")
        if state.kill_switch:
            raise BudgetDenied("Kill switch prevents new reservations")
        run = db.get(Run, run_id)
        rows = _accounts(db, run, cfg, kind, amounts)
        # Create and lock the complete, sorted set in two round trips. Per-row
        # inserts/selects previously flushed each preceding update while holding
        # the global admission fence, serializing unrelated requests for much
        # longer than the actual accounting work required.
        db.execute(insert(BudgetAccount).values([
            dict(id=account_id, tenant=run.tenant, scope=scope, unit=unit,
                 limit=limit, spent=0, reserved=0, period=period)
            for account_id, unit, amount, limit, scope, period in rows
        ]).on_conflict_do_nothing())
        locked = {account.id: account for account in db.scalars(
            select(BudgetAccount).where(BudgetAccount.id.in_([row[0] for row in rows]))
            .order_by(BudgetAccount.id).with_for_update())}
        accounts = {}
        for account_id, unit, amount, limit, scope, period in rows:
            account = locked[account_id]
            account.limit = limit
            if account.spent + account.reserved + amount > limit:
                raise BudgetDenied(f"{scope} {unit} limit has no capacity for this operation")
            account.reserved += amount
            accounts[account_id] = {"unit": unit, "amount": amount}
        reservation = Reservation(tenant=run.tenant, operation_id=operation_id, kind=kind, accounts=accounts)
        db.add(reservation)
        db.flush()
        audit(db, run.tenant, "budget.reserved", {"reservation_id": reservation.id, "kind": kind,
              "amounts": amounts}, run.id, operation_id, locked_state=state)
        return reservation.id


@measured("ledger_settle_ms")
def settle(reservation_id, usage=None, *, confirmed_not_started=False):
    with transaction() as db:
        state = db.execute(select(State).where(State.id == 1).with_for_update()).scalar_one()
        reservation = db.execute(select(Reservation).where(Reservation.id == reservation_id).with_for_update()).scalar_one()
        if reservation.status in ("settled", "released"):
            return reservation.status
        if (usage is None or usage.get("usage_unknown")) and not confirmed_not_started:
            reservation.status = "usage_unknown"
            return "usage_unknown"
        usage = usage or {}
        actual = {"tokens": int(usage.get("total_tokens", usage.get("tokens", 0))),
                  "usd_micros": int(usage.get("usd_micros", 0)),
                  "slot_millis": ceil(float(usage.get("inference_slot_seconds", 0)) * 1000)}
        if any(v < 0 for v in actual.values()):
            raise ValueError("Usage cannot be negative")
        overrun = bool(usage.get("contract_violation"))
        locked = {account.id: account for account in db.scalars(
            select(BudgetAccount).where(BudgetAccount.id.in_(sorted(reservation.accounts)))
            .order_by(BudgetAccount.id).with_for_update())}
        for account_id, entry in sorted(reservation.accounts.items()):
            account = locked[account_id]
            account.reserved -= entry["amount"]
            account.spent += actual[entry["unit"]]
            overrun |= actual[entry["unit"]] > entry["amount"]
        reservation.usage = usage
        reservation.status = "released" if confirmed_not_started else "settled"
        db.add(UsageEntry(reservation_id=reservation.id, tenant=reservation.tenant, usage=usage))
        if overrun:
            db.get(State, 1).kill_switch = True
        audit(db, reservation.tenant, "budget.contract_breach" if overrun else "budget.settled",
            {"reservation_id": reservation.id, "status": reservation.status, "usage": actual},
            operation_id=reservation.operation_id, locked_state=state)
        return reservation.status


def balances(tenant):
    with transaction() as db:
        return [{"id": x.id, "scope": x.scope, "unit": x.unit, "limit": x.limit,
                 "spent": x.spent, "reserved": x.reserved, "available": max(0, x.limit-x.spent-x.reserved),
                 "overcommitted": x.spent + x.reserved > x.limit, "period": x.period}
                for x in db.scalars(select(BudgetAccount).where(BudgetAccount.tenant == tenant))]
