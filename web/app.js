'use strict';
const $ = selector => document.querySelector(selector);
let snapshot = null, clockOffset = 0, loadingMutation = false, toastTimer, undoAction, editingAlarm = null, renderedOnce = false;
const dialog = $('#add-dialog'), grid = $('#alarms'), customGrid = $('#custom-alarms');
const threadDialog = $('#thread-dialog');
let selectedThread = null, sessions = [], sessionLoading = false, sessionError = '', accountChoice = '', availableAccounts = [], sessionsLoadedOnce = false;
let availableModels = [], customModelChoice = '', customEffortChoice = '';
let lastStats = null, telemetryRaf = null, telemetryDeltaTimer = null, lastFastPoll = 0;

function animateTelemetry(fromCount, toCount, fromTokens, toTokens, deltaCount = 0, deltaTokens = 0) {
  const countEl = $('#stat-trigger-count'), tokenEl = $('#stat-trigger-tokens');
  const countDeltaEl = $('#stat-count-delta'), tokenDeltaEl = $('#stat-token-delta');
  const box = $('#tech-telemetry');
  if (!countEl || !tokenEl || !box) return;
  if (telemetryRaf) cancelAnimationFrame(telemetryRaf);
  clearTimeout(telemetryDeltaTimer);

  if (reducedMotion.matches) {
    countEl.textContent = toCount.toLocaleString('zh-CN');
    tokenEl.textContent = toTokens.toLocaleString('zh-CN');
    if (countDeltaEl) countDeltaEl.hidden = true;
    if (tokenDeltaEl) tokenDeltaEl.hidden = true;
    return;
  }

  if (deltaCount > 0 || deltaTokens > 0) {
    box.classList.add('telemetry-pulsing');
    if (typeof Motion !== 'undefined' && Motion.animateMini) {
      Motion.animateMini(box, { transform: ['scale(1)', 'scale(1.035)', 'scale(1)'] }, { duration: .55 });
    }
    if (countDeltaEl && deltaCount > 0) {
      countDeltaEl.textContent = `+${deltaCount}`;
      countDeltaEl.hidden = false;
      if (typeof Motion !== 'undefined' && Motion.animateMini) {
        Motion.animateMini(countDeltaEl, { opacity: [0, 1], transform: ['translateY(6px) scale(.85)', 'translateY(0px) scale(1)'] }, { type: Motion.spring, duration: .5, bounce: .25 });
      }
    }
    if (tokenDeltaEl && deltaTokens > 0) {
      tokenDeltaEl.textContent = `+${deltaTokens.toLocaleString('zh-CN')}`;
      tokenDeltaEl.hidden = false;
      if (typeof Motion !== 'undefined' && Motion.animateMini) {
        Motion.animateMini(tokenDeltaEl, { opacity: [0, 1], transform: ['translateY(6px) scale(.85)', 'translateY(0px) scale(1)'] }, { type: Motion.spring, duration: .5, bounce: .25 });
      }
    }
    telemetryDeltaTimer = setTimeout(() => {
      box.classList.remove('telemetry-pulsing');
      if (countDeltaEl) countDeltaEl.hidden = true;
      if (tokenDeltaEl) tokenDeltaEl.hidden = true;
    }, 3200);
    setMascotLine(`报告！已替睡梦中的主人累计打卡 +${deltaCount} 次 (ง •̀_•́)ง`, 3600);
  }

  const start = performance.now(), duration = (deltaCount > 0 || deltaTokens > 0) ? 1150 : 700;
  function step(nowTs) {
    const t = Math.min(1, (nowTs - start) / duration);
    const ease = 1 - Math.pow(1 - t, 4);
    const curCount = Math.round(fromCount + (toCount - fromCount) * ease);
    const curTokens = Math.round(fromTokens + (toTokens - fromTokens) * ease);
    countEl.textContent = curCount.toLocaleString('zh-CN');
    tokenEl.textContent = curTokens.toLocaleString('zh-CN');
    if (t < 1) {
      telemetryRaf = requestAnimationFrame(step);
    } else {
      telemetryRaf = null;
      countEl.textContent = toCount.toLocaleString('zh-CN');
      tokenEl.textContent = toTokens.toLocaleString('zh-CN');
    }
  }
  telemetryRaf = requestAnimationFrame(step);
}

function renderStats(stats) {
  if (!stats) return;
  const toCount = Number(stats.trigger_count || 0), toTokens = Number(stats.trigger_tokens || 0);
  const todayCount = Number(stats.today_count || 0), todayTokens = Number(stats.today_tokens || 0);
  if (!lastStats) {
    lastStats = { trigger_count: toCount, trigger_tokens: toTokens, today_count: todayCount, today_tokens: todayTokens };
    const startCount = Math.max(0, toCount - todayCount);
    const startTokens = Math.max(0, toTokens - todayTokens);
    const countEl = $('#stat-trigger-count'), tokenEl = $('#stat-trigger-tokens');
    if (countEl) countEl.textContent = startCount.toLocaleString('zh-CN');
    if (tokenEl) tokenEl.textContent = startTokens.toLocaleString('zh-CN');
    if (todayCount > 0 || todayTokens > 0) {
      setTimeout(() => {
        animateTelemetry(startCount, toCount, startTokens, toTokens, todayCount, todayTokens);
      }, 280);
    }
    return;
  }
  if (toCount !== lastStats.trigger_count || toTokens !== lastStats.trigger_tokens) {
    const dCount = Math.max(0, toCount - lastStats.trigger_count);
    const dTokens = Math.max(0, toTokens - lastStats.trigger_tokens);
    const prev = lastStats;
    lastStats = { trigger_count: toCount, trigger_tokens: toTokens, today_count: todayCount, today_tokens: todayTokens };
    animateTelemetry(prev.trigger_count, toCount, prev.trigger_tokens, toTokens, dCount, dTokens);
  } else {
    lastStats.today_count = todayCount;
    lastStats.today_tokens = todayTokens;
  }
}

$('#tech-telemetry')?.addEventListener('click', () => {
  if (!lastStats) return;
  const c = lastStats.trigger_count, tok = lastStats.trigger_tokens;
  const dCount = lastStats.today_count > 0 ? lastStats.today_count : (c > 0 ? 1 : 0);
  const dTokens = lastStats.today_tokens > 0 ? lastStats.today_tokens : (c > 0 ? Math.min(tok, 14824) : 0);
  if (!dCount && !dTokens) return;
  const startCount = Math.max(0, c - dCount), startTokens = Math.max(0, tok - dTokens);
  const countEl = $('#stat-trigger-count'), tokenEl = $('#stat-trigger-tokens');
  if (countEl) countEl.textContent = startCount.toLocaleString('zh-CN');
  if (tokenEl) tokenEl.textContent = startTokens.toLocaleString('zh-CN');
  animateTelemetry(startCount, c, startTokens, tok, dCount, dTokens);
});

function selectedDelivery() { return $('input[name="delivery"]:checked').value; }
function deliveryPayload(alarm) {
  return alarm.target_type === 'thread'
    ? {
        target_type: 'thread',
        thread_id: alarm.thread_id,
        prompt: alarm.prompt,
        custom_model: alarm.custom_model || '',
        custom_effort: alarm.custom_effort || ''
      }
    : { target_type: 'default', account_id: alarm.account_id || '' };
}
function formatModelEffort(model, effort) {
  if (!model && !effort) return '';
  const m = (model || '').replace(/^gpt-/i, '');
  const e = effort ? effort.charAt(0).toUpperCase() + effort.slice(1) : '';
  return [m, e].filter(Boolean).join(' · ');
}
function effortLabel(effort) {
  const names = {
    low: 'Low (轻量)',
    medium: 'Medium (均衡)',
    high: 'High (深入)',
    xhigh: 'XHigh (极高)',
    max: 'Max (最大)',
    ultra: 'Ultra (旗舰)'
  };
  return names[effort] || effort;
}
function renderModelChoices() {
  const mSelect = $('#custom-model'), eSelect = $('#custom-effort');
  if (!mSelect || !eSelect) return;
  mSelect.replaceChildren();
  const inheritOpt = document.createElement('option');
  inheritOpt.value = '';
  const inheritedSpec = selectedThread ? formatModelEffort(selectedThread.model, selectedThread.effort) : '';
  inheritOpt.textContent = inheritedSpec ? `沿用会话设置 (${inheritedSpec})` : '沿用会话当前设置';
  mSelect.append(inheritOpt);

  for (const m of availableModels) {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = m.name && m.name !== m.id ? `${m.name} (${m.id})` : m.id;
    mSelect.append(opt);
  }
  if (customModelChoice && !availableModels.some(m => m.id === customModelChoice)) {
    const fallback = document.createElement('option');
    fallback.value = customModelChoice;
    fallback.textContent = customModelChoice;
    mSelect.append(fallback);
  }
  mSelect.value = customModelChoice;

  eSelect.replaceChildren();
  if (!customModelChoice) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = selectedThread?.effort ? `沿用会话 (${selectedThread.effort})` : '沿用会话档位';
    eSelect.append(opt);
    eSelect.value = '';
    eSelect.disabled = true;
    customEffortChoice = '';
    return;
  }

  eSelect.disabled = false;
  const matched = availableModels.find(m => m.id === customModelChoice);
  const efforts = matched?.efforts?.length ? matched.efforts : ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'];
  for (const eff of efforts) {
    const opt = document.createElement('option');
    opt.value = eff;
    opt.textContent = effortLabel(eff);
    eSelect.append(opt);
  }
  if (!efforts.includes(customEffortChoice)) {
    customEffortChoice = matched?.default_effort || efforts[0] || 'medium';
  }
  eSelect.value = customEffortChoice;
}
async function loadModelChoices(force = false) {
  try {
    availableModels = (await request(`/api/models${force ? '?refresh=1' : ''}`)).models || [];
    renderModelChoices();
  } catch {
    renderModelChoices();
  }
}
$('#custom-model')?.addEventListener('change', event => {
  customModelChoice = event.target.value;
  const matched = availableModels.find(m => m.id === customModelChoice);
  if (customModelChoice && (!customEffortChoice || !(matched?.efforts || []).includes(customEffortChoice))) {
    customEffortChoice = matched?.default_effort || matched?.efforts?.[0] || 'medium';
  }
  renderModelChoices();
});
$('#custom-effort')?.addEventListener('change', event => {
  customEffortChoice = event.target.value;
});

function renderAccountChoices() {
  const select = $('#alarm-account'); select.replaceChildren();
  const follow = document.createElement('option'); follow.value = ''; follow.textContent = '跟随主账号（通用队列）'; select.append(follow);
  for (const account of availableAccounts) {
    const option = document.createElement('option'); option.value = account.id; option.textContent = account.name; select.append(option);
  }
  if (accountChoice && !availableAccounts.some(a => a.id === accountChoice)) {
    const missing = document.createElement('option'); missing.value = accountChoice;
    missing.textContent = editingAlarm?.account_name || '账号暂时不可用'; select.append(missing);
  }
  select.value = accountChoice;
}
async function loadAccountChoices() {
  try {
    availableAccounts = (await request('/api/accounts')).accounts;
    renderAccountChoices();
    if (snapshot) render();
  } catch (error) {
    $('#form-error').textContent = error.message;
  }
}
$('#alarm-account').onchange = event => { accountChoice = event.target.value; };
loadAccountChoices();

function updateDelivery() {
  const custom = selectedDelivery() === 'thread';
  $('#custom-delivery').hidden = !custom;
  $('#default-delivery-note').hidden = custom;
  $('#account-delivery').hidden = custom;
  const spec = selectedThread ? formatModelEffort(selectedThread.model, selectedThread.effort) : '';
  $('#chosen-name').textContent = selectedThread ? `${selectedThread.name}${spec ? ` (${spec})` : ''}` : '选择一个会话';
  $('#chosen-id').textContent = selectedThread ? `session id: ${selectedThread.id}` : '当前服务器全部会话';
  if (custom) renderModelChoices();
}
function renderSessions() {
  const list = $('#session-list'); list.replaceChildren();
  if (sessionLoading || sessionError) {
    const p = document.createElement('p'); p.className = 'session-empty';
    p.textContent = sessionLoading ? '正在读取会话…' : sessionError; list.append(p); return;
  }
  const query = $('#session-search').value.trim().toLocaleLowerCase();
  const filtered = sessions.filter(s => `${s.name} ${s.id} ${s.model || ''}`.toLocaleLowerCase().includes(query));
  if (!filtered.length) {
    const p = document.createElement('p'); p.className = 'session-empty'; p.textContent = '没有找到会话'; list.append(p);
  }
  filtered.forEach(session => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'session-option';
    button.dataset.id = session.id; button.disabled = !session.selectable;
    const name = document.createElement('strong');
    const titleSpan = document.createElement('span'); titleSpan.textContent = session.name;
    name.append(titleSpan);
    const spec = formatModelEffort(session.model, session.effort);
    if (spec) {
      const mBadge = document.createElement('span'); mBadge.className = 'session-model-badge';
      mBadge.textContent = spec; name.append(mBadge);
    }
    const id = document.createElement('small'); id.className = 'session-id'; id.textContent = `session id: ${session.id}`;
    button.append(name, id);
    if (session.archived || !session.selectable) {
      const badge = document.createElement('span'); badge.className = 'session-note';
      badge.textContent = session.reason || (session.archived ? '已归档' : ''); button.append(badge);
    }
    button.title = session.cwd || '';
    button.classList.toggle('selected', session.id === selectedThread?.id);
    button.onclick = () => {
      selectedThread = { id: session.id, name: session.name, model: session.model, effort: session.effort };
      updateDelivery(); threadDialog.close(); $('#choose-thread').focus({ preventScroll: true });
    };
    list.append(button);
  });
}
async function loadSessions(silent = false) {
  if (sessionLoading) return;
  sessionLoading = true; sessionError = '';
  if (!silent) renderSessions();
  try {
    sessions = (await request('/api/sessions')).sessions;
    sessionsLoadedOnce = true;
    if (selectedThread) {
      const found = sessions.find(s => s.id === selectedThread.id);
      if (found) { selectedThread.model = found.model; selectedThread.effort = found.effort; renderModelChoices(); }
    }
    if (snapshot) render();
  } catch (error) {
    sessionError = error.message;
  } finally {
    sessionLoading = false;
    if (!silent || threadDialog.open) renderSessions();
  }
}
$('#choose-thread').onclick = () => { threadDialog.showModal(); $('#session-search').value = ''; $('#session-search').focus(); loadSessions(); };
$('#close-threads').onclick = () => threadDialog.close();
$('#refresh-sessions').onclick = () => loadSessions();
$('#session-search').oninput = renderSessions;
threadDialog.addEventListener('click', event => {
  if (event.target === threadDialog) {
    const r = threadDialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) threadDialog.close();
  }
});
document.querySelectorAll('input[name="delivery"]').forEach(input => input.addEventListener('change', () => {
  if (selectedDelivery() === 'thread' && !availableModels.length) loadModelChoices();
  updateDelivery();
}));

const cardAnimations = new WeakMap(), reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
function updateCardScale(card, enabled) {
  const state = String(enabled), initial = card.dataset.enabled === undefined, changed = card.dataset.enabled !== state, target = enabled ? 1 : .94;
  card.dataset.enabled = state;
  if (initial || reducedMotion.matches) {
    cardAnimations.get(card)?.stop();
    card.style.transform = `scale(${target})`;
    return;
  }
  if (!changed) return;
  const from = getComputedStyle(card).transform;
  cardAnimations.get(card)?.stop();
  card.style.transform = from;
  cardAnimations.set(card, Motion.animateMini(card, { transform: [from, `scale(${target})`] }, { type: Motion.spring, duration: .5, bounce: .18 }));
}
function animateNewCard(card, enabled) {
  if (!renderedOnce || reducedMotion.matches || !card.isConnected) return;
  const target = enabled ? 1 : .94;
  card.classList.add('card-just-added');
  setTimeout(() => card.classList.remove('card-just-added'), 1150);
  cardAnimations.get(card)?.stop();
  cardAnimations.set(card, Motion.animateMini(
    card,
    { transform: [`translateY(12px) scale(${target * 0.92})`, `translateY(0px) scale(${target})`] },
    { type: Motion.spring, duration: .62, bounce: .24 }
  ));
}
reducedMotion.addEventListener('change', () => { if (snapshot) render(); });

const fmtClock = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Taipei', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
const fmtDay = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' });
const fmtTime = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Taipei', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const pad = n => String(n).padStart(2, '0');
const quotaDate = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Taipei', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

let quotaLoading = false;
let controlState = null, controlLoading = false, controlPoll = null, controlAction = null, quotaView = null;
const controlDialog = $('#control-dialog');

function switchButton(account) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'account-switch'; button.textContent = '切换并重启';
  const native = controlState?.accounts?.find(a => a.id === account.id), runtime = controlState?.runtime;
  const current = Boolean(runtime?.running && runtime.email?.toLocaleLowerCase() === account.account?.toLocaleLowerCase());
  button.hidden = current;
  button.disabled = !native?.switchable || Boolean(controlState?.busy) || !controlState;
  button.title = native?.switchable ? '切换主后台账号并重启' : '此账号尚无可用的原生登录副本';
  button.onclick = () => openControl('switch', account);
  return button;
}
function renderControl() {
  const operation = controlState?.operation, banner = $('#backend-operation');
  banner.hidden = !operation; banner.textContent = operation?.message || '';
  banner.classList.toggle('failed', operation?.status === 'failed');
  $('#restart-backend').disabled = !controlState || Boolean(controlState.busy);
  if (quotaView) renderQuota(quotaView);
  if (controlState?.busy && !controlPoll) controlPoll = setInterval(() => refreshControl(), 2000);
  if (!controlState?.busy && controlPoll) { clearInterval(controlPoll); controlPoll = null; refreshQuota(true); }
}
async function refreshControl() {
  if (controlLoading) return; controlLoading = true;
  try { controlState = await request('/api/account-control'); renderControl(); }
  catch (error) { $('#restart-backend').disabled = true; $('#restart-backend').title = error.message; }
  finally { controlLoading = false; }
}
function openControl(action, account = null) {
  controlAction = {
    action, account_id: account?.id || '', confirmed: true,
    request_id: crypto.randomUUID?.() || 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const n = crypto.getRandomValues(new Uint8Array(1))[0] & 15; return (c === 'x' ? n : (n & 3) | 8).toString(16);
    })
  };
  $('#control-title').textContent = action === 'switch' ? '切换账号并重启' : '重启 Codex 后台';
  $('#control-target').textContent = action === 'switch' ? account.account : (controlState?.runtime?.email || '当前主后台');
  $('#confirm-control').textContent = action === 'switch' ? '确认切换并重启' : '确认重启';
  $('#control-error').textContent = '';
  const count = controlState?.runtime?.active_tasks;
  $('#control-active').textContent = Number.isInteger(count) && count > 0 ? `当前检测到 ${count} 个任务正在执行。` : '';
  controlDialog.showModal(); $('#cancel-control').focus({ preventScroll: true });
}
$('#restart-backend').onclick = () => openControl('restart');
$('#close-control').onclick = $('#cancel-control').onclick = () => controlDialog.close();
$('#confirm-control').onclick = async () => {
  if (!controlAction) return; const button = $('#confirm-control'); button.disabled = true;
  try {
    const operation = await request('/api/account-control', 'POST', controlAction);
    controlDialog.close();
    controlState = { ...(controlState || {}), operation, busy: ['queued', 'stopping', 'backing_up', 'activating', 'starting', 'verifying', 'rolling_back'].includes(operation.status) };
    renderControl(); refreshControl();
  } catch (error) { $('#control-error').textContent = error.message; }
  finally { button.disabled = false; }
};
setInterval(() => { if (!document.hidden && !controlPoll) refreshControl(); }, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshControl(); });
refreshControl();

let watchState = null, watchSaving = false, watchVersion = 0;
function renderWatch() {
  const input = $('#auto-switch'); input.checked = Boolean(watchState?.enabled); input.disabled = watchSaving || !watchState;
  input.setAttribute('aria-checked', String(Boolean(watchState?.enabled)));
  $('#auto-switch-status').textContent = watchState?.enabled ? (watchState.message || '已开启 · 等待检查') : '已关闭';
  $('#auto-switch-status').title = watchState?.checked_at ? `上次检查：${quotaDate.format(new Date(watchState.checked_at))}` : '';
}
async function refreshWatch() {
  if (watchSaving || document.hidden) return;
  const version = watchVersion;
  try {
    const value = await request('/api/quota-watch');
    if (!watchSaving && version === watchVersion) { watchState = value; renderWatch(); }
  } catch (error) {
    if (!watchState) $('#auto-switch-status').textContent = '暂时无法读取设置';
  }
}
$('#auto-switch').onchange = async event => {
  if (watchSaving) return; const enabled = event.target.checked; watchSaving = true; watchVersion++; event.target.disabled = true;
  try { watchState = await request('/api/quota-watch', 'PATCH', { enabled }); toast(enabled ? '自动切换已开启' : '自动切换已关闭'); }
  catch (error) { toast(error.message); }
  finally { watchSaving = false; renderWatch(); }
};
setInterval(refreshWatch, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshWatch(); });
refreshWatch();

function quotaWindowLabel(seconds) {
  if (seconds === 18000) return '短期额度';
  if (seconds === 604800) return '每周额度';
  if (seconds === 2592000) return '每月额度';
  if (!seconds) return '订阅额度';
  return seconds >= 86400 ? `${Math.round(seconds / 86400)} 天额度` : '周期额度';
}

function effectiveAccounts() {
  const map = new Map();
  for (const a of quotaView?.accounts || []) {
    map.set(a.id, { ...a });
  }
  const hasBoundAlarms = (snapshot?.alarms || []).some(a => a.target_type !== 'thread' && a.account_id);
  if (!map.size && hasBoundAlarms) {
    for (const a of availableAccounts) {
      map.set(a.id, { id: a.id, account: a.name, current: a.current, windows: [] });
    }
  }
  for (const alarm of snapshot?.alarms || []) {
    if (alarm.target_type !== 'thread' && alarm.account_id && !map.has(alarm.account_id)) {
      const cat = availableAccounts.find(x => x.id === alarm.account_id);
      map.set(alarm.account_id, {
        id: alarm.account_id,
        account: cat?.name || alarm.account_name || '绑定账号',
        current: Boolean(cat?.current),
        windows: []
      });
    }
  }
  return [...map.values()];
}

function alignResetButton(account) {
  const w5h = (account.windows || []).find(w => w.window_seconds === 18000 && w.resets_at);
  if (!w5h) return null;
  const resetTime = fmtTime.format(new Date((w5h.resets_at + 60) * 1000));
  const existing = (snapshot?.alarms || []).find(a => a.target_type !== 'thread' && a.account_id === account.id && a.time === resetTime);
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'lane-action-btn align-reset-btn';
  if (existing && existing.enabled) {
    btn.textContent = `✓ 已对齐重置点 (${resetTime})`;
    btn.classList.add('is-aligned');
    btn.disabled = true;
    btn.title = `已设置 ${resetTime} 触发`;
    return btn;
  }
  btn.textContent = `⚡ 对齐重置点 (${resetTime})`;
  btn.title = `在重置时点后 (${resetTime}) 自动触发一次 ok`;
  btn.disabled = loadingMutation;
  btn.onclick = () => {
    if (existing) {
      mutate(
        () => request(`/api/alarms/${existing.id}`, 'PATCH', { enabled: true }),
        `已开启 ${resetTime} 重置点触发`,
        () => mutate(() => request(`/api/alarms/${existing.id}`, 'PATCH', { enabled: false }), `已暂停 ${resetTime}`)
      );
    } else {
      let createdId;
      mutate(
        async () => {
          const prevIds = new Set((snapshot?.alarms || []).map(a => a.id));
          const data = await request('/api/alarms', 'POST', { time: resetTime, mode: 'once', enabled: true, target_type: 'default', account_id: account.id });
          createdId = (data.alarms || []).find(a => !prevIds.has(a.id))?.id;
          return data;
        },
        `已对齐 ${account.account} 重置点 (${resetTime})`,
        () => createdId ? mutate(() => request(`/api/alarms/${createdId}`, 'DELETE'), `已撤销 ${resetTime} 触发`) : null
      );
    }
  };
  return btn;
}

function syncAccountLanes() {
  const container = $('#quota-accounts');
  const existingRows = new Map([...container.querySelectorAll('.quota-row')].map(r => [r.dataset.id, r]));
  const accounts = effectiveAccounts();
  $('#quota-count').textContent = accounts.length ? String(accounts.length) : '';

  container.querySelectorAll('.quota-empty').forEach(el => el.remove());

  accounts.forEach((account, index) => {
    let row = existingRows.get(account.id);
    if (!row) {
      row = document.createElement('div');
      row.className = 'quota-row';
      row.dataset.id = account.id;
      const top = document.createElement('div'); top.className = 'account-lane-top';
      const windows = document.createElement('div'); windows.className = 'quota-windows';
      const queue = document.createElement('div'); queue.className = 'account-queue';
      const qHead = document.createElement('div'); qHead.className = 'account-queue-head';
      const qGrid = document.createElement('div'); qGrid.className = 'alarm-grid account-queue-grid'; qGrid.dataset.accountId = account.id;
      const qEmpty = document.createElement('div'); qEmpty.className = 'queue-empty';
      qEmpty.innerHTML = '<span>该账号暂无触发时点</span>';
      queue.append(qHead, qGrid, qEmpty);
      row.append(top, windows, queue);
    } else {
      existingRows.delete(account.id);
    }

    const actual = controlState?.runtime;
    const active = actual?.running ? actual.email?.toLocaleLowerCase() === account.account?.toLocaleLowerCase() : account.current;
    row.classList.toggle('is-active-account', Boolean(active));
    row.classList.toggle('quota-stale', Boolean(account.stale || account.error));

    const top = row.querySelector('.account-lane-top');
    top.replaceChildren();
    const person = document.createElement('div'); person.className = 'quota-person';
    const avatar = document.createElement('span'); avatar.className = 'account-avatar'; avatar.setAttribute('aria-hidden', 'true');
    avatar.textContent = (account.account || 'A')[0];
    const name = document.createElement('span'); name.className = 'quota-email'; name.textContent = account.account; name.title = account.account;
    person.append(avatar, name);
    if (active) {
      const badge = document.createElement('span'); badge.className = 'quota-current'; badge.textContent = '当前主后台'; person.append(badge);
    }
    if (account.plan) {
      const plan = document.createElement('span'); plan.className = 'quota-plan'; plan.textContent = account.plan.replace(/_/g, ' '); person.append(plan);
    }
    const routeBadge = document.createElement('span'); routeBadge.className = 'quota-route-badge';
    routeBadge.textContent = active ? '主后台直连' : '独立 Home 队列';
    person.append(routeBadge);
    top.append(person, switchButton(account));

    const windows = row.querySelector('.quota-windows');
    windows.replaceChildren();
    for (const window of account.windows || []) {
      const item = document.createElement('div'); item.className = 'quota-window'; item.classList.toggle('quota-low', window.remaining_percent <= 20);
      const head = document.createElement('div'); head.className = 'quota-window-heading';
      const label = quotaWindowLabel(window.window_seconds), wName = document.createElement('span'); wName.textContent = label.replace('额度', '');
      const amount = document.createElement('strong'); amount.className = 'quota-amount';
      amount.textContent = `${window.remaining_percent.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}%`; amount.title = '剩余额度';
      const bar = document.createElement('progress'); bar.max = 100; bar.value = window.remaining_percent;
      bar.setAttribute('aria-label', `${account.account} ${label}剩余`);
      const reset = document.createElement('span'); reset.className = 'quota-reset';
      reset.textContent = window.resets_at ? `${quotaDate.format(new Date(window.resets_at * 1000))} 重置` : '未提供重置时间'; reset.title = '台北时间';
      head.append(wName, amount, reset); item.append(head, bar); windows.append(item);
    }
    if (account.error) {
      const error = document.createElement('span'); error.className = 'quota-error';
      error.textContent = account.stale ? '更新失败 · 上次数据' : account.error;
      error.title = account.updated_at ? `上次更新：${quotaDate.format(new Date(account.updated_at))}` : '';
      windows.append(error);
    }
    windows.hidden = !windows.children.length;

    const accountAlarms = (snapshot?.alarms || []).filter(a => a.target_type !== 'thread' && a.account_id === account.id);
    const qHead = row.querySelector('.account-queue-head');
    qHead.replaceChildren();
    const qTitle = document.createElement('div'); qTitle.className = 'account-queue-title';
    qTitle.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.2"/><path d="M10 6v4.2l2.8 1.6"/></svg><span>触发队列</span>`;
    const qCount = document.createElement('span'); qCount.className = 'queue-sub-count';
    qCount.textContent = `${accountAlarms.length} 个时点`;
    qTitle.append(qCount);

    const qActions = document.createElement('div'); qActions.className = 'queue-actions';
    const alignBtn = alignResetButton(account);
    if (alignBtn) qActions.append(alignBtn);
    const addBtn = document.createElement('button');
    addBtn.type = 'button'; addBtn.className = 'lane-action-btn add-account-alarm';
    addBtn.textContent = '＋ 添加时点';
    addBtn.disabled = loadingMutation;
    addBtn.onclick = () => openDialog(null, { target_type: 'default', account_id: account.id });
    qActions.append(addBtn);
    qHead.append(qTitle, qActions);

    if (container.children[index] !== row) container.insertBefore(row, container.children[index] || null);
  });

  existingRows.forEach(row => row.remove());

  if (!accounts.length && quotaView) {
    const empty = document.createElement('p'); empty.className = 'quota-empty';
    empty.textContent = quotaView.error || '暂未找到 Codex 账号';
    container.append(empty);
  }
}

function renderQuota(data) {
  quotaView = data;
  document.querySelector('.quota-panel').classList.remove('quota-stale');
  syncAccountLanes();
  if (snapshot) render();
  const status = $('#quota-status'), valid = (data.accounts || []).filter(a => a.updated_at && !a.error);
  const updated = valid.map(a => new Date(a.updated_at)).sort((a, b) => a - b)[0];
  status.textContent = data.error ? '部分不可用' : updated ? `${fmtTime.format(updated)} · 台北` : '台北时间';
  status.title = data.error || '剩余额度 · 每分钟自动刷新 · 重置时间均为台北时间';
}

async function refreshQuota(force = false) {
  if (quotaLoading || document.hidden) return; quotaLoading = true;
  const panel = document.querySelector('.quota-panel'), button = $('#refresh-quota');
  panel.setAttribute('aria-busy', 'true'); button.disabled = true;
  try { renderQuota(await request(`/api/quota${force ? '?refresh=1' : ''}`)); }
  catch (error) {
    $('#quota-status').textContent = $('#quota-accounts').children.length ? '更新失败 · 上次数据' : '暂时无法读取额度';
    panel.classList.add('quota-stale');
  }
  finally { quotaLoading = false; panel.setAttribute('aria-busy', 'false'); button.disabled = false; }
}
$('#refresh-quota').onclick = () => refreshQuota(true);
setInterval(() => refreshQuota(), 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshQuota(); });
refreshQuota();

const wheelLimits = { hour: 24, minute: 60 };
function wheelInput(unit) { return $(`#alarm-${unit}`); }
function wheelValue(unit) {
  const value = Number(wheelInput(unit).dataset.value || 0), raw = wheelInput(unit).value;
  return /^\d{1,2}$/.test(raw) && Number(raw) < wheelLimits[unit] ? Number(raw) : value;
}
function syncWheel(unit, value) {
  const input = wheelInput(unit), limit = wheelLimits[unit], column = $(`#wheel-${unit}`), number = ((value % limit) + limit) % limit;
  input.dataset.value = String(number); input.setAttribute('aria-valuenow', String(number));
  column.querySelector('.wheel-previous').textContent = pad((number + limit - 1) % limit);
  column.querySelector('.wheel-next').textContent = pad((number + 1) % limit);
}
function setWheel(unit, value, updateNote = true) {
  const number = ((value % wheelLimits[unit]) + wheelLimits[unit]) % wheelLimits[unit];
  wheelInput(unit).value = pad(number); wheelInput(unit).removeAttribute('aria-invalid'); syncWheel(unit, number);
  if (updateNote) note();
}
function selectedTime() {
  const values = ['hour', 'minute'].map(unit => {
    const raw = wheelInput(unit).value;
    if (!/^\d{1,2}$/.test(raw) || Number(raw) >= wheelLimits[unit]) throw new Error('请检查时间：小时为 00–23，分钟为 00–59。');
    return Number(raw);
  });
  return `${pad(values[0])}:${pad(values[1])}`;
}
function selectedMode() { return document.querySelector('input[name="repeat"]:checked').value; }
function currentTime() { return new Date(Date.now() + clockOffset); }
function dayString(date) { const p = Object.fromEntries(fmtDay.formatToParts(date).map(x => [x.type, x.value])); return `${p.year}-${p.month}-${p.day}`; }
function dailyTarget(time, now) { let date = new Date(`${dayString(now)}T${time}:00+08:00`); if (date <= now) date = new Date(date.getTime() + 86400000); return date; }
function dayLabel(date, now) { return fmtDay.format(date) === fmtDay.format(now) ? '今天' : '明天'; }
function calendarDayDiff(targetDate, now) {
  const d1 = new Date(`${dayString(now)}T00:00:00+08:00`);
  const d2 = new Date(`${dayString(targetDate)}T00:00:00+08:00`);
  return Math.round((d2 - d1) / 86400000);
}
function isRetiredOnce(alarm, now) {
  return !alarm.enabled && alarm.mode === 'once' && (!alarm.run_at || new Date(alarm.run_at).getTime() < now.getTime() - 60000);
}
function occurrenceTarget(alarm, now) {
  if (alarm.mode === 'once' && alarm.run_at) {
    const runDate = new Date(alarm.run_at);
    if (alarm.enabled || runDate.getTime() >= now.getTime() - 60000) return runDate;
  }
  if (alarm.next_at && alarm.enabled) return new Date(alarm.next_at);
  return dailyTarget(alarm.time, now);
}
function sortAlarmsByNext(alarms, now) {
  return [...alarms].sort((a, b) => {
    const ra = isRetiredOnce(a, now) ? 1 : 0;
    const rb = isRetiredOnce(b, now) ? 1 : 0;
    if (ra !== rb) return ra - rb;
    const diff = occurrenceTarget(a, now).getTime() - occurrenceTarget(b, now).getTime();
    return diff !== 0 ? diff : a.time.localeCompare(b.time);
  });
}
function note() {
  const now = currentTime(), time = `${pad(wheelValue('hour'))}:${pad(wheelValue('minute'))}`;
  $('#occurrence-note').textContent = selectedMode() === 'once' ? `${dayLabel(dailyTarget(time, now), now)} ${time} 发送一次，之后自动关闭。` : `每天 ${time} 发送。`;
}
for (const unit of ['hour', 'minute']) {
  const input = wheelInput(unit), column = $(`#wheel-${unit}`);
  setWheel(unit, unit === 'hour' ? 7 : 0, false);
  input.addEventListener('focus', () => input.select());
  input.addEventListener('input', () => {
    const clean = input.value.replace(/\D/g, '').slice(0, 2); if (input.value !== clean) input.value = clean;
    if (!clean) { input.setAttribute('aria-invalid', 'true'); return; }
    const value = Number(clean); if (value >= wheelLimits[unit]) { input.setAttribute('aria-invalid', 'true'); return; }
    input.removeAttribute('aria-invalid'); syncWheel(unit, value); note();
  });
  input.addEventListener('blur', () => {
    const raw = input.value;
    if (/^\d{1,2}$/.test(raw) && Number(raw) < wheelLimits[unit]) setWheel(unit, Number(raw));
    else input.setAttribute('aria-invalid', 'true');
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); setWheel(unit, wheelValue(unit) + (event.key === 'ArrowUp' ? 1 : -1)); }
    else if (event.key === 'PageUp' || event.key === 'PageDown') { event.preventDefault(); setWheel(unit, wheelValue(unit) + (event.key === 'PageUp' ? 5 : -5)); }
  });
  column.addEventListener('click', event => {
    if (suppressedWheelClick === column) { event.preventDefault(); event.stopImmediatePropagation(); suppressedWheelClick = null; return; }
    if (event.target !== input) input.focus();
  });
  column.addEventListener('wheel', event => {
    event.preventDefault(); const delta = event.deltaMode === 1 ? event.deltaY * 32 : event.deltaMode === 2 ? event.deltaY * 120 : event.deltaY;
    let steps = 0;
    if (Math.abs(delta) >= 50) { steps = Math.sign(delta) * Math.max(1, Math.round(Math.abs(delta) / 100)); wheelRemainder[unit] = 0; }
    else { wheelRemainder[unit] += delta; steps = Math.trunc(wheelRemainder[unit] / 45); wheelRemainder[unit] -= steps * 45; }
    if (steps) setWheel(unit, wheelValue(unit) + steps);
  }, { passive: false });
  column.addEventListener('pointerdown', event => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    pointerDrag = { unit, column, pointerId: event.pointerId, startY: event.clientY, lastY: event.clientY, travel: 0, moved: false };
  });
}
let wheelRemainder = { hour: 0, minute: 0 }, pointerDrag = null, suppressedWheelClick = null, suppressWheelClickTimer;
document.addEventListener('pointermove', event => {
  if (!pointerDrag || event.pointerId !== pointerDrag.pointerId) return;
  const drag = pointerDrag, delta = drag.lastY - event.clientY; drag.lastY = event.clientY; drag.travel += delta;
  const steps = Math.trunc(drag.travel / 36);
  if (steps) { setWheel(drag.unit, wheelValue(drag.unit) + steps); drag.travel -= steps * 36; }
  if (Math.abs(event.clientY - drag.startY) > 7) { drag.moved = true; if (document.activeElement === wheelInput(drag.unit)) wheelInput(drag.unit).blur(); if (event.cancelable) event.preventDefault(); }
}, { passive: false });
function finishWheelPointer(event) {
  if (!pointerDrag || event.pointerId !== pointerDrag.pointerId) return;
  if (pointerDrag.moved) { suppressedWheelClick = pointerDrag.column; clearTimeout(suppressWheelClickTimer); suppressWheelClickTimer = setTimeout(() => { suppressedWheelClick = null; }, 600); }
  pointerDrag = null;
}
document.addEventListener('pointerup', finishWheelPointer);
document.addEventListener('pointercancel', finishWheelPointer);
document.querySelectorAll('input[name="repeat"]').forEach(r => r.addEventListener('change', note));

function toast(text, undo = null) {
  clearTimeout(toastTimer); $('#toast-text').textContent = text; undoAction = undo;
  $('#undo-button').hidden = !undo; $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; undoAction = null; }, undo ? 8000 : 4000);
}
async function request(path, method = 'GET', payload = {}) {
  const options = { method, cache: 'no-store' };
  if (method !== 'GET') { options.headers = { 'Content-Type': 'application/json', 'X-Rhythm-Request': '1' }; options.body = JSON.stringify(payload); }
  let response; try { response = await fetch(path, options); } catch { throw new Error('暂时连接不上服务器，请稍后重试'); }
  const data = await response.json(); if (!response.ok) throw new Error(data.error || '操作失败，请重试'); return data;
}
function accept(data) {
  snapshot = data; clockOffset = new Date(data.now).getTime() - Date.now();
  $('#connection').classList.remove('offline'); $('#connection span').textContent = '已连接';
  $('#error-banner').hidden = true;
  if ((data.alarms || []).some(a => a.target_type === 'thread') && !sessionsLoadedOnce && !sessionLoading) {
    loadSessions(true);
  }
  render();
}

function buildAlarmCard(alarm, existingCard) {
  const article = existingCard || $('#alarm-template').content.querySelector('.alarm-card').cloneNode(true);
  const isCustom = alarm.target_type === 'thread';
  article.dataset.id = alarm.id;
  article.classList.toggle('paused', !alarm.enabled);
  article.classList.toggle('custom-card', isCustom);
  article.classList.toggle('account-card', Boolean(alarm.account_id));
  updateCardScale(article, alarm.enabled);

  article.querySelector('.alarm-time').textContent = alarm.time;
  article.querySelector('.card-kicker').textContent = alarm.mode === 'once' ? '仅一次' : '每天';
  const offsetEl = article.querySelector('.alarm-day-offset');
  if (offsetEl) {
    const now = currentTime();
    if (isRetiredOnce(alarm, now)) {
      offsetEl.hidden = true;
    } else {
      const dayDiff = calendarDayDiff(occurrenceTarget(alarm, now), now);
      offsetEl.hidden = dayDiff < 1;
      if (dayDiff >= 1) {
        offsetEl.textContent = `+${dayDiff}`;
        offsetEl.title = dayDiff === 1 ? '次日触发' : `${dayDiff} 天后触发`;
      }
    }
  }

  const customMeta = article.querySelector('.custom-meta');
  if (customMeta) {
    customMeta.hidden = !isCustom;
    if (isCustom) {
      customMeta.querySelector('.custom-session-title').textContent = alarm.thread_name || '指定会话';
      customMeta.querySelector('.custom-session-id').textContent = alarm.thread_id ? `#${alarm.thread_id.slice(0, 8)}` : '';
      const modelBadge = customMeta.querySelector('.custom-session-model');
      if (modelBadge) {
        const sessionMeta = sessions.find(s => s.id === alarm.thread_id);
        const forced = Boolean(alarm.custom_model);
        const rawModel = forced ? alarm.custom_model : (sessionMeta?.model || alarm.last_run?.model);
        const rawEffort = forced ? (alarm.custom_effort || sessionMeta?.effort || alarm.last_run?.effort) : (sessionMeta?.effort || alarm.last_run?.effort);
        const modelStr = formatModelEffort(rawModel, rawEffort);
        modelBadge.hidden = !modelStr;
        modelBadge.classList.toggle('is-forced', forced);
        modelBadge.textContent = forced ? `指定: ${modelStr}` : `沿用: ${modelStr}`;
        modelBadge.title = forced ? '强制使用此模型与推理档位投递' : '沿用目标会话当前的模型与推理档位';
      }
      const preview = customMeta.querySelector('.alarm-prompt-preview');
      preview.textContent = alarm.prompt || '';
    }
  }

  const target = article.querySelector('.alarm-target');
  target.hidden = !isCustom && !alarm.account_id;
  target.textContent = isCustom ? (alarm.thread_name || '指定会话') : (alarm.account_name || '指定账号');
  target.title = alarm.thread_id ? `session id: ${alarm.thread_id}` : '';
  target.classList.toggle('sr-only', isCustom);

  const statuses = { completed: '已完成', failed: '发送失败', missed: '已错过', pending: '发送中' };
  const result = alarm.last_run?.status;
  const customStatuses = {
    dispatching: '准备发送', checking: '准备发送', sending: '准备发送', accepted: '已启动', running: '执行中',
    waiting_input: '等待确认', observing_disconnected: '等待连接', completed: alarm.enabled ? '上次已完成' : '已完成',
    skipped_busy: '忙碌跳过', skipped_missed: '已错过', skipped_changed: '设置已变更',
    skipped_account_changed: '账号已变更', failed_or_uncertain: '执行失败'
  };
  const status = article.querySelector('.alarm-status');
  status.textContent = (isCustom || alarm.account_id) && result
    ? customStatuses[result] || '已记录'
    : alarm.enabled ? '已开启' : alarm.mode === 'once' && alarm.last_status ? statuses[alarm.last_status] || '已关闭' : '已暂停';
  status.title = alarm.last_run?.error || (alarm.last_run?.route === 'main' ? '通过主后台发送' : alarm.last_run?.route === 'isolated' ? '通过独立 Home 发送' : '');

  const toggle = article.querySelector('.toggle');
  toggle.setAttribute('aria-checked', String(alarm.enabled));
  toggle.setAttribute('aria-label', `${alarm.enabled ? '暂停' : '开启'} ${alarm.time} 发送`);
  toggle.disabled = loadingMutation;
  toggle.onclick = event => {
    event.stopPropagation();
    mutate(
      () => request(`/api/alarms/${alarm.id}`, 'PATCH', { enabled: !alarm.enabled }),
      `${alarm.time} ${alarm.enabled ? '已暂停' : '已开启'}`,
      null,
      data => ({ ...data, alarms: data.alarms.map(a => a.id === alarm.id ? { ...a, enabled: !alarm.enabled } : a) })
    );
  };

  const edit = article.querySelector('.edit-button');
  edit.setAttribute('aria-label', `修改 ${alarm.time} 发送`);
  edit.disabled = loadingMutation;
  edit.onclick = event => { event.stopPropagation(); openDialog(alarm); };

  const del = article.querySelector('.delete-button');
  del.setAttribute('aria-label', `删除 ${alarm.time} 发送`);
  del.disabled = loadingMutation;
  del.onclick = event => {
    event.stopPropagation();
    mutate(
      () => request(`/api/alarms/${alarm.id}`, 'DELETE'),
      `已删除 ${alarm.time}`,
      () => mutate(() => request('/api/alarms', 'POST', { time: alarm.time, enabled: alarm.enabled, mode: alarm.mode, ...deliveryPayload(alarm) }), `已恢复 ${alarm.time}`)
    );
  };

  const advance = article.querySelector('.advance-button');
  advance.hidden = isCustom;
  advance.disabled = loadingMutation;
  advance.setAttribute('aria-label', `基于 ${alarm.time} 顺延新增下一轮定时发送`);
  advance.title = '新增下一轮触发时间，沿用此账号与重复方式';
  advance.onclick = event => {
    event.stopPropagation();
    let createdId;
    mutate(
      async () => { const data = await request(`/api/alarms/${alarm.id}/advance`, 'POST'); createdId = data.created_alarm_id; return data; },
      '已顺延添加下一轮时间',
      () => mutate(() => request(`/api/alarms/${createdId}`, 'DELETE'), '已撤销新增时间')
    );
  };

  article.onclick = null;
  article.onkeydown = null;
  return article;
}

function syncContainerCards(container, alarms, cardPool, sortByUpcoming = true) {
  const shouldSortUpcoming = sortByUpcoming || alarms.some(a => a.mode === 'once');
  const sorted = shouldSortUpcoming ? sortAlarmsByNext(alarms, currentTime()) : alarms;
  sorted.forEach((alarm, index) => {
    const existing = cardPool.get(alarm.id);
    const isNewCard = !existing;
    cardPool.delete(alarm.id);
    const article = buildAlarmCard(alarm, existing);
    if (container.children[index] !== article) container.insertBefore(article, container.children[index] || null);
    if (isNewCard) animateNewCard(article, alarm.enabled);
  });
}

function render() {
  const cards = new Map([...document.querySelectorAll('.alarm-card')].map(card => [card.dataset.id, card]));
  document.querySelectorAll('.loading-card').forEach(card => card.remove());

  syncAccountLanes();

  const allAlarms = snapshot.alarms || [];
  const customAlarms = allAlarms.filter(a => a.target_type === 'thread');
  const followAlarms = allAlarms.filter(a => a.target_type !== 'thread' && !a.account_id);
  const accounts = effectiveAccounts();

  $('#count').textContent = allAlarms.length;
  $('#custom-count').textContent = customAlarms.length;
  if (snapshot.stats) renderStats(snapshot.stats);

  for (const account of accounts) {
    const laneRow = $(`#quota-accounts .quota-row[data-id="${account.id}"]`);
    if (!laneRow) continue;
    const qGrid = laneRow.querySelector('.account-queue-grid');
    const qEmpty = laneRow.querySelector('.queue-empty');
    const acctAlarms = allAlarms.filter(a => a.target_type !== 'thread' && a.account_id === account.id);
    syncContainerCards(qGrid, acctAlarms, cards);
    qGrid.hidden = !acctAlarms.length;
    if (qEmpty) qEmpty.hidden = acctAlarms.length > 0;
  }

  const followLane = $('#default-queue-lane');
  syncContainerCards(grid, followAlarms, cards, false);
  followLane.hidden = followAlarms.length === 0 && accounts.length > 0;
  grid.hidden = !followAlarms.length;

  syncContainerCards(customGrid, customAlarms, cards, true);
  customGrid.hidden = !customAlarms.length;
  $('#empty-state').hidden = customAlarms.length > 0;

  cards.forEach(card => card.remove());
  renderedOnce = true;
  updateClock();
}

function updateClock() {
  const now = currentTime();
  $('#server-time').textContent = `台北时间 ${fmtClock.format(now)}`;
  if (!snapshot) return;
  const candidates = snapshot.alarms
    .filter(a => a.enabled)
    .map(a => ({ ...a, target: a.mode === 'once' ? new Date(a.run_at) : dailyTarget(a.time, now) }))
    .filter(a => a.target.getTime() >= now.getTime() - 60000)
    .sort((a, b) => a.target - b.target);
  const upcoming = candidates[0];
  const nextIds = new Set(candidates.filter(a => a.target.getTime() === upcoming?.target.getTime()).map(a => a.id));
  $('#next-time').textContent = upcoming ? upcoming.time : '—:—';
  const targetHint = upcoming
    ? (upcoming.target_type === 'thread'
        ? ` · ${upcoming.thread_name || '指定会话'}`
        : upcoming.account_name ? ` · ${upcoming.account_name.split('@')[0]}` : '')
    : '';
  $('#next-day').textContent = upcoming
    ? `${dayLabel(upcoming.target, now)} · ${upcoming.time}${upcoming.mode === 'once' ? ' · 仅一次' : ''}${targetHint}`
    : '所有时间已暂停';
  if (!snapshot.alarms.length) $('#next-day').textContent = '添加时间后，在这里见';
  document.querySelectorAll('.alarm-card').forEach(card => {
    const next = nextIds.has(card.dataset.id);
    card.querySelector('.next-tag').hidden = !next;
    card.classList.toggle('is-next', next);
    if (next) card.setAttribute('aria-current', 'true');
    else card.removeAttribute('aria-current');
    const alarm = snapshot.alarms.find(a => a.id === card.dataset.id);
    const offsetEl = card.querySelector('.alarm-day-offset');
    if (alarm && offsetEl) {
      if (isRetiredOnce(alarm, now)) {
        offsetEl.hidden = true;
      } else {
        const dayDiff = calendarDayDiff(occurrenceTarget(alarm, now), now);
        offsetEl.hidden = dayDiff < 1;
        if (dayDiff >= 1) {
          offsetEl.textContent = `+${dayDiff}`;
          offsetEl.title = dayDiff === 1 ? '次日触发' : `${dayDiff} 天后触发`;
        }
      }
    }
  });
  const [hour, minute] = (upcoming?.time || '10:10').split(':').map(Number);
  $('#hour-hand').setAttribute('transform', `rotate(${(hour % 12) * 30 + minute / 2} 60 60)`);
  $('#minute-hand').setAttribute('transform', `rotate(${minute * 6} 60 60)`);
  const nowHm = fmtTime.format(now);
  if (now.getSeconds() <= 20 && snapshot.alarms.some(a => a.enabled && a.target_type !== 'thread' && a.time === nowHm)) {
    if (Date.now() - lastFastPoll >= 2000) {
      lastFastPoll = Date.now();
      refresh();
    }
  }
}

async function mutate(work, message, undo = null, optimistic = null) {
  if (loadingMutation) return;
  const previous = snapshot; loadingMutation = true;
  if (optimistic) snapshot = optimistic(snapshot);
  render();
  try {
    const data = await work();
    loadingMutation = false;
    accept(data);
    toast(message, undo);
  } catch (error) {
    loadingMutation = false;
    snapshot = previous;
    if (snapshot) render();
    toast(error.message);
  }
}

function openDialog(alarm = null, preset = {}) {
  editingAlarm = alarm;
  $('#form-error').textContent = '';
  const targetType = alarm ? alarm.target_type : (preset.target_type || 'default');
  $('#dialog-title').textContent = alarm ? '修改时间' : (targetType === 'thread' ? '新建定向会话发送' : '添加一个时间');
  $('#save-button').textContent = alarm ? '保存修改' : '添加时间';
  accountChoice = alarm ? (alarm.account_id || '') : (preset.account_id !== undefined ? preset.account_id : '');
  renderAccountChoices(); loadAccountChoices();
  const time = alarm?.time || preset.time || fmtTime.format(new Date(currentTime().getTime() + 1800000));
  const [h, m] = time.split(':'); setWheel('hour', Number(h), false); setWheel('minute', Number(m), false);
  const existingSession = alarm?.target_type === 'thread' ? sessions.find(s => s.id === alarm.thread_id) : null;
  selectedThread = alarm?.target_type === 'thread'
    ? { id: alarm.thread_id, name: alarm.thread_name || '指定会话', model: existingSession?.model || alarm.last_run?.model, effort: existingSession?.effort || alarm.last_run?.effort }
    : null;
  customModelChoice = alarm?.target_type === 'thread' ? (alarm.custom_model || '') : '';
  customEffortChoice = alarm?.target_type === 'thread' ? (alarm.custom_effort || '') : '';
  renderModelChoices();
  loadModelChoices();
  if (targetType === 'thread' && !sessionsLoadedOnce) loadSessions(true);
  $('#alarm-prompt').value = alarm?.prompt || '';
  document.querySelector(`input[name="delivery"][value="${targetType === 'thread' ? 'thread' : 'default'}"]`).checked = true;
  updateDelivery();
  document.querySelector(`input[name="repeat"][value="${alarm?.mode || preset.mode || 'daily'}"]`).checked = true;
  note();
  dialog.showModal();
  $('#save-button').focus({ preventScroll: true });
}

const MASCOT_IDLE_LINES = [
  '呜呜…主人睡得好香，我还要盯表发 ok (；′⌒`)',
  '我才刚通电，怎么就赛博早八了 ( >﹏< )',
  '主人鼻涕泡都吹出来了，我还在守窗口…',
  '再趴会儿…等主人起床就有满血额度用了 (´•ω•̥`)',
  '困死本模型了…先干一碗电子浓茶 ( -_-)旦~',
  '定好闹钟了，绝对不掉链子 (ง •̀_•́)ง'
];
const MASCOT_POKE_LINES = [
  '别戳我！！我没打瞌睡，我睁着眼呢 (⊙_⊙)！',
  '清醒了清醒了！这就替你死守重置点 ✦',
  '呜呜别催了，电子黑眼圈都熬出来了 (T_T)',
  '人在床上躺，Codex 替你扛！(๑•̀ㅂ•́)و✧'
];
let mascotIdx = 0, mascotPokeIdx = 0, mascotTimer = null, scrollMascotTimer = null;
function setMascotLine(text, holdMs = 0) {
  const bubble = $('#mascot-bubble'), wrap = $('#cyber-mascot');
  if (!bubble || !wrap) return;
  bubble.textContent = text;
  if (!reducedMotion.matches && typeof Motion !== 'undefined' && Motion.animateMini) {
    Motion.animateMini(bubble, { transform: ['scale(.9)', 'scale(1.04)', 'scale(1)'] }, { duration: .32 });
  }
  if (holdMs > 0) {
    wrap.classList.add('is-startled');
    clearTimeout(mascotTimer);
    mascotTimer = setTimeout(() => { wrap.classList.remove('is-startled'); }, holdMs);
  }
}
$('#mascot-stage')?.addEventListener('click', () => {
  const line = MASCOT_POKE_LINES[mascotPokeIdx % MASCOT_POKE_LINES.length];
  mascotPokeIdx++;
  setMascotLine(line, 1200);
});
$('#mascot-bubble')?.addEventListener('click', () => {
  const line = MASCOT_POKE_LINES[mascotPokeIdx % MASCOT_POKE_LINES.length];
  mascotPokeIdx++;
  setMascotLine(line, 1200);
});
$('#mascot-minimize')?.addEventListener('click', event => {
  event.stopPropagation();
  const wrap = $('#cyber-mascot'), btn = $('#mascot-minimize');
  if (!wrap || !btn) return;
  const min = wrap.classList.toggle('is-minimized');
  btn.textContent = min ? '＋' : '−';
  btn.title = min ? '展开小人偶' : '收起小人偶';
});
window.addEventListener('scroll', () => {
  const wrap = $('#cyber-mascot');
  if (!wrap || wrap.classList.contains('is-minimized')) return;
  wrap.classList.add('is-scrolling');
  if (!scrollMascotTimer) {
    setMascotLine('哇！别滑太快，我扒不住床沿啦 ( >﹏< )！');
  }
  clearTimeout(scrollMascotTimer);
  scrollMascotTimer = setTimeout(() => {
    wrap.classList.remove('is-scrolling');
    scrollMascotTimer = null;
  }, 650);
}, { passive: true });
setInterval(() => {
  const wrap = $('#cyber-mascot');
  if (document.hidden || !wrap || wrap.classList.contains('is-minimized') || wrap.classList.contains('is-startled') || wrap.classList.contains('is-scrolling')) return;
  mascotIdx = (mascotIdx + 1) % MASCOT_IDLE_LINES.length;
  setMascotLine(MASCOT_IDLE_LINES[mascotIdx]);
}, 8000);

$('#intro-copy')?.addEventListener('click', () => {
  if (reducedMotion.matches || typeof Motion === 'undefined' || !Motion.animateMini) return;
  document.querySelectorAll('.title-char').forEach((ch, idx) => {
    setTimeout(() => {
      Motion.animateMini(ch, { transform: ['translateY(0px) scale(1)', 'translateY(-7px) scale(1.12)', 'translateY(0px) scale(1)'] }, { duration: .42 });
    }, idx * 55);
  });
  const emp = $('.slogan-emphasis');
  if (emp) Motion.animateMini(emp, { transform: ['scale(1)', 'scale(1.06)', 'scale(1)'] }, { duration: .45 });
});
$('#add-button').addEventListener('click', () => {
  const defaultAccount = quotaView?.accounts?.find(a => a.current)?.id || quotaView?.accounts?.[0]?.id || '';
  openDialog(null, { target_type: 'default', account_id: defaultAccount });
});
$('#add-follow-button')?.addEventListener('click', () => openDialog(null, { target_type: 'default', account_id: '' }));
$('#add-custom-button')?.addEventListener('click', () => openDialog(null, { target_type: 'thread' }));
$('#empty-add').addEventListener('click', () => openDialog(null, { target_type: 'thread' }));
$('#close-dialog').addEventListener('click', () => dialog.close());
$('#cancel-dialog').addEventListener('click', () => dialog.close());

window.addEventListener('keydown', event => {
  if (!dialog.open || threadDialog.open || event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
  if (event.target.tagName === 'TEXTAREA' && !event.ctrlKey && !event.metaKey) return;
  event.preventDefault(); event.stopPropagation();
  if (!event.repeat && !loadingMutation) $('#save-button').click();
}, true);

dialog.addEventListener('click', event => {
  if (event.target === dialog) {
    const r = dialog.getBoundingClientRect();
    if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
  }
});

$('#add-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (loadingMutation) return;
  $('#form-error').textContent = ''; loadingMutation = true; $('#save-button').disabled = true;
  let payload;
  try {
    payload = { time: selectedTime(), mode: selectedMode(), enabled: editingAlarm ? editingAlarm.enabled : true, target_type: selectedDelivery() };
    if (payload.target_type === 'default') payload.account_id = accountChoice;
    if (payload.target_type === 'thread') {
      if (!selectedThread) throw new Error('请选择一个 Codex 会话');
      payload.thread_id = selectedThread.id;
      payload.prompt = $('#alarm-prompt').value;
      if (!payload.prompt.trim()) throw new Error('请填写要发送的提示词');
      payload.custom_model = customModelChoice || '';
      payload.custom_effort = customModelChoice ? (customEffortChoice || '') : '';
    }
    const data = await request(editingAlarm ? `/api/alarms/${editingAlarm.id}` : '/api/alarms', editingAlarm ? 'PATCH' : 'POST', payload);
    loadingMutation = false;
    $('#save-button').disabled = false;
    accept(data);
    dialog.close();
    toast(`${payload.time} ${editingAlarm ? '已修改' : '已添加'}`);
  } catch (error) {
    loadingMutation = false;
    $('#save-button').disabled = false;
    $('#form-error').textContent = error.message;
    if (snapshot) render();
  }
});

$('#undo-button').addEventListener('click', async () => {
  const action = undoAction; $('#toast').hidden = true; undoAction = null;
  if (action) await action();
});

async function refresh() {
  if (loadingMutation || dialog.open) return;
  try { accept(await request('/api/alarms')); }
  catch (error) {
    $('#connection').classList.add('offline');
    $('#connection span').textContent = '连接中断';
    $('#error-banner').textContent = error.message;
    $('#error-banner').hidden = false;
  }
}
setInterval(updateClock, 1000);
setInterval(refresh, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
refresh();
