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
  const iconPaths = {"plus":"M10 4v12M4 10h12","clock":"M10 6v4.2l2.8 1.6","history":"M3 10a7 7 0 1 0 2.1-5L3 7M3 3v4h4m3-1v4l3 2","chevron":"m8 5 5 5-5 5","edit":"m12.5 3.5 4 4M3.5 16.5l1-4L13 4a2.1 2.1 0 0 1 3 3l-8.5 8.5-4 1Z","monitor":"M3 3.5h14v10H3zM10 13.5V17M6.5 17h7","cloud":"M5 15.5a3.5 3.5 0 0 1-.3-7A5.5 5.5 0 0 1 15.3 7a4.3 4.3 0 0 1 .2 8.5Z"};
  const icon = name => `<svg viewBox="0 0 20 20" aria-hidden="true">${name==='clock'?'<circle cx="10" cy="10" r="7.2"/>':''}<path d="${iconPaths[name]}"/></svg>`;
  const btn = (label,action,kind='',disabled=false) => `<button type="button" class="ok-btn ${kind} cursor-interaction" data-action="${action}" ${disabled?'disabled':''}>${action==='add'?icon('plus'):''}${label}</button>`;
  const minutesOf = value => Number(value.slice(0,2))*60+Number(value.slice(3));
  const clockAt = minutes => String(Math.floor((minutes%1440)/60)).padStart(2,'0')+':'+String(minutes%60).padStart(2,'0');
  const activeTasks = () => data().tasks.filter(t=>t.enabled).sort((a,b)=>a.time.localeCompare(b.time));
  let noticeTimer;
  const notify = text => { notice = text; const live=root.querySelector('#ok-live');live.textContent=text;live.hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>{notice='';live.hidden=true;},3600); };
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
    const completed=data().records.filter(r=>['已完成','已补发'].includes(r.status)).length;
    const clock=task?.time||'00:00';
    const [hour,minute]=clock.split(':').map(Number);
    let html=`<section class="intro" aria-labelledby="title"><div class="intro-copy"><span class="eyebrow"><i class="eyebrow-pulse" aria-hidden="true"></i><span>OKEEPER</span></span><h1 id="title" aria-label="赛博早八."><span class="title-char" style="--i:0">赛</span><span class="title-char" style="--i:1">博</span><span class="title-char" style="--i:2">早</span><span class="title-char" style="--i:3">八</span><span class="title-dot">.</span></h1><p class="hero-slogan"><code class="slogan-codex">Codex</code><span class="slogan-text">，你这个年纪<em class="slogan-emphasis">怎么睡得着的？</em></span><span class="slogan-cursor" aria-hidden="true"></span></p></div><div class="intro-dashboard"><div class="tech-telemetry" aria-label="示例执行统计"><div class="telemetry-head"><span class="telemetry-title">累计触发统计 <span class="demo-caption">示例</span></span><div class="fixed-settings"><span>轻量消息</span><span class="setting-divider"></span><span class="message-badge">ok</span></div></div><div class="telemetry-grid"><div class="telemetry-item"><span class="telemetry-label">触发次数</span><div class="telemetry-value-row"><strong class="telemetry-number">${completed}</strong><span class="telemetry-unit">次</span></div></div><span class="telemetry-divider" aria-hidden="true"></span><div class="telemetry-item"><span class="telemetry-label">触发 Token</span><div class="telemetry-value-row"><strong class="telemetry-number">—</strong><span class="telemetry-unit">未调用模型</span></div></div></div></div><div class="next-panel"><div class="next-copy"><span class="next-label">下一次发送</span><div class="next-time">${task?esc(task.time):'—:—'}</div><span class="next-day">${task?'每天 · Asia/Taipei':'添加时间后，在这里见'}</span></div><div class="dial" aria-hidden="true"><div class="dial-dot d1"></div><div class="dial-dot d2"></div><div class="dial-dot d3"></div><div class="dial-dot d4"></div><svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="48" class="dial-ring"/><g transform="rotate(${hour%12*30+minute*.5} 60 60)"><path d="M60 60V37" class="hour-hand"/></g><g transform="rotate(${minute*6} 60 60)"><path d="M60 60V24" class="minute-hand"/></g><circle cx="60" cy="60" r="3.7" class="dial-center"/></svg></div></div></div></section>`;
    html+=`<section class="quota-panel" aria-labelledby="quota-title"><div class="quota-heading"><div class="quota-title-row"><h2 id="quota-title">账号额度与触发队列</h2><span class="quota-count">1</span><button type="button" class="request-history-button" data-page="history">${icon('history')}近期请求</button></div><div class="quota-tools"><span class="preview-updated">${state.scenario==='login'?'等待重新登录':'示例更新于 08:00'}</span>${btn('添加时间','add','ok-primary',running)}</div></div><div class="preview-runtime"><span class="preview-runtime-state"><i aria-hidden="true"></i>运行在${locationName()}</span><span>${state.location==='local'?'电脑睡眠或关机时无法按时执行':'本地电脑关机不影响云端任务'}</span><button class="runtime-change" type="button" data-page="location">更改运行位置${icon('chevron')}</button></div>${banner()}<section class="quota-row is-active-account" aria-label="示例账号额度与触发队列"><div class="account-lane-top"><div class="quota-person"><span class="account-avatar" aria-hidden="true">P</span><span class="quota-email">personal@example.com</span><span class="quota-current">个人账号</span><span class="quota-route-badge">${locationName()}</span></div></div><div class="quota-windows">`;
    for(const [label,percent,extra] of [['短期剩余',72,'12:30 重置'],['本周剩余',84,'10 月 26 日重置']]) html+=`<div class="quota-window"><div class="quota-window-heading"><span>${label}</span><strong class="quota-amount">${state.scenario==='login'?'—':percent+'%'}</strong><span class="quota-reset">${state.scenario==='login'?'当前不可用':'示例 · '+extra}</span></div><div class="ok-meter" role="meter" aria-label="${label}（示例）" aria-valuemin="0" aria-valuemax="100" ${state.scenario==='login'?'aria-valuetext="当前不可用"':`aria-valuenow="${percent}"`}><span style="width:${state.scenario==='login'?0:percent}%"></span></div></div>`;
    html+=`</div><div class="account-queue"><div class="account-queue-head"><div class="account-queue-title">${icon('clock')}<span>触发队列</span><span class="queue-sub-count">${data().tasks.length} 个时点</span></div><div class="queue-actions">${btn(running?'模拟执行中…':'试运行一次','run','ok-secondary',running||blocked||!task)}</div></div><div class="alarm-grid account-queue-grid">`;
    for(const t of data().tasks) html+=`<article class="alarm-card ok-task ${t.enabled?'':'paused'} ${task?.id===t.id?'is-next':''}">${task?.id===t.id?'<span class="preview-next-tag">下一次</span>':''}<div class="card-top"><span class="card-kicker">每天</span><button class="edit-button" type="button" data-edit="${t.id}" ${running?'disabled':''}>${icon('edit')}<span>修改</span></button></div><div class="card-body-row"><div class="alarm-time ok-task-time">${esc(t.time)}</div></div><div class="preview-catchup">${t.catchup?'错过后 2 小时内补发一次':'错过后跳过本次'}</div><div class="card-bottom"><span class="alarm-status">${t.enabled?'等待发送':'已暂停'}</span><label class="ok-switch"><input type="checkbox" role="switch" aria-label="${esc(t.time)} 的定时消息" data-toggle="${t.id}" ${t.enabled?'checked':''} ${running?'disabled':''}></label></div></article>`;
    html+=`</div>${data().tasks.length?'':'<div class="queue-empty">该账号暂无触发时点</div>'}</div></section><p class="preview-account-note">本演示不会调用模型。正式版试运行会使用一次模型调用；发送消息不代表额度已重置。</p></section>`;
    html+=`<section class="preview-device-section"><div class="section-head"><div><h2>运行位置<span class="count">${state.location==='local'?'本机':'云端'}</span></h2><p>先在自己的电脑上用起来，需要全天运行时再连接常开设备。</p></div><button type="button" class="add-button" data-page="location">${icon('monitor')}管理运行位置</button></div><div class="preview-device-row"><span class="preview-device-icon">${icon(state.location==='local'?'monitor':'cloud')}</span><div><h3>${locationName()}</h3><p>${state.location==='local'?'无需服务器 · 登录后即可设置定时消息':'独立登录与任务 · 关闭本地电脑不影响执行'}</p></div><span class="quota-current">示例连接</span></div></section>`;
    return html;
  }
  function history() {
    return `<h1>每次执行，都有记录。</h1><p class="ok-subline">${locationName()} · 这里显示本次演示产生的记录</p>${banner()}<div style="margin-top:22px">${data().records.length?data().records.map(r=>`<article class="ok-record"><span class="ok-record-time">${esc(r.time)}</span><div><h3>${esc(r.name)}</h3><small class="ok-muted">个人账号 · ${esc(r.location)}</small><details><summary>查看详情</summary><p>${esc(r.detail)}</p></details></div><span class="ok-pill ${['已完成','已补发'].includes(r.status)?'':'warn'}">${esc(r.status)}</span></article>`).join(''):'<div class="ok-empty">这里还没有运行记录。试运行后可以查看结果。</div>'}</div><div class="ok-footer">已提交表示消息已被接收；收到完成回执后才显示已完成。</div>`;
  }
  function locationPage() {
    return `<h1>选择任务运行的位置。</h1><p class="ok-subline">先在自己的电脑上用起来。需要全天运行时，再连接常开设备。</p><div class="ok-choice"><section class="ok-card"><div class="ok-row"><h2>这台电脑</h2><span class="ok-pill">${state.location==='local'?'当前':'本地'}</span></div><p class="ok-muted">使用这台电脑的账号和任务。无需服务器。</p><small class="ok-muted">睡眠或关机后，任务不能按时执行。</small>${btn('管理本机安排','local',state.location==='local'?'':'ok-primary')}</section><section class="ok-card"><div class="ok-row"><h2>自己的常开设备</h2><span class="ok-pill">自托管</span></div><p class="ok-muted">连接已安装 Okeeper 的服务器，让任务在远端执行。</p><small class="ok-muted">需要常开设备和独立登录；云资源可能产生费用。</small>${btn(state.connected?'管理云端安排':'连接演示设备',state.connected?'remote':'connect','ok-primary')}</section></div><section class="ok-card" style="margin-top:18px"><h3>连接之后，哪些会变化？</h3><p class="ok-subline">你会看到远端自己的账号、安排和记录。本机任务保留在这里，已有会话不会自动搬过去。</p><hr class="ok-divider"><h3>还没有服务器？</h3><p class="ok-subline">可以先用本地版。自托管需要自己准备设备；开源首版不包含公共托管服务。</p></section>`;
  }
  function connectPage() {
    return `<h1>连接自己的云端。</h1><p class="ok-subline">实际产品会使用常开设备生成的一次性配对信息。</p><form class="ok-form" id="ok-connect-form"><label class="ok-field">设备地址<input name="address" type="url" value="https://okeeper.example.com" required></label><label class="ok-field">配对码<input name="pair" value="DEMO-4821" required autocomplete="off"></label><p class="ok-status-note">这里只连接虚构的演示设备，请勿填写真实地址或密钥。接下来会展示远端已登录后的工作台。</p><div class="ok-row"><button class="ok-btn ok-primary cursor-interaction" type="button" data-save-form>连接演示设备</button>${btn('返回','location')}</div></form>`;
  }
  function editor() {
    const task=state.editing==='new'?{time:'13:30',catchup:true}:data().tasks.find(t=>t.id===state.editing);
    return `<h1>${state.editing==='new'?'添加一个时间。':'调整这次安排。'}</h1><p class="ok-subline">运行在${locationName()} · 个人账号 · 每日发送一次「ok」</p><form id="ok-task-form" class="ok-form"><div class="ok-form-row"><label class="ok-field">每天几点<input name="time" type="time" value="${esc(task.time)}" required></label><div class="ok-field"><span>任务时区</span><div class="ok-card" style="padding:9px 11px">Asia/Taipei · UTC+8</div></div></div><label class="ok-check"><input name="catchup" type="checkbox" ${task.catchup?'checked':''}><span>错过后 2 小时内补发一次<br><small class="ok-muted">只补发最近一次确认未发送的轻量消息。</small></span></label><p class="ok-subline">本原型固定演示时区。正式版默认使用系统时区，并可为每个任务调整。</p><p id="ok-form-error" class="ok-error" role="alert"></p><div class="ok-row"><div style="display:flex;gap:10px;flex-wrap:wrap"><button type="button" data-save-form class="ok-btn ok-primary cursor-interaction">保存安排</button>${btn('取消','cancel')}</div>${state.editing!=='new'?btn('删除这条安排','delete','ok-quiet'):''}</div></form>`;
  }
  function onboarding() {
    let html=`<div class="ok-welcome"><h1>你的第一条定时消息。</h1><p class="ok-subline">默认在这台电脑运行，无需准备服务器。</p><div class="ok-steps">${['连接账号','设置时间','试运行并开启'].map((x,i)=>`<span class="ok-step ${state.wizard===i+1?'active':''}"><b>${i+1}</b>${x}</span>`).join('')}</div>`;
    if(state.wizard===1) html+=`<section class="ok-card ok-stack"><span class="ok-pill">演示：已检测到 Codex</span><h2>连接你的 Codex 账号</h2><p class="ok-muted">通过官方登录页面完成授权，登录信息留在这台电脑上。</p><p class="ok-subline">本原型使用虚构账号，不会打开真实授权页面。</p>${btn('模拟登录完成','wizard-account','ok-primary')}<details><summary>如果电脑还没装 Codex？</summary><p class="ok-subline">正式版提供“安装所需组件”的引导，显示来源和安装进度；完成后再登录。</p></details></section>`;
    if(state.wizard===2) html+=`<form id="ok-wizard-form" class="ok-card ok-form"><h2>希望每天几点发送？</h2><label class="ok-field">发送时间<input name="time" type="time" value="${esc(state.wizardTime)}" required></label><p class="ok-subline">Asia/Taipei · 向个人账号发送「ok」</p><label class="ok-check"><input name="catchup" type="checkbox" ${state.wizardCatchup?'checked':''}><span>错过后 2 小时内补发一次<br><small class="ok-muted">睡眠期间不会执行；唤醒后按这个设置处理。</small></span></label><button type="button" data-save-form class="ok-btn ok-primary cursor-interaction">下一步</button></form>`;
    if(state.wizard===3) html+=`<section class="ok-card ok-stack"><h2>先试一次，确认能收到回复。</h2><p class="ok-muted">正式版会在这里确认账号、模型和一次真实完成回执，再开启定时。</p><p class="ok-subline">会使用一次模型调用；本演示不产生调用或费用。</p>${state.tested?'<div class="ok-receipt"><span class="ok-pill">演示已完成</span><div>收到回复：ok<br><small class="ok-muted">这只是示例回执。</small></div></div>':btn(state.trial==='running'?'正在模拟发送…':'试运行（演示）','wizard-test','ok-primary',state.trial==='running')}${btn(`开启每天 ${esc(state.wizardTime)} 的安排`,'wizard-finish',state.tested?'ok-primary':'',!state.tested)}</section>`;
    return html+`<div style="margin-top:16px">${btn('返回示例工作台','wizard-close','ok-quiet')}</div></div>`;
  }
  function render() {
    root.querySelector('#ok-location-label').innerHTML=`<i aria-hidden="true"></i><span style="white-space:nowrap">${state.location==='local'?'本机':'云端'} · ${state.scenario==='sleep'&&state.location==='local'?'模拟休眠':'示例连接'}</span>`;
    root.querySelectorAll('[data-page]').forEach(el=>{el.setAttribute('aria-current',!state.wizard&&state.editing===null&&el.dataset.page===state.page?'page':'false');});
    root.querySelector('#ok-scenario').value=state.scenario;
    root.querySelector('#ok-scenario').disabled=running||state.wizard>0;
    root.querySelector('#ok-demo-clock').textContent=`演示时间：10 月 22 日 ${state.clock}`;
    main.className=state.wizard?'':state.editing!==null?'preview-editor':state.page==='home'?'':'preview-subpage';
    main.innerHTML=state.wizard?onboarding():state.editing!==null?editor():state.page==='home'?home():state.page==='history'?history():state.page==='connect'?connectPage():locationPage();
    root.querySelector('#ok-live').textContent=notice;
    root.querySelector('#ok-live').hidden=!notice;
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
  const mascot=document.getElementById('cyber-mascot');
  const orb=document.getElementById('mascot-orb');
  if (matchMedia('(min-width:1320px)').matches) {mascot.classList.remove('is-minimized');orb.hidden=true;}
  document.getElementById('mascot-minimize').addEventListener('click',()=>{mascot.classList.add('is-minimized');orb.hidden=false;});
  orb.addEventListener('click',()=>{mascot.classList.remove('is-minimized');orb.hidden=true;});
  const stage=document.getElementById('mascot-stage');
  function pose(){mascot.dataset.ownerState=mascot.dataset.ownerState==='sleeping'?'phone':'sleeping';}
  stage.addEventListener('click',pose);
  stage.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();pose();}});
  render();
})();
