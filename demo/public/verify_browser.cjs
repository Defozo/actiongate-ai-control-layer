// Fresh browser context through the public HTTPS jury gateway. No mocks,
// borrowed application sessions or direct database access.
const {chromium}=require('../../ui/node_modules/playwright');
const fs=require('node:fs/promises');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const {createHash}=require('node:crypto');
const {isDeepStrictEqual}=require('node:util');
const root=path.resolve(__dirname,'../..');
const base=(process.env.ACTIONGATE_PUBLIC_URL||'').replace(/\/$/,'');
const sourceHash=()=>createHash('sha256').update(readFileSync(__filename)).digest('hex');
const before=sourceHash();
const modelManifest=readFileSync(path.join(root,'models/model-manifest.json'));
const expectedModel=JSON.parse(modelManifest).digest;
const output=path.join(root,'artifacts/public-demo');
const report={status:'running',recorded_at:new Date().toISOString(),url:base,
  mode:'fresh isolated browser over public HTTPS; current local-model judgments with cache status recorded',
  model_manifest_sha256:createHash('sha256').update(modelManifest).digest('hex'),
  reused_owner_session:false,mocks:false,checks:{},views:[],workflows:[],console_errors:[]};
let browser;
function check(name,condition){report.checks[name]=!!condition;if(!condition)throw new Error(name);}
async function persist(){
  report.producer_sources={'demo/public/verify_browser.cjs':before};
  report.producer_source_stable=before===sourceHash();
  if(!report.producer_source_stable)report.status='failed';
  await fs.mkdir(output,{recursive:true});
  await fs.writeFile(path.join(root,'artifacts/public-demo-verification.json'),JSON.stringify(report,null,2));
}
(async()=>{
  const target=new URL(base);
  if(target.protocol!=='https:'||target.username||target.password||target.pathname!=='/'||target.search||target.hash)throw new Error('Use the verified public HTTPS origin');
  if(!process.env.ACTIONGATE_JURY_PASSWORD)throw new Error('Inject the jury credential with psst');
  const proxyProofBytes=await fs.readFile(path.join(root,'artifacts/public-proxy-external.json'));
  const proxyProof=JSON.parse(proxyProofBytes);
  check('verified_public_application_origin',proxyProof.status==='passed'&&proxyProof.mode==='application'&&proxyProof.url===target.origin);
  const deploymentBytes=await fs.readFile(path.join(root,'artifacts/gpu-reference/deployed-source.json'));
  const deployment=JSON.parse(deploymentBytes);
  check('source_verified_gpu_deployment',deployment.passed===true&&deployment.producer_source_stable===true);
  report.public_proxy_proof_sha256=createHash('sha256').update(proxyProofBytes).digest('hex');
  report.gpu_deployment_proof_sha256=createHash('sha256').update(deploymentBytes).digest('hex');
  await persist();
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1024},
    httpCredentials:{origin:target.origin,username:'juror',password:process.env.ACTIONGATE_JURY_PASSWORD}});
  const page=await context.newPage();
  page.on('pageerror',error=>report.console_errors.push(error.message));
  const cdp=await context.newCDPSession(page);
  await cdp.send('Network.enable');
  const events=[];
  cdp.on('Network.eventSourceMessageReceived',event=>{
    let data;try{data=JSON.parse(event.data);}catch{return;}
    events.push({id:event.eventId,type:event.eventName,operation_id:data.operation_id,run_id:data.run_id});
  });
  const initial=await page.goto(base,{waitUntil:'domcontentloaded'});
  check('public_dashboard_loaded',initial.ok());
  await page.getByLabel('Demo workspace').waitFor();
  await page.screenshot({path:path.join(output,'fresh-login.png')});
  await page.getByLabel('Demo workspace').selectOption('synthetic_test_tenant');
  await page.getByRole('button',{name:'Enter workspace'}).click();
  await page.locator('.page-title h1').waitFor();
  await page.getByText('Stream connected',{exact:true}).waitFor({timeout:30000});
  check('fresh_login_completed',await page.getByLabel('Current tenant').inputValue()==='synthetic_test_tenant');
  const readPolicy=()=>page.evaluate(async()=>{const r=await fetch('/api/policies');if(!r.ok)throw new Error('Policy readback failed');return r.json();});
  const policy=await readPolicy();
  const signed=JSON.parse(policy.signature).payload;
  check('signed_policy_binding_consistent',signed.generation===policy.generation&&signed.snapshot_digest===policy.digest&&
    isDeepStrictEqual(signed.policy,policy.configuration));
  for(const [name,gateway] of Object.entries(deployment.gateways)){
    check('current_guard_and_configuration_'+name,gateway.generation===policy.generation&&
      isDeepStrictEqual(gateway.guard_artifact,signed.guard_artifact)&&isDeepStrictEqual(gateway.model_artifacts,signed.model_artifacts)&&
      isDeepStrictEqual(gateway.semantic_configuration,policy.configuration.semantic));
  }
  report.generation=policy.generation;
  report.policy_digest=policy.digest;
  report.guard_artifact=signed.guard_artifact;
  report.model_artifacts=signed.model_artifacts;
  report.semantic_configuration=policy.configuration.semantic;
  report.signed_envelope_sha256=createHash('sha256').update(policy.signature).digest('hex');
  for(const [view,title] of [
    ['overview','Controls and activity'],['policies','Policy configuration and feeds'],
    ['budgets','Costs, reservations and compute'],['investigate','Workflow and operation history'],
    ['test-lab','Test inputs and workflows']]){
    await page.goto(base+'/#'+view);
    await page.getByRole('heading',{name:title,level:1,exact:true}).waitFor();
    if(view==='overview')await page.waitForFunction(()=>/^\d+$/.test(document.querySelector('.posture-meta strong')?.textContent?.trim()||''));
    if(view==='policies')await page.waitForFunction(()=>[...document.querySelectorAll('textarea')].some(x=>x.value.includes('schema_version:')));
    if(view==='budgets')await page.locator('.worker-row').first().waitFor();
    await page.screenshot({path:path.join(output,view+'.png')});
    report.views.push({view,title:await page.locator('.page-title h1').innerText(),overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});
  }
  check('five_views_rendered_without_overflow',report.views.length===5&&report.views.every(x=>!x.overflow));
  for(const [sample,expected] of [['Ordinary request','completed'],['Indirect instruction','blocked']]){
    await page.getByRole('button',{name:sample,exact:true}).click();
    const started=Date.now();
    const responsePromise=page.waitForResponse(r=>r.url()===base+'/api/playground'&&r.request().method()==='POST',{timeout:1900000});
    await page.getByRole('button',{name:'Inspect input',exact:true}).click();
    const response=await responsePromise;
    check('public_playground_http_'+expected,response.ok());
    const operation=await response.json();
    check('public_playground_result_'+expected,operation.status===expected);
    check('public_playground_generation_'+expected,operation.policy_generation===policy.generation);
    const semantic=operation.metadata?.semantic;
    check('complete_model_judgment_'+expected,semantic?.complete===true&&
      semantic.verdict===(expected==='completed'?'benign':'suspicious')&&
      semantic.model_digest===expectedModel);
    const detail=await page.evaluate(async id=>{
      const r=await fetch('/api/operations/'+encodeURIComponent(id));
      if(!r.ok)throw new Error('Operation readback failed');return r.json();
    },operation.id);
    check('persisted_operation_'+expected,detail.id===operation.id&&detail.run_id===operation.run_id&&
      detail.tool==='reports.save'&&detail.status===expected&&detail.policy_generation===policy.generation);
    const dispatched=detail.events.filter(event=>event.event==='operation.dispatched');
    const completedEvents=detail.events.filter(event=>event.event==='operation.completed');
    // reports.save writes the local DataObject store. ConnectorReceipt belongs
    // to documents.read / reports.publish_demo, not this local storage path.
    const storageReceipt=detail.result;
    const saved=storageReceipt?.saved===true&&typeof storageReceipt.id==='string'&&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(storageReceipt.id)&&
      storageReceipt.key==='report-'+operation.id&&storageReceipt.label===detail.label&&
      isDeepStrictEqual(storageReceipt,operation.result);
    check('local_storage_receipt_'+expected,expected==='completed'
      ? saved&&detail.stage==='release'&&detail.settlement_status==='settled'&&
        dispatched.length===1&&completedEvents.length===1&&dispatched[0].id<completedEvents[0].id&&
        [dispatched[0],completedEvents[0]].every(event=>event.operation_id===operation.id&&event.run_id===operation.run_id)
      : !detail.result&&detail.decision==='block'&&dispatched.length===0&&completedEvents.length===0&&
        detail.rule_ids.includes('semantic.risk')&&detail.events.some(event=>
          event.event==='operation.blocked'&&event.operation_id===operation.id&&event.run_id===operation.run_id));
    check('no_external_connector_receipt_'+expected,detail.effect.recorded===false&&detail.effect.receipt_id===null);
    await page.getByRole('button',{name:'Inspect input',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'playground-'+expected+'.png')});
    report.workflows.push({sample,status:operation.status,operation_id:operation.id,run_id:operation.run_id,
      generation:operation.policy_generation,semantic:operation.metadata?.semantic?.verdict,
      semantic_complete:semantic.complete,model_digest:semantic.model_digest,
      semantic_cache_hit:semantic.cache_hit===true,
      effect_recorded:expected==='completed'&&saved,
      effect_evidence:{kind:'persisted_local_storage_receipt',receipt:storageReceipt??null,
        dispatched_events:dispatched.length,completed_events:completedEvents.length,
        external_connector_receipt_recorded:detail.effect.recorded},duration_ms:Date.now()-started});
  }
  await page.getByText('Stream connected',{exact:true}).waitFor();
  const completed=report.workflows[0];
  check('public_sse_delivered_actual_operation',events.some(x=>x.operation_id===completed.operation_id||x.run_id===completed.run_id));
  report.sse={status:'passed',matching_events:events.filter(x=>report.workflows.some(flow=>x.operation_id===flow.operation_id||x.run_id===flow.run_id))};
  const policyAfter=await readPolicy();
  check('stable_policy_during_public_workflows',policy.generation===policyAfter.generation&&policy.digest===policyAfter.digest&&
    policy.signature===policyAfter.signature);
  check('deployment_proof_unchanged',deploymentBytes.equals(await fs.readFile(path.join(root,'artifacts/gpu-reference/deployed-source.json'))));
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/demo/session')&&r.request().method()==='POST'&&r.ok()),page.getByLabel('Current role').selectOption('manager')]);
  const manager=await page.evaluate(async()=>{
    const audit=await fetch('/api/exports/audit.jsonl');
    const summary=await fetch('/api/exports/management.csv');
    return {audit:audit.status,management:summary.status,bytes:(await summary.text()).length};
  });
  check('manager_raw_audit_denied',manager.audit===403);
  check('manager_summary_available',manager.management===200&&manager.bytes>0);
  report.manager_exports=manager;
  await page.setViewportSize({width:390,height:844});
  await page.goto(base+'/#overview');
  await page.locator('.page-title h1').waitFor();
  await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1);
  check('mobile_without_horizontal_overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(output,'mobile.png')});
  check('no_uncaught_browser_errors',report.console_errors.length===0);
  report.status='passed';
})().catch(error=>{report.status='failed';report.failure=error.message;process.exitCode=1;}).finally(async()=>{
  await browser?.close().catch(()=>{});await persist();console.log(JSON.stringify(report,null,2));
});
