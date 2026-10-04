import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowDownToLine,
  ArrowUpRight,
  BookOpen,
  ChevronDown,
  CircleDollarSign,
  FileSliders,
  FlaskConical,
  LayoutDashboard,
  LogIn,
  Menu,
  Printer,
  RefreshCw,
  Search,
  Shield,
  X,
} from "lucide-react";
import { request, time, useApi } from "./api";
import type { Data } from "./api";
import { Badge, ErrorMessage, Loading, OperationDetail } from "./components";

const Overview = lazy(() => import("./Overview"));
const Investigate = lazy(() => import("./Investigate"));
const Policies = lazy(() => import("./Policies"));
const Budgets = lazy(() => import("./Budgets"));
const TestLab = lazy(() => import("./TestLab"));
const navigation = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "investigate", label: "Investigate", icon: Search },
  { id: "policies", label: "Policies & feeds", icon: FileSliders },
  { id: "budgets", label: "Budgets", icon: CircleDollarSign },
  { id: "test-lab", label: "Test Lab", icon: FlaskConical },
];
const roles = ["admin", "analyst", "approver", "manager"];
function initialPage() {
  const value = location.hash.slice(1);
  return navigation.some((item) => item.id === value) ? value : "overview";
}

function Workspace({
  session,
  onSwitch,
}: {
  session: Data;
  onSwitch: (role: string, tenant: string) => Promise<void>;
}) {
  const [page, setPage] = useState(initialPage);
  const [menuOpen, setMenuOpen] = useState(false);
  const [operationId, setOperationId] = useState<string | null>(null);
  const [connection, setConnection] = useState("connecting");
  const [lastEvent, setLastEvent] = useState<number | null>(null);
  const [serverBacklog, setServerBacklog] = useState<number | null>(null);
  const [switchError, setSwitchError] = useState<unknown>(null);
  const [switching, setSwitching] = useState(false);
  const client = useQueryClient();
  const role = session.role ?? session.principal?.role ?? "analyst";
  const tenant =
    session.tenant ??
    session.tenant_id ??
    session.principal?.tenant ??
    "unknown";
  const navigate = useCallback((target: string) => {
    location.hash = target;
    setPage(target);
    setMenuOpen(false);
  }, []);
  const onOperation = useCallback((id: string) => setOperationId(id), []);
  useEffect(() => {
    const update = () => setPage(initialPage());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    const stream = new EventSource("/api/events?tail=true", { withCredentials: true });
    let invalidateTimer: ReturnType<typeof setTimeout> | undefined;
    let receivedAt = 0;
    let backlog: number | null = null;
    const receive = (event: Event) => {
      receivedAt = Date.now();
      try {
        const delivery = JSON.parse((event as MessageEvent).data)?._delivery;
        if (delivery) backlog = typeof delivery.server_backlog_age_ms === "number" && delivery.server_backlog_age_ms >= 0
          ? delivery.server_backlog_age_ms : null;
      } catch { /* Connection status remains separate from optional delivery metadata. */ }
      if (!invalidateTimer)
        invalidateTimer = setTimeout(() => {
          setLastEvent(receivedAt);
          setServerBacklog(backlog);
          void client.invalidateQueries();
          invalidateTimer = undefined;
        }, 100);
    };
    stream.onopen = () => setConnection("connected");
    stream.onmessage = receive;
    for (const event of [
      "audit",
      "operation",
      "policy",
      "budget",
      "update",
      "heartbeat",
      "ready",
    ])
      stream.addEventListener(event, receive);
    stream.onerror = () => setConnection("reconnecting");
    return () => {
      stream.close();
      clearTimeout(invalidateTimer);
    };
  }, [client, role, tenant]);
  async function switchIdentity(nextRole: string, nextTenant: string) {
    setSwitchError(null);
    setSwitching(true);
    try {
      await onSwitch(nextRole, nextTenant);
    } catch (error) {
      setSwitchError(error);
    } finally {
      setSwitching(false);
    }
  }
  const currentPage = navigation.find((item) => item.id === page);
  return (
    <div className="app-shell">
      <a
        href="#main-content"
        className="skip-link"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        Skip to content
      </a>
      {menuOpen && (
        <button
          className="mobile-overlay"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <a
          className="brand"
          href="#overview"
          onClick={() => navigate("overview")}
        >
          <span className="brand-mark">
            <Shield size={24} strokeWidth={2} />
            <span />
          </span>
          <strong>
            ActionGate<span>CONTROL LAYER</span>
          </strong>
        </a>
        <div className="workspace-name">
          <span className="workspace-avatar">
            {String(tenant).slice(0, 1).toUpperCase()}
          </span>
          <div>
            <strong>{String(tenant).toUpperCase()}</strong>
            <span>Local workspace</span>
          </div>
          <ChevronDown size={15} />
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map((item) => (
            <button
              key={item.id}
              className={page === item.id ? "active" : ""}
              onClick={() => navigate(item.id)}
              aria-current={page === item.id ? "page" : undefined}
            >
              <item.icon size={18} />
              <span>{item.label}</span>
              {item.id === "test-lab" && <span className="nav-lab-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-mode">
            <div>
              <span className="local-mode-dot" />
              <strong>Controlled environment</strong>
            </div>
            <p>
              Synthetic supplier workflows.
              <br />
              Evidence from the running system.
            </p>
          </div>
          <a
            className="sidebar-link"
            href="/api/docs"
            target="_blank"
            rel="noreferrer"
          >
            <BookOpen size={16} />
            API documentation
            <ArrowUpRight size={13} />
          </a>
          <div className="sidebar-footer">
            <Shield size={14} />
            <span>
              ActionGate <b>1.0</b>
            </span>
            <span className="footer-symbol">⌘</span>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button menu-button"
              aria-label={menuOpen ? "Close menu" : "Open menu"}
              onClick={() => setMenuOpen(!menuOpen)}
            >
              {menuOpen ? <X size={19} /> : <Menu size={19} />}
            </button>
            <span>Workspace</span>
            <span>/</span>
            <strong>{currentPage?.label}</strong>
          </div>
          <div className="topbar-actions">
            <span
              className={`live-status ${connection === "connected" ? "online" : ""}`}
              title={
                lastEvent
                  ? `Last event received ${time(new Date(lastEvent).toISOString())}`
                  : "Waiting for a persisted event"
              }
            >
              <i />
              {connection === "connected"
                ? "Stream connected"
                : connection === "reconnecting"
                  ? "Reconnecting"
                  : "Connecting"}
            </span>
            <span className="small muted server-backlog" title="Database-clock age from Outbox creation to server dispatch. Excludes network and browser rendering.">
              Server backlog: {serverBacklog === null ? "not measured" : `${Math.round(serverBacklog)} ms`}
            </span>
            <button
              className="icon-button"
              aria-label="Refresh data"
              title="Refresh server data"
              onClick={() => client.invalidateQueries()}
            >
              <RefreshCw size={16} />
            </button>
            <div className="identity-select">
              <select
                aria-label="Current tenant"
                value={tenant}
                disabled={switching || session.demo === false}
                onChange={(e) => switchIdentity(role, e.target.value)}
              >
                <option value="acme">Acme</option>
                <option value="globex">Globex</option>
                <option value="synthetic_test_tenant">Synthetic test tenant</option>
              </select>
              <select
                aria-label="Current role"
                value={role}
                disabled={switching || session.demo === false}
                onChange={(e) => switchIdentity(e.target.value, tenant)}
              >
                {roles.map((value) => (
                  <option value={value} key={value}>
                    {value.charAt(0).toUpperCase() + value.slice(1)}
                  </option>
                ))}
              </select>
            </div>
            <span className="user-avatar" title={role}>
              {role.slice(0, 1).toUpperCase()}
            </span>
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          <ErrorMessage error={switchError} />
          <Suspense fallback={<Loading text="Loading workspace view…" />}>
            {page === "overview" ? (
              <Overview
                onOperation={onOperation}
                navigate={navigate}
                role={role}
              />
            ) : page === "investigate" ? (
              <Investigate onOperation={onOperation} role={role} tenant={tenant} />
            ) : page === "policies" ? (
              <Policies role={role} />
            ) : page === "budgets" ? (
              <Budgets />
            ) : (
              <TestLab role={role} tenant={tenant} />
            )}
          </Suspense>
          <footer className="main-footer">
            <span>
              <Activity size={13} />
              Server records are the source of truth.
            </span>
            <div>
              <a href="/api/exports/audit.jsonl" download>
                <ArrowDownToLine size={13} />
                Audit JSONL
              </a>
              <a href="/api/exports/management.csv" download>
                <ArrowDownToLine size={13} />
                Management CSV
              </a>
              <button onClick={() => window.print()}>
                <Printer size={13} />
                Print report
              </button>
            </div>
          </footer>
        </main>
      </div>
      <OperationDetail
        id={operationId}
        onClose={() => setOperationId(null)}
        role={role}
      />
    </div>
  );
}
export default function App() {
  const session = useApi("/session");
  const client = useQueryClient();
  const [role, setRole] = useState("admin");
  const [tenant, setTenant] = useState("acme");
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  async function signIn(nextRole: string, nextTenant: string) {
    const result = await request("/demo/session", {
      role: nextRole,
      tenant: nextTenant,
    });
    await client.cancelQueries();
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== "/session" });
    client.getMutationCache().clear();
    client.setQueryData(["/session"], result.session ?? result);
    await client.invalidateQueries({ queryKey: ["/session"] });
  }
  const identity = session.data?.session ?? session.data;
  if (
    identity &&
    (identity.role || identity.principal?.role) &&
    identity.authenticated !== false
  )
    return (
      <Workspace
        key={`${identity.tenant ?? identity.tenant_id}-${identity.role}`}
        session={identity}
        onSwitch={signIn}
      />
    );
  return (
    <div className="login-screen">
      <div className="login-art">
        <div className="login-grid" />
        <a className="brand" href="#overview">
          <span className="brand-mark">
            <Shield size={25} />
            <span />
          </span>
          <strong>
            ActionGate<span>CONTROL LAYER</span>
          </strong>
        </a>
        <div className="login-message">
          <span className="eyebrow">AI CONTROL LAYER</span>
          <h1>
            Control access to
            <br />
            <em>models and tools</em>
          </h1>
          <p>
            Review permissions, data handling, budgets and recorded decisions.
          </p>
          <div className="login-flow">
            <span>Intent</span>
            <i />
            <Shield size={23} />
            <i />
            <span>Verified effect</span>
          </div>
        </div>
        <span className="login-footnote">
          AI Control Layer · Local demonstration
        </span>
      </div>
      <div className="login-form">
        <span className="eyebrow">CONTROL CENTER</span>
        <h2>Open your workspace</h2>
        <p>Use a local demonstration identity to inspect the live system.</p>
        {session.isPending ? (
          <Loading text="Checking session…" />
        ) : (
          <>
            <label>
              Workspace
              <select
                aria-label="Demo workspace"
                value={tenant}
                onChange={(e) => setTenant(e.target.value)}
              >
                <option value="acme">Acme</option>
                <option value="globex">Globex</option>
                <option value="synthetic_test_tenant">Synthetic test tenant</option>
              </select>
            </label>
            <label>
              Role
              <select
                aria-label="Demo role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
              >
                {roles.map((value) => (
                  <option value={value} key={value}>
                    {value.charAt(0).toUpperCase() + value.slice(1)}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button primary full"
              disabled={pending}
              onClick={async () => {
                setPending(true);
                setError(null);
                try {
                  await signIn(role, tenant);
                } catch (err) {
                  setError(err);
                } finally {
                  setPending(false);
                }
              }}
            >
              <LogIn size={17} />
              {pending ? "Opening workspace…" : "Enter workspace"}
            </button>
            <ErrorMessage error={error} />
            {session.error && !String(session.error).includes("401") && (
              <ErrorMessage error={session.error} />
            )}
            <div className="login-notice">
              <Badge value="local demo" />
              <span>
                Roles are enforced by the API. Local identity switching is only
                available in demonstration mode.
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
