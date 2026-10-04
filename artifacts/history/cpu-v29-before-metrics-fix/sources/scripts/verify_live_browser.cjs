// Actual UI/API views and role checks, without model calls or intercepted responses.
const { chromium } = require('../ui/node_modules/playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const {createHash}=require('node:crypto');
const {readFileSync}=require('node:fs');
const root=path.resolve(__dirname,'..');
const base=(process.env.ACTIONGATE_BASE_URL||'http://127.0.0.1:8080').replace(/\/$/,'');
function producerSources(){return {'scripts/verify_live_browser.cjs':createHash('sha256').update(readFileSync(__filename)).digest('hex')};}
const sourceStart=producerSources();
const failures=[];
const evidence={recorded_at:new Date().toISOString(),status:'running',target:base,views:[],console_errors:failures,producer_sources:sourceStart};
async function persistReport(){
 evidence.producer_source_stable=JSON.stringify(sourceStart)===JSON.stringify(producerSources());
 if(!evidence.producer_source_stable)evidence.status='failed';
 await fs.mkdir(path.join(root,'artifacts/reports'),{recursive:true});
 await fs.writeFile(path.join(root,'artifacts/reports/live-browser.json'),JSON.stringify(evidence,null,2));
}
let browser;
(async () => {
 await persistReport();
 const target=new URL(base);
 if(target.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(target.hostname)||target.username||target.password)throw new Error('Use the local operator edge');
 await fs.mkdir(path.join(root,'artifacts/reports'),{recursive:true});
 await fs.mkdir(path.join(root,'docs/images'),{recursive:true});
 browser = await chromium.launch({headless:true});
 const page = await browser.newPage({viewport:{width:1440,height:1024}});
 page.on('pageerror', e=>failures.push(e.message));
 await page.goto(base);
 await page.getByLabel('Demo workspace').selectOption('synthetic_test_tenant');
 await page.getByRole('button',{name:'Enter workspace'}).click();
 await page.getByRole('heading',{name:'Controls and activity'}).waitFor();
 await page.waitForFunction(()=>document.querySelector('.posture-copy h2')?.textContent !== 'Reading protection state' && /^\d+$/.test(document.querySelector('.posture-meta strong')?.textContent?.trim() ?? ''));
 Object.assign(evidence,{url:page.url(),initial_tenant:'synthetic_test_tenant'});
 for (const [name,hash] of [['overview','overview'],['policies','policies'],['budgets','budgets'],['investigate','investigate'],['test-lab','test-lab']]) {
  await page.goto(base+'/#'+hash);
  await page.locator('.page-title h1').waitFor();
  if(name==='overview')await page.waitForFunction(()=>/^\d+$/.test(document.querySelector('.posture-meta strong')?.textContent?.trim() ?? ''));
  if(name==='policies')await page.waitForFunction(()=>Array.from(document.querySelectorAll('textarea')).some(element=>element.value.includes('schema_version:')));
  if(name==='budgets')await page.locator('.worker-row').first().waitFor();
  await page.waitForTimeout(1000);
  await page.screenshot({path:path.join(root,'docs/images/live-'+name+'.png'),fullPage:false});
  evidence.views.push({name,title:await page.locator('.page-title h1').innerText(),horizontal_overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});
 }
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/demo/session')&&r.request().method()==='POST'&&r.ok()),page.getByLabel('Current role').selectOption('manager')]);
 await page.waitForFunction(()=>document.querySelector('[aria-label="Current role"]').value==='manager'&&!document.querySelector('[aria-label="Current role"]').disabled);
 const denied=await page.evaluate(async()=>{const r=await fetch('/api/exports/audit.jsonl');return r.status});
 if(denied!==403) throw new Error('Manager raw export was not denied: '+denied);
 evidence.manager_audit_export=denied;
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/demo/session')&&r.request().method()==='POST'&&r.ok()),page.getByLabel('Current tenant').selectOption('globex')]);
 await page.waitForTimeout(1200);
 evidence.final_tenant=await page.getByLabel('Current tenant').inputValue();
 await page.setViewportSize({width:390,height:844});
 await page.goto(base+'/#overview');
 await page.locator('.page-title h1').waitFor();
 await page.waitForFunction(()=>document.querySelector('.posture-copy h2')?.textContent !== 'Reading protection state' && /^\d+$/.test(document.querySelector('.posture-meta strong')?.textContent?.trim() ?? ''));
 await page.waitForFunction(()=>document.querySelector('.sidebar').getBoundingClientRect().right<=1);
 await page.screenshot({path:path.join(root,'docs/images/live-mobile.png'),fullPage:false});
 evidence.mobile_overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
 evidence.producer_sources=sourceStart;
 evidence.producer_source_stable=JSON.stringify(sourceStart)===JSON.stringify(producerSources());
 evidence.status=!failures.length&&!evidence.mobile_overflow&&!evidence.views.some(x=>x.horizontal_overflow)&&evidence.producer_source_stable&&evidence.final_tenant==='globex'?'passed':'failed';
 await persistReport();
 await browser.close();
 console.log(JSON.stringify(evidence,null,2));
 if(evidence.status!=='passed')process.exitCode=1;
})().catch(error=>{evidence.status='failed';failures.push(error.message);console.error(error.message);process.exitCode=1;}).finally(async()=>{await browser?.close().catch(()=>{});await persistReport();});

