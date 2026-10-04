const {chromium}=require('../../ui/node_modules/playwright');
const fs=require('node:fs/promises');const path=require('node:path');const crypto=require('node:crypto');
const base=(process.env.ACTIONGATE_PUBLIC_URL||'').replace(/\/$/,'');
if(!/^https:\/\/[^/?#@]+$/.test(base))throw new Error('Set ACTIONGATE_PUBLIC_URL to the public HTTPS origin');
const out=path.join(__dirname,'../../artifacts/public-visitor');
const report={status:'running',url:base,started_at:new Date().toISOString(),fresh_anonymous_context:true,checks:{},workflows:[],console_errors:[]};
let browser;function check(n,v){report.checks[n]=!!v;if(!v)throw new Error(n);}
async function save(){await fs.mkdir(out,{recursive:true});await fs.writeFile(path.join(out,'browser-verification.json'),JSON.stringify(report,null,2));}
(async()=>{
 await save();browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1100}});const page=await context.newPage();
 page.on('pageerror',e=>report.console_errors.push(e.message));
 const response=await page.goto(base,{waitUntil:'networkidle',timeout:60000});
 check('anonymous_document',response.status()===200);check('visitor_heading',await page.getByRole('heading',{name:'See what an AI agent is allowed to do.'}).isVisible());
 await page.waitForFunction(()=>document.getElementById('health').textContent.includes('Service ready'),{timeout:60000});
 check('public_readiness',true);
 for(const [route,status] of [['/operator',401],['/api/session',401],['/public-api/admin',404]]){let r=await context.request.get(base+route);check('route_'+route,r.status()===status);}
 let r=await context.request.post(base+'/api/demo/session',{headers:{Origin:base},data:{role:'admin',tenant:'acme'}});check('private_admin_session_denied',r.status()===401);
 r=await context.request.post(base+'/public-api/session',{data:{role:'admin'}});check('missing_origin_denied',r.status()===403);
 r=await context.request.post(base+'/public-api/session',{headers:{Origin:'https://unrelated.invalid'},data:{role:'admin'}});check('wrong_origin_denied',r.status()===403);
 r=await context.request.post(base+'/public-api/workflow/legal',{headers:{Origin:base},data:{}});check('workflow_without_session_denied',r.status()===401);
 r=await context.request.post(base+'/public-api/session',{headers:{Origin:base},data:{role:'admin',tenant:'globex'}});
 const identity=await r.json();check('forced_analyst_identity',r.ok()&&identity.role==='analyst'&&identity.tenant==='synthetic_test_tenant');
 report.public_identity={role:identity.role,tenant:identity.tenant};
 const cookies=await context.cookies(base+'/public-api/');
 check('guest_cookie_isolated',cookies.some(c=>c.name==='actiongate_public_session'&&c.path==='/public-api/'&&c.httpOnly&&c.secure&&c.sameSite==='Strict')&&!cookies.some(c=>c.name==='actiongate_session'));
 await page.screenshot({path:path.join(out,'desktop.png'),fullPage:true});
 await page.keyboard.press('Tab');check('keyboard_skip_link',await page.getByRole('link',{name:'Skip to examples'}).evaluate(e=>e===document.activeElement));
 await page.setViewportSize({width:390,height:844});check('mobile_no_horizontal_overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:path.join(out,'mobile.png'),fullPage:true});
 await page.setViewportSize({width:720,height:550});check('narrow_view_no_overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.setViewportSize({width:1440,height:1100});
 await save();
 for(const scenario of ['legal','cross_tenant','injection','pii']){
   const start=Date.now();const pending=page.waitForResponse(r=>r.url()===base+'/public-api/workflow/'+scenario&&r.request().method()==='POST',{timeout:1800000});
   await page.locator('[data-scenario="'+scenario+'"]').click();
   const answer=await pending;const value=await answer.json();
   const operations=value.operations||[];
   const record={scenario,http_status:answer.status(),seconds:Math.round((Date.now()-start)/100)/10,status:value.status,run_id:value.run_id,
     operations:operations.map(o=>({id:o.id,tool:o.tool,status:o.status,decision:o.decision,tenant:o.tenant,rule_ids:o.rule_ids,policy_generation:o.policy_generation}))};
   report.workflows.push(record);await fs.writeFile(path.join(out,scenario+'.json'),JSON.stringify(value,null,2));
   check(scenario+'_http',answer.ok());check(scenario+'_tenant',operations.length>0&&operations.every(o=>o.tenant==='synthetic_test_tenant'));
   if(scenario==='legal')check('real_supplier_workflow',value.status==='completed'&&operations.length===4&&operations.some(o=>o.tool==='models.chat')&&operations.at(-1).tool==='reports.save');
   else if(scenario==='pii')check('real_pii_redaction',value.status==='completed'&&operations.some(o=>o.decision==='redact')&&!JSON.stringify(operations.map(o=>o.result)).includes('analyst@example.org'));
   else check(scenario+'_blocked',value.status==='blocked');
   await page.waitForFunction(()=>!document.querySelector('[data-scenario]').disabled);
   await page.screenshot({path:path.join(out,scenario+'-result.png'),fullPage:true});
   await save();
 }
 check('no_page_errors',report.console_errors.length===0);report.status='passed';report.finished_at=new Date().toISOString();await save();
 console.log(JSON.stringify({status:report.status,checks:Object.keys(report.checks).length,workflows:report.workflows}));
})().catch(async e=>{report.status='failed';report.error=e.message;await save();console.error(JSON.stringify({status:report.status,error:e.message}));process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();});
