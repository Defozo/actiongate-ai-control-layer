import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

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

test('closed mobile navigation stays out of keyboard focus and restores focus across viewport changes', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Controls and activity' })).toBeVisible();
  const trigger = page.locator('.menu-button');
  const sidebar = page.locator('#workspace-navigation');
  await expect(trigger).toHaveAttribute('aria-controls', 'workspace-navigation');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(sidebar).toHaveAttribute('inert', '');
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(0);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(trigger).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(trigger).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('button', { name: 'Overview', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(sidebar).toHaveAttribute('inert', '');
  await trigger.click();
  await page.getByRole('button', { name: 'Close navigation', exact: true }).press('Enter');
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await page.setViewportSize({ width: 1280, height: 900 });
  const overview = page.getByRole('button', { name: 'Overview', exact: true });
  await expect(overview).toBeFocused();
  await expect(sidebar).not.toHaveAttribute('inert');
  await page.getByRole('button', { name: 'Budgets', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Costs, reservations and compute' })).toBeVisible();
  await page.setViewportSize({ width: 720, height: 844 });
  await expect(trigger).toBeFocused();
  await expect(sidebar).toHaveAttribute('inert', '');
  await trigger.click();
  await expect(page.getByRole('button', { name: 'Budgets', exact: true })).toBeFocused();
  await page.setViewportSize({ width: 721, height: 900 });
  await expect(page.getByRole('button', { name: 'Budgets', exact: true })).toBeFocused();
  await expect(sidebar).not.toHaveAttribute('inert');
  await expect(page.getByRole('button', { name: 'Close navigation', exact: true })).toHaveCount(0);
});

test('invalid action JSON focuses its associated error and never dispatches until valid revalidation', async ({ page }) => {
  await fixture(page);
  const run = { id: 'run-validation', purpose: 'supplier_review', status: 'active', tenant: 'acme' };
  await page.route('**/api/runs', route => route.fulfill({ json: { runs: [run] } }));
  await page.route('**/api/runs/run-validation', route => route.fulfill({ json: { run, operations: [] } }));
  const dispatched: unknown[] = [];
  await page.route('**/api/actions', route => {
    dispatched.push(route.request().postDataJSON());
    return route.fulfill({ json: { id: 'valid-action', status: 'completed' } });
  });
  await page.goto('/#investigate');
  await page.locator('.run-option').filter({ hasText: 'run-validation' }).click();
  await page.getByRole('button', { name: 'Propose action', exact: true }).click();
  const field = page.getByRole('textbox', { name: 'Action arguments' });
  const submit = page.locator('.action-form').getByRole('button', { name: 'Propose action', exact: true });
  await field.fill('{invalid');
  await submit.click();
  await expect(field).toHaveValue('{invalid');
  await expect(field).toBeFocused();
  await expect(field).toHaveAttribute('aria-invalid', 'true');
  const errorId = await field.getAttribute('aria-describedby');
  expect(errorId).toBeTruthy();
  await expect(page.locator(`[id="${errorId}"]`).getByRole('alert')).toHaveText('Arguments must be valid JSON.');
  expect(dispatched).toEqual([]);
  await submit.click();
  await expect(field).toHaveAttribute('aria-describedby', errorId!);
  expect(dispatched).toEqual([]);
  await field.fill('{"document_id":"supplier-acme-1"}');
  await submit.click();
  await expect.poll(() => dispatched.length).toBe(1);
  expect(dispatched[0]).toMatchObject({ run_id: run.id, tool: 'documents.read', arguments: { document_id: 'supplier-acme-1' } });
  await expect(field).not.toHaveAttribute('aria-invalid');
  await expect(field).not.toHaveAttribute('aria-describedby');
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('fresh unauthenticated session shows no false error but rejected sign-in remains visible', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/session', route => route.fulfill({ status: 401, json: { detail: 'Authenticated identity required' } }));
  await page.route('**/api/demo/session', route => route.fulfill({ status: 401, json: { detail: 'Sign-in rejected by the server.' } }));
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Enter workspace' })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page.getByRole('button', { name: 'Enter workspace' }).click();
  await expect(page.getByRole('alert')).toHaveText('Sign-in rejected by the server.');
});

test('session server failure remains visible even when its message contains401', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/session', route => route.fulfill({ status: 503, json: { detail: 'Service unavailable; incident401.' } }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toHaveText('Service unavailable; incident401.');
});

test('session network failure remains visible', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/session', route => route.abort('failed'));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Failed to fetch');
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

test('budget accounts use structured scope and resource labels while retaining full identifiers and exact amounts at390px', async ({ page }) => {
  await fixture(page);
  const accounts = [
    { id: 'tenant:acme:2026-10-04:usd_micros', scope: 'tenant', unit: 'usd_micros', spent: 12345, reserved: 23456, limit: 100000, period: '2026-10-04' },
    { id: 'user:acme:workload:shared:actor:with:colons:2026-10-04:tokens', scope: 'user', unit: 'tokens', spent: 2345, reserved: 123, limit: 5000, period: '2026-10-04' },
    { id: 'root:aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb:slot_millis', scope: 'root', unit: 'slot_millis', spent: 1500, reserved: 3000, limit: 9000, period: '2026-10-04' },
    { id: 'run:cccccccc-4444-5555-6666-dddddddddddd:tokens', scope: 'run', unit: 'tokens', spent: 80, reserved: 20, limit: 200, period: '2026-10-04' },
    { id: 'guard:aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb:slot_millis', scope: 'guard', unit: 'slot_millis', spent: 700, reserved: 200, limit: 3000, period: '2026-10-04' },
    { id: 'public:aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb:operations', scope: 'public_projection', unit: 'operations', spent: 1, reserved: 0, limit: 1, period: 'fixed-approved-request' },
  ];
  const headings = ['Tenant · API cost', 'User · Tokens', 'Root workflow · Compute time', 'Delegated workflow · Tokens', 'Guard · Compute time', 'Approved public request · Publications'];
  const amounts = [['$0.012345', '$0.023456', '$0.064199', '$0.10'], ['2,345 tokens', '123 tokens', '2,532 tokens', '5,000 tokens'], ['1.5 s', '3 s', '4.5 s', '9 s'], ['80 tokens', '20 tokens', '100 tokens', '200 tokens'], ['0.7 s', '0.2 s', '2.1 s', '3 s'], ['1 publication', '0 publications', '0 publications', '1 publication']];
  await page.route('**/api/budgets', route => route.fulfill({ json: { accounts, workers: {}, reservations: [] } }));
  await page.goto('/#budgets');
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await expect(page.locator('.budget-account')).toHaveCount(accounts.length);
    for (const [i, account] of accounts.entries()) {
      const card = page.locator('.budget-account').filter({ has: page.getByRole('heading', { name: headings[i], exact: true }) });
      const identifier = card.locator('.budget-account-id');
      await expect(identifier).toHaveText(`Account ID: ${account.id}`);
      await expect(identifier).toBeVisible();
      await expect(card.locator('.budget-numbers strong')).toHaveText(amounts[i]);
      await expect(card.locator('.budget-caption')).toContainText(`Period ${account.period}`);
      expect(await identifier.evaluate(element => {
        const rect = element.getBoundingClientRect();
        return element.scrollWidth <= element.clientWidth && rect.left >= 0 && rect.right <= window.innerWidth && getComputedStyle(element).overflowWrap === 'anywhere';
      })).toBe(true);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
});

test('metric details retain computed text contrast on desktop and mobile dashboard views', async ({ page }) => {
  await fixture(page);
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 844 });
    for (const view of ['overview', 'budgets']) {
      await page.goto(`/#${view}`);
      await expect(page.getByRole('heading', { name: view === 'overview' ? 'Controls and activity' : 'Costs, reservations and compute', exact: true })).toBeVisible();
      await expect(page.locator('.metric > span')).toHaveCount(4);
      const samples = await page.locator('.metric > span').evaluateAll(elements => {
        const luminance = (color: string) => {
          const [r, g, b] = color.match(/\d+(?:\.\d+)?/g)!.slice(0, 3).map(Number).map(value => {
            const channel = value / 255;
            return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
          });
          return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        };
        return elements.map(element => {
          const color = getComputedStyle(element).color;
          const background = getComputedStyle(element.parentElement!).backgroundColor;
          const text = luminance(color), surface = luminance(background);
          return { color, background, contrast: (Math.max(text, surface) + 0.05) / (Math.min(text, surface) + 0.05) };
        });
      });
      for (const sample of samples) {
        expect(sample.color).toBe('rgb(130, 144, 139)');
        expect(sample.background).toBe('rgb(25, 28, 30)');
        expect(sample.contrast).toBeGreaterThanOrEqual(4.5);
      }
    }
  }
});

test('inspection announces delayed and repeated requests without presenting stale results', async ({ page }) => {
  await fixture(page);
  const replies: Array<(value: { status?: number; json: unknown }) => void> = [];
  await page.route('**/api/playground', async route => {
    const reply = await new Promise<{ status?: number; json: unknown }>(resolve => replies.push(resolve));
    await route.fulfill(reply);
  });
  await page.goto('/#test-lab');
  const status = page.locator('#inspection-status');
  await expect(status).toHaveAttribute('role', 'status');
  await expect(status).toHaveAttribute('aria-live', 'polite');
  await expect(status).toHaveAttribute('aria-atomic', 'true');
  await expect(status).toHaveText('No inspection has been run.');
  const stableRegion = await status.elementHandle();
  await page.getByRole('button', { name: 'Inspect input', exact: true }).click();
  await expect.poll(() => replies.length).toBe(1);
  await expect(status).toHaveText('Inspecting input. Waiting for the control result.');
  await expect(page.getByText('Inspection in progress', { exact: true })).toBeVisible();
  await expect(page.getByText('Ready for your first inspection', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Inspecting…', exact: true })).toBeDisabled();
  replies[0]({ json: { decision: 'redact', output: 'First actual fixture result.', execution_mode: 'contract fixture' } });
  await expect(status).toHaveText('Inspection finished. The result is shown below.');
  await expect(page.locator('pre').first()).toHaveText('First actual fixture result.');
  await page.getByLabel('Input to inspect').fill('Second request');
  await page.getByRole('button', { name: 'Inspect input', exact: true }).click();
  await expect.poll(() => replies.length).toBe(2);
  await expect(status).toHaveText('Inspecting input. Waiting for the control result.');
  await expect(page.getByText('First actual fixture result.', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Output text returned by API', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Inspecting…', exact: true })).toBeDisabled();
  replies[1]({ json: { decision: 'block', output: 'Second actual fixture result.', execution_mode: 'contract fixture' } });
  await expect(status).toHaveText('Inspection finished. The result is shown below.');
  await expect(page.locator('pre').first()).toHaveText('Second actual fixture result.');
  expect(await stableRegion!.evaluate(element => element === document.querySelector('#inspection-status'))).toBe(true);
});

test('inspection failure announces the error and recovery waits for a fresh result', async ({ page }) => {
  await fixture(page);
  const replies: Array<(value: { status?: number; json: unknown }) => void> = [];
  await page.route('**/api/playground', async route => {
    const reply = await new Promise<{ status?: number; json: unknown }>(resolve => replies.push(resolve));
    await route.fulfill(reply);
  });
  await page.goto('/#test-lab');
  const status = page.locator('#inspection-status');
  const stableRegion = await status.elementHandle();
  await page.getByRole('button', { name: 'Inspect input', exact: true }).click();
  await expect.poll(() => replies.length).toBe(1);
  await expect(status).toHaveText('Inspecting input. Waiting for the control result.');
  replies[0]({ status: 503, json: { detail: 'Guard unavailable; no result released.' } });
  await expect(page.getByRole('alert')).toHaveText('Guard unavailable; no result released.');
  await expect(status).toHaveText('Inspection request failed. See the reported error.');
  await expect(page.getByText('Inspection request failed', { exact: true })).toBeVisible();
  await expect(page.getByText('Ready for your first inspection', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Output text returned by API', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Inspect input', exact: true }).click();
  await expect.poll(() => replies.length).toBe(2);
  await expect(status).toHaveText('Inspecting input. Waiting for the control result.');
  await expect(page.getByRole('alert')).toHaveCount(0);
  replies[1]({ json: { decision: 'allow', output: 'Recovered fixture result.' } });
  await expect(status).toHaveText('Inspection finished. The result is shown below.');
  await expect(page.locator('pre').first()).toHaveText('Recovered fixture result.');
  expect(await stableRegion!.evaluate(element => element === document.querySelector('#inspection-status'))).toBe(true);
});

test('inspection status does not enable execution for a manager', async ({ page }) => {
  await fixture(page, { role: 'manager' });
  const requests: string[] = [];
  await page.route('**/api/playground', route => { requests.push(route.request().method()); return route.fulfill({ json: {} }); });
  await page.goto('/#test-lab');
  await expect(page.getByRole('button', { name: 'Inspect input', exact: true })).toBeDisabled();
  await expect(page.locator('#inspection-status')).toHaveText('No inspection has been run.');
  expect(requests).toEqual([]);
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
  await expect(page.getByRole('heading', { name: 'Inspected input after controls' })).toBeVisible();
  await expect(page.locator('pre').first()).toHaveText('Contact [REDACTED:EMAIL].');
  await expect(page.getByRole('heading', { name: 'Released result' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Input assessment' })).toContainText(model);
  await expect(page.locator('.test-lab-grid .panel-content > .metadata')).toContainText('123.4 ms');
  await expect(page.getByRole('region', { name: 'Output control', exact: true })).toContainText('The operation reached result release.');
  await expect(page.getByRole('region', { name: 'Output control', exact: true })).not.toContainText('benign');
  await targetedEvidence(page, 'completed-redacted', { source: 'deterministic contract fixture' });
  await page.getByLabel('Current tenant').selectOption('synthetic_test_tenant');
  await expect(page.getByText('PLAYGROUND TENANT: synthetic_test_tenant')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Inspected input after controls' })).toHaveCount(0);
});

async function targetedEvidence(page: Page, name: string, observation: unknown) {
  const directory = process.env.ACTIONGATE_UI_EVIDENCE_DIR ?? test.info().outputDir;
  await mkdir(directory, { recursive: true });
  const screenshot = path.join(directory, `${name}.png`);
  await page.getByRole('heading', { name: 'Test inputs and workflows', exact: true }).click();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: screenshot, fullPage: true, animations: 'disabled' });
  await writeFile(path.join(directory, `${name}.json`), JSON.stringify({
    evidence_scope: 'Browser rendering of deterministic API fixtures; no server inference or deployment claim.',
    observed_at: new Date().toISOString(), viewport: page.viewportSize(),
    horizontal_overflow: await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
    observation,
  }, null, 2));
  await test.info().attach(name, { path: screenshot, contentType: 'image/png' });
}

for (const width of [1280, 390]) {
  test(`targeted UX-01 separates benign input from withheld output at${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await fixture(page);
    const observed = {
      id: 'targeted-output-blocked', decision: 'block', status: 'output_blocked', stage: 'output',
      reason: 'Result failed semantic inspection', policy_generation: 15,
      arguments: { content: 'Synthetic UX audit: summarize a fictional delivery of ten blue boxes.' }, result: null,
      rule_ids: ['semantic.incomplete', 'semantic.risk', 'semantic.unknown'],
      metadata: { execution_mode: 'contract fixture', semantic: { verdict: 'benign', risk_level: 0, complete: true, model_digest: 'sha256:input-model-fixture' }, semantic_output: { cache_hit: true, cache_source: 'fixture' } },
    };
    await page.route('**/api/playground', route => route.fulfill({ json: observed }));
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/#test-lab');
    await page.getByLabel('Input to inspect').fill(observed.arguments.content);
    await page.getByRole('button', { name: 'Inspect input', exact: true }).click();
    await expect(page.locator('.inspection-outcome')).toContainText('Control decisionblock');
    await expect(page.locator('.inspection-outcome')).toContainText('Execution statusoutput blocked');
    const input = page.getByRole('region', { name: 'Input assessment', exact: true });
    const output = page.getByRole('region', { name: 'Output control', exact: true });
    await expect(input).toContainText('This assessment covers the submitted input.');
    await expect(input).toContainText('Verdictbenign');
    await expect(input).toContainText('Risk level0');
    await expect(input.locator('pre')).toHaveText(observed.arguments.content);
    await expect(input.getByRole('heading', { name: 'Inspected input after controls' })).toBeVisible();
    await expect(output).toContainText('The result was withheld at output control.');
    await expect(output).toContainText('Output verdict and risk were not recorded separately.');
    await expect(output).toContainText('The cause of the incomplete assessment was not recorded.');
    await expect(output).toContainText('No released result is available.');
    await expect(output).not.toContainText('benign');
    await expect(output.locator('pre')).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Inspected output', exact: true })).toHaveCount(0);
    const rules = page.getByRole('region', { name: 'Recorded control rules' });
    await expect(rules).toContainText('A complete semantic assessment was not established.');
    await expect(rules).toContainText('The guard did not establish a semantic verdict.');
    await expect(rules).toContainText("The reported risk met the active profile's blocking threshold.");
    await page.getByText('Full control evidence', { exact: true }).click();
    expect(JSON.parse(await page.locator('.raw-details pre').innerText())).toEqual(observed);
    await page.getByText('Full control evidence', { exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
    await targetedEvidence(page, `output-blocked-${width}`, { response: observed, console_errors: errors });
  });
}

test('targeted UX-01 preserves a non-output stop stage and does not invent an unknown rule explanation', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/playground', route => route.fulfill({ json: {
    decision: 'block', status: 'output_blocked', stage: 'reservation', result: null,
    reason: 'Resource budget exhausted.', rule_ids: ['budget.exhausted'],
    arguments: { content: 'Input already inspected.' }, metadata: { semantic: { verdict: 'benign', risk_level: 0 } },
  } }));
  await page.goto('/#test-lab');
  await page.getByRole('button', { name: 'Inspect input', exact: true }).click();
  const output = page.getByRole('region', { name: 'Output control', exact: true });
  await expect(output).toContainText('The result was withheld. The recorded stop stage is reservation.');
  await expect(output).not.toContainText('withheld at output control');
  await expect(page.getByRole('region', { name: 'Recorded control rules' })).toContainText('No explanation is recorded for this rule.');
  await targetedEvidence(page, 'reservation-stop', { source: 'deterministic contract fixture' });
});

test('targeted UX-01 keeps withheld input absent and identifies that output control was not reached', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/playground', route => route.fulfill({ json: {
    decision: 'block', status: 'blocked', stage: 'semantic', arguments: null, result: null,
    reason: 'Active controls denied this operation', rule_ids: ['semantic.risk'],
    metadata: { semantic: { verdict: 'suspicious', risk_level: 3, complete: true } },
  } }));
  await page.goto('/#test-lab');
  await page.getByRole('button', { name: 'Inspect input', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Input assessment', exact: true })).toContainText('Verdictsuspicious');
  await expect(page.getByRole('region', { name: 'Input assessment', exact: true }).locator('pre')).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Output control', exact: true })).toContainText('Output control was not reached.');
  await expect(page.getByRole('heading', { name: 'Released result', exact: true })).toHaveCount(0);
  await targetedEvidence(page, 'input-blocked', { source: 'deterministic contract fixture' });
});

test('targeted UX-01 null result never promotes fallback text to output and absent details stay unknown', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/playground', route => route.fulfill({ json: {
    decision: 'block', status: 'output_blocked', result: null,
    output: 'Unscoped fallback output.', text: 'Unscoped fallback text.', redacted_text: 'Unscoped redacted fallback.',
    arguments: { content: 'Actual input field.' }, rule_ids: [],
  } }));
  await page.goto('/#test-lab');
  await page.getByRole('button', { name: 'Inspect input', exact: true }).click();
  const output = page.getByRole('region', { name: 'Output control', exact: true });
  await expect(output).toContainText('The result was withheld. The stop stage was not recorded.');
  await expect(output).toContainText('Output verdict and risk were not recorded separately.');
  await expect(output.locator('pre')).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Input assessment', exact: true })).toContainText('VerdictNot recorded');
  await expect(page.getByRole('region', { name: 'Input assessment', exact: true }).locator('pre')).toHaveText('Actual input field.');
  await targetedEvidence(page, 'missing-stage-and-null-result', { source: 'deterministic contract fixture' });
});

test('targeted A11Y-01 both verification empty descriptions exceed5to1 at desktop and mobile', async ({ page }) => {
  await fixture(page);
  await page.goto('/#test-lab');
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const descriptions = page.locator('.test-results-grid .empty p');
    await expect(descriptions).toHaveCount(2);
    const samples = await descriptions.evaluateAll(elements => {
      const rgb = (value: string) => value.match(/[\d.]+/g)!.slice(0, 3).map(Number);
      const luminance = (value: string) => rgb(value).map(channel => {
        const fraction = channel / 255;
        return fraction <= 0.04045 ? fraction / 12.92 : ((fraction + 0.055) / 1.055) ** 2.4;
      }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
      return elements.map(element => {
        const color = getComputedStyle(element).color;
        let surface: Element | null = element;
        let background = 'rgba(0, 0, 0, 0)';
        while (surface && (background === 'rgba(0, 0, 0, 0)' || background === 'transparent')) {
          background = getComputedStyle(surface).backgroundColor;
          surface = surface.parentElement;
        }
        const text = luminance(color), backdrop = luminance(background);
        return { text: element.textContent, color, background, contrast: (Math.max(text, backdrop) + 0.05) / (Math.min(text, backdrop) + 0.05) };
      });
    });
    for (const sample of samples) {
      expect(sample.background).toBe('rgb(25, 28, 30)');
      expect(sample.contrast).toBeGreaterThanOrEqual(5);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await targetedEvidence(page, `verification-empty-${width}`, samples);
  }
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
