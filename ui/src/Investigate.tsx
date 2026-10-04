import { useRef, useState } from "react";
import { ArrowRight, Plus, Search, Send } from "lucide-react";
import { rows, short, time, useAction, useApi } from "./api";
import {
  Badge,
  Empty,
  ErrorMessage,
  JsonView,
  Loading,
  Meta,
  OperationTable,
  Panel,
  Timeline,
} from "./components";

export default function Investigate({
  onOperation,
  role,
  tenant,
}: {
  onOperation: (id: string) => void;
  role: string;
  tenant: string;
}) {
  const runs = useApi("/runs");
  const operations = useApi("/operations");
  const [runId, setRunId] = useState("");
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState("operations");
  const run = useApi(`/runs/${runId}`, Boolean(runId));
  const createRun = useAction("/runs");
  const action = useAction("/actions");
  const checkpoint = useAction("/audit/checkpoint");
  const [tool, setTool] = useState("documents.read");
  const [args, setArgs] = useState(JSON.stringify({ document_id: `supplier-${tenant}-1` }, null, 2));
  const [inputError, setInputError] = useState("");
  const argsRef = useRef<HTMLTextAreaElement>(null);
  const allRuns = rows(runs.data, "runs", "items");
  const current = run.data?.run ?? run.data;
  const runOps = runId
    ? rows(run.data, "operations").length
      ? rows(run.data, "operations")
      : rows(operations.data, "operations", "items").filter(
          (o) => o.run_id === runId,
        )
    : rows(operations.data, "operations", "items");
  const execute = () => {
    setInputError("");
    let arguments_;
    try {
      arguments_ = JSON.parse(args);
    } catch {
      setInputError("Arguments must be valid JSON.");
      argsRef.current?.focus();
      return;
    }
    action.mutate({
      run_id: runId,
      tool,
      arguments: arguments_,
      idempotency_key: crypto.randomUUID(),
    });
  };
  return (
    <>
      <div className="page-title">
        <div>
          <span className="eyebrow">INVESTIGATE</span>
          <h1>Workflow and operation history</h1>
          <p>Trace permissions, labels, decisions and the actual effect.</p>
        </div>
        <div className="button-row">{role === "admin" && <button className="button" disabled={checkpoint.isPending} onClick={() => checkpoint.mutate({})}>Sign audit checkpoint</button>}
        {["admin", "analyst"].includes(role) && <a className="button" href="/api/exports/audit.jsonl" download>
          Export audit
          <ArrowRight size={16} />
        </a>}</div>
      </div>
      <ErrorMessage error={checkpoint.error} />
      {checkpoint.data && <Panel title="Signed audit checkpoint" subtitle="Store a copy independently to detect later history changes"><div className="panel-content"><JsonView value={checkpoint.data} /></div></Panel>}
      <div className="investigation-grid">
        <Panel
          className="run-sidebar"
          title="Workflows"
          action={<span className="count-bubble">{allRuns.length}</span>}
        >
          <div className="run-search">
            <label className="search">
              <Search size={15} />
              <input
                aria-label="Search workflows"
                placeholder="Find a workflow…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
          </div>
          <button
            className={`run-option ${!runId ? "selected" : ""}`}
            onClick={() => setRunId("")}
          >
            <div>
              <strong>All operations</strong>
              <span>Every run in this tenant</span>
            </div>
            <ArrowRight size={15} />
          </button>
          <ErrorMessage error={runs.error} />
          {runs.isPending && <Loading />}
          {allRuns
            .filter((item) =>
              JSON.stringify([item.id, item.purpose, item.status])
                .toLowerCase()
                .includes(search.toLowerCase()),
            )
            .map((item) => (
              <button
                className={`run-option ${runId === item.id ? "selected" : ""}`}
                key={item.id}
                onClick={() => setRunId(item.id)}
              >
                <div>
                  <strong>
                    {String(item.purpose ?? "Workflow").replaceAll("_", " ")}
                  </strong>
                  <code>{short(item.id, 20)}</code>
                  <span>{time(item.created_at)}</span>
                </div>
                <span
                  className={`run-dot ${item.status === "completed" ? "green" : ""}`}
                />
              </button>
            ))}
          {!allRuns.length && !runs.isPending && (
            <Empty title="No workflows yet">
              Start a scenario from Overview or create a workflow below.
            </Empty>
          )}
          {["admin", "analyst"].includes(role) && (
            <div className="create-run">
              <button
                className="button full"
                disabled={createRun.isPending}
                onClick={() =>
                  createRun.mutate(
                    {
                      purpose: "supplier_review",
                      document_ids: [`supplier-${tenant}-1`],
                      allow_publish: true,
                    },
                    {
                      onSuccess: (result) =>
                        setRunId(result.id ?? result.run?.id ?? result.run_id),
                    },
                  )
                }
              >
                <Plus size={15} />
                Create workflow
              </button>
              <ErrorMessage error={createRun.error} />
            </div>
          )}
        </Panel>
        <div className="investigation-main">
          {runId && (
            <Panel title="Workflow context" subtitle={runId}>
              <ErrorMessage error={run.error} />
              {run.isPending ? (
                <Loading />
              ) : (
                current && (
                  <div className="panel-content">
                    <Meta
                      values={{
                        Purpose: current.purpose,
                        Status: current.status,
                        Classification:
                          current.label ??
                          current.context_label ??
                          current.confidentiality,
                        "Label version": current.label_version,
                        "Root workflow": current.root_run_id ?? current.root_id ?? current.id,
                        Tenant: current.tenant_id ?? current.tenant ?? tenant,
                        "Untrusted origins":
                          current.origins ?? current.untrusted_origins,
                        Created: time(current.created_at),
                      }}
                    />
                    <div className="label-flow">
                      <span>Granted purpose</span>
                      <ArrowRight size={14} />
                      <Badge
                        value={
                          current.label ?? current.context_label ?? "unknown"
                        }
                      />
                      <ArrowRight size={14} />
                      <span>Inherited by memory and delegated actions</span>
                    </div>
                  </div>
                )
              )}
            </Panel>
          )}
          <Panel
            title={runId ? "Workflow evidence" : "Operation ledger"}
            subtitle="Persisted decision history, including denied and uncertain outcomes"
          >
            <div className="tabs">
              <button
                className={tab === "operations" ? "active" : ""}
                onClick={() => setTab("operations")}
              >
                Operations
              </button>
              <button
                className={tab === "timeline" ? "active" : ""}
                onClick={() => setTab("timeline")}
              >
                Event timeline
              </button>
              {runId && ["admin", "analyst"].includes(role) && (
                <button
                  className={tab === "action" ? "active" : ""}
                  onClick={() => setTab("action")}
                >
                  Propose action
                </button>
              )}
            </div>
            <ErrorMessage error={operations.error} />
            {tab === "operations" && (
              <OperationTable operations={runOps} onSelect={onOperation} />
            )}
            {tab === "timeline" && (
              <div className="panel-content">
                <Timeline
                  events={
                    runId
                      ? rows(run.data, "events", "audit_events")
                      : rows(operations.data, "events", "audit_events")
                  }
                />
              </div>
            )}
            {tab === "action" && (
              <div className="panel-content action-form">
                <p className="muted">
                  The broker validates the workflow grant and arguments. An
                  action can require approval or be blocked before dispatch.
                </p>
                <label>
                  Registered tool
                  <select
                    value={tool}
                    onChange={(e) => setTool(e.target.value)}
                  >
                    {[
                      "documents.read",
                      "memory.read",
                      "memory.write",
                      "reports.save",
                      "reports.publish_demo",
                      "calculator.evaluate",
                    ].map((name) => (
                      <option key={name}>{name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Arguments
                  <textarea
                    ref={argsRef}
                    className="code-editor small-editor"
                    aria-label="Action arguments"
                    aria-invalid={inputError ? true : undefined}
                    aria-describedby={inputError ? "action-arguments-error" : undefined}
                    spellCheck={false}
                    value={args}
                    onChange={(e) => setArgs(e.target.value)}
                  />
                </label>
                <button
                  className="button primary"
                  disabled={!runId || action.isPending}
                  onClick={execute}
                >
                  <Send size={15} />
                  {action.isPending ? "Evaluating…" : "Propose action"}
                </button>
                {inputError ? (
                  <div id="action-arguments-error"><ErrorMessage error={inputError} /></div>
                ) : <ErrorMessage error={action.error} />}
                {action.data && (
                  <>
                    <div className="button-row">
                      <Badge value={action.data.status} />
                      <button
                        className="text-button"
                        onClick={() =>
                          onOperation(
                            action.data.id ?? action.data.operation_id,
                          )
                        }
                      >
                        Inspect evidence
                        <ArrowRight size={14} />
                      </button>
                    </div>
                    <JsonView value={action.data} />
                  </>
                )}
              </div>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
