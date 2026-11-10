const {chromium}=require('playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),artifacts=path.join(root,'artifacts/browser');
fs.mkdirSync(artifacts,{recursive:true});
(async()=>{
 const state=fs.mkdtempSync(path.join(os.tmpdir(),'okeeper-manual-'));let server,browser;
 try{
  const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
  server=spawn(process.env.OKEEPER_PYTHON||'python3',[path.join(root,'tests/browser/fixture_server.py')],{env:{...process.env,PYTHONUNBUFFERED:'1',OKEEPER_FIXTURE_MANUAL:'1',OKEEPER_BIND:'127.0.0.1',OKEEPER_PORT:String(port),OKEEPER_STATE_DIR:state},stdio:['ignore','pipe','pipe']});
  await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(new Error('fixture startup timed out')),5000);server.stdout.on('data',c=>{output+=c;if(output.includes('Okeeper listening')){clearTimeout(timer);resolve();}});server.stderr.on('data',c=>output+=c);server.once('exit',code=>{clearTimeout(timer);reject(new Error(output+code));});});
  browser=await chromium.launch({headless:true,executablePath:process.env.OKEEPER_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--no-proxy-server']});
  const page=await browser.newPage({viewport:{width:1280,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const base=`http://127.0.0.1:${port}`,account='a'.repeat(20),other='b'.repeat(20),headers={'Content-Type':'application/json','X-Rhythm-Request':'1'};
  const quota={accounts:[account,other].map((id,i)=>({id,account:i?'evening@example.com':'morning@example.com',windows:[],current:!i})),error:null};
  await page.route('**/api/quota*',r=>r.request().url().includes('quota-watch')?r.continue():r.fulfill({json:quota}));
  const view=async()=>await(await page.request.get(base+'/api/alarms')).json();
  const before=(await view()).alarms;
  await page.goto(base,{waitUntil:'networkidle'});
  const lane=page.locator(`.quota-row[data-id="${account}"]`),button=lane.locator('.manual-send-btn');
  await button.waitFor();let posted=[];
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/trigger'))posted.push(r.postDataJSON().request_id);});
  await button.dblclick();
  await page.waitForFunction(id=>document.querySelector(`.quota-row[data-id="${id}"] .manual-send-status`)?.textContent==='已发送',account);
  assert.equal(posted.length,1);
  const completed=(await view()).manual_runs[account];assert.equal(completed.status,'completed');
  assert.deepEqual((await view()).alarms,before);
  const retry=await page.request.post(`${base}/api/accounts/${account}/trigger`,{headers,data:{request_id:posted[0]}});
  assert.equal(retry.status(),202);assert.equal((await retry.json()).manual_runs[account].slot,completed.slot);
  assert.equal((await page.request.post(`${base}/api/accounts/${account}/trigger`,{headers,data:{request_id:'invalid'}})).status(),400);
  assert.equal((await page.request.post(`${base}/api/accounts/${account}/trigger`,{headers,data:{request_id:posted[0],prompt:'override'}})).status(),400);
  assert.equal((await page.request.post(`${base}/api/accounts/${'c'.repeat(20)}/trigger`,{headers,data:{request_id:posted[0]}})).status(),400);
  assert.equal((await page.request.post(`${base}/api/accounts/${account}/trigger`,{data:{request_id:posted[0]}})).status(),403);

  // A lost POST response is recovered through the persisted receipt, not a resend.
  let dropped=false;
  await page.route(`**/api/accounts/${other}/trigger`,async route=>{await route.fetch();dropped=true;await route.abort('failed');});
  await page.locator(`.quota-row[data-id="${other}"] .manual-send-btn`).click();
  await page.waitForFunction(()=>document.querySelector('#toast-text').textContent.includes('连接不上'));
  assert.ok(dropped);
  await page.unroute(`**/api/accounts/${other}/trigger`);
  await page.evaluate(()=>refresh());
  await page.waitForFunction(id=>document.querySelector(`.quota-row[data-id="${id}"] .manual-send-status`)?.textContent==='已发送',other);
  const firstOther=(await view()).manual_runs[other].slot;
  await page.locator(`.quota-row[data-id="${other}"] .manual-send-btn`).click();
  await page.waitForFunction(id=>document.querySelector(`.quota-row[data-id="${id}"] .manual-send-status`)?.textContent==='已发送',other);
  assert.notEqual((await view()).manual_runs[other].slot,firstOther);

  // Consume the 05:26 one-shot at 05:26:00.3 and check the actual DOM immediately.
  const make=(id,time,extra={})=>({id,time,enabled:true,mode:'daily',target_type:'default',account_id:account,...extra});
  const once=make('00000000-0000-4000-8000-000000000001','05:26',{mode:'once',run_at:'2026-10-18T05:26:00+08:00'});
  const later=make('00000000-0000-4000-8000-000000000002','15:17');
  const ordered=()=>lane.locator('.alarm-card').evaluateAll(cards=>cards.map(c=>c.dataset.id));
  await page.route('**/api/alarms',r=>r.fulfill({json:{now:'2026-10-18T05:26:00.300+08:00',alarms:[{...once,enabled:false,consumed_at:'2026-10-18T05:26:00.100+08:00',last_run:{status:'accepted'}},later]}}));
  await page.evaluate(data=>{accept(data);window.onceCard=document.querySelector(`[data-id="${data.alarms[0].id}"]`);},{now:'2026-10-18T05:25:59+08:00',alarms:[once,later]});
  assert.deepEqual(await ordered(),[once.id,later.id]);
  const consumed={...once,enabled:false,consumed_at:'2026-10-18T05:26:00.100+08:00',last_run:{status:'accepted'}};
  await page.evaluate(data=>accept(data),{now:'2026-10-18T05:26:00.300+08:00',alarms:[consumed,later]});
  assert.deepEqual(await ordered(),[later.id,once.id]);
  assert.equal(await page.evaluate(id=>window.onceCard===document.querySelector(`[data-id="${id}"]`),once.id),true);
  assert.equal(await lane.locator(`[data-id="${once.id}"] .alarm-day-offset`).isHidden(),true);
  // Daily rollover must move without waiting for a fresh server snapshot.
  const daily={...once,mode:'daily',next_at:'2026-10-18T05:26:00+08:00'};
  await page.unroute('**/api/alarms');
  await page.route('**/api/alarms',r=>r.abort());
  await page.evaluate(data=>accept(data),{now:'2026-10-18T05:25:59.800+08:00',alarms:[daily,later]});
  assert.deepEqual(await ordered(),[daily.id,later.id]);
  await page.waitForTimeout(1300);
  assert.deepEqual(await ordered(),[later.id,daily.id]);
  assert.equal(await lane.locator(`[data-id="${daily.id}"] .alarm-day-offset`).textContent(),'+1');

  await page.screenshot({path:path.join(artifacts,'manual-trigger-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.ok(await button.isVisible());assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(artifacts,'manual-trigger-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  const proof={browser:browser.version(),isolated_fixture:true,real_inference:false,double_click_one_request:true,persistent_retry_one_run:true,lost_response_recovered:true,alarms_unchanged:true,once_moves_within_current_minute:true,daily_moves_without_poll:true,card_node_preserved:true,mobile_no_overflow:true,page_errors:errors};
  fs.writeFileSync(path.join(artifacts,'manual-trigger-result.json'),JSON.stringify(proof,null,2)+'\n');console.log(JSON.stringify(proof,null,2));
 }finally{
  if(browser)await browser.close();
  if(server){server.kill();await new Promise(resolve=>server.once('exit',resolve));}
  fs.rmSync(state,{recursive:true,force:true});
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
