(() => {
  const root = document.getElementById('okeeper-demo');
  const main = root.querySelector('#ok-main');
  const initialTask = (remote = false) => ({id:remote ? 2 : 1, time:'08:30', enabled:true, catchup:true});
  const stores = {local:{tasks:[initialTask()],records:[{time:'昨日',name:'早间轻量消息',status:'已完成',detail:'示例回执 · 昨日 08:30 收到完成结果。',location:'这台电脑'}]},remote:{tasks:[initialTask(true)],records:[]}};
  let state = {page:'home',location:'local',scenario:'normal',connected:false,editing:null,wizard:0,tested:false,trial:'idle',wizardTime:'08:30',wizardCatchup:true,clock:'08:00'};
  let running = false, notice = '', runVersion = 0, nextId = 3;
  const data = () => stores[state.location];
  const locationName = () => state.location==='local' ? '这台电脑' : '我的云端';
  const esc = value => String(value).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const btn = (label,action,kind='',disabled=false) => `<button type="button" class="ok-btn ${kind} cursor-interaction" data-action="${action}" ${disabled?'disabled':''}>${label}</button>`;
  const minutesOf = value => Number(value.slice(0,2))*60+Number(value.slice(3));
  const clockAt = minutes => String(Math.floor((minutes%1440)/60)).padStart(2,'0')+':'+String(minutes%60).padStart(2,'0');
  const activeTasks = () => data().tasks.filter(t=>t.enabled).sort((a,b)=>a.time.localeCompare(b.time));
  const notify = text => { notice = text; root.querySelector('#ok-live').textContent = text; };
  function banner() {
    if (state.scenario==='login') return `<div class="ok-status-note">账号登录已失效，定时任务暂时停下了。${btn('重新登录（演示）','relogin','ok-quiet')}</div>`;
    if (state.scenario==='uncertain') return `<div class="ok-status-note">消息已提交，尚未确认结果。先查看这次记录，再决定下一步。${btn('查看这次结果（演示）','verify','ok-quiet')}</div>`;
    if (state.scenario==='sleep') return state.location==='local'
      ? `<div class="ok-status-note">${esc(state.clock)} 的消息尚未发送。电脑处于睡眠中。${btn(`模拟 ${clockAt(minutesOf(state.clock)+40)} 唤醒`,'wake','ok-quiet')}</div>`
      : `<div class="ok-status-note">本地电脑已睡眠。云端仍在线，会按自己的安排执行。${btn('演示云端到点执行','cloud-due','ok-quiet')}</div>`;
    return '';
  }
  function home() {
    const task=activeTasks()[0], blocked=state.scenario==='login'||state.scenario==='uncertain'||(state.scenario==='sleep'&&state.location==='local');
    const title = state.scenario==='sleep'&&state.location==='local' ? '先休息，醒来再接上。' : state.location==='remote' ? '合上电脑，也有安排。' : '时间安排好，少惦记一点。';
    let html=`<div class="ok-eyebrow">YOUR DAILY RHYTHM</div><h1>${title}</h1><p class="ok-subline">${state.location==='local'?'正在本机运行 · 电脑睡眠或关机时无法按时执行':'正在云端运行 · 本地电脑关机不影响云端任务'}</p>${banner()}`;
    html+=`<section class="ok-card ok-next" aria-label="下一次安排"><div><div class="ok-row"><span>${task?'下次轻量消息':'暂无待执行安排'}</span><span class="ok-pill">${state.scenario==='sleep'&&state.location==='local'?'等待电脑唤醒':task?'每日':'已暂停'}</span></div><div class="ok-time">${task?esc(task.time):'—'}</div><div class="ok-subline">${task?'向个人账号发送「ok」':'开启一个安排，Okeeper 会记住时间。'} · ${locationName()}</div></div>${btn(running?'正在模拟执行…':'试运行一次','run','ok-primary',running||blocked||!task)}</section>`;
    html+=`<p class="ok-subline">本演示不会调用模型。正式版试运行会使用一次模型调用。</p><div class="ok-section-head"><h2>账号额度</h2><small class="ok-muted">${state.scenario==='login'?'等待重新登录':'示例更新于 08:00'}</small></div><section class="ok-card ok-account" aria-label="示例账号额度"><div class="ok-account-head"><span class="ok-avatar">P</span><div><h3>个人账号</h3><small class="ok-muted">personal@example.com · ${locationName()}</small></div></div>`;
    for (const [label,percent,extra] of [['短期剩余',72,'服务显示 12:30 重置'],['本周剩余',84,'服务显示 10 月 26 日重置']]) html+=`<div><div class="ok-row"><span class="ok-muted">${label}</span><span class="ok-value">${state.scenario==='login'?'—':percent+'%'}</span></div><div class="ok-meter" role="meter" aria-label="${label}（示例）" aria-valuemin="0" aria-valuemax="100" ${state.scenario==='login'?'aria-valuetext="当前不可用"':`aria-valuenow="${percent}"`}><span style="width:${state.scenario==='login'?0:percent}%"></span></div><small class="ok-muted">${state.scenario==='login'?'当前不可用':extra}</small></div>`;
    html+=`</section><div class="ok-section-head"><h2>定时安排</h2>${btn('＋ 添加时间','add','ok-quiet',running)}</div>`;
    if (!data().tasks.length) html+='<div class="ok-empty">还没有安排。添加一个时间就可以开始。</div>';
    for (const t of data().tasks) html+=`<div class="ok-task"><div class="ok-task-time">${esc(t.time)}</div><div><h3>每日轻量消息</h3><div class="ok-subline">${t.catchup?'错过后 2 小时内补发一次':'错过后跳过本次'} · Asia/Taipei</div><button type="button" class="ok-btn ok-quiet cursor-interaction" data-edit="${t.id}" ${running?'disabled':''}>编辑安排</button></div><label class="ok-switch"><input type="checkbox" data-toggle="${t.id}" ${t.enabled?'checked':''} ${running?'disabled':''}><span>${t.enabled?'已开启':'已暂停'}</span></label></div>`;
    html+='<div class="ok-footer">额度和重置时间以服务返回为准。发送消息不代表额度已重置。</div>';
    return html;
  }
  function history() {
    return `<div class="ok-eyebrow">ACTIVITY</div><h1>每次执行，都有记录。</h1><p class="ok-subline">${locationName()} · 这里显示本次演示产生的记录</p>${banner()}<div style="margin-top:22px">${data().records.length?data().records.map(r=>`<article class="ok-record"><span class="ok-record-time">${esc(r.time)}</span><div><h3>${esc(r.name)}</h3><small class="ok-muted">个人账号 · ${esc(r.location)}</small><details><summary>查看详情</summary><p>${esc(r.detail)}</p></details></div><span class="ok-pill ${['已完成','已补发'].includes(r.status)?'':'warn'}">${esc(r.status)}</span></article>`).join(''):'<div class="ok-empty">这里还没有运行记录。试运行后可以查看结果。</div>'}</div><div class="ok-footer">已提交表示消息已被接收；收到完成回执后才显示已完成。</div>`;
  }
  function locationPage() {
    return `<div class="ok-eyebrow">WHERE IT RUNS</div><h1>选择任务运行的位置。</h1><p class="ok-subline">先在自己的电脑上用起来。需要全天运行时，再连接常开设备。</p><div class="ok-choice"><section class="ok-card"><div class="ok-row"><h2>这台电脑</h2><span class="ok-pill">${state.location==='local'?'当前':'本地'}</span></div><p class="ok-muted">使用这台电脑的账号和任务。无需服务器。</p><small class="ok-muted">睡眠或关机后，任务不能按时执行。</small>${btn('管理本机安排','local',state.location==='local'?'':'ok-primary')}</section><section class="ok-card"><div class="ok-row"><h2>自己的常开设备</h2><span class="ok-pill">自托管</span></div><p class="ok-muted">连接已安装 Okeeper 的服务器，让任务在远端执行。</p><small class="ok-muted">需要常开设备和独立登录；云资源可能产生费用。</small>${btn(state.connected?'管理云端安排':'连接演示设备',state.connected?'remote':'connect','ok-primary')}</section></div><section class="ok-card" style="margin-top:18px"><h3>连接之后，哪些会变化？</h3><p class="ok-subline">你会看到远端自己的账号、安排和记录。本机任务保留在这里，已有会话不会自动搬过去。</p><hr class="ok-divider"><h3>还没有服务器？</h3><p class="ok-subline">可以先用本地版。自托管需要自己准备设备；开源首版不包含公共托管服务。</p></section>`;
  }
  function connectPage() {
    return `<div class="ok-eyebrow">CONNECT A DEVICE</div><h1>连接自己的云端。</h1><p class="ok-subline">实际产品会使用常开设备生成的一次性配对信息。</p><form class="ok-form" id="ok-connect-form"><label class="ok-field">设备地址<input name="address" type="url" value="https://okeeper.example.com" required></label><label class="ok-field">配对码<input name="pair" value="DEMO-4821" required autocomplete="off"></label><p class="ok-status-note">这里只连接虚构的演示设备，请勿填写真实地址或密钥。接下来会展示远端已登录后的工作台。</p><div class="ok-row"><button class="ok-btn ok-primary cursor-interaction" type="button" data-save-form>连接演示设备</button>${btn('返回','location')}</div></form>`;
  }
  function editor() {
    const task=state.editing==='new'?{time:'13:30',catchup:true}:data().tasks.find(t=>t.id===state.editing);
    return `<div class="ok-eyebrow">SCHEDULE</div><h1>${state.editing==='new'?'添加一个时间。':'调整这次安排。'}</h1><p class="ok-subline">运行在${locationName()} · 个人账号 · 每日发送一次「ok」</p><form id="ok-task-form" class="ok-form"><div class="ok-form-row"><label class="ok-field">每天几点<input name="time" type="time" value="${esc(task.time)}" required></label><div class="ok-field"><span>任务时区</span><div class="ok-card" style="padding:9px 11px">Asia/Taipei · UTC+8</div></div></div><label class="ok-check"><input name="catchup" type="checkbox" ${task.catchup?'checked':''}><span>错过后 2 小时内补发一次<br><small class="ok-muted">只补发最近一次确认未发送的轻量消息。</small></span></label><p class="ok-subline">本原型固定演示时区。正式版默认使用系统时区，并可为每个任务调整。</p><p id="ok-form-error" class="ok-error" role="alert"></p><div class="ok-row"><div style="display:flex;gap:10px;flex-wrap:wrap"><button type="button" data-save-form class="ok-btn ok-primary cursor-interaction">保存安排</button>${btn('取消','cancel')}</div>${state.editing!=='new'?btn('删除这条安排','delete','ok-quiet'):''}</div></form>`;
  }
  function onboarding() {
    let html=`<div class="ok-welcome"><div class="ok-eyebrow">WELCOME TO OKEEPER</div><h1>你的第一条定时消息。</h1><p class="ok-subline">默认在这台电脑运行，无需准备服务器。</p><div class="ok-steps">${['连接账号','设置时间','试运行并开启'].map((x,i)=>`<span class="ok-step ${state.wizard===i+1?'active':''}"><b>${i+1}</b>${x}</span>`).join('')}</div>`;
    if(state.wizard===1) html+=`<section class="ok-card ok-stack"><span class="ok-pill">演示：已检测到 Codex</span><h2>连接你的 Codex 账号</h2><p class="ok-muted">通过官方登录页面完成授权，登录信息留在这台电脑上。</p><p class="ok-subline">本原型使用虚构账号，不会打开真实授权页面。</p>${btn('模拟登录完成','wizard-account','ok-primary')}<details><summary>如果电脑还没装 Codex？</summary><p class="ok-subline">正式版提供“安装所需组件”的引导，显示来源和安装进度；完成后再登录。</p></details></section>`;
    if(state.wizard===2) html+=`<form id="ok-wizard-form" class="ok-card ok-form"><h2>希望每天几点发送？</h2><label class="ok-field">发送时间<input name="time" type="time" value="${esc(state.wizardTime)}" required></label><p class="ok-subline">Asia/Taipei · 向个人账号发送「ok」</p><label class="ok-check"><input name="catchup" type="checkbox" ${state.wizardCatchup?'checked':''}><span>错过后 2 小时内补发一次<br><small class="ok-muted">睡眠期间不会执行；唤醒后按这个设置处理。</small></span></label><button type="button" data-save-form class="ok-btn ok-primary cursor-interaction">下一步</button></form>`;
    if(state.wizard===3) html+=`<section class="ok-card ok-stack"><h2>先试一次，确认能收到回复。</h2><p class="ok-muted">正式版会在这里确认账号、模型和一次真实完成回执，再开启定时。</p><p class="ok-subline">会使用一次模型调用；本演示不产生调用或费用。</p>${state.tested?'<div class="ok-receipt"><span class="ok-pill">演示已完成</span><div>收到回复：ok<br><small class="ok-muted">这只是示例回执。</small></div></div>':btn(state.trial==='running'?'正在模拟发送…':'试运行（演示）','wizard-test','ok-primary',state.trial==='running')}${btn(`开启每天 ${esc(state.wizardTime)} 的安排`,'wizard-finish',state.tested?'ok-primary':'',!state.tested)}</section>`;
    return html+`<div style="margin-top:16px">${btn('返回示例工作台','wizard-close','ok-quiet')}</div></div>`;
  }
  function render() {
    root.querySelector('#ok-location-label').innerHTML=`<span class="ok-dot"></span><span style="white-space:nowrap">${state.location==='local'?'本机':'云端'} · ${state.scenario==='sleep'&&state.location==='local'?'模拟休眠':'示例连接'}</span>`;
    root.querySelectorAll('[data-page]').forEach(el=>{el.setAttribute('aria-current',el.dataset.page===state.page?'page':'false');});
    root.querySelector('#ok-scenario').value=state.scenario;
    root.querySelector('#ok-scenario').disabled=running||state.wizard>0;
    root.querySelector('#ok-demo-clock').textContent=`演示时间：10 月 22 日 ${state.clock}`;
    main.innerHTML=state.wizard?onboarding():state.editing!==null?editor():state.page==='home'?home():state.page==='history'?history():state.page==='connect'?connectPage():locationPage();
    root.querySelector('#ok-live').textContent=notice;
  }
  function addRecord(status,detail,name='轻量消息',time='08:00',loc=state.location) {
    const record={time,name,status,detail,location:loc==='local'?'这台电脑':'我的云端'};
    stores[loc].records.unshift(record);return record;
  }
  function runDemo(name='手动试运行',status='已完成',time=state.clock) {
    if(running) return;
    running=true;const version=++runVersion;
    const record=addRecord('已提交','模拟：消息已提交，等待完成回执。',name,time);
    notify('演示：已提交，正在等待完成回执。');render();
    setTimeout(()=>{
      if(version!==runVersion)return;
      record.status=status;record.detail='模拟：收到同一次请求的完成回执。回复为 ok；额度重置情况未由本次回执证明。';
      running=false;notify('演示完成：收到回复 ok。可在运行记录中查看。');render();
    },900);
  }
  root.addEventListener('click',event=>{
    const saveButton=event.target.closest('[data-save-form]');
    if(saveButton){const form=saveButton.closest('form');if(form.reportValidity())saveForm(form);return;}
    const navigation=event.target.closest('[data-page]');
    if(navigation){state.page=navigation.dataset.page;state.editing=null;state.wizard=0;render();return;}
    const edit=event.target.closest('[data-edit]');
    if(edit){state.editing=Number(edit.dataset.edit);render();return;}
    const button=event.target.closest('[data-action]');
    if(!button||button.disabled)return;
    switch(button.dataset.action){
      case 'onboarding':state.wizard=1;state.location='local';state.scenario='normal';state.tested=false;state.trial='idle';state.clock='08:00';break;
      case 'wizard-account':state.wizard=2;break;
      case 'wizard-test':state.trial='running';render();setTimeout(()=>{state.tested=true;state.trial='done';render();notify('示例试运行完成，可以开启安排。');},850);return;
      case 'wizard-finish':stores.local.tasks=[{id:nextId++,time:state.wizardTime,catchup:state.wizardCatchup,enabled:true}];addRecord('已完成','首次设置中的模拟试运行已完成。','首次试运行');state.wizard=0;state.page='home';state.editing=null;notify('演示安排已开启，关闭此页面不会创建真实任务。');break;
      case 'wizard-close':state.wizard=0;state.page='home';state.editing=null;break;
      case 'run':runDemo();return;
      case 'add':state.editing='new';break;
      case 'cancel':state.editing=null;break;
      case 'delete':stores[state.location].tasks=data().tasks.filter(t=>t.id!==state.editing);state.editing=null;notify('已移除这条示例安排。');break;
      case 'location':state.page='location';break;
      case 'connect':state.page='connect';break;
      case 'local':state.location='local';state.page='home';state.scenario='normal';state.editing=null;break;
      case 'remote':state.location='remote';state.page='home';state.scenario='normal';state.editing=null;break;
      case 'relogin':state.scenario='normal';notify('演示账号已重新连接。');break;
      case 'verify':{const r=data().records.find(r=>r.status==='结果未确认');if(r){r.status='已完成';r.detail='模拟：查询同一请求后确认已经完成，没有再次发送消息。';}state.scenario='normal';notify('已查询原请求：演示结果为已完成，没有重发。');break;}
      case 'cloud-due':runDemo('云端定时消息','已完成',state.clock);return;
      case 'wake':{const wakeMinutes=minutesOf(state.clock)+40;state.clock=clockAt(wakeMinutes);state.scenario='normal';const t=activeTasks()[0];const minutes=t?minutesOf(t.time):0;const delay=wakeMinutes-minutes;if(t&&delay>=0&&delay<=120&&t.catchup){runDemo('唤醒后补发','已补发',state.clock);return;}addRecord('已错过','模拟：已恢复运行；按任务设置或补发时限跳过本次，不补发积压消息。','每日轻量消息',state.clock);notify('演示电脑已唤醒，本次按设置跳过。');break;}
    }
    render();
  });
  root.addEventListener('change',event=>{
    const el=event.target;
    if(el.matches('[data-toggle]')){data().tasks.find(t=>t.id===Number(el.dataset.toggle)).enabled=el.checked;render();return;}
    if(el.id==='ok-scenario'){
      state.scenario=el.value;state.clock=el.value==='sleep'?(activeTasks()[0]?.time||'08:30'):'08:00';state.editing=null;state.wizard=0;state.page='home';
      if(el.value==='uncertain')addRecord('结果未确认','模拟：已取得提交记录，但完成结果尚未确认。应查询同一次请求，不自动重发。','定时轻量消息','08:00');
      notify('已切换演示场景。');render();
    }
  });
  function saveForm(form) {
    const fd=new FormData(form);
    if(form.id==='ok-connect-form'){state.connected=true;state.location='remote';state.page='home';state.scenario='normal';notify('已打开虚构云端设备：这里使用独立的示例任务与记录。');}
    if(form.id==='ok-wizard-form'){state.wizardTime=String(fd.get('time'));state.wizardCatchup=fd.has('catchup');state.wizard=3;}
    if(form.id==='ok-task-form'){
      const time=String(fd.get('time'));
      if(data().tasks.some(t=>t.time===time&&t.id!==state.editing)){root.querySelector('#ok-form-error').textContent='这个时间已经有安排，请选择另一个时间。';return;}
      if(state.editing==='new')data().tasks.push({id:nextId++,time,catchup:fd.has('catchup'),enabled:true});
      else Object.assign(data().tasks.find(t=>t.id===state.editing),{time,catchup:fd.has('catchup')});
      state.editing=null;notify('示例安排已保存。');
    }
    render();
  }
  root.addEventListener('keydown',event=>{
    if(event.key==='Enter' && event.target.matches('input')) {const form=event.target.closest('form');if(form){event.preventDefault();if(form.reportValidity())saveForm(form);}}
  });
  render();
})();
