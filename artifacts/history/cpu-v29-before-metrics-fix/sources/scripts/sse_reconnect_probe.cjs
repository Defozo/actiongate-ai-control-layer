// A loopback HTTP proxy forwards bytes unchanged, then drops only this test's
// EventSource TCP response. The real browser reconnects to the real gateway.
const http=require('node:http');
const {chromium}=require('../ui/node_modules/playwright');

module.exports=async function reconnect(cookies,base){
 const allowed=new URL(base);
 if(allowed.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(allowed.hostname)||allowed.username||allowed.password)throw new Error('Use the local operator edge');
 let browser,activeStream;
 const streams=[];
 const sockets=new Set();
 const proxy=http.createServer((request,response)=>{
  let target;
  try{target=new URL(request.url);}catch{response.writeHead(400).end();return;}
  if(target.origin!==allowed.origin||target.username||target.password){response.writeHead(403).end();return;}
  const headers={...request.headers,host:target.host};
  delete headers['proxy-connection'];
  const outgoing=http.request({hostname:target.hostname.replace(/^\[|\]$/g,''),port:target.port||80,path:target.pathname+target.search,
   method:request.method,headers},incoming=>{
    response.writeHead(incoming.statusCode,incoming.headers);
    if(target.pathname==='/api/events'){
     const entry={last_event_id:request.headers['last-event-id']??null,events:[]};streams.push(entry);
     activeStream=response;
     let pending='';
     incoming.on('data',chunk=>{
      pending+=chunk.toString('utf8');
      const blocks=pending.split('\n\n');pending=blocks.pop();
      for(const block of blocks){
       const id=block.match(/^id: (\d+)$/m),kind=block.match(/^event: (.+)$/m),data=block.match(/^data: (.+)$/m);
       if(id){let payload;try{payload=JSON.parse(data?.[1]??'{}');}catch{}entry.events.push({id:Number(id[1]),type:kind?.[1],run_id:payload?.run_id});}
      }
     });
    }
    incoming.pipe(response);
   });
  outgoing.on('error',()=>{if(!response.destroyed)response.destroy();});
  response.on('close',()=>outgoing.destroy());
  request.on('aborted',()=>outgoing.destroy());
  request.pipe(outgoing);
 });
 proxy.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
 await new Promise(resolve=>proxy.listen(0,'127.0.0.1',resolve));
 const port=proxy.address().port;
 try{
  browser=await chromium.launch({headless:true,proxy:{server:'http://127.0.0.1:'+port,bypass:'<-loopback>'}});
  const context=await browser.newContext({viewport:{width:1440,height:960}});
  await context.addCookies(cookies);
  const page=await context.newPage();
  await page.goto(base+'/#investigate');
  await page.getByText('Stream connected',{exact:true}).waitFor();
  await page.waitForTimeout(800);
  if(!streams[0]?.events.length||!activeStream)throw new Error('Fault proxy did not observe the initial real EventSource');
  const before=streams[0].events.at(-1).id;
  // An abrupt socket close, without altering any SSE event or API response.
  activeStream.destroy();
  await page.getByText('Reconnecting',{exact:true}).waitFor({timeout:5000});
  const run=await page.evaluate(async()=>{
   const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"document_ids":[]}'});
   if(!response.ok)throw new Error('Reconnect test workflow admission failed');
   const result=await response.json();return {id:result.id};
  });
  await page.getByText('Stream connected',{exact:true}).waitFor({timeout:15000});
  await page.waitForFunction(prefix=>[...document.querySelectorAll('.run-option code')].some(node=>node.textContent.startsWith(prefix)),run.id.slice(0,20));
  const resumed=streams.find((stream,index)=>index>0&&stream.last_event_id!==null);
  const event=resumed?.events.find(event=>event.run_id===run.id);
  if(!resumed||!event)throw new Error('Reconnect did not replay the actual disconnected-period workflow');
  return {status:'passed',transport_fault:'Abrupt close of this browser SSE response by a byte-forwarding loopback proxy',
   cursor_before_disconnect:before,reconnect_last_event_id:Number(resumed.last_event_id),first_replayed_id:resumed.events[0].id,
   disconnected_period_run_id:run.id,replayed_run_event_id:event.id,visible_after_reconnect:true,
   cursor_preserved:Number(resumed.last_event_id)===before,
   replay_strictly_after_cursor:resumed.events.every(event=>event.id>before),streams};
 }finally{
  await browser?.close().catch(()=>{});
  for(const socket of sockets)socket.destroy();
  await new Promise(resolve=>proxy.close(resolve));
 }
};
