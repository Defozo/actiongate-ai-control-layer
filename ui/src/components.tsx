import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowDown,
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  LoaderCircle,
  Search,
  Shield,
  X,
} from "lucide-react";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import type { ColumnDef, SortingState } from "@tanstack/react-table";
import * as Dialog from "@radix-ui/react-dialog";
import type { Data } from "./api";
import { decision, number, short, time, useAction, useApi, rows } from "./api";

const tones: Record<string, string> = {
  allow: "green",
  allowed: "green",
  completed: "green",
  active: "green",
  enabled: "green",
  healthy: "green",
  ready: "green",
  passed: "green",
  pass: "green",
  settled: "green",
  connected: "green",
  redact: "blue",
  redacted: "blue",
  running: "blue",
  dispatched: "blue",
  block: "red",
  blocked: "red",
  failed: "red",
  unavailable: "red",
  error: "red",
  output_blocked: "red",
  overcommitted: "red",
  require_approval: "amber",
  waiting_approval: "amber",
  reserved: "amber",
  suspicious: "amber",
  unknown: "amber",
  usage_unknown: "amber",
  outcome_unknown: "amber",
  insufficient_evidence: "amber",
  advisory: "amber",
};
export function Badge({
  value,
  children,
}: {
  value?: unknown;
  children?: ReactNode;
}) {
  const label = String(value ?? "unknown");
  return (
    <span className={`badge ${tones[label.toLowerCase()] ?? "neutral"}`}>
      <span className="badge-dot" />
      {children ?? label.replaceAll("_", " ")}
    </span>
  );
}
export function Panel({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <div className="panel-heading">
          <div>
            <h2>{title}</h2>
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function Empty({
  title = "No events yet",
  children,
}: {
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <Shield size={26} strokeWidth={1.3} />
      <strong>{title}</strong>
      <p>
        {children ?? "Recorded activity will appear here when a workflow runs."}
      </p>
    </div>
  );
}
export function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="alert error" role="alert">
      <CircleAlert size={17} />
      <span>{error instanceof Error ? error.message : String(error)}</span>
    </div>
  );
}
export function Loading({
  text = "Reading current state…",
}: {
  text?: string;
}) {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={18} />
      {text}
    </div>
  );
}
export function JsonView({
  value,
  maxHeight = false,
}: {
  value: unknown;
  maxHeight?: boolean;
}) {
  return (
    <pre className={`json-view ${maxHeight ? "scroll-json" : ""}`}>
      {typeof value === "string"
        ? value
        : (JSON.stringify(value, null, 2) ?? "No data recorded")}
    </pre>
  );
}
export function Metric({
  label,
  value,
  detail,
  icon,
}: {
  label: string;
  value: ReactNode;
  detail: string;
  icon?: ReactNode;
}) {
  return (
    <div className="metric">
      <div className="metric-label">
        {label}
        {icon}
      </div>
      <strong>{value}</strong>
      <span>{detail}</span>
    </div>
  );
}
export function Meta({ values }: { values: Record<string, unknown> }) {
  return (
    <dl className="metadata">
      {Object.entries(values).map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>
            {value == null
              ? "Not recorded"
              : typeof value === "object"
                ? JSON.stringify(value)
                : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
export function Timeline({ events }: { events: Data[] }) {
  if (!events.length)
    return (
      <Empty title="No timeline available">
        No persisted events were returned for this selection.
      </Empty>
    );
  return (
    <ol className="timeline">
      {events.map((event, i) => (
        <li key={event.id ?? event.event_id ?? i}>
          <span
            className={`timeline-dot ${tones[String(event.decision ?? event.status)] ?? ""}`}
          />
          <div className="timeline-title">
            <strong>
              {String(
                  event.event ?? event.event_type ??
                  event.type ??
                  event.stage ??
                  event.status ??
                  "Recorded event",
              ).replaceAll("_", " ")}
            </strong>
            <time>{time(event.created_at ?? event.timestamp)}</time>
          </div>
          <p>
            {event.safe_reason ??
              event.reason ??
              event.message ??
              event.tool ??
              event.operation_id ??
              "Event recorded in the audit ledger."}
          </p>
          {event.label && <Badge value={event.label} />}
          {event.rule_ids?.length > 0 && (
            <div className="chip-list">
              {event.rule_ids.map((r: string) => (
                <code key={r}>{r}</code>
              ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
export function OperationTable({
  operations,
  onSelect,
  compact = false,
}: {
  operations: Data[];
  onSelect: (id: string) => void;
  compact?: boolean;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filter, setFilter] = useState("");
  const columns = useMemo<ColumnDef<Data>[]>(
    () => [
      {
        accessorKey: "id",
        header: "Operation",
        cell: (c) => (
          <button
            className="table-link mono"
            onClick={() => onSelect(String(c.getValue()))}
          >
            {short(c.getValue(), 14)}
            <ArrowUpRight size={13} />
          </button>
        ),
      },
      {
        accessorKey: "tool",
        header: "Action",
        cell: (c) => (
          <span className="tool-name">
            {String(c.getValue() ?? c.row.original.action ?? "Unspecified")}
          </span>
        ),
      },
      {
        id: "decision",
        accessorFn: decision,
        header: "Decision",
        cell: (c) => <Badge value={c.getValue()} />,
      },
      {
        accessorKey: "status",
        header: "Execution",
        cell: (c) => (
          <span className="muted">
            {String(c.getValue() ?? "unknown").replaceAll("_", " ")}
          </span>
        ),
      },
      {
        accessorKey: "created_at",
        header: "Time",
        cell: (c) => <time className="table-time">{time(c.getValue())}</time>,
      },
    ],
    [onSelect],
  );
  const table = useReactTable({
    data: operations,
    columns,
    state: { sorting, globalFilter: filter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: compact ? 7 : 12 } },
  });
  return (
    <>
      {!compact && (
        <div className="table-toolbar">
          <label className="search">
            <Search size={16} />
            <input
              aria-label="Search operations"
              placeholder="Search actions, decisions, IDs…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </label>
          <span className="muted small">
            {number(table.getFilteredRowModel().rows.length)} operations
          </span>
        </div>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => (
                  <th key={header.id}>
                    <button
                      className="sort-button"
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      {flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )}
                      {header.column.getIsSorted() && <ArrowDown size={12} />}
                    </button>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id}>
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!operations.length && <Empty />}
      {!compact && operations.length > 12 && (
        <div className="table-footer">
          <span>
            Page {table.getState().pagination.pageIndex + 1} of{" "}
            {table.getPageCount()}
          </span>
          <div>
            <button
              className="icon-button"
              aria-label="Previous page"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
            >
              <ChevronLeft size={16} />
            </button>
            <button
              className="icon-button"
              aria-label="Next page"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
export function OperationDetail({
  id,
  onClose,
  role,
}: {
  id: string | null;
  onClose: () => void;
  role: string;
}) {
  const operation = useApi(`/operations/${id}`, Boolean(id));
  const approval = useAction(`/approvals/${id}`);
  const data = operation.data?.operation ?? operation.data;
  const canApprove = ["admin", "approver"].includes(role);
  const pending =
    data?.status === "waiting_approval" ||
    decision(data ?? {}) === "require_approval";
  return (
    <Dialog.Root
      open={Boolean(id)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="detail-dialog">
          <div className="dialog-heading">
            <div>
              <span className="eyebrow">DECISION EVIDENCE</span>
              <Dialog.Title>Operation details</Dialog.Title>
              <Dialog.Description>
                Recorded controls, the execution outcome and settlement.
              </Dialog.Description>
            </div>
            <Dialog.Close
              className="icon-button"
              aria-label="Close operation details"
            >
              <X size={21} />
            </Dialog.Close>
          </div>
          <div className="dialog-body">
            <ErrorMessage error={operation.error} />
            {operation.isPending ? (
              <Loading />
            ) : (
              data && (
                <>
                  <div className="operation-status">
                    <Badge value={decision(data)} />
                    <Badge value={data.status} />
                    <code>{data.tool}</code>
                  </div>
                  <Meta
                    values={{
                      "Operation ID": data.id,
                      Workflow: data.run_id,
                      Tenant: data.tenant_id ?? data.tenant,
                      Purpose: data.purpose,
                      Classification: data.label ?? data.confidentiality,
                      "Label version": data.label_version,
                      "Policy generation": data.policy_generation,
                      "Feed version": data.feed_version,
                      Settlement: data.settlement_status ?? data.settlement,
                      "Execution mode": data.metadata?.execution_mode ?? "Not reported",
                      "Guard model": data.metadata?.semantic?.model_digest,
                      "Price revision": data.metadata?.price_revision,
                      "Recorded latency": data.metadata?.latency_ms == null ? undefined : `${data.metadata.latency_ms} ms`,
                      "Payload hash": data.payload_hash,
                      Created: time(data.created_at),
                    }}
                  />
                  <section className="detail-section">
                    <h3>Why this decision</h3>
                    <p>
                      {data.safe_reason ??
                        data.reason ??
                        data.decision?.reason ??
                        "See the recorded controls and audit events below."}
                    </p>
                    <div className="chip-list">
                      {(data.rule_ids ?? data.decision?.rule_ids ?? []).map(
                        (rule: string) => (
                          <code key={rule}>{rule}</code>
                        ),
                      )}
                    </div>
                  </section>
                  {data.controls && (
                    <section className="detail-section">
                      <h3>Control results</h3>
                      <JsonView value={data.controls} />
                    </section>
                  )}
                  {pending && (
                    <section className="approval-card">
                      <h3>Review the exact proposed effect</h3>
                      <p>
                        Approval is bound to this payload hash. The current
                        policy, permissions, labels and budget are checked again
                        before dispatch.
                      </p>
                      <JsonView
                        value={
                          data.arguments ??
                          data.payload ??
                          data.approval_payload ??
                          "Payload unavailable. Approval cannot proceed."
                        }
                      />
                      <code className="hash">
                        {data.payload_hash ?? "Payload hash unavailable"}
                      </code>
                      <div className="button-row">
                        <button
                          className="button primary"
                          disabled={
                            !canApprove ||
                            !data.payload_hash ||
                            approval.isPending ||
                            !(
                              data.arguments ??
                              data.payload ??
                              data.approval_payload
                            )
                          }
                          onClick={() =>
                            approval.mutate({
                              approved: true,
                              payload_hash: data.payload_hash,
                            })
                          }
                        >
                          <Check size={16} />
                          Approve exact payload
                        </button>
                        <button
                          className="button danger"
                          disabled={
                            !canApprove ||
                            !data.payload_hash ||
                            approval.isPending
                          }
                          onClick={() =>
                            approval.mutate({
                              approved: false,
                              payload_hash: data.payload_hash,
                            })
                          }
                        >
                          Reject
                        </button>
                      </div>
                      {!canApprove && (
                        <p className="muted">
                          An approver or administrator must review this
                          operation.
                        </p>
                      )}
                      <ErrorMessage error={approval.error} />
                      {approval.isSuccess && (
                        <p className="success-text">
                          Approval decision recorded. The operation state has
                          been refreshed.
                        </p>
                      )}
                    </section>
                  )}
                  {data.result !== undefined && (
                    <section className="detail-section">
                      <h3>Released result</h3>
                      <JsonView value={data.result} />
                    </section>
                  )}
                  {data.effect && (
                    <section className="detail-section">
                      <h3>Connector receipt</h3>
                      <JsonView value={data.effect} />
                    </section>
                  )}
                  <section className="detail-section">
                    <h3>Audit trail</h3>
                    <Timeline
                      events={rows(data, "events", "audit_events", "timeline")}
                    />
                  </section>
                  <details className="raw-details">
                    <summary>Complete authorized evidence</summary>
                    <JsonView value={data} maxHeight />
                  </details>
                </>
              )
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
