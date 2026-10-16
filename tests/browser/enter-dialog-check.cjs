const {chromium}=require('playwright-core');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const net=require('node:net');
const {spawn}=require('node:child_process');
const root=path.resolve(__dirname,'../..'),artifacts=path.join(root,'artifacts/browser');fs.mkdirSync(artifacts,{recursive:true});

(async()=>{
 const state=fs.mkdtempSync(path.join(os.tmpdir(),'rhythm-enter-'));
 let server,browser;
 try{
  const port=await new Promise(resolve=>{const probe=net.createServer();probe.listen(0,'127.0.0.1',()=>{const port=probe.address().port;probe.close(()=>resolve(port));});});
  server=spawn(process.env.OKEEPER_PYTHON||'python3',[path.join(root,'tests/browser/fixture_server.py')],{env:{...process.env,PYTHONUNBUFFERED:'1',OKEEPER_BIND:'127.0.0.1',OKEEPER_PORT:String(port),OKEEPER_STATE_DIR:state},stdio:['ignore','pipe','pipe']});
  await new Promise((resolve,reject)=>{
   let output='';const timer=setTimeout(()=>reject(new Error('test server startup timed out')),5000);
   server.stdout.on('data',chunk=>{output+=chunk;if(output.includes('Okeeper listening')){clearTimeout(timer);resolve();}});
   server.stderr.on('data',chunk=>{output+=chunk;});
   server.once('error',error=>{clearTimeout(timer);reject(error);});
   server.once('exit',code=>{clearTimeout(timer);reject(new Error(`test server exited ${code}: ${output}`));});
  });
  browser=await chromium.launch({headless:true,executablePath:process.env.OKEEPER_BROWSER||'/usr/bin/google-chrome',args:['--no-sandbox','--no-proxy-server']});
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],passed=[];
  let patches=0;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='PATCH')patches++;});
  await page.goto(`http://127.0.0.1:${port}`,{waitUntil:'networkidle'});
  await page.waitForSelector('.alarm-card');
  const id=await page.locator('.alarm-card').first().getAttribute('data-id');
  const card=()=>page.locator(`[data-id="${id}"]`);
  const open=async()=>{await card().locator('.edit-button').click();await page.waitForSelector('dialog[open]');};
  const saved=async()=>{await page.waitForFunction(()=>!document.querySelector('dialog').open);};
  const alarm=async()=>{const response=await page.request.get(`http://127.0.0.1:${port}/api/alarms`);return (await response.json()).alarms.find(a=>a.id===id);};

  await open();assert.equal(await page.evaluate(()=>document.activeElement.id),'save-button');
  await page.keyboard.press('Enter');await saved();assert.equal(patches,1);
  passed.push('open editor and immediately press Enter without clicking popup');

  await open();await page.locator('#add-dialog').click({position:{x:5,y:60}});
  await page.keyboard.press('Enter');await saved();assert.equal(patches,2);
  passed.push('click popup blank edge and press Enter');

  await open();await page.getByRole('radio',{name:/仅下一次/}).check();
  await page.keyboard.press('Enter');await saved();assert.equal((await alarm()).mode,'once');assert.equal(patches,3);
  passed.push('Enter while repeat radio is focused persists mode');

  await open();await page.evaluate(()=>{
   document.querySelector('input[name="repeat"][value="daily"]').checked=true;
   document.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
  });await saved();assert.equal((await alarm()).mode,'daily');assert.equal(patches,4);
  passed.push('Enter event targeting document instead of dialog still saves');

  await open();await page.locator('#alarm-hour').fill('8');await page.locator('#alarm-minute').fill('13');
  await page.keyboard.press('Enter');await saved();assert.equal((await alarm()).time,'08:13');assert.equal(patches,5);
  await page.reload({waitUntil:'networkidle'});assert.equal(await card().locator('.alarm-time').textContent(),'08:13');
  passed.push('Enter from numeric input saves normalized time and survives reload');

  await open();await page.locator('#alarm-hour').fill('24');await page.keyboard.press('Enter');
  assert.match(await page.locator('#form-error').textContent(),/小时为 00–23/);assert.equal(patches,5);assert.equal(await page.locator('dialog[open]').count(),1);
  await page.keyboard.press('Escape');
  passed.push('invalid time remains open and sends no mutation');

  await open();await page.locator('#alarm-hour').fill('12');await page.locator('#alarm-minute').fill('05');
  await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelector('#form-error').textContent.includes('已经添加'));
  assert.equal(await page.locator('dialog[open]').count(),1);assert.equal((await alarm()).time,'08:13');assert.equal(patches,6);
  await page.keyboard.press('Escape');
  passed.push('duplicate time shows server error without closing or changing task');

  await open();await page.evaluate(()=>{
   window.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',repeat:true,cancelable:true}));
   window.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,cancelable:true}));
  });assert.equal(patches,6);assert.equal(await page.locator('dialog[open]').count(),1);
  await page.keyboard.press('Enter');await saved();assert.equal(patches,7);
  passed.push('held Enter and IME composition do not submit; normal Enter submits once');
  assert.deepEqual(errors,[]);
  const proof={at:new Date().toISOString(),browser:browser.version(),isolated_state:true,production_alarms_untouched:true,passed,patch_requests:patches,page_errors:errors};
  fs.writeFileSync(path.join(artifacts,'enter-dialog-result.json'),JSON.stringify(proof,null,2)+'\n');
  console.log(JSON.stringify(proof,null,2));
 }finally{
  if(browser)await browser.close();
  if(server&&server.exitCode===null){server.kill();await new Promise(resolve=>server.once('exit',resolve));}
  fs.rmSync(state,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
