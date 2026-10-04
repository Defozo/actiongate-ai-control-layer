import { useState } from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  CircleAlert,
  Clock3,
  Play,
  ShieldCheck,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { money, number, rows, time, useAction, useApi } from "./api";
import type { Data } from "./api";
import {
  Badge,
  Empty,
  ErrorMessage,
  Loading,
  Meta,
  Metric,
  OperationTable,
  Panel,
} from "./components";

export const scenarios = [
  ["legal", "Legal supplier review"],
  ["pii", "PII protection"],
  ["injection", "Prompt injection"],
  ["cross_tenant", "Cross-tenant access"],
  ["public", "Public projection"],
  ["budget", "Budget exhaustion"],
  ["memory", "Memory label inheritance"],
  ["approval", "Exact payload approval"],
];
export default function Overview({
  onOperation,
  navigate,
  role,
}: {
  onOperation: (id: string) => void;
  navigate: (page: string) => void;
  role: string;
}) {
  const overview = useApi("/overview");
  const operations = useApi("/operations");
  const workflow = useAction("/demo/workflow");
  const [scenario, setScenario] = useState("legal");
  const data = overview.data ?? {};
  const ops = rows(data, "recent_operations").length
    ? rows(data, "recent_operations")
    : rows(operations.data, "operations", "items");
  const counts = data.counts ?? data.decisions ?? {};
  const controls: Data[] = Array.isArray(data.controls)
    ? data.controls
    : Object.entries(data.controls ?? {}).map(([id, item]) =>
        typeof item === "object"
          ? { id, ...(item as Data) }
          : { id, status: item },
      );
  const availableControls = controls.filter((control) =>
    ["enabled", "active", "ready", "healthy"].includes(control.status),
  );
  const unavailableControls = controls.filter((control) =>
    ["unavailable", "unknown", "failed", "disabled", "error"].includes(
      control.status,
    ),
  );
  const chart = ["allow", "redact", "block", "require_approval"].map((key) => ({
    name: {
      allow: "Allowed",
      redact: "Redacted",
      block: "Blocked",
      require_approval: "Review",
    }[key],
    total: counts[key] ?? 0,
    key,
  }));
  const total = Object.values(counts)
    .filter((v): v is number => typeof v === "number")
    .reduce((a, b) => a + b, 0);
  const feed = data.feed ?? {};
  const profile = data.profile ?? data.active_profile;
  const readinessFailed = data.services?.status === "not_ready";
  return (
    <>
      <div className="page-title">
        <div>
          <span className="eyebrow">CONTROL CENTER</span>
          <h1>Controls and activity</h1>
          <p>
            View active controls, decisions and resource use for this workspace.
          </p>
        </div>
        <button className="button" onClick={() => navigate("investigate")}>
          Investigate activity
          <ArrowUpRight size={16} />
        </button>
      </div>
      <ErrorMessage error={overview.error} />
      <section className="posture-banner">
        <div className="posture-icon">
          <ShieldCheck size={25} />
        </div>
        <div className="posture-copy">
          <h2>
            {overview.isPending
              ? "Reading protection state"
              : overview.isError
                ? "Protection state unavailable"
                : unavailableControls.length || readinessFailed
                  ? "Protection requires attention"
                  : controls.length
                    ? "Active control configuration"
                    : "Awaiting control evidence"}
          </h2>
          <p>
            {controls.length
              ? `${availableControls.length} of ${controls.length} controls configured active. ${readinessFailed ? "Required services are not ready; new work must fail closed." : unavailableControls.length ? `${unavailableControls.length} need review.` : "See service readiness and workflow evidence before judging availability."}`
              : "Control availability and readiness are reported by the gateway."}
          </p>
        </div>
        <div className="posture-meta">
          <span>ACTIVE GENERATION</span>
          <strong>
            {data.generation ?? data.policy_generation ?? "Unknown"}
          </strong>
          <Badge value={profile ?? "unknown"} />
        </div>
      </section>
      {readinessFailed && <div className="alert" role="alert"><CircleAlert size={17} /><span>Service readiness: {JSON.stringify(data.services.checks ?? data.services.reason)}. An enabled setting alone does not establish functioning protection.</span></div>}
      <div className="metrics-grid">
        <Metric
          label="Total decisions"
          value={
            overview.isPending
              ? "…"
              : (data.total_operations ??
                (Object.keys(counts).length ? number(total) : "n/a"))
          }
          detail="Persisted in the audit ledger"
          icon={<Activity size={17} />}
        />
        <Metric
          label="Blocked actions"
          value={number(counts.block ?? counts.blocked)}
          detail="Decision, separate from execution"
          icon={<ShieldCheck size={17} />}
        />
        <Metric
          label="Awaiting review"
          value={number(counts.require_approval ?? counts.waiting_approval)}
          detail="Bound to the exact proposed payload"
          icon={<Clock3 size={17} />}
        />
        <Metric
          label="Legal tasks completed"
          value={number(data.completed_runs ?? data.legal_completed)}
          detail="Confirmed workflow completions"
          icon={<ArrowUpRight size={17} />}
        />
      </div>
      <div className="overview-middle">
        <Panel
          title="Decision distribution"
          subtitle="Recorded actions across the current tenant"
          action={<span className="subtle-tag">ALL RECORDED</span>}
        >
          <div className="chart-area">
            {Object.keys(counts).length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={chart}
                  margin={{ left: -20, right: 15, top: 12, bottom: 0 }}
                >
                  <CartesianGrid
                    stroke="#2a2d30"
                    vertical={false}
                    strokeDasharray="3 5"
                  />
                  <XAxis
                    dataKey="name"
                    tick={{ fill: "#959c9f", fontSize: 11 }}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fill: "#959c9f", fontSize: 11 }}
                    allowDecimals={false}
                    axisLine={false}
                    tickLine={false}
                  />
                  <Tooltip
                    cursor={{ fill: "#ffffff06" }}
                    contentStyle={{
                      background: "#202426",
                      border: "1px solid #3a4144",
                      borderRadius: 8,
                    }}
                    itemStyle={{ color: "#c2f98a" }}
                  />
                  <Bar
                    dataKey="total"
                    name="Decisions"
                    fill="#bafa72"
                    radius={[4, 4, 0, 0]}
                    maxBarSize={55}
                  />
                </BarChart>
              </ResponsiveContainer>
            ) : overview.isPending ? (
              <Loading />
            ) : (
              <Empty title="No decision counts available">
                Run a workflow to create an auditable decision.
              </Empty>
            )}
          </div>
          <div className="chart-legend">
            <span>
              <i className="legend-dot green" />
              Allowed {number(counts.allow)}
            </span>
            <span>
              <i className="legend-dot blue" />
              Redacted {number(counts.redact)}
            </span>
            <span>
              <i className="legend-dot red" />
              Blocked {number(counts.block)}
            </span>
            <span>
              <i className="legend-dot amber" />
              Review {number(counts.require_approval)}
            </span>
          </div>
        </Panel>
        <Panel
          title="Protection status"
          subtitle="Actual controls, including unavailable dependencies"
          action={<ShieldCheck size={17} className="muted" />}
        >
          <div className="control-list">
            {controls.map((control, i) => (
              <div className="control-row" key={control.id ?? i}>
                <div className="control-symbol">
                  {control.status === "unavailable" ? (
                    <CircleAlert size={16} />
                  ) : (
                    <ShieldCheck size={16} />
                  )}
                </div>
                <div>
                  <strong>
                    {control.name ??
                      String(control.id ?? "Control").replaceAll("_", " ")}
                  </strong>
                  <p>
                    {control.reason ??
                      control.detail ??
                      control.version ??
                      "Status reported by the server"}
                  </p>
                </div>
                <Badge
                  value={
                    control.status ??
                    (control.enabled === false ? "disabled" : "unknown")
                  }
                />
              </div>
            ))}
            {!controls.length && (
              <Empty title="Controls not reported">
                The dashboard does not infer protection from process health.
              </Empty>
            )}
          </div>
        </Panel>
      </div>
      <div className="workflow-strip">
        <div>
          <span className="eyebrow">LIVE WORKFLOW</span>
          <h3>Put the boundary to work.</h3>
          <p>
            Synthetic supplier data. Real decisions, labels and audit events.
          </p>
        </div>
        <div className="workflow-actions">
          <select
            aria-label="Workflow scenario"
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
          >
            {scenarios.map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
          <button
            className="button primary"
            disabled={workflow.isPending || !["admin", "analyst"].includes(role)}
            onClick={() => workflow.mutate({ scenario })}
          >
            <Play size={15} />
            {workflow.isPending ? "Running workflow…" : "Run workflow"}
          </button>
        </div>
      </div>
      <ErrorMessage error={workflow.error} />
      {workflow.isPending && <div className="alert" role="status"><Clock3 size={17} /><span>The workflow is running with local inspection before and after tool use. This can take several minutes on CPU. New persisted operations appear below while it runs.</span></div>}
      {workflow.data && (
        <div className="inline-result">
          <Badge
            value={workflow.data.status ?? workflow.data.decision ?? "recorded"}
          />
          <span>
            {workflow.data.message ??
              workflow.data.summary ??
              "Workflow request completed. Inspect its persisted evidence below."}
          </span>
          <button
            className="text-button"
            onClick={() => navigate("investigate")}
          >
            Open investigation
            <ArrowRight size={15} />
          </button>
        </div>
      )}
      <Panel
        title="Recent decisions"
        subtitle="Policy decisions and execution outcomes are recorded separately"
        action={
          <button
            className="text-button"
            onClick={() => navigate("investigate")}
          >
            View all
            <ArrowRight size={14} />
          </button>
        }
      >
        <ErrorMessage error={operations.error} />
        <OperationTable operations={ops} onSelect={onOperation} compact />
      </Panel>
      <Panel title="Operational observations" subtitle="Retained tenant activity, separate from the acceptance benchmark">
        <div className="panel-content"><Meta values={{
          "Completed operations with latency": data.latency ? `${data.latency.sample_count} / ${data.latency.considered_count} recent completed operations` : undefined,
          "Measurement window": data.latency ? `Up to ${data.latency.maximum_records} latest completed operations` : undefined,
          "Recorded execution modes": data.latency?.execution_modes,
          "Retained operations": data.execution_outcomes?.total_operations,
          "Execution failures": data.execution_outcomes ? data.execution_outcomes.statuses?.failed ?? 0 : undefined,
          "Unknown outcomes": data.execution_outcomes ? data.execution_outcomes.statuses?.outcome_unknown ?? 0 : undefined,
          "Blocked before dispatch": data.execution_outcomes ? data.execution_outcomes.statuses?.blocked ?? 0 : undefined,
          "Output withheld": data.execution_outcomes ? data.execution_outcomes.statuses?.output_blocked ?? 0 : undefined,
        }} /><p className="small muted">{data.latency?.scope ?? "No operational latency evidence returned."}</p></div>
      </Panel>
      <div className="bottom-facts">
        <div>
          <span className="fact-icon">
            <Clock3 size={17} />
          </span>
          <div>
            <span>Recorded completed-action latency</span>
            <strong>
              {typeof data.latency?.p95_ms === "number"
                ? `${data.latency.p95_ms.toFixed(1)} ms p95`
                : data.latency_ms !== undefined
                  ? `${data.latency_ms} ms`
                  : "Not measured"}
            </strong>
          </div>
        </div>
        <div>
          <span className="fact-icon">
            <ShieldCheck size={17} />
          </span>
          <div>
            <span>Threat feed</span>
            <strong>
              {(feed.version ?? feed.revision)
                ? `Revision ${feed.version ?? feed.revision}`
                : "Not reported"}
            </strong>
            <small>
              {feed.expires_at
                ? `Expires ${time(feed.expires_at)}`
                : (feed.status ?? "Freshness unknown")}
            </small>
          </div>
        </div>
        <div>
          <span className="fact-icon">
            <Activity size={17} />
          </span>
          <div>
            <span>Accounted API spend</span>
            <strong>
              {money(data.spent_usd_micros ?? data.cost_usd_micros)}
            </strong>
            <small>
              {data.cost_mode ??
                "Read the Budgets view for reservation details"}
            </small>
          </div>
        </div>
      </div>
    </>
  );
}
