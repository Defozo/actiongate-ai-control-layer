import { useMemo, useState } from "react";
import {
  Check,
  CheckCheck,
  FileCode2,
  GitCompareArrows,
  History,
  Radio,
  Save,
  ShieldAlert,
} from "lucide-react";
import { rows, time, useAction, useApi } from "./api";
import {
  Badge,
  Empty,
  ErrorMessage,
  JsonView,
  Loading,
  Meta,
  Panel,
} from "./components";

function Diff({ original, modified }: { original: string; modified: string }) {
  const lines = useMemo(() => {
    const oldLines = original.split("\n");
    const newLines = modified.split("\n");
    const result: { type: string; text: string; number: number }[] = [];
    for (let i = 0; i < Math.max(oldLines.length, newLines.length); i++) {
      if (oldLines[i] === newLines[i])
        result.push({ type: "same", text: oldLines[i] ?? "", number: i + 1 });
      else {
        if (oldLines[i] !== undefined)
          result.push({ type: "removed", text: oldLines[i], number: i + 1 });
        if (newLines[i] !== undefined)
          result.push({ type: "added", text: newLines[i], number: i + 1 });
      }
    }
    return result;
  }, [original, modified]);
  return (
    <pre className="diff">
      {lines.map((line, i) => (
        <div className={line.type} key={i}>
          <span>{line.number}</span>
          <b>
            {line.type === "removed" ? "−" : line.type === "added" ? "+" : " "}
          </b>
          {line.text}
        </div>
      ))}
    </pre>
  );
}
export default function Policies({ role }: { role: string }) {
  const policy = useApi("/policies");
  const feed = useApi("/feed");
  const validate = useAction("/policies/validate");
  const activate = useAction("/policies/activate");
  const compare = useAction("/policies/compare");
  const compareSynthetic = useAction("/policies/compare-synthetic");
  const testRuns = useApi("/tests");
  const publish = useAction("/feed/publish");
  const [draft, setDraft] = useState<string | undefined>();
  const [validatedText, setValidatedText] = useState<string | null>(null);
  const [draftRules, setDraftRules] = useState<string | undefined>();
  const [feedError, setFeedError] = useState("");
  const [tab, setTab] = useState("editor");
  const yaml = draft ?? policy.data?.yaml ?? "";
  const original = policy.data?.yaml ?? "";
  const rules =
    draftRules ??
    JSON.stringify(feed.data?.rules ?? feed.data?.feed?.rules ?? [], null, 2);
  const isAdmin = role === "admin";
  const valid =
    validatedText === yaml &&
    validate.isSuccess &&
    validate.data?.valid !== false &&
    !validate.data?.errors?.length;
  const history = rows(policy.data, "history", "generations");
  const syntheticReport = rows(testRuns.data, "runs").find(row => row.id === compareSynthetic.data?.id);
  function validateDraft() {
    validate.mutate({ yaml }, { onSuccess: () => setValidatedText(yaml) });
  }
  function publishFeed() {
    setFeedError("");
    let parsed;
    try {
      parsed = JSON.parse(rules);
    } catch {
      setFeedError("Feed rules must be valid JSON.");
      return;
    }
    if (!Array.isArray(parsed)) {
      setFeedError("Feed rules must be a JSON array.");
      return;
    }
    publish.mutate(
      { rules: parsed },
      { onSuccess: () => setDraftRules(undefined) },
    );
  }
  return (
    <>
      <div className="page-title">
        <div>
          <span className="eyebrow">POLICIES & FEEDS</span>
          <h1>Policy configuration and feeds</h1>
          <p>
            Review changes, evaluate their impact and activate a complete
            generation.
          </p>
        </div>
        <Badge
          value={
            policy.data?.configuration?.active_profile ?? policy.data?.active_profile ?? policy.data?.profile ?? "unknown"
          }
        />
      </div>
      <ErrorMessage error={policy.error} />
      {!isAdmin && (
        <div className="alert">
          <ShieldAlert size={17} />
          <span>
            Your role can review configurations. Activation and feed publishing
            require an administrator.
          </span>
        </div>
      )}
      <div className="policy-status">
        <div>
          <span>ACTIVE GENERATION</span>
          <strong>{policy.data?.generation ?? "Unknown"}</strong>
        </div>
        <div>
          <span>PUBLISHER</span>
          <strong>
            {policy.data?.publication?.status ?? "Not reported"}
          </strong>
        </div>
        <div>
          <span>STAGED</span>
          <strong>{history.find(row => row.status === "staging")?.generation ?? "None reported"}</strong>
        </div>
        <div>
          <span>ACTIVE RECORD CREATED</span>
          <strong>
            {time(
              history.find(row => row.generation === policy.data?.generation)?.created_at,
            )}
          </strong>
        </div>
      </div>
      <Panel
        title="Control catalog"
        subtitle="YAML is the same configuration consumed by the gateway"
        action={
          <span className="subtle-tag">
            <FileCode2 size={12} />
            control.yaml
          </span>
        }
      >
        <div className="tabs">
          <button
            className={tab === "editor" ? "active" : ""}
            onClick={() => setTab("editor")}
          >
            Editor
          </button>
          <button
            className={tab === "diff" ? "active" : ""}
            onClick={() => setTab("diff")}
          >
            Changes{yaml !== original && <span className="tab-dot" />}
          </button>
          <button
            className={tab === "history" ? "active" : ""}
            onClick={() => setTab("history")}
          >
            <History size={14} />
            History
          </button>
        </div>
        {policy.isPending ? (
          <Loading />
        ) : tab === "editor" ? (
          <textarea
            aria-label="Policy YAML"
            className="code-editor policy-editor"
            value={yaml}
            onChange={(e) => {
              setDraft(e.target.value);
              setValidatedText(null);
            }}
            readOnly={!isAdmin}
            spellCheck={false}
          />
        ) : tab === "diff" ? (
          yaml === original ? (
            <Empty title="No changes to review">
              Edit the YAML to see additions and removals here.
            </Empty>
          ) : (
            <Diff original={original} modified={yaml} />
          )
        ) : (
          <div className="panel-content">
            {history.length ? (
              <div className="history-list">
                {history.map((version, i) => (
                  <div key={version.generation ?? version.id ?? i}>
                    <div className="history-mark">
                      <History size={15} />
                    </div>
                    <div>
                      <strong>
                        Generation{" "}
                        {version.generation ?? version.revision ?? version.id}
                      </strong>
                      <p>
                        {time(version.activated_at ?? version.created_at)} ·{" "}
                        {version.actor ??
                          version.created_by ??
                          "Recorded publisher"}
                      </p>
                    </div>
                    <Badge
                      value={
                        version.status ??
                        (version.generation === policy.data?.generation
                          ? "active"
                          : "historical")
                      }
                    />
                  </div>
                ))}
              </div>
            ) : (
              <Empty title="No generation history returned" />
            )}
          </div>
        )}
        <div className="editor-actions">
          <span className="muted small">
            {yaml !== original
              ? "Unpublished changes"
              : "Matches the active catalog"}
          </span>
          <div className="button-row">
            <button
              className="button"
              disabled={!isAdmin || !yaml || validate.isPending}
              onClick={validateDraft}
            >
              <CheckCheck size={15} />
              {validate.isPending ? "Validating…" : "Validate"}
            </button>
            <button
              className="button"
              disabled={!isAdmin || !yaml || compare.isPending}
              onClick={() => compare.mutate({ yaml })}
            >
              <GitCompareArrows size={15} />
              {compare.isPending ? "Comparing…" : "Compare decisions"}
            </button>
            <button
              className="button primary"
              disabled={!isAdmin || !valid || activate.isPending}
              onClick={() =>
                activate.mutate(
                  { yaml },
                  {
                    onSuccess: () => {
                      setDraft(undefined);
                      setValidatedText(null);
                    },
                  },
                )
              }
            >
              <Save size={15} />
              {activate.isPending ? "Activating…" : "Activate generation"}
            </button>
            <button className="button" disabled={!isAdmin || !yaml || compareSynthetic.isPending || syntheticReport?.status === "running"}
              onClick={() => compareSynthetic.mutate({yaml})}>
              {syntheticReport?.status === "running" ? "Comparing synthetic corpus…" : "Compare synthetic corpus"}
            </button>
          </div>
        </div>
      </Panel>
      <ErrorMessage error={validate.error ?? activate.error ?? compare.error} />
      <ErrorMessage error={compareSynthetic.error} />
      {compareSynthetic.data && <Panel title="Synthetic policy comparison" subtitle="Real local guard observations with a separate synthetic test budget; business effects are never executed">
        <div className="panel-content"><Badge value={syntheticReport?.status ?? compareSynthetic.data.status} />
          <p className="muted">The 16 calibration cases are rescanned. A changed semantic runtime requires its own deployment and evaluation. Live progress is also available in Test Lab.</p>
          <JsonView value={syntheticReport ?? compareSynthetic.data} maxHeight />
        </div>
      </Panel>}
      {validate.data && (
        <div className={`alert ${valid ? "success" : ""}`}>
          <Check size={17} />
          <span>
            {valid
              ? "This exact draft passed validation and is ready to activate."
              : "The validation result is no longer current, or the draft contains errors."}
          </span>
          <details>
            <summary>Validation evidence</summary>
            <JsonView value={validate.data} />
          </details>
        </div>
      )}
      {activate.data && (
        <div className="alert success">
          <Check size={17} />
          <span>
            Activation recorded. Active configuration has been read back from
            the server.
          </span>
        </div>
      )}
      {compare.data && (
        <Panel
          title="Decision comparison"
          subtitle="Evaluation does not execute business tools or reproduce their side effects"
        >
          <div className="panel-content">
            <Meta
              values={{
                Evaluated: compare.data.coverage?.evaluated ?? compare.data.evaluated ?? compare.data.covered,
                "Total records":
                  compare.data.coverage?.total ?? compare.data.total ?? compare.data.total_records,
                Changed: compare.data.changed,
                "Insufficient evidence": compare.data.insufficient_evidence ?? compare.data.comparisons?.filter((c: {after: string}) => c.after === "insufficient_evidence").length,
              }}
            />
            <div className="alert">
              <ShieldAlert size={16} />
              <span>
                Historical redacted content cannot prove a new semantic or DLP
                result. Records without the necessary evidence are reported as
                insufficient evidence.
              </span>
            </div>
            <JsonView value={compare.data} maxHeight />
          </div>
        </Panel>
      )}
      <div className="policy-bottom">
        <Panel
          title="Threat feed"
          subtitle="Publish a signed revision from the approved local publisher"
          action={<Radio size={17} className="muted" />}
        >
          <div className="panel-content">
            <ErrorMessage error={feed.error} />
            <Meta
              values={{
                Revision: feed.data?.revision ?? feed.data?.version,
                Publisher: feed.data?.publisher ?? feed.data?.issuer,
                Issued: time(feed.data?.issued_at ?? feed.data?.issued),
                Expires: time(feed.data?.expires_at ?? feed.data?.expires),
                Signature:
                  feed.data?.signature_status ??
                  (feed.data?.signature_verified === true
                    ? "Verified"
                    : "Not reported"),
              }}
            />
            <label className="field-label" htmlFor="feed-rules">
              Rules (JSON array)
            </label>
            <textarea
              id="feed-rules"
              className="code-editor feed-editor"
              value={rules}
              readOnly={!isAdmin}
              spellCheck={false}
              onChange={(e) => setDraftRules(e.target.value)}
            />
            <div className="button-row">
              <button
                className="button primary"
                disabled={!isAdmin || publish.isPending}
                onClick={publishFeed}
              >
                <Radio size={15} />
                {publish.isPending ? "Publishing…" : "Publish signed revision"}
              </button>
            </div>
            <ErrorMessage error={feedError || publish.error} />
            {publish.data && (
              <div className="success-text">
                Feed publication recorded. The current revision is shown above.
              </div>
            )}
          </div>
        </Panel>
        <Panel
          title="Generation propagation"
          subtitle="A replica must hold the active snapshot to admit work"
        >
          <div className="panel-content">
            {rows(policy.data, "replicas").length ? (
              rows(policy.data, "replicas").map((replica, i) => (
                <div className="replica-row" key={replica.id ?? i}>
                  <div>
                    <strong>{replica.name ?? replica.id}</strong>
                    <p>Generation {replica.generation ?? "unknown"}</p>
                  </div>
                  <Badge value={replica.status} />
                </div>
              ))
            ) : (
              <Empty title="Replica evidence unavailable">
                No replica acknowledgements were returned by the publisher.
              </Empty>
            )}
            {policy.data?.controls && (
              <>
                <h3>Control scope</h3>
                <JsonView value={policy.data.controls} maxHeight />
              </>
            )}
          </div>
        </Panel>
      </div>
    </>
  );
}
