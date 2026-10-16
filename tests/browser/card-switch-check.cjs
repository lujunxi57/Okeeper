const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path'),os=require('node:os'),net=require('node:net'),{spawn}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),artifacts=path.join(root,'artifacts/browser');fs.mkdirSync(artifacts,{recursive:true});
(async()=>{
 const state=fs.mkdtempSync(path.join(os.tmpdir(),'okeeper-animation-'));
 const port=await new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});
 const server=spawn(process.env.OKEEPER_PYTHON||'python3',[path.join(root,'tests/browser/fixture_server.py')],{env:{...process.env,PYTHONUNBUFFERED:'1',OKEEPER_BIND:'127.0.0.1',OKEEPER_PORT:String(port),OKEEPER_STATE_DIR:state},stdio:['ignore','pipe','pipe']});
 await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>{server.kill();reject(new Error('fixture startup timed out'));},5000);server.stdout.on('data',c=>{output+=c;if(output.includes('Okeeper listening')){clearTimeout(timer);resolve();}});server.once('exit',code=>{clearTimeout(timer);reject(new Error(output+code));});});
 const browser=await chromium.launch({headless:true,executablePath:process.env.OKEEPER_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--no-proxy-server']});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
  let fail=false,patches=0;
  const alarms=['07:00','12:05','17:10'].map((time,i)=>({id:`00000000-0000-4000-8000-00000000000${i+1}`,time,enabled:true,mode:'daily'}));
  const snapshot=()=>({now:'2026-10-14T06:00:00+08:00',timezone:'Asia/Taipei',alarms:structuredClone(alarms),next:null});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/alarms**',async route=>{
   if(route.request().method()==='PATCH'){
    patches++;await new Promise(resolve=>setTimeout(resolve,450));
    if(fail)return route.fulfill({status:503,json:{error:'测试保存失败'}});
    const id=route.request().url().split('/').pop();Object.assign(alarms.find(a=>a.id===id),route.request().postDataJSON());
   }
   return route.fulfill({json:snapshot()});
  });
  await page.goto(`http://127.0.0.1:${port}`,{waitUntil:'networkidle'});await page.waitForSelector('.alarm-card.is-next');
  const first=page.locator(`[data-id="${alarms[0].id}"]`),second=page.locator(`[data-id="${alarms[1].id}"]`),toggle=first.locator('.toggle');
  assert.equal(await page.locator('.is-next').count(),1);assert.ok(await first.locator('.next-tag').isVisible());
  await page.screenshot({path:path.join(artifacts,'cards-desktop.png'),fullPage:true});
  await page.evaluate(()=>{window.savedSwitch=document.querySelector('.alarm-card .toggle');});
  const before=await toggle.evaluate(el=>getComputedStyle(el).gridTemplateColumns);
  const cardBefore=(await first.boundingBox()).width;
  await toggle.click();await page.waitForTimeout(60);
  const during=await toggle.evaluate(el=>getComputedStyle(el).gridTemplateColumns);
  const cardDuring=(await first.boundingBox()).width;
  assert.equal(await toggle.getAttribute('aria-checked'),'false');assert.equal(await first.getAttribute('class').then(c=>c.includes('is-next')),false);
  assert.ok((await second.getAttribute('class')).includes('is-next'));assert.equal(await page.evaluate(()=>window.savedSwitch===document.querySelector('.alarm-card .toggle')),true);
  await page.waitForFunction(()=>!document.querySelector('.alarm-card .toggle').disabled);
  const after=await toggle.evaluate(el=>getComputedStyle(el).gridTemplateColumns);
  await page.waitForTimeout(100);const cardClosed=(await first.boundingBox()).width;
  assert.ok(cardDuring<cardBefore&&cardDuring>cardClosed);assert.ok(Math.abs(cardClosed/cardBefore-.94)<.001);
  assert.notEqual(before,after);assert.notEqual(during,before);assert.notEqual(during,after);assert.equal(patches,1);
  await toggle.click();await page.waitForTimeout(60);const cardOpening=(await first.boundingBox()).width;
  await page.waitForFunction(()=>!document.querySelector('.alarm-card .toggle').disabled);await page.waitForTimeout(100);const cardOpened=(await first.boundingBox()).width;
  assert.ok(cardOpening>cardClosed&&cardOpening<cardOpened);assert.ok(Math.abs(cardOpened-cardBefore)<.1);assert.equal(await toggle.getAttribute('aria-checked'),'true');assert.ok((await first.getAttribute('class')).includes('is-next'));assert.equal(patches,2);
  fail=true;await toggle.click();assert.equal(await toggle.getAttribute('aria-checked'),'false');await page.waitForFunction(()=>!document.querySelector('.alarm-card .toggle').disabled);assert.equal(await toggle.getAttribute('aria-checked'),'true');assert.ok((await first.getAttribute('class')).includes('is-next'));assert.equal(patches,3);
  fail=false;await toggle.focus();await page.keyboard.press('Space');await page.waitForFunction(()=>!document.querySelector('.alarm-card .toggle').disabled);assert.equal(await toggle.getAttribute('aria-checked'),'false');assert.equal(await page.locator('dialog[open]').count(),0);
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(artifacts,'cards-mobile.png'),fullPage:true});assert.ok(await second.locator('.next-tag').isVisible());assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await toggle.evaluate(el=>getComputedStyle(el).transitionDuration),'0s');
  await toggle.click();const mobileLayoutWidth=await first.evaluate(el=>parseFloat(getComputedStyle(el).width));assert.ok(Math.abs((await first.boundingBox()).width-mobileLayoutWidth)<.1);await page.waitForFunction(()=>!document.querySelector('.alarm-card .toggle').disabled);
  assert.deepEqual(errors,[]);
  const proof={at:new Date().toISOString(),browser:browser.version(),library:'daisyUI 5.7.50 + Motion 14.2.0',isolated_fixture:true,mutations_intercepted:true,grid_columns:{before,during,after},card_widths:{before:cardBefore,closing:cardDuring,closed:cardClosed,opening:cardOpening,opened:cardOpened},node_preserved:true,immediate_switch:true,next_card_moves:true,failure_rollback:true,keyboard_space:true,mobile_badge_visible:true,mobile_no_overflow:true,reduced_motion:true,page_errors:errors};
  fs.writeFileSync(path.join(artifacts,'card-switch-result.json'),JSON.stringify(proof,null,2)+'\n');console.log(JSON.stringify(proof,null,2));
 }finally{await browser.close();server.kill();await new Promise(resolve=>server.once('exit',resolve));fs.rmSync(state,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
