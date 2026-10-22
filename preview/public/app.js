(() => {
  const root = document.getElementById('okeeper-demo');
  const main = root.querySelector('#ok-main');
  const initialTask = (remote = false) => ({id:remote ? 2 : 1, time:'08:30', enabled:true, catchup:true});
  const stores = {local:{tasks:[initialTask()],records:[{time:'昨日',name:'早间轻量消息',status:'已完成',detail:'示例回执 · 昨日 08:30 收到完成结果。',location:'这台电脑'}]},remote:{tasks:[initialTask(true)],records:[]}};
  let state = {page:'home',location:'local',scenario:'normal',connected:false,editing:null,clock:'08:00'};
  let running = false, notice = '', runVersion = 0, nextId = 3;
  const data = () => stores[state.location];
  const locationName = () => state.location==='local' ? (setup.demo?'这台电脑':setup.device) : '我的云端（演示）';
  const esc = value => String(value).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const iconPaths = {"plus":"M10 4v12M4 10h12","clock":"M10 6v4.2l2.8 1.6","history":"M3 10a7 7 0 1 0 2.1-5L3 7M3 3v4h4m3-1v4l3 2","chevron":"m8 5 5 5-5 5","edit":"m12.5 3.5 4 4M3.5 16.5l1-4L13 4a2.1 2.1 0 0 1 3 3l-8.5 8.5-4 1Z","monitor":"M3 3.5h14v10H3zM10 13.5V17M6.5 17h7","cloud":"M5 15.5a3.5 3.5 0 0 1-.3-7A5.5 5.5 0 0 1 15.3 7a4.3 4.3 0 0 1 .2 8.5Z"};
  iconPaths.terminal="M3 5l4 5-4 5M10 15h7";
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
    const task=activeTasks()[0], bound=currentAccount(), blocked=state.scenario==='login'||state.scenario==='uncertain'||(state.scenario==='sleep'&&state.location==='local');
    const completed=data().records.filter(r=>['已完成','已补发'].includes(r.status)).length;
    const clock=task?.time||'00:00';
    const [hour,minute]=clock.split(':').map(Number);
    let html=`<section class="intro" aria-labelledby="title"><div class="intro-copy"><span class="eyebrow"><i class="eyebrow-pulse" aria-hidden="true"></i><span>OKEEPER</span></span><h1 id="title" aria-label="赛博早八."><span class="title-char" style="--i:0">赛</span><span class="title-char" style="--i:1">博</span><span class="title-char" style="--i:2">早</span><span class="title-char" style="--i:3">八</span><span class="title-dot">.</span></h1><p class="hero-slogan"><code class="slogan-codex">Codex</code><span class="slogan-text">，你这个年纪<em class="slogan-emphasis">怎么睡得着的？</em></span><span class="slogan-cursor" aria-hidden="true"></span></p></div><div class="intro-dashboard"><div class="tech-telemetry" aria-label="示例执行统计"><div class="telemetry-head"><span class="telemetry-title">累计触发统计 <span class="demo-caption">示例</span></span><div class="fixed-settings"><span>轻量消息</span><span class="setting-divider"></span><span class="message-badge">ok</span></div></div><div class="telemetry-grid"><div class="telemetry-item"><span class="telemetry-label">触发次数</span><div class="telemetry-value-row"><strong class="telemetry-number">${bound?'—':completed}</strong><span class="telemetry-unit">次</span></div></div><span class="telemetry-divider" aria-hidden="true"></span><div class="telemetry-item"><span class="telemetry-label">触发 Token</span><div class="telemetry-value-row"><strong class="telemetry-number">—</strong><span class="telemetry-unit">未调用模型</span></div></div></div></div><div class="next-panel"><div class="next-copy"><span class="next-label">下一次发送（演示）</span><div class="next-time">${task?esc(task.time):'—:—'}</div><span class="next-day">${task?'每天 · Asia/Taipei':'添加时间后，在这里见'}</span></div><div class="dial" aria-hidden="true"><div class="dial-dot d1"></div><div class="dial-dot d2"></div><div class="dial-dot d3"></div><div class="dial-dot d4"></div><svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="48" class="dial-ring"/><g transform="rotate(${hour%12*30+minute*.5} 60 60)"><path d="M60 60V37" class="hour-hand"/></g><g transform="rotate(${minute*6} 60 60)"><path d="M60 60V24" class="minute-hand"/></g><circle cx="60" cy="60" r="3.7" class="dial-center"/></svg></div></div></div></section>`;
    html+=`<section class="quota-panel" aria-labelledby="quota-title"><div class="quota-heading"><div class="quota-title-row"><h2 id="quota-title">账号额度与触发队列</h2><span class="quota-count">1</span><button type="button" class="request-history-button" data-page="history">${icon('history')}近期请求</button></div><div class="quota-tools"><span class="preview-updated">${bound?'额度尚未接入':state.scenario==='login'?'等待重新登录':'示例更新于 08:00'}</span>${btn('添加时间','add','ok-primary',running)}</div></div><div class="preview-runtime"><span class="preview-runtime-state"><i aria-hidden="true"></i>运行在${locationName()}</span><span>${state.location==='local'?'电脑睡眠或关机时无法按时执行':'本地电脑关机不影响云端任务'}</span><button class="runtime-change" type="button" data-page="location">更改运行位置${icon('chevron')}</button></div>${banner()}<section class="quota-row is-active-account" aria-label="账号与演示触发队列"><div class="account-lane-top"><div class="quota-person"><span class="account-avatar" aria-hidden="true">${esc(bound?.email[0].toUpperCase()||'P')}</span><span class="quota-email">${esc(currentAccount()?.email||'personal@example.com')}</span><span class="quota-current">${currentAccount()?'已绑定':'个人账号'}</span><span class="quota-route-badge">${locationName()}</span></div></div><div class="quota-windows">`;
    for(const [label,percent,extra] of [['短期剩余',72,'12:30 重置'],['本周剩余',84,'10 月 26 日重置']]) html+=`<div class="quota-window"><div class="quota-window-heading"><span>${label}</span><strong class="quota-amount">${bound||state.scenario==='login'?'—':percent+'%'}</strong><span class="quota-reset">${bound?'尚未读取额度':state.scenario==='login'?'当前不可用':'示例 · '+extra}</span></div><div class="ok-meter" role="meter" aria-label="${label}（示例）" aria-valuemin="0" aria-valuemax="100" ${bound||state.scenario==='login'?'aria-valuetext="当前不可用"':`aria-valuenow="${percent}"`}><span style="width:${bound||state.scenario==='login'?0:percent}%"></span></div></div>`;
    html+=`</div><div class="account-queue"><div class="account-queue-head"><div class="account-queue-title">${icon('clock')}<span>触发队列</span><span class="queue-sub-count">${data().tasks.length} 个时点</span></div><div class="queue-actions">${btn(running?'模拟执行中…':'试运行（演示）','run','ok-secondary',running||blocked||!task)}</div></div><div class="alarm-grid account-queue-grid">`;
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
  const setup={loading:true,error:'',environment:null,accounts:[],selected:null,device:'运行设备',view:'welcome',demo:false,busy:false,login:{status:'idle'},removeId:null};
  let loginPoll, setupRequest=0;
  const currentAccount=()=>setup.demo?null:setup.accounts.find(a=>a.id===setup.selected);
  const waitingLogin=()=>['starting','waiting'].includes(setup.login.status);
  async function api(path,method='GET',data) {
    const response=await fetch(path,{method,cache:'no-store',headers:method==='GET'?{}:{'Content-Type':'application/json','X-Okeeper-Request':'1'},body:method==='GET'?undefined:JSON.stringify(data||{})});
    const payload=await response.json();
    if(!response.ok)throw new Error(payload.error||'请求没有完成，请重试。');
    return payload;
  }
  function scheduleLoginPoll() {
    clearTimeout(loginPoll);
    if(waitingLogin())loginPoll=setTimeout(async()=>{
      try {
        const login=await api('/api/login');setup.login=login;
        if(login.status==='completed'){
          await refreshSetup();setup.demo=false;setup.view='workspace';state.page='accounts';notify('账号已添加，可在账号管理中查看。');
        }
        render();scheduleLoginPoll();
      } catch(error){setup.error='登录状态暂时无法读取。请点“重新检测”恢复。';render();}
    },1500);
  }
  async function refreshSetup() {
    const request=++setupRequest;
    try {
      const value=await api('/api/bootstrap');
      if(request!==setupRequest)return;
      Object.assign(setup,{environment:value.environment,accounts:value.accounts,selected:value.selected,device:value.device,login:value.login,loading:false,error:''});
      scheduleLoginPoll();
    } catch(error){if(request===setupRequest){setup.loading=false;setup.error='无法连接 Okeeper 服务，请确认服务已启动，再重新检测。';}}
  }
  async function setupAction(action,id) {
    if(setup.busy)return;
    setup.busy=true;setup.error='';render();
    try {
      if(action==='setup-refresh'){await refreshSetup();}
      if(action==='bind-detected'){
        const result=await api('/api/accounts/bind','POST',{candidate_id:setup.environment.candidate.id});
        Object.assign(setup,{accounts:result.accounts,selected:result.selected,demo:false,view:'workspace'});state.page='accounts';
        notify(result.already_bound?'已更新此账号的登录记录，没有重复添加。':'账号已绑定。你可以继续添加账号，或进入工作台。');
      }
      if(action==='login-start'){setup.login=await api('/api/login','POST');scheduleLoginPoll();}
      if(action==='login-cancel'){setup.login=await api('/api/login','DELETE');clearTimeout(loginPoll);}
      if(action==='account-select'){
        const result=await api('/api/accounts/select','POST',{account_id:id});Object.assign(setup,result);setup.demo=false;notify('已更新工作台查看的账号。');
      }
      if(action==='account-remove-confirm'){
        const result=await api('/api/accounts/'+id,'DELETE');Object.assign(setup,result);setup.removeId=null;
        if(!setup.accounts.length){setup.view='welcome';setup.demo=false;}
        notify('已移除 Okeeper 绑定和保存的登录副本。');
      }
    } catch(error){setup.error=error.message;}
    finally{setup.busy=false;render();}
  }
  function loginPanel() {
    if(setup.login.status==='starting')return `<div class="setup-login" role="status"><span class="setup-spinner" aria-hidden="true"></span><div><h3>正在申请官方登录码</h3><p>登录会在独立环境中进行，请稍候。</p></div>${setupButton('取消','login-cancel')}</div>`;
    if(setup.login.status==='waiting')return `<section class="setup-login"><h3>在官方页面完成登录</h3><p>打开授权页面，输入下面的一次性登录码。完成后，这里会自动添加账号。</p><div class="setup-code">${esc(setup.login.code)}</div><div class="ok-row"><a class="ok-btn ok-primary" href="${esc(setup.login.url)}" target="_blank" rel="noopener noreferrer">打开官方授权页面</a>${setupButton('取消本次登录','login-cancel')}</div><p class="ok-subline">若官方页面提示未开启设备码登录，请先在 ChatGPT 安全设置中开启。</p></section>`;
    if(['failed','expired','cancelled'].includes(setup.login.status))return `<div class="ok-status-note" role="status">${esc(setup.login.error||'本次登录已取消，可以重新开始。')}</div>`;
    return '';
  }
  const setupButton=(label,action,kind='',id='')=>`<button type="button" class="ok-btn ${kind}" data-setup="${action}" ${id?`data-account-id="${esc(id)}"`:''} ${setup.busy||(action==='login-start'&&(!setup.environment?.codex_installed||waitingLogin()))?'disabled':''}>${label}</button>`;
  function setupErrors(){return setup.error?`<div class="ok-status-note" role="alert">${esc(setup.error)} ${setupButton('重新检测','setup-refresh')}</div>`:'';}
  function environmentRows() {
    const env=setup.environment;
    if(!env)return setup.loading?`<div class="setup-check"><span class="setup-spinner" aria-hidden="true"></span><div><h3>正在检测运行环境</h3><p>查看系统、Codex 安装情况和已有登录记录。</p></div></div>`:'<p>尚未完成检测。恢复服务后，请重新检测。</p>';
    return `<div class="setup-check"><span class="setup-check-icon">${icon('monitor')}</span><div><h3>${esc(env.system)} · ${esc(env.architecture)}</h3><p>检测对象：${esc(setup.device)}${setup.device==='当前服务器'?'。浏览器所在电脑不会被扫描。':''}</p></div><span class="ok-pill">已识别</span></div><div class="setup-check"><span class="setup-check-icon">${icon('terminal')}</span><div><h3>Codex ${env.codex_installed?'已安装':'尚未就绪'}</h3><p>${env.codex_installed?esc(env.codex_version):'请先在这台运行设备上安装 Codex，然后重新检测。'}</p></div><span class="ok-pill ${env.codex_installed?'':'warn'}">${env.codex_installed?'已就绪':'需要安装'}</span></div>`;
  }
  function detectedAccount() {
    const env=setup.environment, candidate=env?.candidate;
    if(!env)return '';
    const bound=candidate&&setup.accounts.some(a=>a.id===candidate.id);
    if(candidate)return `<section class="setup-detected"><div class="setup-check"><span class="account-avatar" aria-hidden="true">${esc(candidate.email[0].toUpperCase())}</span><div><h3>${esc(candidate.email)}</h3><p>检测到${esc(setup.device)}已有的 Codex 登录记录。</p></div><span class="ok-pill">${bound?'已绑定':'可绑定'}</span></div><p class="ok-subline">确认后将此账号添加到 Okeeper。检测到登录记录不代表已验证额度或模型调用。</p>${bound?'<button type="button" class="ok-btn ok-primary" data-page="accounts">查看已绑定账号</button>':setupButton(setup.accounts.length?'绑定检测到的账号':'绑定为第一个账号','bind-detected','ok-primary')}</section>`;
    return `<div class="setup-empty-login"><h3>还没有可绑定的登录记录</h3><p>${esc(env.login_note||'可以在下方登录你的 Codex 账号。')}</p></div>`;
  }
  function startScreen() {
    return `<section class="setup-welcome"><div class="setup-welcome-title"><img src="/icon.png" alt=""><div><h1>从连接第一个账号开始。</h1><p>先确认运行环境，再把 Codex 账号交给 Okeeper 管理。</p></div></div>${setupErrors()}<section class="setup-section"><div class="section-head"><h2>运行环境</h2>${setupButton(setup.busy?'检测中…':'重新检测','setup-refresh')}</div>${environmentRows()}</section><section class="setup-section"><div class="section-head"><h2>${setup.accounts.length?'添加 Codex 账号':'绑定第一个 Codex 账号'}</h2></div>${detectedAccount()}${loginPanel()}<div class="setup-add-row"><div><h3>登录另一个账号</h3><p>在官方页面完成授权，无需复制登录文件。</p></div>${setupButton('登录并添加账号','login-start')}</div>${!setup.environment?.codex_installed&&!setup.loading?'<a class="setup-doc-link" href="https://learn.chatgpt.com/docs/quickstart" target="_blank" rel="noopener noreferrer">查看 Codex 官方安装说明</a>':''}</section><div class="setup-welcome-footer">${setup.accounts.length?'<button type="button" class="ok-btn ok-primary" data-setup="enter-workspace">进入工作台</button>':'<button type="button" class="ok-btn ok-quiet" data-setup="explore-demo">先看看演示</button>'}<span>账号管理保存实际绑定；定时执行仍在开发中。</span></div></section>`;
  }
  function accountManager() {
    return `<section class="setup-manager"><div class="section-head"><div><h1>管理 Codex 账号</h1><p>账号保存在${esc(setup.device)}，可在这里添加和移除。</p></div><button type="button" class="ok-btn ok-primary" data-setup="enter-workspace">进入工作台</button></div>${setupErrors()}<div class="setup-account-list">${setup.accounts.length?setup.accounts.map(a=>`<article class="setup-account ${a.id===setup.selected?'is-selected':''}"><div class="setup-account-main"><span class="account-avatar" aria-hidden="true">${esc(a.email[0].toUpperCase())}</span><div><h3>${esc(a.email)}</h3><p>${a.source==='official_login'?'通过官方页面登录':'从设备已有登录记录绑定'}${a.plan?' · '+esc(a.plan):''}</p></div>${a.id===setup.selected?'<span class="quota-current">工作台账号</span>':''}</div><div class="setup-account-actions">${a.id===setup.selected?'':setupButton('在工作台查看','account-select','',a.id)}${setupButton('移除账号','account-remove','ok-quiet',a.id)}</div>${setup.removeId===a.id?`<div class="setup-remove"><p>从 Okeeper 移除 ${esc(a.email)}？</p><p>会删除这里的绑定和登录副本；原 Codex 登录及聊天记录保留。${setup.accounts.length===1?'这是最后一个账号，移除后会返回开始页。':''}</p><div class="ok-row">${setupButton('确认移除','account-remove-confirm','',a.id)}${setupButton('保留账号','account-remove-cancel')}</div></div>`:''}</article>`).join(''):'<div class="ok-empty">尚未绑定 Codex 账号，可使用下面检测到的账号，或登录一个新账号。</div>'}</div><section class="setup-section"><div class="section-head"><h2>添加账号</h2>${setupButton('重新检测','setup-refresh')}</div>${detectedAccount()}${loginPanel()}<div class="setup-add-row"><div><h3>添加其他 Codex 账号</h3><p>使用独立登录环境，已有账号保持原样。</p></div>${setupButton('登录并添加账号','login-start','ok-primary')}</div></section><p class="ok-subline">绑定后可管理登录记录；实际额度和定时执行将在后续接入。</p></section>`;
  }
  function render() {
    root.classList.toggle('setup-start',setup.view==='welcome'||setup.loading);
    root.querySelector('.preview-toolbar').hidden=setup.view==='welcome'||setup.loading;
    root.querySelector('.ok-lab').hidden=setup.view==='welcome'||setup.loading||state.page==='accounts';
    root.querySelector('.preview-label').textContent=setup.demo?'开源版预览 · 示例数据':'账号管理已接入 · 任务演示';
    root.querySelector('#ok-location-label').innerHTML=`<i aria-hidden="true"></i><span style="white-space:nowrap">${setup.demo?(state.location==='local'?'本机':'云端'):esc(setup.device)} · ${setup.demo?'示例连接':'账号设置'}</span>`;
    root.querySelectorAll('[data-page]').forEach(el=>{el.setAttribute('aria-current',setup.view==='workspace'&&state.editing===null&&el.dataset.page===state.page?'page':'false');});
    root.querySelector('#ok-scenario').value=state.scenario;
    root.querySelector('#ok-scenario').disabled=running;
    root.querySelector('#ok-demo-clock').textContent=`演示时间：10 月 22 日 ${state.clock}`;
    const welcome=setup.view==='welcome'||setup.loading;
    main.className=welcome||state.page==='accounts'?'':state.editing!==null?'preview-editor':state.page==='home'?'':'preview-subpage';
    main.innerHTML=welcome?startScreen():state.page==='accounts'?accountManager():state.editing!==null?editor():state.page==='home'?home():state.page==='history'?history():state.page==='connect'?connectPage():locationPage();
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
    const control=event.target.closest('[data-setup]');
    if(control&&!control.disabled){
      const action=control.dataset.setup,id=control.dataset.accountId;
      if(action==='explore-demo'){setup.demo=true;setup.view='workspace';state.page='home';render();return;}
      if(action==='enter-workspace'){setup.view='workspace';state.page=setup.accounts.length?'home':'accounts';setup.demo=false;render();return;}
      if(action==='account-remove'){setup.removeId=id;render();return;}
      if(action==='account-remove-cancel'){setup.removeId=null;render();return;}
      setupAction(action,id);return;
    }

    const saveButton=event.target.closest('[data-save-form]');
    if(saveButton){const form=saveButton.closest('form');if(form.reportValidity())saveForm(form);return;}
    const navigation=event.target.closest('[data-page]');
    if(navigation){setup.view='workspace';state.page=navigation.dataset.page;state.editing=null;render();return;}
    const edit=event.target.closest('[data-edit]');
    if(edit){state.editing=Number(edit.dataset.edit);render();return;}
    const button=event.target.closest('[data-action]');
    if(!button||button.disabled)return;
    switch(button.dataset.action){
      case 'onboarding':setup.view='welcome';refreshSetup().then(render);break;
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
      state.scenario=el.value;state.clock=el.value==='sleep'?(activeTasks()[0]?.time||'08:30'):'08:00';state.editing=null;state.page='home';
      if(el.value==='uncertain')addRecord('结果未确认','模拟：已取得提交记录，但完成结果尚未确认。应查询同一次请求，不自动重发。','定时轻量消息','08:00');
      notify('已切换演示场景。');render();
    }
  });
  function saveForm(form) {
    const fd=new FormData(form);
    if(form.id==='ok-connect-form'){state.connected=true;state.location='remote';state.page='home';state.scenario='normal';notify('已打开虚构云端设备：这里使用独立的示例任务与记录。');}
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
  refreshSetup().then(()=>{if(setup.accounts.length){setup.view='workspace';state.page='home';}render();});
})();
