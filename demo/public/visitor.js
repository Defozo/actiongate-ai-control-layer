"use strict";
const buttons=Array.from(document.querySelectorAll("[data-scenario]"));
const progress=document.getElementById("progress");
const evidence=document.getElementById("evidence");
const operations=document.getElementById("operations");
const region=document.querySelector(".results");
let busy=false;
async function request(path,method){
 const response=await fetch(path,{method:method||"GET",credentials:"same-origin",headers:method?{"Content-Type":"application/json"}:{},body:method?"{}":undefined,cache:"no-store"});
 if(!response.ok){if(response.status===429)throw new Error("The shared demo is busy. Wait a little before trying again.");throw new Error("The request did not complete (HTTP "+response.status+"). A submitted action may still be running; do not repeat it immediately.");}
 return response.json();
}
request("/public-api/health").then(function(data){document.getElementById("health").textContent=data.status==="ready"?"Service ready. Local model protection is available.":"Service is preparing. Please try again shortly.";}).catch(function(){document.getElementById("health").textContent="Readiness is temporarily unavailable. Please try again shortly.";});
buttons.forEach(function(button){button.addEventListener("click",async function(){
 if(busy)return;busy=true;buttons.forEach(function(b){b.disabled=true;});region.setAttribute("aria-busy","true");
 operations.replaceChildren();evidence.textContent="Waiting for the gateway...";progress.textContent="Running "+button.textContent.toLowerCase()+". Local model processing can take several minutes. Please keep this page open.";
 const started=Date.now();const timer=setInterval(function(){progress.textContent="Processing through ActionGate ("+Math.floor((Date.now()-started)/1000)+" seconds). Please keep this page open.";},1000);
 try{
  await request("/public-api/session","POST");
  const result=await request("/public-api/workflow/"+button.dataset.scenario,"POST");
  clearInterval(timer);
  progress.textContent="Execution status: "+String(result.status)+". Run "+String(result.run_id)+". Completed response in "+Math.round((Date.now()-started)/1000)+" seconds.";
  (result.operations||[]).forEach(function(op){const item=document.createElement("li");item.textContent=String(op.tool||op.tool_name||"Operation")+": "+String(op.status)+(op.decision?"; decision: "+String(op.decision):"")+(op.id?"; ID: "+String(op.id):"");operations.appendChild(item);});
  evidence.textContent=JSON.stringify(result,null,2);
 }catch(error){clearInterval(timer);progress.textContent=error.message;evidence.textContent="No completed response was received. The demo never automatically retries a submitted workflow.";}
 finally{clearInterval(timer);busy=false;buttons.forEach(function(b){b.disabled=false;});region.setAttribute("aria-busy","false");}
});});
