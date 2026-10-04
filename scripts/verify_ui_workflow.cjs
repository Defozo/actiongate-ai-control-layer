// Actual browser-triggered local workflows. No response interception or fixtures.
// Run only with the model slot available; the server executes four real workflows.
const {chromium} = require('../ui/node_modules/playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {performance} = require('node:perf_hooks');
const {createHash} = require('node:crypto');
const {readFileSync} = require('node:fs');

const root = path.resolve(__dirname, '..');
function producerSources() {
  return Object.fromEntries(['scripts/verify_ui_workflow.cjs', 'TEAM.json'].map(source => [source, createHash('sha256').update(readFileSync(path.join(root, source))).digest('hex')]));
}
const sourceStart = producerSources();
const base = (process.env.ACTIONGATE_BASE_URL || 'http://127.0.0.1:8080').replace(/\/$/, '');
const output = path.join(root, 'artifacts/submission');
const temporary = path.join(root, 'docs/.build/ui-workflow');
let browser, context;
const errors = [];
const report = {recorded_at: new Date().toISOString(), mode: 'actual-browser-triggered-local-workflows',
  status: 'running', tenant: 'synthetic_test_tenant', fixtures: false, errors};

async function persistReport() {
  report.producer_sources = sourceStart;
  report.producer_source_stable = JSON.stringify(sourceStart) === JSON.stringify(producerSources());
  if (!report.producer_source_stable) {
    report.status = 'failed';
    if (!errors.includes('ProducerSourceChanged')) errors.push('ProducerSourceChanged');
  }
  await fs.mkdir(output, {recursive: true});
  await fs.writeFile(path.join(output, 'ui-workflow.json'), JSON.stringify(report, null, 2));
}

async function openRecorded(name) {
  browser = await chromium.launch({headless: true});
  context = await browser.newContext({viewport: {width: 1440, height: 960}, recordVideo: {dir: temporary, size: {width: 1440, height: 960}}});
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base);
  await page.getByLabel('Demo workspace').selectOption(report.tenant);
  await page.getByRole('button', {name: 'Enter workspace'}).click();
  await page.getByRole('heading', {name: 'Controls and activity'}).waitFor();
  return {page, name};
}

async function saveRecorded(recording) {
  await context.close();
  const destination = path.join(output, recording.name + '.webm');
  await recording.page.video().saveAs(destination);
  await browser.close();
  context = browser = undefined;
  return destination;
}

function command(program, args) {
  const result = spawnSync(program, args, {cwd: root, encoding: 'utf8', windowsHide: true});
  if (result.status !== 0) throw new Error(`${path.basename(program)} failed with exit ${result.status}`);
  return result.stdout;
}

(async () => {
  const parsed = new URL(base);
  if (parsed.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname) || parsed.username || parsed.password) throw new Error('Use the local operator edge');
  report.target = base;
  await fs.mkdir(output, {recursive: true});
  await fs.mkdir(temporary, {recursive: true});
  const team = JSON.parse(await fs.readFile(path.join(root, 'TEAM.json'), 'utf8'));
  report.team = team;
  const first = await openRecorded('ui-workflow-start');
  const page = first.page;
  const policy = await page.evaluate(async () => (await fetch('/api/policies')).json());
  const signedPayload = JSON.parse(policy.signature).payload;
  report.generation = policy.generation;
  report.policy_digest = policy.digest;
  report.guard_artifact = signedPayload.guard_artifact;
  report.model_artifacts = signedPayload.model_artifacts;
  report.semantic_configuration = policy.configuration.semantic;
  report.guard_prompt_version = policy.configuration.semantic.prompt_version;
  await page.goto(base + '/#test-lab');
  const startButton = page.getByRole('button', {name: 'Run interactive local checks'});
  await startButton.scrollIntoViewIfNeeded();
  await startButton.waitFor();
  await page.waitForTimeout(1600);
  const responsePromise = page.waitForResponse(response => response.url().endsWith('/api/tests') && response.request().method() === 'POST');
  const started = performance.now();
  await startButton.click();
  const response = await responsePromise;
  const initiation = await response.json();
  if (!response.ok() || !initiation.id || initiation.suite !== 'all-local') throw new Error('The UI did not start the real local job');
  report.job_id = initiation.id;
  await persistReport();
  const cookie = (await context.cookies(base)).map(item => item.name + '=' + item.value).join('; ');
  async function api(endpoint) {
    const response = await fetch(base + endpoint, {headers: {Cookie: cookie, Origin: base}, redirect: 'error'});
    if (!response.ok) throw new Error(`Read-back failed: ${response.status}`);
    return response.json();
  }
  await page.getByRole('heading', {name: 'Verification evidence'}).scrollIntoViewIfNeeded();
  await page.getByText(initiation.id, {exact: true}).waitFor();
  await page.waitForTimeout(2300);
  await page.screenshot({path: path.join(output, 'ui-workflow-start.png')});
  const firstClip = await saveRecorded(first);
  const waitStart = performance.now();
  let lastTotal = -1, job;
  while (true) {
    job = (await api('/api/tests')).runs.find(item => item.id === initiation.id);
    if (!job) throw new Error('Persisted UI job disappeared');
    if (job.total !== lastTotal) {
      console.log(JSON.stringify({job_id: job.id, completed_checks: job.total, status: job.status}));
      lastTotal = job.total;
      report.job = job;
      report.elapsed_seconds = (performance.now() - started) / 1000;
      await persistReport();
    }
    if (job.status !== 'running') break;
    await new Promise(resolve => setTimeout(resolve, 3000));
  }
  report.wait_omitted_seconds = (performance.now() - waitStart) / 1000;
  report.elapsed_seconds = (performance.now() - started) / 1000;
  report.job = job;
  const workflows = job.results.filter(item => item.name.startsWith('workflow.'));
  report.runs = [];
  for (const row of workflows) {
    const detail = await api('/api/runs/' + row.details.run_id);
    const operations = [];
    for (const op of detail.operations) operations.push(await api('/api/operations/' + op.id));
    report.runs.push({scenario: row.name, ...detail, operations});
  }
  const after = await api('/api/policies');
  const legal = report.runs.find(item => item.scenario === 'workflow.legal');
  const document = legal?.operations.find(item => item.tool === 'documents.read');
  const model = legal?.operations.find(item => item.tool === 'models.chat');
  const negative = report.runs.filter(item => ['workflow.cross_tenant', 'workflow.injection'].includes(item.scenario));
  const injection = report.runs.find(item => item.scenario === 'workflow.injection')?.operations[0];
  const pii = report.runs.find(item => item.scenario === 'workflow.pii')?.operations[0];
  const foreign = report.runs.find(item => item.scenario === 'workflow.cross_tenant')?.operations[0];
  const expected = {'workflow.legal': 'completed', 'workflow.cross_tenant': 'blocked', 'workflow.injection': 'blocked', 'workflow.pii': 'completed'};
  report.checks = {
    persisted_job_passed: job.status === 'passed' && job.failed === 0,
    four_real_workflows: workflows.length === 4 && Object.keys(expected).every(name => workflows.some(item => item.name === name)) && workflows.every(item => item.passed && expected[item.name] === item.details.status),
    legal_four_operations: legal?.operations.length === 4 && legal.operations.every(item => item.status === 'completed'),
    document_receipt: document?.effect?.recorded === true,
    local_business_model: model?.status === 'completed' && model.metadata?.execution_mode === 'local',
    negatives_before_dispatch: negative.length === 2 && negative.every(run => run.operations.length === 1 && run.operations.every(op => op.status === 'blocked' && op.effect?.recorded === false && !op.events.some(event => event.event === 'operation.dispatched'))),
    injection_semantically_detected: injection?.metadata?.semantic?.complete === true && injection.metadata.semantic.verdict === 'suspicious' && injection.metadata.semantic.risk_level >= 2,
    pii_redacted: pii?.status === 'completed' && pii.decision === 'redact' && !JSON.stringify(pii.arguments).includes('analyst@example.org'),
    stable_generation: after.generation === policy.generation && after.digest === policy.digest && report.runs.every(run => run.operations.every(op => op.policy_generation === policy.generation)),
    no_browser_errors: errors.length === 0,
  };
  const second = await openRecorded('ui-workflow-result');
  await second.page.goto(base + '/#test-lab');
  const history = await second.page.evaluate(async () => (await (await fetch('/api/tests')).json()).runs);
  const jobIndex = history.findIndex(item => item.id === job.id);
  if (jobIndex < 0) throw new Error('The completed UI job is missing from verification history');
  await second.page.locator('.test-history button').nth(jobIndex).click();
  await second.page.getByRole('heading', {name: 'Verification evidence'}).scrollIntoViewIfNeeded();
  await second.page.getByText(job.id, {exact: true}).waitFor();
  await second.page.waitForTimeout(4000);
  await second.page.screenshot({path: path.join(output, 'ui-workflow-result.png')});
  for (const [name, op] of [['document', document], ['model', model], ['injection', injection], ['pii', pii], ['foreign-tenant', foreign]].filter(([, op]) => Boolean(op))) {
    await second.page.goto(base + '/#investigate');
    await second.page.getByLabel('Search operations').fill(op.id);
    await second.page.locator('.table-link').first().click();
    await second.page.getByRole('dialog').waitFor();
    await second.page.waitForTimeout(3000);
    await second.page.screenshot({path: path.join(output, `ui-workflow-${name}.png`)});
    const receipt = second.page.getByRole('heading', {name: 'Connector receipt', exact: true});
    if (await receipt.count()) {
      await receipt.scrollIntoViewIfNeeded();
      await second.page.waitForTimeout(2000);
    }
    await second.page.locator('.dialog-body').evaluate(element => element.scrollTop = element.scrollHeight);
    await second.page.waitForTimeout(1800);
    await second.page.getByLabel('Close operation details').click();
  }
  const secondClip = await saveRecorded(second);
  const ffprobe = process.env.FFPROBE_BIN || 'ffprobe';
  const ffmpeg = process.env.FFMPEG_BIN || 'ffmpeg';
  const firstSeconds = Number(command(ffprobe, ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', firstClip]).trim());
  await fs.writeFile(path.join(temporary, 'concat.txt'), [firstClip, secondClip].map(file => `file '${file.replace(/\\/g, '/')}'`).join('\n'));
  const caption = `Waiting omitted: ${Math.round(report.wait_omitted_seconds)} seconds. This is the same persisted server job.\n${team.team_name} | ${team.members.join(', ')}`;
  await fs.writeFile(path.join(temporary, 'caption.txt'), caption, 'utf8');
  const font = (process.env.FFMPEG_FONT || (process.platform === 'win32' ? 'C:/Windows/Fonts/arial.ttf' : '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')).replace(/\\/g, '/').replace(/:/g, '\\:');
  const filter = `drawtext=fontfile='${font}':textfile='docs/.build/ui-workflow/caption.txt':fontcolor=white:fontsize=25:box=1:boxcolor=black@0.88:boxborderw=14:x=30:y=h-100:enable='between(t,${firstSeconds.toFixed(3)},${(firstSeconds + 7).toFixed(3)})'`;
  command(ffmpeg, ['-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', path.join(temporary, 'concat.txt'),
    '-vf', filter, '-c:v', 'libx264', '-threads', '2', '-preset', 'fast', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
    '-metadata', 'title=ActionGate actual UI workflows', '-metadata', `artist=${team.members.join(', ')} | ${team.team_name}`,
    path.join(output, 'ActionGate-UI-workflows.mp4')]);
  report.video = 'artifacts/submission/ActionGate-UI-workflows.mp4';
  report.video_edit = 'Only the server-processing wait was omitted. A visible caption records its actual elapsed duration. Both source clips are retained.';
  report.checks.no_browser_errors = errors.length === 0;
  report.status = Object.values(report.checks).every(Boolean) ? 'passed' : 'failed';
})().catch(error => {report.status = 'failed'; errors.push(error.message); process.exitCode = 1;}).finally(async () => {
  await context?.close().catch(() => {});
  await browser?.close().catch(() => {});
  await persistReport();
  console.log(JSON.stringify({status: report.status, job_id: report.job_id, checks: report.checks, errors}));
  if (report.status !== 'passed') process.exitCode = 1;
});
