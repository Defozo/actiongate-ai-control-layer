// Real browser/API evidence: no intercepted responses, model calls or fake UI data.
const {chromium}=require('../ui/node_modules/playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const {createHash}=require('node:crypto');
const {readFileSync}=require('node:fs');
const reconnect=require('./sse_reconnect_probe.cjs');
const root=path.resolve(__dirname,'..');
const base=(process.env.ACTIONGATE_BASE_URL||'http://127.0.0.1:8080').replace(/\/$/,'');
function producerSources(){return Object.fromEntries(['scripts/verify_dashboard_events.cjs','scripts/sse_reconnect_probe.cjs'].map(source=>[source,createHash('sha256').update(readFileSync(path.join(root,source))).digest('hex')]));}
const sourceStart=producerSources();
let evidence={recorded_at:new Date().toISOString(),status:'running',tenant:'synthetic_test_tenant',target:base,new_inference:false,producer_sources:sourceStart,errors:[]};
async function persistReport(){
 evidence.producer_source_stable=JSON.stringify(sourceStart)===JSON.stringify(producerSources());
 if(!evidence.producer_source_stable)evidence.status='failed';
 await fs.mkdir(path.join(root,'artifacts/submission'),{recursive:true});
 await fs.writeFile(path.join(root,'artifacts/submission/dashboard-events.json'),JSON.stringify(evidence,null,2));
}
let browser;
(async()=>{
 await persistReport();
 const target=new URL(base);
 if(target.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(target.hostname)||target.username||target.password)throw new Error('Use the local operator edge');
 await fs.mkdir(path.join(root,'artifacts/submission'),{recursive:true});
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:960}});
 const page=await context.newPage();
 const cdp=await context.newCDPSession(page);
 await cdp.send('Network.enable');
 const actualEvents=[];
 cdp.on('Network.eventSourceMessageReceived',event=>actualEvents.push({id:event.eventId,type:event.eventName,time:Date.now()}));
 const reads=[],streamRequests=[],errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('request',request=>{
  const url=new URL(request.url());
  if(url.pathname.startsWith('/api/') && request.method()==='GET')reads.push({path:url.pathname,time:Date.now()});
  if(url.pathname==='/api/events')streamRequests.push({url:request.url(),last_event_id:request.headers()['last-event-id']??null,time:Date.now()});
 });
 await page.goto(base);
 await page.getByLabel('Demo workspace').selectOption('synthetic_test_tenant');
 await page.getByRole('button',{name:'Enter workspace'}).click();
 await page.getByText('Stream connected',{exact:true}).waitFor();
 await page.goto(base+'/#investigate');
 await page.getByRole('heading',{name:'Workflow and operation history'}).waitFor();
 await page.waitForTimeout(1500);
 const idleStart=Date.now();
 await page.waitForTimeout(10000);
 const idleReads=reads.filter(row=>row.time>=idleStart && row.path!=='/api/events');
 const measurements=[];
 for(let sample=0;sample<5;sample++){
  measurements.push(await page.evaluate(async()=>{
   const sent=performance.now(),sentWall=Date.now();
   const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({document_ids:[]})});
   if(!response.ok)throw new Error('Synthetic workflow creation failed: '+response.status);
   const created=await response.json();
   const acknowledgedWall=Date.now();
   const prefix=created.id.slice(0,20);
   await new Promise((resolve,reject)=>{
    let observer;
    const limit=setTimeout(()=>{observer?.disconnect();reject(new Error('Persisted run did not appear in the dashboard'));},5000);
    const check=()=>{
     if([...document.querySelectorAll('.run-option code')].some(node=>node.textContent.startsWith(prefix))){
      clearTimeout(limit);observer?.disconnect();resolve();
     }
    };
    observer=new MutationObserver(check);observer.observe(document.body,{childList:true,subtree:true});check();
   });
   const visibleWall=Date.now(),upperBound=performance.now()-sent;
   const detail=await (await fetch('/api/runs/'+created.id)).json();
   const event=detail.events.find(row=>row.event==='run.created');
   if(!event)throw new Error('Persisted audit event is missing');
   const storedWall=Date.parse(event.created_at);
   const clockAligned=storedWall>=sentWall-5 && storedWall<=acknowledgedWall+5;
   return {run_id:created.id,event_id:event.id,event_created_at:event.created_at,visible_at:new Date(visibleWall).toISOString(),
    cross_clock_timestamp_delta_ms:visibleWall-storedWall,stored_event_to_visible_ms:clockAligned?visibleWall-storedWall:null,
    request_to_visible_upper_bound_ms:Math.round(upperBound*1000)/1000,
    stored_clock_inside_request_bracket:clockAligned,
    target_met:upperBound<=1000};
  }));
  await page.waitForTimeout(550);
 }
 // Chrome's native EventSource reconnect must carry its actual last event ID.
 await fs.writeFile(path.join(root,'artifacts/submission/dashboard-events-progress.json'),JSON.stringify({measurements,streamRequests,actualEvents,producer_sources:sourceStart},null,2));
 const replay=await reconnect(await context.cookies(),base);
 const exports={};
 for(const [url,file] of [['/api/exports/audit.jsonl','sample-synthetic-audit.jsonl'],['/api/exports/management.csv','sample-synthetic-management.csv']]){
  const response=await context.request.get(base+url);
  if(response.status()!==200)throw new Error('Evidence export failed');
  const data=await response.body();
  if(file.endsWith('.jsonl')){
   const rows=data.toString('utf8').trim().split('\n').map(JSON.parse);
   if(rows.some(row=>row.tenant!=='synthetic_test_tenant'))throw new Error('Foreign tenant in exported evidence');
   exports.audit_events=rows.length;
  }
  await fs.writeFile(path.join(root,'artifacts/submission',file),data);
  exports[file]={bytes:data.length,source:url};
 }
 const sorted=measurements.map(row=>row.request_to_visible_upper_bound_ms).sort((a,b)=>a-b);
 evidence={recorded_at:new Date().toISOString(),tenant:'synthetic_test_tenant',target:base,new_inference:false,
  producer_sources:sourceStart,producer_source_stable:JSON.stringify(sourceStart)===JSON.stringify(producerSources()),
  method:'Stored audit timestamp to real DOM visibility is recorded with an explicit clock-alignment check. Acceptance uses monotonic request-to-visible, a conservative upper bound beginning before the DB commit. Timing uses the direct deployment URL; only reconnect uses a fault proxy.',
  target_ms:1000,measurements,maximum_ms:Math.max(...sorted),samples_count:measurements.length,
  p50_ms:sorted[Math.ceil(sorted.length*.5)-1],p95_ms:sorted[Math.ceil(sorted.length*.95)-1],
  target_met:measurements.every(row=>row.target_met),idle_read_requests_in_10_seconds:idleReads,
  replay,replay_passed:replay.cursor_preserved&&replay.replay_strictly_after_cursor&&replay.visible_after_reconnect,exports,errors};
 evidence.status=evidence.target_met&&evidence.replay_passed&&!errors.length&&evidence.producer_source_stable?'passed':'failed';
 await persistReport();
 console.log(JSON.stringify(evidence,null,2));
 if(evidence.status!=='passed')process.exitCode=1;
})().catch(error=>{evidence.status='failed';evidence.errors.push(error.message);console.error(error.message);process.exitCode=1;}).finally(async()=>{await browser?.close().catch(()=>{});await persistReport();});
