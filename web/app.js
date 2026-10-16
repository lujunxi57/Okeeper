'use strict';
const $ = selector => document.querySelector(selector);
let snapshot = null, clockOffset = 0, loadingMutation = false, toastTimer, undoAction, editingAlarm = null;
const dialog = $('#add-dialog'), grid = $('#alarms');
const threadDialog=$('#thread-dialog');
let selectedThread=null,sessions=[],sessionLoading=false,sessionError='',accountChoice='',availableAccounts=[];
function selectedDelivery(){return $('input[name="delivery"]:checked').value;}
function deliveryPayload(alarm){return alarm.target_type==='thread'?{target_type:'thread',thread_id:alarm.thread_id,prompt:alarm.prompt}:{target_type:'default',account_id:alarm.account_id||''};}
function renderAccountChoices(){
 const select=$('#alarm-account');select.replaceChildren();
 const follow=document.createElement('option');follow.value='';follow.textContent='跟随主账号';select.append(follow);
 for(const account of availableAccounts){const option=document.createElement('option');option.value=account.id;option.textContent=account.name;select.append(option);}
 if(accountChoice&&!availableAccounts.some(a=>a.id===accountChoice)){const missing=document.createElement('option');missing.value=accountChoice;missing.textContent=editingAlarm?.account_name||'账号暂时不可用';select.append(missing);}
 select.value=accountChoice;
}
async function loadAccountChoices(){
 try{availableAccounts=(await request('/api/accounts')).accounts;renderAccountChoices();}catch(error){$('#form-error').textContent=error.message;}
}
$('#alarm-account').onchange=event=>{accountChoice=event.target.value;};
function updateDelivery(){
 const custom=selectedDelivery()==='thread';$('#custom-delivery').hidden=!custom;$('#default-delivery-note').hidden=custom;
 $('#account-delivery').hidden=custom;
 $('#chosen-name').textContent=selectedThread?.name||'选择一个会话';$('#chosen-id').textContent=selectedThread?`session id: ${selectedThread.id}`:'当前服务器全部会话';
}
function renderSessions(){
 const list=$('#session-list');list.replaceChildren();
 if(sessionLoading||sessionError){const p=document.createElement('p');p.className='session-empty';p.textContent=sessionLoading?'正在读取会话…':sessionError;list.append(p);return;}
 const query=$('#session-search').value.trim().toLocaleLowerCase(),filtered=sessions.filter(s=>`${s.name} ${s.id}`.toLocaleLowerCase().includes(query));
 if(!filtered.length){const p=document.createElement('p');p.className='session-empty';p.textContent='没有找到会话';list.append(p);}
 filtered.forEach(session=>{
  const button=document.createElement('button');button.type='button';button.className='session-option';button.dataset.id=session.id;button.disabled=!session.selectable;
  const name=document.createElement('strong');name.textContent=session.name;const id=document.createElement('small');id.className='session-id';id.textContent=`session id: ${session.id}`;
  button.append(name,id);
  if(session.archived||!session.selectable){const badge=document.createElement('span');badge.className='session-note';badge.textContent=session.reason||(session.archived?'已归档':'');button.append(badge);}
  button.title=session.cwd||'';button.classList.toggle('selected',session.id===selectedThread?.id);
  button.onclick=()=>{selectedThread={id:session.id,name:session.name};updateDelivery();threadDialog.close();$('#choose-thread').focus({preventScroll:true});};list.append(button);
 });
}
async function loadSessions(){
 if(sessionLoading)return;sessionLoading=true;sessionError='';renderSessions();
 try{sessions=(await request('/api/sessions')).sessions;}catch(error){sessionError=error.message;}finally{sessionLoading=false;renderSessions();}
}
$('#choose-thread').onclick=()=>{threadDialog.showModal();$('#session-search').value='';$('#session-search').focus();loadSessions();};
$('#close-threads').onclick=()=>threadDialog.close();$('#refresh-sessions').onclick=loadSessions;$('#session-search').oninput=renderSessions;
threadDialog.addEventListener('click',event=>{if(event.target===threadDialog){const r=threadDialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)threadDialog.close();}});
document.querySelectorAll('input[name="delivery"]').forEach(input=>input.addEventListener('change',updateDelivery));
const cardAnimations=new WeakMap(),reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
function updateCardScale(card,enabled){
 const state=String(enabled),initial=card.dataset.enabled===undefined,changed=card.dataset.enabled!==state,target=enabled?1:.94;
 card.dataset.enabled=state;
 if(initial||reducedMotion.matches){cardAnimations.get(card)?.stop();card.style.transform=`scale(${target})`;return;}
 if(!changed)return;
 const from=getComputedStyle(card).transform;
 cardAnimations.get(card)?.stop();card.style.transform=from;
 cardAnimations.set(card,Motion.animateMini(card,{transform:[from,`scale(${target})`]},{type:Motion.spring,duration:.45,bounce:.1}));
}
reducedMotion.addEventListener('change',()=>{if(snapshot)render();});
const fmtClock = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Taipei',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
const fmtDay = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'});
const fmtTime = new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Taipei',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
const pad = n => String(n).padStart(2,'0');
const quotaDate = new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Taipei',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
let quotaLoading=false;
let controlState=null,controlLoading=false,controlPoll=null,controlAction=null,quotaView=null;
const controlDialog=$('#control-dialog');
function switchButton(account){
 const button=document.createElement('button');button.type='button';button.className='account-switch';button.textContent='切换并重启';
 const native=controlState?.accounts?.find(a=>a.id===account.id),runtime=controlState?.runtime;
 const current=Boolean(runtime?.running&&runtime.email?.toLocaleLowerCase()===account.account?.toLocaleLowerCase());
 button.hidden=current;button.disabled=!native?.switchable||Boolean(controlState?.busy)||!controlState;
 button.title=native?.switchable?'切换主后台账号并重启':'此账号尚无可用的原生登录副本';
 button.onclick=()=>openControl('switch',account);return button;
}
function renderControl(){
 const operation=controlState?.operation,banner=$('#backend-operation');
 banner.hidden=!operation;banner.textContent=operation?.message||'';
 banner.classList.toggle('failed',operation?.status==='failed');
 $('#restart-backend').disabled=!controlState||Boolean(controlState.busy);
 if(quotaView)renderQuota(quotaView);
 if(controlState?.busy&&!controlPoll)controlPoll=setInterval(()=>refreshControl(),2000);
 if(!controlState?.busy&&controlPoll){clearInterval(controlPoll);controlPoll=null;refreshQuota(true);}
}
async function refreshControl(){
 if(controlLoading)return;controlLoading=true;
 try{controlState=await request('/api/account-control');renderControl();}
 catch(error){$('#restart-backend').disabled=true;$('#restart-backend').title=error.message;}
 finally{controlLoading=false;}
}
function openControl(action,account=null){
 controlAction={action,account_id:account?.id||'',confirmed:true,request_id:crypto.randomUUID?.()||'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const n=crypto.getRandomValues(new Uint8Array(1))[0]&15;return(c==='x'?n:(n&3)|8).toString(16);})};
 $('#control-title').textContent=action==='switch'?'切换账号并重启':'重启 Codex 后台';
 $('#control-target').textContent=action==='switch'?account.account:(controlState?.runtime?.email||'当前主后台');
 $('#confirm-control').textContent=action==='switch'?'确认切换并重启':'确认重启';
 $('#control-error').textContent='';
 const count=controlState?.runtime?.active_tasks;$('#control-active').textContent=Number.isInteger(count)&&count>0?`当前检测到 ${count} 个任务正在执行。`:'';
 controlDialog.showModal();$('#cancel-control').focus({preventScroll:true});
}
$('#restart-backend').onclick=()=>openControl('restart');
$('#close-control').onclick=$('#cancel-control').onclick=()=>controlDialog.close();
$('#confirm-control').onclick=async()=>{
 if(!controlAction)return;const button=$('#confirm-control');button.disabled=true;
 try{
  const operation=await request('/api/account-control','POST',controlAction);
  controlDialog.close();controlState={...(controlState||{}),operation,busy:['queued','stopping','backing_up','activating','starting','verifying','rolling_back'].includes(operation.status)};
  renderControl();refreshControl();
 }catch(error){$('#control-error').textContent=error.message;}
 finally{button.disabled=false;}
};
setInterval(()=>{if(!document.hidden&&!controlPoll)refreshControl();},30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshControl();});
refreshControl();
let watchState=null,watchSaving=false,watchVersion=0;
function renderWatch(){
 const input=$('#auto-switch');input.checked=Boolean(watchState?.enabled);input.disabled=watchSaving||!watchState;
 input.setAttribute('aria-checked',String(Boolean(watchState?.enabled)));
 $('#auto-switch-status').textContent=watchState?.enabled?(watchState.message||'已开启 · 等待检查'):'已关闭';
 $('#auto-switch-status').title=watchState?.checked_at?`上次检查：${quotaDate.format(new Date(watchState.checked_at))}`:'';
}
async function refreshWatch(){
 if(watchSaving||document.hidden)return;
 const version=watchVersion;
 try{const value=await request('/api/quota-watch');if(!watchSaving&&version===watchVersion){watchState=value;renderWatch();}}
 catch(error){if(!watchState)$('#auto-switch-status').textContent='暂时无法读取设置';}
}
$('#auto-switch').onchange=async event=>{
 if(watchSaving)return;const enabled=event.target.checked;watchSaving=true;watchVersion++;event.target.disabled=true;
 try{watchState=await request('/api/quota-watch','PATCH',{enabled});toast(enabled?'自动切换已开启':'自动切换已关闭');}
 catch(error){toast(error.message);}finally{watchSaving=false;renderWatch();}
};
setInterval(refreshWatch,15000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshWatch();});refreshWatch();
function quotaWindowLabel(seconds){
 if(seconds===18000)return '5 小时额度';if(seconds===604800)return '每周额度';if(seconds===2592000)return '每月额度';
 if(!seconds)return '订阅额度';return seconds>=86400?`${Math.round(seconds/86400)} 天额度`:`${Math.round(seconds/3600)} 小时额度`;
}
function renderQuota(data){
 quotaView=data;
 document.querySelector('.quota-panel').classList.remove('quota-stale');
 const accounts=$('#quota-accounts');accounts.replaceChildren();
 $('#quota-count').textContent=data.accounts?.length?String(data.accounts.length):'';
 for(const account of data.accounts||[]){
  const row=document.createElement('div');row.className='quota-row';row.dataset.id=account.id;
  row.classList.toggle('quota-stale',Boolean(account.stale||account.error));
  const person=document.createElement('div');person.className='quota-person';
  const name=document.createElement('span');name.className='quota-email';name.textContent=account.account;name.title=account.account;
  person.append(name);
  const actual=controlState?.runtime,active=actual?.running?actual.email?.toLocaleLowerCase()===account.account?.toLocaleLowerCase():account.current;
  if(active){const badge=document.createElement('span');badge.className='quota-current';badge.textContent='当前';person.append(badge);}
  if(account.plan){const plan=document.createElement('span');plan.className='quota-plan';plan.textContent=account.plan.replace(/_/g,' ');person.append(plan);}
  row.append(person);
  const windows=document.createElement('div');windows.className='quota-windows';
  for(const window of account.windows||[]){
   const item=document.createElement('div');item.className='quota-window';item.classList.toggle('quota-low',window.remaining_percent<=20);
   const head=document.createElement('div');head.className='quota-window-heading';
   const label=quotaWindowLabel(window.window_seconds),name=document.createElement('span');name.textContent=label.replace('额度','');
   const amount=document.createElement('strong');amount.className='quota-amount';amount.textContent=`${window.remaining_percent.toLocaleString('zh-CN',{maximumFractionDigits:1})}%`;amount.title='剩余额度';
   const bar=document.createElement('progress');bar.max=100;bar.value=window.remaining_percent;bar.setAttribute('aria-label',`${account.account} ${label}剩余`);
   const reset=document.createElement('span');reset.className='quota-reset';reset.textContent=window.resets_at?`${quotaDate.format(new Date(window.resets_at*1000))} 重置`:'未提供重置时间';reset.title='台北时间';
   head.append(name,amount,reset);item.append(head,bar);windows.append(item);
  }
  if(account.error){const error=document.createElement('span');error.className='quota-error';error.textContent=account.stale?'更新失败 · 上次数据':account.error;error.title=account.updated_at?`上次更新：${quotaDate.format(new Date(account.updated_at))}`:'';windows.append(error);}
  row.append(windows,switchButton(account));accounts.append(row);
 }
 const status=$('#quota-status'),valid=(data.accounts||[]).filter(a=>a.updated_at&&!a.error);
 const updated=valid.map(a=>new Date(a.updated_at)).sort((a,b)=>a-b)[0];
 status.textContent=data.error?'部分不可用':updated?`${fmtTime.format(updated)} · 台北`:'台北时间';
 status.title=data.error||'剩余额度 · 每分钟自动刷新 · 重置时间均为台北时间';
 if(!data.accounts?.length){const empty=document.createElement('p');empty.className='quota-empty';empty.textContent=data.error||'暂未找到 Codex 账号';accounts.append(empty);}
}
async function refreshQuota(force=false){
 if(quotaLoading||document.hidden)return;quotaLoading=true;
 const panel=document.querySelector('.quota-panel'),button=$('#refresh-quota');panel.setAttribute('aria-busy','true');button.disabled=true;
 try{renderQuota(await request(`/api/quota${force?'?refresh=1':''}`));}
 catch(error){$('#quota-status').textContent=$('#quota-accounts').children.length?'更新失败 · 上次数据':'暂时无法读取额度';panel.classList.add('quota-stale');}
 finally{quotaLoading=false;panel.setAttribute('aria-busy','false');button.disabled=false;}
}
$('#refresh-quota').onclick=()=>refreshQuota(true);
setInterval(()=>refreshQuota(),60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshQuota();});
refreshQuota();
const wheelLimits = {hour:24,minute:60};
function wheelInput(unit) { return $(`#alarm-${unit}`); }
function wheelValue(unit) {
 const value=Number(wheelInput(unit).dataset.value||0),raw=wheelInput(unit).value;
 return /^\d{1,2}$/.test(raw)&&Number(raw)<wheelLimits[unit]?Number(raw):value;
}
function syncWheel(unit,value) {
 const input=wheelInput(unit),limit=wheelLimits[unit],column=$(`#wheel-${unit}`),number=((value%limit)+limit)%limit;
 input.dataset.value=String(number);input.setAttribute('aria-valuenow',String(number));
 column.querySelector('.wheel-previous').textContent=pad((number+limit-1)%limit);
 column.querySelector('.wheel-next').textContent=pad((number+1)%limit);
}
function setWheel(unit,value,updateNote=true) {
 const number=((value%wheelLimits[unit])+wheelLimits[unit])%wheelLimits[unit];
 wheelInput(unit).value=pad(number);wheelInput(unit).removeAttribute('aria-invalid');syncWheel(unit,number);
 if(updateNote)note();
}
function selectedTime() {
 const values=['hour','minute'].map(unit=>{
  const raw=wheelInput(unit).value;
  if(!/^\d{1,2}$/.test(raw)||Number(raw)>=wheelLimits[unit])throw new Error('请检查时间：小时为 00–23，分钟为 00–59。');
  return Number(raw);
 });
 return `${pad(values[0])}:${pad(values[1])}`;
}
function selectedMode() { return document.querySelector('input[name="repeat"]:checked').value; }
function currentTime() { return new Date(Date.now()+clockOffset); }
function dayString(date) { const p=Object.fromEntries(fmtDay.formatToParts(date).map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day}`; }
function dailyTarget(time,now) { let date=new Date(`${dayString(now)}T${time}:00+08:00`);if(date<=now)date=new Date(date.getTime()+86400000);return date; }
function dayLabel(date,now) {return fmtDay.format(date)===fmtDay.format(now)?'今天':'明天';}
function note() {
 const now=currentTime(),time=`${pad(wheelValue('hour'))}:${pad(wheelValue('minute'))}`;
 $('#occurrence-note').textContent=selectedMode()==='once'?`${dayLabel(dailyTarget(time,now),now)} ${time} 发送一次，之后自动关闭。`:`每天 ${time} 发送。`;
}
for(const unit of ['hour','minute']) {
 const input=wheelInput(unit),column=$(`#wheel-${unit}`);
 setWheel(unit,unit==='hour'?7:0,false);
 input.addEventListener('focus',()=>input.select());
 input.addEventListener('input',()=>{
  const clean=input.value.replace(/\D/g,'').slice(0,2);if(input.value!==clean)input.value=clean;
  if(!clean){input.setAttribute('aria-invalid','true');return;}
  const value=Number(clean);if(value>=wheelLimits[unit]){input.setAttribute('aria-invalid','true');return;}
  input.removeAttribute('aria-invalid');syncWheel(unit,value);note();
 });
 input.addEventListener('blur',()=>{
  const raw=input.value;
  if(/^\d{1,2}$/.test(raw)&&Number(raw)<wheelLimits[unit])setWheel(unit,Number(raw));
  else input.setAttribute('aria-invalid','true');
 });
 input.addEventListener('keydown',event=>{
  if(event.key==='ArrowUp'||event.key==='ArrowDown'){event.preventDefault();setWheel(unit,wheelValue(unit)+(event.key==='ArrowUp'?1:-1));}
  else if(event.key==='PageUp'||event.key==='PageDown'){event.preventDefault();setWheel(unit,wheelValue(unit)+(event.key==='PageUp'?5:-5));}
 });
 column.addEventListener('click',event=>{
  if(suppressedWheelClick===column){event.preventDefault();event.stopImmediatePropagation();suppressedWheelClick=null;return;}
  if(event.target!==input)input.focus();
 });
 column.addEventListener('wheel',event=>{
  event.preventDefault();const delta=event.deltaMode===1?event.deltaY*32:event.deltaMode===2?event.deltaY*120:event.deltaY;
  let steps=0;if(Math.abs(delta)>=50){steps=Math.sign(delta)*Math.max(1,Math.round(Math.abs(delta)/100));wheelRemainder[unit]=0;}else{wheelRemainder[unit]+=delta;steps=Math.trunc(wheelRemainder[unit]/45);wheelRemainder[unit]-=steps*45;}
  if(steps)setWheel(unit,wheelValue(unit)+steps);
 },{passive:false});
 column.addEventListener('pointerdown',event=>{
  if(event.pointerType==='mouse'&&event.button!==0)return;
  pointerDrag={unit,column,pointerId:event.pointerId,startY:event.clientY,lastY:event.clientY,travel:0,moved:false};
 });
}
let wheelRemainder={hour:0,minute:0},pointerDrag=null,suppressedWheelClick=null,suppressWheelClickTimer;
document.addEventListener('pointermove',event=>{
 if(!pointerDrag||event.pointerId!==pointerDrag.pointerId)return;
 const drag=pointerDrag,delta=drag.lastY-event.clientY;drag.lastY=event.clientY;drag.travel+=delta;
 const steps=Math.trunc(drag.travel/36);
 if(steps){setWheel(drag.unit,wheelValue(drag.unit)+steps);drag.travel-=steps*36;}
 if(Math.abs(event.clientY-drag.startY)>7){drag.moved=true;if(document.activeElement===wheelInput(drag.unit))wheelInput(drag.unit).blur();if(event.cancelable)event.preventDefault();}
},{passive:false});
function finishWheelPointer(event){
 if(!pointerDrag||event.pointerId!==pointerDrag.pointerId)return;
 if(pointerDrag.moved){suppressedWheelClick=pointerDrag.column;clearTimeout(suppressWheelClickTimer);suppressWheelClickTimer=setTimeout(()=>{suppressedWheelClick=null;},600);}
 pointerDrag=null;
}
document.addEventListener('pointerup',finishWheelPointer);document.addEventListener('pointercancel',finishWheelPointer);
document.querySelectorAll('input[name="repeat"]').forEach(r=>r.addEventListener('change',note));
function toast(text,undo=null) { clearTimeout(toastTimer);$('#toast-text').textContent=text;undoAction=undo;$('#undo-button').hidden=!undo;$('#toast').hidden=false;toastTimer=setTimeout(()=>{$('#toast').hidden=true;undoAction=null;},undo?8000:4000); }
async function request(path,method='GET',payload={}) {
 const options={method,cache:'no-store'};
 if(method!=='GET'){options.headers={'Content-Type':'application/json','X-Rhythm-Request':'1'};options.body=JSON.stringify(payload);}
 let response;try{response=await fetch(path,options);}catch{throw new Error('暂时连接不上服务器，请稍后重试');}
 const data=await response.json();if(!response.ok)throw new Error(data.error||'操作失败，请重试');return data;
}
function accept(data) {snapshot=data;clockOffset=new Date(data.now).getTime()-Date.now();$('#connection').classList.remove('offline');$('#connection span').textContent='已连接';$('#error-banner').hidden=true;render();}
function render() {
 const cards=new Map([...grid.querySelectorAll('.alarm-card')].map(card=>[card.dataset.id,card]));
 grid.querySelectorAll('.loading-card').forEach(card=>card.remove());
 $('#count').textContent=snapshot.alarms.length;$('#empty-state').hidden=!!snapshot.alarms.length;grid.hidden=!snapshot.alarms.length;
 snapshot.alarms.forEach((alarm,index)=>{
  const article=cards.get(alarm.id)||$('#alarm-template').content.querySelector('.alarm-card').cloneNode(true);
  cards.delete(alarm.id);
  article.dataset.id=alarm.id;article.tabIndex=0;article.setAttribute('aria-label',`编辑 ${alarm.time} 发送`);article.classList.toggle('paused',!alarm.enabled);
  updateCardScale(article,alarm.enabled);
  article.querySelector('.alarm-time').textContent=alarm.time;article.querySelector('.card-kicker').textContent=alarm.mode==='once'?'仅一次':'每天';
  const target=article.querySelector('.alarm-target');target.hidden=alarm.target_type!=='thread'&&!alarm.account_id;target.textContent=alarm.target_type==='thread'?(alarm.thread_name||'指定会话'):(alarm.account_name||'指定账号');target.title=alarm.thread_id?`session id: ${alarm.thread_id}`:'';article.classList.toggle('custom-card',alarm.target_type==='thread');article.classList.toggle('account-card',Boolean(alarm.account_id));
  const statuses={completed:'已完成',failed:'发送失败',missed:'已错过',pending:'发送中'};
  const result=alarm.last_run?.status,customStatuses={dispatching:'准备发送',checking:'准备发送',sending:'准备发送',accepted:'已启动',running:'执行中',waiting_input:'等待确认',observing_disconnected:'等待连接',completed:alarm.enabled?'上次已完成':'已完成',skipped_busy:'忙碌跳过',skipped_missed:'已错过',skipped_changed:'设置已变更',skipped_account_changed:'账号已变更',failed_or_uncertain:'执行失败'};
  const status=article.querySelector('.alarm-status');status.textContent=(alarm.target_type==='thread'||alarm.account_id)&&result?customStatuses[result]||'已记录':alarm.enabled?'已开启':alarm.mode==='once'&&alarm.last_status?statuses[alarm.last_status]||'已关闭':'已暂停';status.title=alarm.last_run?.error||(alarm.last_run?.route==='main'?'通过主后台发送':alarm.last_run?.route==='isolated'?'通过独立 Home 发送':'');
  const toggle=article.querySelector('.toggle');toggle.setAttribute('aria-checked',String(alarm.enabled));toggle.setAttribute('aria-label',`${alarm.enabled?'暂停':'开启'} ${alarm.time} 发送`);toggle.disabled=loadingMutation;
  toggle.onclick=event=>{event.stopPropagation();mutate(()=>request(`/api/alarms/${alarm.id}`,'PATCH',{enabled:!alarm.enabled}),`${alarm.time} ${alarm.enabled?'已暂停':'已开启'}`,null,data=>({...data,alarms:data.alarms.map(a=>a.id===alarm.id?{...a,enabled:!alarm.enabled}:a)}));};
  const edit=article.querySelector('.edit-button');edit.setAttribute('aria-label',`修改 ${alarm.time} 发送`);edit.disabled=loadingMutation;edit.onclick=event=>{event.stopPropagation();openDialog(alarm);};
  const del=article.querySelector('.delete-button');del.setAttribute('aria-label',`删除 ${alarm.time} 发送`);del.disabled=loadingMutation;
  del.onclick=event=>{event.stopPropagation();mutate(()=>request(`/api/alarms/${alarm.id}`,'DELETE'),`已删除 ${alarm.time}`,()=>mutate(()=>request('/api/alarms','POST',{time:alarm.time,enabled:alarm.enabled,mode:alarm.mode,...deliveryPayload(alarm)}),`已恢复 ${alarm.time}`));};
  const advance=article.querySelector('.advance-button');advance.hidden=alarm.target_type==='thread';advance.disabled=loadingMutation;
  advance.setAttribute('aria-label',`在 ${alarm.time} 基础上加5小时01分，新建定时发送`);
  advance.title='新建时间，沿用此账号与重复方式';
  advance.onclick=event=>{event.stopPropagation();let createdId;
   mutate(async()=>{const data=await request(`/api/alarms/${alarm.id}/advance`,'POST');createdId=data.created_alarm_id;return data;},'已添加 ＋5小时01分 的时间',()=>mutate(()=>request(`/api/alarms/${createdId}`,'DELETE'),'已撤销新增时间'));
  };
  article.onclick=event=>{if(!event.target.closest('button')&&!loadingMutation)openDialog(alarm);};
  article.onkeydown=event=>{if(event.target===article&&['Enter',' '].includes(event.key)){event.preventDefault();if(!loadingMutation)openDialog(alarm);}};
  if(grid.children[index]!==article)grid.insertBefore(article,grid.children[index]||null);
 });cards.forEach(card=>card.remove());updateClock();
}
function updateClock() {
 const now=currentTime();$('#server-time').textContent=`台北时间 ${fmtClock.format(now)}`;if(!snapshot)return;
 const candidates=snapshot.alarms.filter(a=>a.enabled).map(a=>({...a,target:a.mode==='once'?new Date(a.run_at):dailyTarget(a.time,now)})).filter(a=>a.target.getTime()>=now.getTime()-60000).sort((a,b)=>a.target-b.target),upcoming=candidates[0],nextIds=new Set(candidates.filter(a=>a.target.getTime()===upcoming?.target.getTime()).map(a=>a.id));
 $('#next-time').textContent=upcoming?upcoming.time:'—:—';$('#next-day').textContent=upcoming?`${dayLabel(upcoming.target,now)} · ${upcoming.time}${upcoming.mode==='once'?' · 仅一次':''}`:'所有时间已暂停';if(!snapshot.alarms.length)$('#next-day').textContent='添加时间后，在这里见';
 document.querySelectorAll('.alarm-card').forEach(card=>{const next=nextIds.has(card.dataset.id);card.querySelector('.next-tag').hidden=!next;card.classList.toggle('is-next',next);if(next)card.setAttribute('aria-current','true');else card.removeAttribute('aria-current');});
 const [hour,minute]=(upcoming?.time||'10:10').split(':').map(Number);$('#hour-hand').setAttribute('transform',`rotate(${(hour%12)*30+minute/2} 60 60)`);$('#minute-hand').setAttribute('transform',`rotate(${minute*6} 60 60)`);
}
async function mutate(work,message,undo=null,optimistic=null) {
 if(loadingMutation)return;const previous=snapshot;loadingMutation=true;if(optimistic)snapshot=optimistic(snapshot);render();try{accept(await work());toast(message,undo);}catch(error){snapshot=previous;toast(error.message);}finally{loadingMutation=false;if(snapshot)render();}
}
function openDialog(alarm=null) {
 editingAlarm=alarm;$('#form-error').textContent='';$('#dialog-title').textContent=alarm?'修改时间':'添加一个时间';$('#save-button').textContent=alarm?'保存修改':'添加时间';
 accountChoice=alarm?.account_id||'';renderAccountChoices();loadAccountChoices();
 const time=alarm?.time||fmtTime.format(new Date(currentTime().getTime()+1800000));const [h,m]=time.split(':');setWheel('hour',Number(h),false);setWheel('minute',Number(m),false);
 selectedThread=alarm?.target_type==='thread'?{id:alarm.thread_id,name:alarm.thread_name||'指定会话'}:null;$('#alarm-prompt').value=alarm?.prompt||'';
 document.querySelector(`input[name="delivery"][value="${alarm?.target_type==='thread'?'thread':'default'}"]`).checked=true;updateDelivery();
 document.querySelector(`input[name="repeat"][value="${alarm?.mode||'daily'}"]`).checked=true;note();dialog.showModal();$('#save-button').focus({preventScroll:true});
}
$('#add-button').addEventListener('click',()=>openDialog());$('#empty-add').addEventListener('click',()=>openDialog());$('#close-dialog').addEventListener('click',()=>dialog.close());$('#cancel-dialog').addEventListener('click',()=>dialog.close());
window.addEventListener('keydown',event=>{
 if(!dialog.open||threadDialog.open||event.key!=='Enter'||event.isComposing||event.keyCode===229)return;
 if(event.target.tagName==='TEXTAREA'&&!event.ctrlKey&&!event.metaKey)return;
 event.preventDefault();event.stopPropagation();
 if(!event.repeat&&!loadingMutation)$('#save-button').click();
},true);
dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
$('#add-form').addEventListener('submit',async event=>{
 event.preventDefault();if(loadingMutation)return;$('#form-error').textContent='';loadingMutation=true;$('#save-button').disabled=true;
 let payload;
 try{
  payload={time:selectedTime(),mode:selectedMode(),enabled:editingAlarm?editingAlarm.enabled:true,target_type:selectedDelivery()};
  if(payload.target_type==='default')payload.account_id=accountChoice;
  if(payload.target_type==='thread'){if(!selectedThread)throw new Error('请选择一个 Codex 会话');payload.thread_id=selectedThread.id;payload.prompt=$('#alarm-prompt').value;if(!payload.prompt.trim())throw new Error('请填写要发送的提示词');}
  accept(await request(editingAlarm?`/api/alarms/${editingAlarm.id}`:'/api/alarms',editingAlarm?'PATCH':'POST',payload));dialog.close();toast(`${payload.time} ${editingAlarm?'已修改':'已添加'}`);
 }catch(error){$('#form-error').textContent=error.message;}finally{loadingMutation=false;$('#save-button').disabled=false;if(snapshot)render();}
});
$('#undo-button').addEventListener('click',async()=>{const action=undoAction;$('#toast').hidden=true;undoAction=null;if(action)await action();});
async function refresh(){if(loadingMutation||dialog.open)return;try{accept(await request('/api/alarms'));}catch(error){$('#connection').classList.add('offline');$('#connection span').textContent='连接中断';$('#error-banner').textContent=error.message;$('#error-banner').hidden=false;}}
setInterval(updateClock,1000);setInterval(refresh,15000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});refresh();
