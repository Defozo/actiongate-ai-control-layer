import { CircleDollarSign, Clock3, Coins, Layers3 } from "lucide-react";
import { money, number, rows, time, useApi } from "./api";
import type { Data } from "./api";
import {
  Badge,
  Empty,
  ErrorMessage,
  Loading,
  Meta,
  Metric,
  Panel,
} from "./components";

function resourceAmount(unit: string, value: number) {
  return unit === "usd_micros" ? money(value) : unit === "slot_millis" ? `${number(value / 1000)} s` : `${number(value)} ${unit}`;
}
function reservationBound(reservation: Data) {
  // The same reservation is charged to overlapping tenant, user and root scopes.
  // Display its resource bound once, never sum duplicate scope commitments.
  const amounts: Record<string, number> = {};
  for (const entry of Object.values(reservation.accounts ?? {}) as Data[]) {
    if (typeof entry.amount === "number") amounts[entry.unit] = Math.max(amounts[entry.unit] ?? 0, entry.amount);
  }
  return Object.entries(amounts).map(([unit, amount]) => resourceAmount(unit, amount)).join(" · ") || "Not recorded";
}
function reservationUsage(reservation: Data) {
  const usage = reservation.usage;
  if (usage?.usage_unknown || reservation.status === "usage_unknown") return "Unknown; commitment retained";
  if (!usage || !Object.keys(usage).length) return "Not settled";
  return [typeof usage.usd_micros === "number" ? money(usage.usd_micros) : null,
    typeof (usage.total_tokens ?? usage.tokens) === "number" ? `${number(usage.total_tokens ?? usage.tokens)} tokens` : null,
    typeof usage.inference_slot_seconds === "number" ? `${number(usage.inference_slot_seconds)} s` : null].filter(Boolean).join(" · ") || "Not recorded";
}

function BudgetBar({ account }: { account: Data }) {
  const format = (value: number) => account.unit === "tokens" ? `${number(value)} tokens` : account.unit === "slot_millis" ? `${number(value / 1000)} s` : account.unit === "operations" ? `${number(value)} ${value === 1 ? "publication" : "publications"}` : account.unit === "usd_micros" ? money(value) : `${number(value)} ${account.unit}`;
  const limit = account.limit_usd_micros ?? account.limit ?? 0;
  const spent = account.spent_usd_micros ?? account.spent ?? 0;
  const reserved = account.reserved_usd_micros ?? account.reserved ?? 0;
  const spentPercent = limit > 0 ? Math.min(100, (spent / limit) * 100) : 0;
  const reservedPercent =
    limit > 0 ? Math.min(100 - spentPercent, (reserved / limit) * 100) : 0;
  return (
    <div className="budget-account">
      <div className="budget-heading">
        <div>
          <span className="eyebrow">
            {account.scope ?? account.kind ?? "BUDGET ACCOUNT"}
          </span>
          <h3>
            {account.name ?? account.run_id ?? account.id ?? account.account_id}
          </h3>
        </div>
        <Badge
          value={
            account.status ??
            (spent + reserved > limit ? "overcommitted" : "accounted")
          }
        />
      </div>
      <div className="budget-numbers">
        <div>
          <span>Spent</span>
          <strong>{format(spent)}</strong>
        </div>
        <div>
          <span>Reserved</span>
          <strong>{format(reserved)}</strong>
        </div>
        <div>
          <span>Available</span>
          <strong>
            {format(
              account.available_usd_micros ??
                Math.max(0, limit - spent - reserved),
            )}
          </strong>
        </div>
        <div>
          <span>Limit</span>
          <strong>{format(limit)}</strong>
        </div>
      </div>
      <div
        className="budget-track"
        role="img"
        aria-label={`Spent ${format(spent)}, reserved ${format(reserved)}, limit ${format(limit)}`}
      >
        <span className="spent" style={{ width: `${spentPercent}%` }} />
        <span className="reserved" style={{ width: `${reservedPercent}%` }} />
      </div>
      <div className="budget-caption">
        <span>
          Period {account.period ?? account.period_start ?? "Not reported"}
        </span>
        <span>
          {account.price_version
            ? `Price version ${account.price_version}`
            : account.unit === "usd_micros" ? "Historical amounts; see reservation price revisions" : account.unit === "operations" ? "Approved public publications" : "Measured local resource"}
        </span>
      </div>
      {(account.unknown_usd_micros > 0 || account.uncertain_usd_micros > 0) && (
        <div className="alert">
          Uncertain commitment:{" "}
          {money(account.unknown_usd_micros ?? account.uncertain_usd_micros)}.
          Retained until the outcome is reconciled.
        </div>
      )}
    </div>
  );
}
export default function Budgets() {
  const budgets = useApi("/budgets");
  const hasData = budgets.data !== undefined;
  const data = budgets.data ?? {};
  const summary = data.summary ?? data;
  const accounts = rows(data, "accounts", "budgets");
  const tenantMoney = accounts.filter(a => a.scope === "tenant" && a.unit === "usd_micros");
  const total = (items: Data[], field: string) => items.reduce((sum, a) => sum + (Number(a[field]) || 0), 0);
  const rootTokens = total(accounts.filter(a => a.scope === "root" && a.unit === "tokens"), "spent");
  const guardTokens = total(accounts.filter(a => a.scope === "guard" && a.unit === "tokens"), "spent");
  const workers: Data[] = data.workers && !Array.isArray(data.workers)
    ? Object.entries(data.workers).map(([role, value]) => ({ role, ...(value as Data) }))
    : rows(data, "workers", "local_workers");
  const availableWorkers = workers.filter(worker => worker.status !== "unavailable");
  const workerState = (role: string) => {
    const worker = workers.find(item => item.role === role);
    return !worker ? undefined : worker.status === "unavailable" ? "Unavailable" : worker.quarantined ? "Quarantined" : worker.active ? "Occupied" : "Idle";
  };
  return (
    <>
      <div className="page-title">
        <div>
          <span className="eyebrow">BUDGETS & RESOURCES</span>
          <h1>Costs, reservations and compute</h1>
          <p>
            Track API cost, retained reservations and local compute
            independently.
          </p>
        </div>
        <span className="subtle-tag">USD · UTC PERIODS</span>
      </div>
      <ErrorMessage error={budgets.error} />
      <div className="metrics-grid">
        <Metric
          label="Settled API spend"
          value={budgets.isPending ? "…" : money(hasData ? summary.spent_usd_micros ?? total(tenantMoney, "spent") : undefined)}
          detail={summary.cost_mode ?? "Actual ledger entries"}
          icon={<CircleDollarSign size={17} />}
        />
        <Metric
          label="Reserved exposure"
          value={budgets.isPending ? "…" : money(hasData ? summary.reserved_usd_micros ?? total(tenantMoney, "reserved") : undefined)}
          detail="Includes commitments not yet settled"
          icon={<Coins size={17} />}
        />
        <Metric
          label="Business tokens"
          value={budgets.isPending ? "…" : number(hasData ? summary.model_tokens ?? Math.max(0, rootTokens - guardTokens) : undefined)}
          detail="Root usage minus protection usage"
          icon={<Layers3 size={17} />}
        />
        <Metric
          label="Guard tokens"
          value={budgets.isPending ? "…" : number(hasData ? summary.guard_tokens ?? guardTokens : undefined)}
          detail="Protection remains within parent budgets"
          icon={<Clock3 size={17} />}
        />
      </div>
      <div className="budgets-grid">
        <Panel
          title="Budget accounts"
          subtitle="Admission checks spent + reserved + new reservation against every applicable limit"
        >
          <div className="budget-legend">
            <span>
              <i className="legend-dot green" />
              Spent
            </span>
            <span>
              <i className="legend-dot amber" />
              Reserved
            </span>
            <span>
              <i className="legend-dot neutral" />
              Available
            </span>
          </div>
          {budgets.isPending ? (
            <Loading />
          ) : accounts.length ? (
            accounts.map((account, i) => (
              <BudgetBar key={account.id ?? i} account={account} />
            ))
          ) : (
            <Empty title="No budget accounts reported">
              Accounts become visible after the server creates a workflow
              budget.
            </Empty>
          )}
        </Panel>
        <Panel
          title="Local execution capacity"
          subtitle="Worker slot time is measured separately from API cost"
        >
          <div className="panel-content">
            <Meta
              values={{
                "Business worker": workerState("business"),
                "Guard worker": workerState("guard"),
                "Queued jobs": availableWorkers.length === workers.length && workers.length ? total(workers, "waiting") : undefined,
                "Settled slot seconds": hasData ? total(accounts.filter(a => a.scope === "root" && a.unit === "slot_millis"), "spent") / 1000 : undefined,
                "Reserved slot seconds": hasData ? total(accounts.filter(a => a.scope === "root" && a.unit === "slot_millis"), "reserved") / 1000 : undefined,
                "Confirmed stops in retained history": availableWorkers.length ? availableWorkers.reduce((sum, worker) => sum + rows(worker, "stops").filter(stop => stop.confirmed === true).length, 0) : undefined,
              }}
            />
            {workers.length ? (
              workers.map((worker, i) => (
                <div key={worker.id ?? i} className="worker-row">
                  <div className="worker-icon">
                    <Layers3 size={17} />
                  </div>
                  <div>
                    <strong>{worker.name ?? worker.role ?? worker.id}</strong>
                    <p>
                      {worker.status === "unavailable" ? "Telemetry unavailable" : `${number(worker.completed)} completed · ${number(worker.waiting)} queued · ${number(worker.reserved_output_jobs)} output reservations`}
                    </p>
                    {worker.resources?.process_rss_bytes !== undefined && <p>{number(Math.round(worker.resources.process_rss_bytes / 1024 / 1024))} MiB worker process RSS</p>}
                  </div>
                  <Badge value={worker.status ?? (worker.quarantined ? "quarantined" : worker.active ? "active" : "idle")} />
                </div>
              ))
            ) : (
              <div className="muted small">
                No worker telemetry returned. A request timeout alone does not
                confirm that inference stopped.
              </div>
            )}
          </div>
        </Panel>
      </div>
      <Panel
        title="Reservations & uncertain usage"
        subtitle="Reservations are not released merely because a timeout expires"
      >
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Reservation</th>
                <th>Operation</th>
                <th>Initial resource bound</th>
                <th>Recorded usage</th>
                <th>Recorded price revision</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {rows(data, "reservations").map((reservation, i) => (
                <tr key={reservation.id ?? i}>
                  <td className="mono">{reservation.id}</td>
                  <td className="mono">{reservation.operation_id}</td>
                  <td>{reservationBound(reservation)}</td>
                  <td>{reservationUsage(reservation)}</td>
                  <td>{reservation.price_revision ?? (reservation.kind?.startsWith("guard") ? "Local guard; no API price" : "Not recorded")}
                    {reservation.price_revision && <p className="small muted">Generation {reservation.policy_generation ?? "not recorded"} · {reservation.execution_mode ?? "mode not recorded"}</p>}
                  </td>
                  <td>
                    <Badge value={reservation.status} />
                  </td>
                  <td>{time(reservation.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!rows(data, "reservations").length && (
          <Empty title="No reservation rows returned">
            Uncertain usage is retained in the ledger for reconciliation.
          </Empty>
        )}
      </Panel>
      <Panel title="Pricing provenance" subtitle="The current catalog does not reprice historical usage">
          <div className="panel-content">
            {data.pricing ? <>
              <Badge value={data.pricing.status} />
              <Meta values={{
                "Current revision": data.pricing.catalog?.revision,
                "Signature": data.pricing.signature_verified ? "Verified in signed control generation" : "Not verified",
                "Generation": data.pricing.generation,
                "Cloud execution": data.pricing.cloud_enabled === true ? "Enabled" : data.pricing.cloud_enabled === false ? "Disabled" : undefined,
                "Provider / model": data.pricing.catalog ? `${data.pricing.catalog.provider} / ${data.pricing.catalog.model}` : undefined,
                "Price reviewed": data.pricing.catalog?.verified_at,
                "Valid until": data.pricing.catalog?.valid_until,
                "Input per million tokens": data.pricing.catalog ? money(data.pricing.catalog.input_usd_micros_per_million) : undefined,
                "Output per million tokens": data.pricing.catalog ? money(data.pricing.catalog.output_usd_micros_per_million) : undefined,
                "Catalog source": data.pricing.catalog?.source,
              }} />
              <p className="small muted">{data.pricing.note}</p>
            </> : <p className="muted">Pricing provenance unavailable.</p>}
          </div>
        </Panel>
    </>
  );
}
