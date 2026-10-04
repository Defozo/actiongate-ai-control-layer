// Verify the published bytes and native player in a fresh anonymous browser.
const {chromium}=require('../../ui/node_modules/playwright');
const fs=require('node:fs/promises');
const path=require('node:path');
const {createHash}=require('node:crypto');
const root=path.resolve(__dirname,'../..');
const base='https://defozo.github.io/actiongate-ai-control-layer/';
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const report={status:'running',recorded_at:new Date().toISOString(),url:base,
  reused_owner_session:false,checks:{},assets:[],console_errors:[]};
let browser;
function check(name,condition){report.checks[name]=!!condition;if(!condition)throw new Error(name);}
(async()=>{
  const manifest=JSON.parse(await fs.readFile(path.join(root,'.state/public-demo/site-build.json'),'utf8'));
  const source=await fs.readFile(__filename);
  report.producer_sources={'demo/public/verify_materials.cjs':digest(source)};
  report.site_manifest_sha256=digest(await fs.readFile(path.join(root,'.state/public-demo/site-build.json')));
  for(const item of manifest.assets){
    const response=await fetch(new URL(item.published_name,base),{signal:AbortSignal.timeout(120000)});
    check('asset_http_'+item.published_name,response.ok);
    const bytes=Buffer.from(await response.arrayBuffer());
    const hash=digest(bytes);
    check('asset_bytes_'+item.published_name,hash===item.sha256&&bytes.length===item.size_bytes);
    report.assets.push({name:item.published_name,url:response.url,sha256:hash,size_bytes:bytes.length,
      content_type:response.headers.get('content-type')});
  }
  report.site_files=[];
  for(const item of manifest.site_files){
    if(item.path==='.nojekyll')continue; // Pages build control, not a public asset.
    const verifiedAsset=report.assets.find(asset=>asset.name===item.path);
    if(verifiedAsset){
      check('site_bytes_'+item.path,verifiedAsset.sha256===item.sha256&&verifiedAsset.size_bytes===item.size_bytes);
      report.site_files.push({path:item.path,sha256:verifiedAsset.sha256,size_bytes:verifiedAsset.size_bytes});
      continue;
    }
    const response=await fetch(new URL(item.path,base),{signal:AbortSignal.timeout(120000)});
    const bytes=Buffer.from(await response.arrayBuffer());
    check('site_bytes_'+item.path,response.ok&&digest(bytes)===item.sha256&&bytes.length===item.size_bytes);
    report.site_files.push({path:item.path,sha256:digest(bytes),size_bytes:bytes.length});
  }
  report.public_fetch_exclusions=[{path:'.nojekyll',reason:'GitHub Pages build control file'}];
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1024}});
  const page=await context.newPage();
  page.on('pageerror',error=>report.console_errors.push(error.message));
  const response=await page.goto(base,{waitUntil:'domcontentloaded',timeout:60000});
  check('landing_anonymous_http',response.ok());
  check('correct_demo_link',await page.getByRole('link',{name:'Open live demo'}).getAttribute('href')===manifest.demo_url);
  const expectedLinks=['https://github.com/Defozo/actiongate-ai-control-layer',
    'https://github.com/Defozo/actiongate-ai-control-layer/releases/latest',
    'https://github.com/Defozo/actiongate-ai-control-layer/blob/main/docs/jury-runbook.md',
    'https://github.com/Defozo/actiongate-ai-control-layer/blob/main/docs/acceptance.md'];
  report.links=[];
  for(const url of expectedLinks){
    const r=await fetch(url,{signal:AbortSignal.timeout(60000)});
    check('published_link_'+expectedLinks.indexOf(url),r.ok);
    report.links.push({url,status:r.status});
  }
  await page.locator('#product-film').scrollIntoViewIfNeeded();
  await page.locator('#product-film').evaluate(async video=>{video.muted=true;await video.play();});
  await page.waitForFunction(()=>{
    const video=document.querySelector('#product-film');
    return video.currentTime>=3&&video.videoWidth>0&&video.readyState>=2;
  },{},{timeout:60000});
  report.player=await page.locator('#product-film').evaluate(video=>({duration:video.duration,
    width:video.videoWidth,height:video.videoHeight,current_time:video.currentTime,
    captions:[...video.textTracks].map(track=>({language:track.language,cues:track.cues?.length||0})),
    error:video.error?.code||null}));
  check('video_decodes_and_plays',report.player.current_time>=3&&report.player.duration>=153&&report.player.duration<=155&&!report.player.error);
  check('english_captions_loaded',report.player.captions.some(track=>track.language==='en'&&track.cues>=40));
  await page.locator('#product-film').evaluate(video=>video.pause());
  await fs.mkdir(path.join(root,'artifacts/public-demo'),{recursive:true});
  await page.screenshot({path:path.join(root,'artifacts/public-demo/published-player.png')});
  await page.locator('.music-variant summary').click();
  await page.locator('#song-film').scrollIntoViewIfNeeded();
  await page.locator('#song-film').evaluate(async video=>{
    video.muted=true;for(const track of video.textTracks)track.mode='hidden';await video.play();
  });
  await page.waitForFunction(()=>{
    const video=document.querySelector('#song-film');
    return video.currentTime>=3&&video.videoWidth>0&&[...video.textTracks].some(track=>track.cues?.length>=30);
  },{},{timeout:60000});
  report.music_player=await page.locator('#song-film').evaluate(video=>({duration:video.duration,
    width:video.videoWidth,height:video.videoHeight,current_time:video.currentTime,
    captions:[...video.textTracks].map(track=>({language:track.language,cues:track.cues?.length||0})),
    error:video.error?.code||null}));
  check('music_video_decodes_and_plays',report.music_player.current_time>=3&&report.music_player.duration>128&&report.music_player.duration<130&&!report.music_player.error);
  check('music_video_translation_loads',report.music_player.captions.some(track=>track.language==='en'&&track.cues===36));
  await page.locator('#song-film').evaluate(video=>video.pause());
  await page.screenshot({path:path.join(root,'artifacts/public-demo/published-music-player.png')});
  await page.setViewportSize({width:390,height:844});
  check('mobile_without_horizontal_overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(root,'artifacts/public-demo/published-mobile.png')});
  check('no_uncaught_browser_errors',report.console_errors.length===0);
  check('producer_unchanged',digest(await fs.readFile(__filename))===digest(source));
  report.status='passed';
})().catch(error=>{report.status='failed';report.failure=error.message;process.exitCode=1;}).finally(async()=>{
  await browser?.close().catch(()=>{});
  await fs.writeFile(path.join(root,'artifacts/public-materials-verification.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
});
