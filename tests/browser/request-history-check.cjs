const {chromium}=require('playwright-core');
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn,spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),python=process.env.OKEEPER_PYTHON||'python3',artifacts=path.join(root,'artifacts/browser');
fs.mkdirSync(artifacts,{recursive:true});
(async()=>{
 const state=fs.mkdtempSync(path.join(os.tmpdir(),'okeeper-history-'));let server,browser;
 try{
  const port=await new Promise(resolve=>{const socket=net.createServer();socket.listen(0,'127.0.0.1',()=>{const port=socket.address().port;socket.close(()=>resolve(port));});});
  server=spawn(python,[path.join(root,'tests/browser/fixture_server.py')],{env:{...process.env,PYTHONUNBUFFERED:'1',OKEEPER_BIND:'127.0.0.1',OKEEPER_PORT:String(port),OKEEPER_STATE_DIR:state},stdio:['ignore','pipe','pipe']});
  await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(new Error('fixture timeout')),5000);server.stdout.on('data',c=>{output+=c;if(output.includes('Okeeper listening')){clearTimeout(timer);resolve();}});server.stderr.on('data',c=>output+=c);server.once('exit',code=>{clearTimeout(timer);reject(new Error(output+code));});});
  browser=await chromium.launch({headless:true,executablePath:process.env.OKEEPER_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--no-proxy-server']});
  const page=await browser.newPage({viewport:{width:1280,height:1000}}),errors=[],mutations=[];let historyReads=0;
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!['GET','HEAD'].includes(r.method()))mutations.push(r.url());if(r.url().endsWith('/api/requests'))historyReads++;});
  const base=`http://127.0.0.1:${port}`;
  await page.route('**/api/quota',r=>r.fulfill({json:{accounts:[{id:'a'.repeat(20),account:'morning@example.com',current:true,windows:[]},{id:'b'.repeat(20),account:'evening@example.com',current:false,windows:[]}],error:null}}));
  await page.goto(base,{waitUntil:'networkidle'});
  const dialog=page.locator('#request-history-dialog'),opener=page.locator('#open-request-history');
  assert.equal(historyReads,0);assert.equal(await dialog.isVisible(),false);
  await opener.click();await page.getByText('暂无请求记录，Okeeper 发起请求后会显示在这里。',{exact:true}).waitFor();
  assert.equal((await(await page.request.get(base+'/api/requests')).json()).requests.length,0);
  await page.keyboard.press('Escape');assert.equal(await dialog.isVisible(),false);assert.equal(await opener.evaluate(e=>e===document.activeElement),true);
  const seeded=spawnSync(python,['-c',`
from pathlib import Path
import datetime as dt,sys
from schedule_store import database
from account_quota import atomic_json
state=Path(sys.argv[1]);now=dt.datetime.now(dt.timezone.utc)
stamp=lambda offset:(now-dt.timedelta(seconds=offset)).isoformat()
with database(state) as db:
 for slot,age,route,status in [('auto',0,'isolated','accepted'),('account-manual-v1:a:request',30,'main','completed')]:
  db.execute('INSERT INTO account_runs(slot,alarm_id,revision,account_id,account_name,scheduled,status,at,route,thread_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(slot,'',0,'a'*20,'morning@example.com',stamp(age),status,stamp(age),route,'day-chat'))
 db.execute('INSERT INTO custom_runs(slot,alarm_id,revision,thread_id,scheduled,prompt,status,at,thread_name,account_name,route) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('custom','deleted',0,'chat',stamp(90),'<img src=x onerror=alert(1)>\\n第二行','completed',stamp(90),'Long project title','evening@example.com','main'))
 db.execute('INSERT INTO runs(slot,status,at,account_name) VALUES(?,?,?,?)',('daily-v2:'+stamp(120),'verified_completed',stamp(120),'old@example.com'))
atomic_json(state/'account-continue-receipts'/'continue.json',{'at':stamp(60),'thread_id':'original','status':'accepted','account_name':'evening@example.com','route':'main','operation_id':'operation','turn_id':'continue-turn'})
`,state],{cwd:path.join(root,'app'),encoding:'utf8'});assert.equal(seeded.status,0,seeded.stderr);
  await opener.click();await page.waitForFunction(()=>document.querySelectorAll('.request-history-record').length===5);
  assert.equal(await page.locator('.request-history-record img').count(),0);
  const continued=page.locator('.request-history-record').filter({hasText:'发送 continue'});
  assert.equal(await continued.locator('.request-history-status').textContent(),'已提交');
  const home=page.locator('.request-history-record').first();assert.equal(await home.locator('.request-history-location').textContent(),'Home');
  await home.locator('summary').click();assert.equal(await home.locator('.request-history-detail').getByText('morning@example.com 的独立 Home',{exact:true}).isVisible(),true);
  const custom=page.locator('.request-history-record').filter({hasText:'定向发送'});await custom.locator('summary').click();
  assert.ok((await custom.locator('code').first().textContent()).includes('<img src=x onerror=alert(1)>'));
  await custom.locator('summary').click();
  const failedSettings=spawnSync(python,['-c',"import sqlite3,sys;from pathlib import Path;db=sqlite3.connect(Path(sys.argv[1])/'runs.sqlite');db.execute(\"UPDATE custom_runs SET status='failed_or_uncertain',error=? WHERE slot='custom'\",('无法确认会话当前的模型和思考强度，未发送',));db.commit()",state],{encoding:'utf8'});assert.equal(failedSettings.status,0,failedSettings.stderr);
  await page.locator('#refresh-request-history').click();await page.waitForFunction(()=>[...document.querySelectorAll('.request-history-record')].some(e=>e.textContent.includes('无法确认会话当前的模型和思考强度，未发送。')));
  assert.equal(await custom.locator('.request-history-status').textContent(),'未发送');await custom.locator('summary').click();assert.ok(await custom.getByText('无法确认会话当前的模型和思考强度，未发送。',{exact:true}).isVisible());await custom.locator('summary').click();
  const changed=spawnSync(python,['-c',"import sqlite3,sys;from pathlib import Path;db=sqlite3.connect(Path(sys.argv[1])/'runs.sqlite');db.execute(\"UPDATE account_runs SET status='completed' WHERE slot='auto'\");db.commit()",state],{encoding:'utf8'});assert.equal(changed.status,0,changed.stderr);
  await page.waitForFunction(()=>document.querySelector('.request-history-record .request-history-status')?.textContent==='已完成',{},{timeout:12000});
  assert.equal(await page.locator('.request-history-record').first().evaluate(e=>e.open),true);
  const timedOut=spawnSync(python,['-c',"import sqlite3,sys;from pathlib import Path;from turn_result import FAILURE_MESSAGES;db=sqlite3.connect(Path(sys.argv[1])/'runs.sqlite');db.execute(\"UPDATE account_runs SET status='failed_or_uncertain',error=? WHERE slot='auto'\",(FAILURE_MESSAGES['observation_timeout'],));db.commit()",state],{cwd:path.join(root,'app'),encoding:'utf8'});assert.equal(timedOut.status,0,timedOut.stderr);
  await page.locator('#refresh-request-history').click();await page.waitForFunction(()=>document.querySelector('.request-history-record .request-history-status')?.textContent==='等待超时');
  assert.ok(await home.getByText('等待本轮完成超时，结果未确认；不会自动补发。',{exact:true}).isVisible());
  await page.screenshot({path:path.join(artifacts,'request-history-desktop.png'),fullPage:true});
  for(const width of [1280,768,620,390,320]){
   await page.setViewportSize({width,height:844});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`page width ${width}`);
   assert.ok(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth+1),`dialog width ${width}`);
   assert.ok(await page.locator('#request-history-body').evaluate(e=>e.scrollWidth<=e.clientWidth+1),`body width ${width}`);
   assert.ok(await page.locator('#dismiss-request-history').evaluate(e=>e.getBoundingClientRect().bottom<=innerHeight),`close visible ${width}`);
  }
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(artifacts,'request-history-mobile.png'),fullPage:true});
  await page.route('**/api/requests',r=>r.fulfill({status:503,json:{error:'fixture failure'}}));
  await page.locator('#refresh-request-history').click();await page.waitForFunction(()=>document.querySelector('#request-history-state').textContent.includes('读取失败'));
  assert.equal(await page.locator('.request-history-record').count(),5);
  await page.unroute('**/api/requests');await page.locator('#refresh-request-history').click();await page.waitForFunction(()=>document.querySelector('#request-history-state').hidden);
  await page.locator('#close-request-history').click();assert.equal(await dialog.isVisible(),false);
  const closedReads=historyReads;await page.waitForTimeout(5200);assert.equal(historyReads,closedReads);
  await opener.click();await dialog.waitFor({state:'visible'});await page.mouse.click(1,1);assert.equal(await dialog.isVisible(),false);
  assert.deepEqual(mutations,[]);assert.deepEqual(errors,[]);
  const proof={real_read_only_endpoint:true,closed_by_default:true,empty_state:true,all_four_kinds:true,continue_is_submission:true,account_home_ownership:true,prompt_is_literal_text:true,known_pre_submit_failure_explained:true,account_observation_timeout_explained:true,auto_refresh_preserves_details:true,error_keeps_old_records:true,retry_recovers:true,close_stops_polling:true,escape_restores_focus:true,backdrop_closes:true,mobile_fit:true,mutations,page_errors:errors};
  fs.writeFileSync(path.join(artifacts,'request-history-result.json'),JSON.stringify(proof,null,2)+'\n');console.log(JSON.stringify(proof,null,2));
 }finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(resolve=>server.once('exit',resolve));}fs.rmSync(state,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
