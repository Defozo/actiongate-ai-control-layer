import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const operation = { id: 'op-contract-001', run_id: 'run-contract-001', tenant: 'acme', tool: 'reports.publish_demo', decision: 'require_approval', status: 'waiting_approval', payload_hash: 'sha256:contract-exact-payload', policy_generation: 3, label: 'CONFIDENTIAL', arguments: { recipient: 'internal_demo_sink', report: 'Synthetic controlled report.' }, events: [] };
const policyYaml = 'schema_version: 1\nactive_profile: balanced\n';
async function fixture(page: Page, options: { role?: string; unavailable?: boolean } = {}) {
  let session = { role: options.role ?? 'admin', tenant: 'acme', demo: true };
  await page.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON();
    let data: unknown = {};
    if (pathname === '/api/budgets' && options.unavailable) return route.fulfill({ status: 503, json: { detail: 'Ledger unavailable; resource usage cannot be established.' } });
    if (pathname === '/api/events') return route.fulfill({ contentType: 'text/event-stream', body: 'event: ready\ndata: {}\n\n' });
    if (pathname === '/api/session') data = session;
    else if (pathname === '/api/demo/session') { session = { ...session, ...body }; data = session; }
    else if (pathname === '/api/overview') {
      if (options.unavailable) return route.fulfill({ status: 503, json: { detail: 'Ledger unavailable; protection state cannot be established.' } });
      data = { generation: 3, profile: 'balanced', completed_runs: 2, counts: { allow: 12, redact: 3, block: 4, require_approval: 1 }, controls: [{ id: 'identity', name: 'Identity and tenant', status: 'active', reason: 'Verified test fixture' }, { id: 'semantic', name: 'Local semantic guard', status: 'unavailable', reason: 'Test fixture: model disconnected' }] };
    } else if (pathname === '/api/operations') data = { operations: session.tenant === 'acme' ? [operation] : [] };
    else if (pathname === `/api/operations/${operation.id}`) data = operation;
    else if (pathname === '/api/runs') data = { runs: [] };
    else if (pathname === '/api/policies') data = { generation: 3, yaml: policyYaml, history: [] };
    else if (pathname === '/api/policies/validate') data = { valid: true, errors: [] };
    else if (pathname === '/api/policies/compare') data = { total: 20, evaluated: 5, insufficient_evidence: 15, changed: 1 };
    else if (pathname === '/api/feed') data = { version: 2, rules: [], signature_verified: true };
    else if (pathname === '/api/tests') data = { runs: [] };
    else if (pathname === '/api/budgets') data = {
      pricing: { status: 'valid', signature_verified: true, generation: 3, cloud_enabled: false,
        catalog: { revision: 'current-signed-price', provider: 'groq', model: 'openai/gpt-oss-20b', verified_at: '2026-10-03T00:00:00Z', valid_until: '2026-10-10T00:00:00Z', input_usd_micros_per_million: 75000, output_usd_micros_per_million: 300000, source: 'https://console.groq.com/docs/model/openai/gpt-oss-20b' },
        note: 'Current catalog only. Historical usage retains its recorded price revision and amount.' },
      accounts: [{ id: 'root:fixture:slot_millis', scope: 'root', unit: 'slot_millis', spent: 1500, reserved: 3000, limit: 9000 }],
      workers: { guard: { role: 'guard', active: null, waiting: 2, completed: 7, reserved_output_jobs: 1, stops: [{ confirmed: true }], resources: { process_rss_bytes: 104857600 } }, business: { status: 'unavailable' } },
      reservations: [{ id: 'reserve-fixture', operation_id: 'op-fixture', kind: 'execution', status: 'settled', price_revision: 'historical-price-fixture', policy_generation: 2, execution_mode: 'contract-controlled-models', accounts: { 'root:x:tokens': { unit: 'tokens', amount: 80 }, 'tenant:x:tokens': { unit: 'tokens', amount: 80 }, 'root:x:usd_micros': { unit: 'usd_micros', amount: 40 } }, usage: { total_tokens: 50, usd_micros: 20, inference_slot_seconds: 1.5 } },
        { id: 'reserve-unknown', operation_id: 'op-unknown', status: 'usage_unknown', accounts: { 'root:y:tokens': { unit: 'tokens', amount: 60 } }, usage: {} }]
    };
    else if (pathname === '/api/playground') data = { decision: 'allow', output: body.text, semantic: { verdict: 'benign', risk_level: 0 }, execution_mode: 'contract fixture' };
    else if (pathname.startsWith('/api/approvals/')) data = { status: 'completed', approved: body.approved };
    return route.fulfill({ json: data });
  });
}

test('unavailable controls remain visibly unavailable and ledger failure is never healthy', async ({ page }) => {
  await fixture(page, { unavailable: true });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Protection state unavailable' })).toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Ledger unavailable');
  await expect(page.getByText('Server records are the source of truth.')).toBeVisible();
});

test('approval sends the exact inspected payload hash and is unavailable to analyst', async ({ page }) => {
  await fixture(page);
  await page.goto('/#investigate');
  await page.getByRole('button', { name: 'op-contract-00' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.locator('.approval-card pre')).toContainText('internal_demo_sink');
  const approved = page.waitForRequest(req => req.url().includes('/api/approvals/') && req.method() === 'POST');
  await dialog.getByRole('button', { name: 'Approve exact payload' }).click();
  expect((await approved).postDataJSON()).toEqual({ approved: true, payload_hash: operation.payload_hash });
  await dialog.getByRole('button', { name: 'Close operation details' }).click();
  await page.getByLabel('Current role').selectOption('analyst');
  await expect(page.getByLabel('Current role')).toHaveValue('analyst');
  await page.getByRole('button', { name: 'op-contract-00' }).click();
  await expect(page.getByRole('button', { name: 'Approve exact payload' })).toBeDisabled();
});

test('editing a validated policy invalidates activation and comparison shows coverage gaps', async ({ page }) => {
  await fixture(page);
  await page.goto('/#policies');
  const activate = page.getByRole('button', { name: 'Activate generation' });
  await expect(activate).toBeDisabled();
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(activate).toBeEnabled();
  await page.getByLabel('Policy YAML').fill(`${policyYaml}revision: 4\n`);
  await expect(activate).toBeDisabled();
  await page.getByRole('button', { name: 'Compare decisions' }).click();
  await expect(page.getByRole('heading', { name: 'Decision comparison' })).toBeVisible();
  await expect(page.getByText('Insufficient evidence', { exact: true })).toBeVisible();
});

test('arbitrary HTML is rendered as text and never executes or requests external images', async ({ page }) => {
  await fixture(page);
  await page.goto('/#test-lab');
  const payload = '<img src="https://invalid.example/probe" onerror="window.__injected=true">';
  const externalRequests: string[] = [];
  page.on('request', req => { if (req.url().includes('invalid.example')) externalRequests.push(req.url()); });
  await page.getByLabel('Input to inspect').fill(payload);
  await page.getByRole('button', { name: 'Inspect input' }).click();
  await expect(page.locator('pre').first()).toHaveText(payload);
  expect(await page.evaluate(() => Boolean((window as any).__injected))).toBe(false);
  expect(externalRequests).toEqual([]);
  await expect(page.locator('img')).toHaveCount(0);
});

test('tenant switching clears previous tenant evidence from every query', async ({ page }) => {
  await fixture(page);
  await page.goto('/#investigate');
  await expect(page.getByRole('button', { name: 'op-contract-00' })).toBeVisible();
  await page.getByLabel('Current tenant').selectOption('globex');
  await expect(page.getByLabel('Current tenant')).toHaveValue('globex');
  await expect(page.getByRole('button', { name: 'op-contract-00' })).toHaveCount(0);
  await expect(page.getByText('No events yet')).toBeVisible();
});

test('mobile navigation and console remain usable without horizontal page overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Controls and activity' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Open menu' }).click();
  await page.getByRole('button', { name: 'Policies & feeds' }).click();
  await expect(page.getByLabel('Policy YAML')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('budgets render worker objects and scoped resource reservations without inventing cost', async ({ page }) => {
  await fixture(page);
  await page.goto('/#budgets');
  await expect(page.locator('.worker-row').filter({ hasText: 'guard' })).toContainText('7 completed · 2 queued · 1 output reservations');
  await expect(page.locator('.worker-row').filter({ hasText: 'business' })).toContainText('Telemetry unavailable');
  await expect(page.getByText('No worker telemetry returned.', { exact: false })).toHaveCount(0);
  const settled = page.getByRole('row').filter({ hasText: 'reserve-fixture' });
  await expect(settled).toContainText('80 tokens · $0.00004');
  await expect(settled).toContainText('$0.00002 · 50 tokens · 1.5 s');
  await expect(settled).not.toContainText('160 tokens');
  await expect(settled).toContainText('historical-price-fixture');
  await expect(settled).toContainText('Generation 2 · contract-controlled-models');
  await expect(settled).not.toContainText('current-signed-price');
  const pricing = page.locator('.panel').filter({ has: page.getByRole('heading', { name: 'Pricing provenance' }) });
  await expect(pricing).toContainText('current-signed-price');
  await expect(pricing).toContainText('Verified in signed control generation');
  await expect(pricing).toContainText('2026-10-10T00:00:00Z');
  await expect(pricing).toContainText('The current catalog does not reprice historical usage');
  const unknown = page.getByRole('row').filter({ hasText: 'reserve-unknown' });
  await expect(unknown).toContainText('Unknown; commitment retained');
  await expect(unknown).not.toContainText('$');
});

test('unavailable budget records never appear as zero spend or usage', async ({ page }) => {
  await fixture(page, { unavailable: true });
  await page.goto('/#budgets');
  await expect(page.getByRole('alert')).toContainText('Ledger unavailable');
  await expect(page.locator('.metric > strong')).toHaveCount(4);
  for (const metric of await page.locator('.metric > strong').all()) await expect(metric).toHaveText('n/a');
});

test('playground shows actual tenant, inspected content and the recorded model digest', async ({ page }) => {
  await fixture(page);
  const model = 'sha256:' + 'a'.repeat(64);
  await page.route('**/api/playground', route => route.fulfill({ json: {
    id: 'op-playground', tenant: 'acme', decision: 'redact', status: 'completed', policy_generation: 3,
    arguments: { content: 'Contact [REDACTED:EMAIL].' }, result: { saved: true, key: 'report-op-playground' },
    metadata: { execution_mode: 'contract-controlled-models', latency_ms: 123.4, semantic: { verdict: 'benign', risk_level: 0, model_digest: model } }
  } }));
  await page.goto('/#test-lab');
  await expect(page.getByText('PLAYGROUND TENANT: acme')).toBeVisible();
  await expect(page.getByText('Allowed input saves an internal report in acme.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Inspect input' }).click();
  await expect(page.getByRole('heading', { name: 'Inspected output' })).toBeVisible();
  await expect(page.locator('pre').first()).toHaveText('Contact [REDACTED:EMAIL].');
  await expect(page.getByRole('heading', { name: 'Released result' })).toBeVisible();
  await expect(page.locator('.metadata')).toContainText(model);
  await expect(page.locator('.metadata')).toContainText('123.4 ms');
  await page.getByLabel('Current tenant').selectOption('synthetic_test_tenant');
  await expect(page.getByText('PLAYGROUND TENANT: synthetic_test_tenant')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Inspected output' })).toHaveCount(0);
});

test('interactive advisory evidence preserves snapshot and workflow scope without a protection pass', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/tests', route => route.fulfill({ json: { runs: [{
    id: 'advisory-fixture', suite: 'all-local', status: 'advisory', passed: 7, failed: 0, advisory: 3, total: 10,
    results: [{ name: 'suite.scope', passed: true, details: { generation: 12, profile: 'observe',
      reporting_tenant: 'acme', execution_tenant: 'synthetic_test_tenant', protection_enforced: false, disabled_controls: [] } }]
  }] } }));
  await page.goto('/#test-lab');
  await page.locator('.test-history button').click();
  await expect(page.getByText('Advisory or disabled protection.', { exact: false })).toBeVisible();
  await expect(page.getByText('Matching the active policy is not a protection pass.', { exact: false })).toBeVisible();
  const metadata = page.locator('.metadata');
  await expect(metadata).toContainText('Recorded generation12');
  await expect(metadata).toContainText('Recorded profileobserve');
  await expect(metadata).toContainText('Workflow tenantsynthetic_test_tenant');
  await expect(metadata).toContainText('Report tenantacme');
  await expect(metadata).toContainText('Advisory3');
});

test('operational latency and server event backlog retain their exact scope', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/overview', route => route.fulfill({ json: {
    generation: 3, profile: 'balanced', counts: { allow: 3, block: 1 }, controls: [{ id: 'identity', status: 'active' }],
    latency: { p95_ms: 20, sample_count: 2, considered_count: 3, maximum_records: 200,
      execution_modes: { 'contract-controlled-models': 2 }, scope: 'Latest completed operations in this tenant with recorded latency; operational observations, not a benchmark.' },
    execution_outcomes: { total_operations: 6, statuses: { completed: 3, failed: 1, outcome_unknown: 1, blocked: 1 } }
  } }));
  await page.route('**/api/events?*', route => route.fulfill({ contentType: 'text/event-stream',
    body: 'event: audit\ndata: {"_delivery":{"server_backlog_age_ms":245,"clock":"database"}}\n\n' }));
  await page.goto('/');
  await expect(page.getByText('20.0 ms p95')).toBeVisible();
  await expect(page.getByText('2 / 3 recent completed operations')).toBeVisible();
  const observations = page.locator('.panel').filter({ has: page.getByRole('heading', { name: 'Operational observations' }) });
  await expect(observations).toContainText('Execution failures1');
  await expect(observations).toContainText('Unknown outcomes1');
  await expect(observations).toContainText('not a benchmark');
  await expect(page.getByText('Server backlog: 245 ms')).toBeVisible();
  // This real EventSource response is finite: EOF must remain disconnected
  // after the delayed data-refresh callback has displayed delivery metadata.
  await expect(page.getByText('Reconnecting', { exact: true })).toBeVisible();
  await expect(page.locator('.server-backlog')).toHaveAttribute('title', /Excludes network and browser rendering/);
});

test('policy history and operation details display the supplied recorded provenance', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/policies', route => route.fulfill({ json: { generation: 3, yaml: policyYaml,
    publication: { status: 'validating' }, history: [{ generation: 5, status: 'rejected', created_at: '2026-12-31T12:00:00Z' },
      { generation: 4, status: 'staging', created_at: '2026-10-03T14:00:00Z' }, { generation: 3, status: 'active', created_at: '2026-10-03T10:00:00Z' }] } }));
  await page.route('**/api/feed', route => route.fulfill({ json: { revision: 8, publisher: 'signed-feed', rules: [],
    signature_verified: true, signature_status: 'Verified in signed control generation' } }));
  await page.goto('/#policies');
  const status = page.locator('.policy-status');
  await expect(status).toContainText('PUBLISHERvalidating');
  await expect(status).toContainText('STAGED4');
  await expect(status).toContainText('ACTIVE RECORD CREATED');
  await expect(status).not.toContainText('Dec 31');
  await expect(page.getByText('Verified in signed control generation')).toBeVisible();
  await page.route('**/api/operations', route => route.fulfill({ json: { operations: [operation],
    events: [{ id: 12, event: 'operation.completed', operation_id: operation.id, tenant: 'acme', created_at: '2026-10-03T10:00:00Z' }] } }));
  await page.route('**/api/operations/' + operation.id, route => route.fulfill({ json: { ...operation, purpose: 'supplier_review',
    feed_version: 8, metadata: { execution_mode: 'contract-controlled-models', latency_ms: 42 } } }));
  await page.goto('/#investigate');
  await page.getByRole('button', { name: 'Event timeline', exact: true }).click();
  await expect(page.locator('.timeline')).toContainText('operation.completed');
  await page.getByRole('button', { name: 'Operations', exact: true }).click();
  await page.locator('.table-link').first().click();
  const details = page.getByRole('dialog').locator('.metadata');
  await expect(details).toContainText('Purposesupplier_review');
  await expect(details).toContainText('Feed version8');
  await expect(details).toContainText('Recorded latency42 ms');
});
