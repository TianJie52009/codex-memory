'use strict';
const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('memory-token') || '';
sessionStorage.setItem('memory-token',token);
history.replaceState(null, '', '/');
const kinds = {fact:'事实', preference:'偏好', decision:'决定', workflow:'工作方式', state:'阶段状态', reference:'参考信息'};
const views = {all:'全部记忆', global:'全局记忆', project:'项目记忆', muted:'暂不提起', provisional:'待确认'};
const originKinds = {user:'用户表述', verified:'已验证', inference:'推断 · 待确认'};
const model = {view:'all', project:'', items:[], selected:null, next:null, state:null, generation:0, selection:0, busy:false};
const date = value => new Date(value).toLocaleDateString('zh-CN', {year:'numeric', month:'short', day:'numeric'});
const shortDate = value => new Date(value).toLocaleDateString('zh-CN', {month:'numeric', day:'numeric'});
const projectName = value => value.split(/[\\/]/).filter(Boolean).pop() || value;
const node = (tag, cls, text) => {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS(svg.namespaceURI, 'use');
  use.setAttribute('href', '#i-' + name);
  svg.setAttribute('aria-hidden', 'true'); svg.append(use); return svg;
}
async function api(path, data) {
  const response = await fetch('/api/' + path, {
    headers: {Authorization:'Bearer ' + token, ...(data ? {'Content-Type':'application/json'} : {})},
    method: data ? 'POST' : 'GET', ...(data ? {body:JSON.stringify(data)} : {})
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || '暂时无法连接记忆面板');
  return result;
}
function report(error) { $('banner').textContent = error.message; $('banner').hidden = false; }
function perform(callback) { return async event => { try { await callback(event); } catch (error) { report(error); } }; }
const root = document.documentElement;
const systemTheme = matchMedia('(prefers-color-scheme: dark)');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let preferences = {theme:'system', accent:'#7866c5', motion:true, zoom:100, sort:'updated'};
let appearanceReady = false, keyboardInput = false, preferencePending = 0, preferenceQueue = Promise.resolve(), appearanceEpoch=0;
const effects = new WeakMap();
let titlebarFrame,lastOverlay;
function syncWindowTheme() {
  cancelAnimationFrame(titlebarFrame);
  titlebarFrame=requestAnimationFrame(()=>{
    const overlay=navigator.windowControlsOverlay, visible=!!overlay?.visible;
    const area=visible?overlay.getTitlebarAreaRect():null, scale=preferences.zoom/100;
    root.dataset.overlay=String(visible);
    root.style.setProperty('--window-right',(area?Math.ceil(Math.max(0,innerWidth-area.right)/scale):0)+'px');
    root.style.setProperty('--window-left',(area?Math.ceil(area.x/scale):0)+'px');
    if (lastOverlay!==visible) {
      lastOverlay=visible;
      api('window-state',{overlay:visible}).catch(()=>{});
    }
  });
}
navigator.windowControlsOverlay?.addEventListener('geometrychange',syncWindowTheme);
const easeOut = 'cubic-bezier(.23,1,.32,1)';
// Critically damped spring: damping=1, response=.35s; the browser interpolates natively.
const spring = 'linear(' + Array.from({length:41}, (_,i) => {
  const t=i/40, w=2*Math.PI;
  return ((1-(1+w*t)*Math.exp(-w*t))/(1-(1+w)*Math.exp(-w))).toFixed(5);
}).join(',') + ')';
root.style.setProperty('--ease-spring',spring);
const motionAllowed = () => preferences.motion && !reducedMotion.matches && !keyboardInput;
function motion(element, frames, duration=280, easing=spring) {
  const prior=effects.get(element);
  if (!motionAllowed()) { prior?.cancel(); return null; }
  if (prior?.playState === 'running') {
    const live=getComputedStyle(element);
    frames[0]={...frames[0], opacity:live.opacity, transform:live.transform};
  }
  prior?.cancel();
  const effect=element.animate(frames,{duration,easing});
  effects.set(element,effect); effect.finished.catch(()=>{});
  return effect;
}
function replaceIcon(container, name) {
  let slot=container.querySelector('.icon-slot');
  if (!slot) {
    slot=node('span','icon-slot'); const old=container.querySelector('svg');
    if (old) { old.replaceWith(slot); slot.append(old); }
    else container.prepend(slot);
  }
  if (slot.dataset.icon === name) return;
  slot.dataset.icon=name;
  const old=slot.lastElementChild;
  for (const extra of [...slot.children]) if (extra !== old) extra.remove();
  const next=icon(name); slot.append(next);
  if (old && appearanceReady && motionAllowed()) {
    const exiting=motion(old,[{opacity:1,transform:'rotate(0) scale(1)'},{opacity:0,transform:'rotate(70deg) scale(.55)'}],220,easeOut);
    exiting.finished.finally(()=>old.remove()).catch(()=>{});
    motion(next,[{opacity:0,transform:'rotate(-70deg) scale(.55)'},{opacity:1,transform:'rotate(0) scale(1)'}]);
  } else old?.remove();
}
function showDialog(dialog) {
  delete dialog.dataset.closing;
  if (!dialog.open) dialog.showModal();
  motion(dialog,[{opacity:0,transform:'translateY(15px) scale(.96)'},{opacity:1,transform:'translateY(0) scale(1)'}],300);
}
async function closeDialog(dialog) {
  const closing=String(performance.now()); dialog.dataset.closing=closing;
  const effect=motion(dialog,[{opacity:1,transform:'translateY(0) scale(1)'},{opacity:0,transform:'translateY(10px) scale(.97)'}],180,easeOut);
  await effect?.finished.catch(()=>{});
  if (dialog.dataset.closing === closing) { dialog.close(); delete dialog.dataset.closing; effect?.cancel(); }
}
const rgb = hex => [1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));
function mix(a,b,amount) {
  return '#' + rgb(a).map((n,i)=>Math.round(n+(rgb(b)[i]-n)*amount).toString(16).padStart(2,'0')).join('');
}
function luminance(color) {
  return rgb(color).map(n=>n/255).map(n=>n<=.04045?n/12.92:((n+.055)/1.055)**2.4).reduce((sum,n,i)=>sum+n*[.2126,.7152,.0722][i],0);
}
function contrast(a,b) { const x=luminance(a),y=luminance(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05); }
function applyAppearance(value, epoch=++appearanceEpoch) {
  if (epoch!==appearanceEpoch) return;
  preferences={...value};
  const dark=value.theme === 'dark' || (value.theme === 'system' && systemTheme.matches);
  const wasReady=appearanceReady;
  root.dataset.theme=dark?'dark':'light'; root.dataset.motion=value.motion?'on':'off';
  if (!value.motion) document.getAnimations().forEach(effect=>effect.cancel());
  const surface=getComputedStyle(root).getPropertyValue('--surface').trim(), target=dark?'#ffffff':'#19171f';
  let readable=value.accent;
  for (let i=0;i<20 && contrast(readable,surface)<4.5;i++) readable=mix(readable,target,.12);
  const header=mix(dark?'#343642':'#f6f6f8',value.accent,.035);
  root.style.setProperty('--accent-base',value.accent);
  root.style.setProperty('--accent',readable);
  root.style.setProperty('--accent-strong',value.accent);
  root.style.setProperty('--accent-ink',contrast(value.accent,'#ffffff')>=4.5?'#ffffff':'#17151d');
  root.style.setProperty('--tint',mix(surface,value.accent,dark?.24:.13));
  root.style.setProperty('--soft',mix(surface,value.accent,dark?.12:.055));
  root.style.setProperty('--hover',mix(surface,value.accent,dark?.18:.075));
  root.style.setProperty('--header',header);
  root.style.setProperty('--sidebar',mix(header,value.accent,dark?.025:.015));
  $('window-color').content=header;
  syncWindowTheme();
  replaceIcon($('theme-toggle'),dark?'sun':'moon');
  $('theme-toggle').setAttribute('aria-label',dark?'切换浅色外观':'切换深色外观');
  document.querySelectorAll('[data-theme-value]').forEach(button=>{
    const selected=button.dataset.themeValue===value.theme;
    button.classList.toggle('selected',selected); button.setAttribute('aria-pressed',selected);
  });
  $('appearance').style.setProperty('--segment', ['light','dark','system'].indexOf(value.theme));
  document.querySelectorAll('[data-accent]').forEach(button=>{
    button.classList.toggle('selected',button.dataset.accent===value.accent);
    button.setAttribute('aria-pressed',button.dataset.accent===value.accent);
  });
  $('accent-picker').value=value.accent;
  if (document.activeElement !== $('accent-hex')) $('accent-hex').value=value.accent.toUpperCase();
  $('motion-toggle').setAttribute('aria-checked',value.motion);
  root.style.setProperty('--ui-scale',value.zoom/100);
  $('zoom-range').value=value.zoom; $('zoom-value').textContent=value.zoom+'%';
  $('zoom-range').style.setProperty('--range-fill',(value.zoom-75)/75*100+'%');
  $('sort-order').value=value.sort || 'updated';
  requestAnimationFrame(moveIndicator);
  appearanceReady=true;
  if (!wasReady) requestAnimationFrame(()=>{moveIndicator();requestAnimationFrame(()=>root.dataset.ready='true');});
}
let themeTransition;
function themeRevealOrigin(element) {
  const box=element?.getBoundingClientRect?.();
  const x=box ? box.left+box.width/2 : innerWidth/2;
  const y=box ? box.top+box.height/2 : 0;
  const radius=Math.hypot(Math.max(x,innerWidth-x),Math.max(y,innerHeight-y));
  root.style.setProperty('--theme-x',x+'px');
  root.style.setProperty('--theme-y',y+'px');
  root.style.setProperty('--theme-radius',radius+'px');
}
function changeAppearance(value, reveal=false, origin=null) {
  const epoch=++appearanceEpoch;
  preferences={...value};
  const update=()=>applyAppearance(value,epoch);
  themeTransition?.skipTransition();
  if (reveal && appearanceReady && motionAllowed() && document.startViewTransition) {
    themeRevealOrigin(origin);
    themeTransition=document.startViewTransition(update);
    themeTransition.ready.catch(()=>{});
    themeTransition.updateCallbackDone.catch(report);
    themeTransition.finished.catch(()=>{});
  } else update();
}
function savePreferences(patch, reveal=false, origin=null) {
  changeAppearance({...preferences,...patch},reveal,origin);
  preferencePending++; $('settings-saved').textContent='保存中…';
  preferenceQueue=preferenceQueue.catch(()=>{}).then(()=>api('preferences',patch));
  return preferenceQueue.then(()=>{
    if (preferencePending===1) $('settings-saved').textContent='已保存';
  }).catch(error=>{
    $('settings-saved').textContent='保存失败'; throw error;
  }).finally(()=>preferencePending--);
}
function moveIndicator() {
  const selected=document.querySelector('.sidebar .nav-item[aria-current="page"]'), indicator=$('nav-indicator');
  if (!selected) { indicator.hidden=true; return; }
  const parent=selected.closest('.sidebar'), box=selected.getBoundingClientRect(), sidebar=parent.getBoundingClientRect();
  const scale=sidebar.width/parent.offsetWidth;
  indicator.hidden=false; indicator.style.width=box.width/scale+'px'; indicator.style.height=box.height/scale+'px';
  indicator.style.transform='translate3d('+(box.left-sidebar.left)/scale+'px,'+(box.top-sidebar.top)/scale+'px,0)';
}
document.addEventListener('pointerdown',()=>{keyboardInput=false;delete root.dataset.input;},true);
document.addEventListener('keydown',()=>{keyboardInput=true;root.dataset.input='keyboard';},true);
addEventListener('resize',()=>{moveIndicator();syncWindowTheme();});
addEventListener('focus',syncWindowTheme);
let toastTimer;
function toast(message) {
  clearTimeout(toastTimer); $('toast').textContent = message; $('toast').classList.add('visible');
  toastTimer = setTimeout(() => $('toast').classList.remove('visible'), 2200);
}
function renderState(state) {
  model.state = state;
  if (!preferencePending && state.preferences) applyAppearance(state.preferences);
  document.querySelectorAll('[data-count]').forEach(el => el.textContent = state.counts[el.dataset.count] || 0);
  $('total-count').textContent = state.counts.all || 0;
  $('project-count').textContent = state.projects.length;
  $('projects').replaceChildren(); $('project-options').replaceChildren();
  for (const project of state.projects) {
    const button = node('button', 'nav-item');
    button.title = project.path; button.dataset.project = project.path;
    button.append(icon('folder'), node('span', '', project.name), node('span', 'count', project.count));
    button.addEventListener('click', perform(() => filter('project', project.path)));
    $('projects').append(button);
    const option = node('option'); option.value = project.path; $('project-options').append(option);
  }
  $('data-location').textContent = state.data_dir;
  $('connection-state').textContent = state.codex_configured ? '已配置 Codex 记忆工具' : '此目录尚未接入 Codex';
  $('app-version').textContent = 'v' + state.version;
  if (state.warnings.length) {
    $('banner').textContent = '部分记忆暂时无法读取，原文件仍保留。请让 Codex 检查数据完整性。';
    $('banner').hidden = false;
  }
  renderNavigation();
}
function renderNavigation() {
  const previous=document.querySelector('.sidebar .nav-item[aria-current="page"]');
  document.querySelectorAll('[data-view], [data-project]').forEach(button => {
    const selected = button.dataset.project ? button.dataset.project === model.project
      : !model.project && button.dataset.view === model.view;
    button.classList.toggle('selected', selected);
    if (selected) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  $('section-title').textContent = model.project ? projectName(model.project) : views[model.view];
  moveIndicator();
  const active=document.querySelector('.sidebar .nav-item[aria-current="page"]');
  if (active && active!==previous) motion(active.querySelector('svg'),[{opacity:.6,transform:'scale(.78) rotate(-12deg)'},{opacity:1,transform:'scale(1) rotate(0)'}]);
}
function renderList() {
  const scroll = $('memory-list').scrollTop;
  const existing=new Map([...$('memory-list').querySelectorAll('.memory-item')].map(el=>[el.dataset.id,el]));
  const retained=new Set();
  for (const [index,item] of model.items.entries()) {
    const button = existing.get(item.id) || node('button','memory-item');
    retained.add(button);
    button.classList.toggle('selected',model.selected?.id === item.id);
    button.setAttribute('aria-pressed', String(model.selected?.id === item.id));
    if (!button.dataset.id) {
      button.dataset.id=item.id;
      button.addEventListener('click',perform(()=>select(button.dataset.id,true)));
    }
    if (button.dataset.revision!==String(item.revision)) {
    button.dataset.revision=item.revision;
    const heading = node('div', 'memory-heading');
    const dot = node('span', 'memory-dot'); dot.dataset.kind = item.kind;
    heading.append(dot, node('strong', '', item.title));
    const meta = node('div', 'memory-meta');
    meta.append(icon(item.scope === 'global' ? 'globe' : 'folder'), node('span', '', item.scope === 'global' ? '全局' : projectName(item.project)));
    if (item.muted) meta.append(icon('mute'));
    if (item.status === 'provisional') meta.append(node('span', '', '待确认'));
    meta.append(node('span', 'date', shortDate(item.updated_at)));
    button.replaceChildren(heading, node('p', 'memory-excerpt', item.excerpt), meta);
    }
    if ($('memory-list').children[index]!==button) $('memory-list').insertBefore(button,$('memory-list').children[index] || null);
  }
  for (const child of [...$('memory-list').children]) if (!retained.has(child)) child.remove();
  if (!model.items.length) $('memory-list').append(node('p', 'list-empty', $('search').value ? '没有找到匹配的记忆。\n试试其他关键词。' : '这里还没有记忆。\n点击右上角，留下第一条。'));
  $('memory-list').scrollTop = scroll;
  $('load-more').hidden = model.next === null;
}
async function loadList({more=false, selectId=null} = {}) {
  const generation = ++model.generation;
  const query = new URLSearchParams({q:$('search').value, view:model.view, project:model.project, offset:more ? model.next : 0,sort:$('sort-order').value});
  const result = await api('notes?' + query);
  if (generation !== model.generation) return;
  model.items = more ? [...model.items, ...result.items] : result.items;
  model.next = result.next_offset;
  $('result-count').textContent = result.total + ' 条记忆';
  renderList();
  const target = selectId || (model.items.some(item => item.id === model.selected?.id) ? model.selected.id : model.items[0]?.id);
  if (target) await select(target);
  else {
    model.selection++; model.selected = null; $('note-detail').hidden = true; $('detail-empty').hidden = false;
    delete document.body.dataset.detail;
  }
}
async function filter(view, project='') {
  model.view = view; model.project = project;
  delete document.body.dataset.detail; $('memory-list').scrollTop = 0;
  renderNavigation(); await loadList();
  motion($('memory-list'),[{opacity:.15,transform:'translateY(9px)'},{opacity:1,transform:'translateY(0)'}],260,easeOut);
}
async function select(id, reveal=false) {
  const sequence = ++model.selection;
  const note = await api('notes/' + id);
  if (sequence !== model.selection) return;
  const changed=model.selected?.id !== note.id;
  model.selected = note;
  $('detail-empty').hidden = true; $('note-detail').hidden = false;
  $('note-title').textContent = note.title;
  $('note-body').textContent = note.body;
  $('note-kind').textContent = kinds[note.kind];
  $('note-date').textContent = date(note.updated_at) + ' 更新';
  $('note-importance').textContent = ['','普通','重要','核心'][note.importance];
  const expired = note.expires_at && new Date(note.expires_at) <= new Date();
  const state = [note.muted ? '暂不提起' : '', note.status === 'provisional' ? '待确认' : '', expired ? '已过期' : ''].filter(Boolean).join(' · ');
  $('note-status').textContent = state; $('note-status').hidden = !state;
  let locationText=$('note-location').querySelector('.location-text');
  if (!locationText) { locationText=node('span','location-text'); $('note-location').append(locationText); }
  replaceIcon($('note-location'),note.scope === 'global' ? 'globe' : 'folder');
  locationText.textContent=note.scope === 'global'?'所有项目可用':projectName(note.project);
  $('note-location').title = note.project;
  $('mute-note').querySelector('span:not(.icon-slot)').textContent = note.muted ? '恢复回忆' : '暂不提起';
  $('mute-note').setAttribute('aria-label', note.muted ? '恢复主动回忆' : '暂不提起');
  replaceIcon($('mute-note'),note.muted?'bell':'mute');
  $('source-count').textContent = note.sources.length + ' 份';
  $('revision-count').textContent = '第 ' + note.revision + ' 版';
  for (const name of ['sources', 'history']) {
    $(name).open = false; $(name).dataset.loaded = '';
    $(name === 'sources' ? 'source-list' : 'history-list').replaceChildren();
  }
  if (reveal) document.body.dataset.detail = 'true';
  if (changed) {
    document.querySelector('.note-scroll').scrollTop=0;
    motion(document.querySelector('.note-content'),[{opacity:.2,transform:'translateY(12px)'},{opacity:1,transform:'translateY(0)'}]);
  }
  renderList();
}
async function disclose(name) {
  if (!$(name).open || $(name).dataset.loaded || !model.selected) return;
  const id = model.selected.id;
  const container = $(name === 'sources' ? 'source-list' : 'history-list');
  container.replaceChildren(node('p', 'small', '正在读取…'));
  const result = await api('notes/' + id + '/' + name);
  if (model.selected?.id !== id) return;
  container.replaceChildren();
  for (const item of name === 'sources' ? result.sources : result.items) {
    const card = node('section', 'evidence-card');
    if (name === 'sources') {
      card.append(node('p', 'evidence-caption', (originKinds[item.kind] || item.kind) + (item.observed_at ? ' · ' + date(item.observed_at) : '')));
      card.append(node('div', 'evidence-origin', item.origin), node('p', 'evidence-text', item.text));
    } else {
      card.append(node('p', 'evidence-caption', '第 ' + item.revision + ' 版 · ' + date(item.updated_at)), node('div', 'evidence-origin', item.title), node('p', 'evidence-text', item.body));
    }
    container.append(card);
  }
  if (!container.children.length) container.append(node('p', 'small', '还没有更正记录。'));
  $(name).dataset.loaded = 'true';
  motion(container,[{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],220,easeOut);
}
let editing = null;
function editor(note=null) {
  editing = note ? {...note} : null;
  $('editor-title').textContent = note ? '修改记忆' : '添加记忆';
  $('edit-title').value = note?.title || '';
  $('edit-body').value = note?.body || '';
  $('edit-kind').value = note?.kind || 'fact';
  $('edit-scope').value = note?.scope || (model.project ? 'project' : 'global');
  $('edit-project').value = note?.project || model.project;
  $('edit-scope').disabled = !!note; $('edit-project').disabled = !!note;
  $('project-field').hidden = $('edit-scope').value !== 'project';
  document.querySelector('input[name="importance"][value="' + (note?.importance || 2) + '"]').checked = true;
  $('edit-error').textContent = '';
  showDialog($('editor')); $('edit-title').focus();
}
async function reload(selectId) {
  renderState(await api('state')); await loadList({selectId});
}
$('edit-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (model.busy) return;
  const data = {title:$('edit-title').value.trim(), body:$('edit-body').value.trim(), kind:$('edit-kind').value, importance:Number(document.querySelector('input[name="importance"]:checked').value)};
  if (editing) Object.assign(data, {id:editing.id, expected_revision:editing.revision});
  else Object.assign(data, {scope:$('edit-scope').value, project:$('edit-scope').value === 'project' ? $('edit-project').value.trim() : ''});
  model.busy = true; $('save-note').disabled = true; $('save-note').textContent = '保存中…';
  try {
    const result = await api('action', {action:editing ? 'revise' : 'save', data});
    await closeDialog($('editor'));
    await reload(result.id); toast('记忆已保存');
  } catch (error) { $('edit-error').textContent = error.message; }
  finally { model.busy = false; $('save-note').disabled = false; $('save-note').textContent = '保存记忆'; }
});
$('mute-note').addEventListener('click', perform(async () => {
  if (model.busy || !model.selected) return;
  const note = model.selected;
  model.busy = true; $('mute-note').disabled = true;
  try {
    await api('action', {action:note.muted ? 'unmute' : 'mute', data:{id:note.id, expected_revision:note.revision}});
    await reload(); toast(note.muted ? '已恢复主动回忆' : '已暂不提起，可随时恢复');
  } finally { model.busy = false; $('mute-note').disabled = false; }
}));
let deleting;
$('delete-note').addEventListener('click', () => {
  deleting = {...model.selected};
  $('delete-description').textContent = '“' + deleting.title + '”';
  $('delete-error').textContent = ''; showDialog($('delete-dialog'));
});
$('delete-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (model.busy) return;
  model.busy = true; $('confirm-delete').disabled = true;
  try {
    await api('action', {action:'forget', data:{id:deleting.id, expected_revision:deleting.revision}});
    await closeDialog($('delete-dialog')); await reload(); toast('记忆已删除');
  } catch (error) { $('delete-error').textContent = error.message; }
  finally { model.busy = false; $('confirm-delete').disabled = false; }
});
$('add-note').addEventListener('click', () => editor());
$('edit-note').addEventListener('click', () => editor(model.selected));
$('edit-scope').addEventListener('change', () => $('project-field').hidden = $('edit-scope').value !== 'project');
$('views').addEventListener('click', perform(async event => {
  const button = event.target.closest('[data-view]'); if (button) await filter(button.dataset.view);
}));
$('load-more').addEventListener('click', perform(() => loadList({more:true})));
$('mobile-back').addEventListener('click', () => delete document.body.dataset.detail);
for (const name of ['sources', 'history']) $(name).addEventListener('toggle', perform(() => disclose(name)));
for (const id of ['help-open','empty-help']) $(id).addEventListener('click', () => showDialog($('help')));
$('settings-open').addEventListener('click', () => showDialog($('settings')));
for (const name of ['settings','help']) {
  const dialog=$(name);let backdropDown=false;
  const outside=event=>{
    const box=dialog.getBoundingClientRect();
    return event.clientX<box.left || event.clientX>box.right || event.clientY<box.top || event.clientY>box.bottom;
  };
  dialog.addEventListener('pointerdown',event=>{backdropDown=event.target===dialog && outside(event);});
  dialog.addEventListener('click',event=>{
    if (backdropDown && event.target===dialog && outside(event)) closeDialog(dialog);
    backdropDown=false;
  });
}
$('sort-order').addEventListener('change',perform(async()=>{
  savePreferences({sort:$('sort-order').value}).catch(report);
  await loadList();
}));
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => closeDialog($(button.dataset.close))));
document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('cancel',event=>{event.preventDefault();closeDialog(dialog);}));
$('open-folder').addEventListener('click', perform(async () => { await api('open-folder', {where:'data'}); toast('已打开记忆文件夹'); }));
let searchTimer;
$('search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadList().catch(report), 160);
});
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) {
    event.preventDefault(); delete document.body.dataset.detail; $('search').focus(); $('search').select();
  }
});
$('theme-toggle').addEventListener('click', perform(event=>{
  const dark=preferences.theme==='dark' || (preferences.theme==='system' && systemTheme.matches);
  return savePreferences({theme:dark?'light':'dark'},true,event.currentTarget);
}));
$('appearance').addEventListener('click',perform(event=>{
  const button=event.target.closest('[data-theme-value]');
  if (button) return savePreferences({theme:button.dataset.themeValue},true,button);
}));
$('accent-presets').addEventListener('click',perform(event=>{
  const button=event.target.closest('[data-accent]');
  if (button) return savePreferences({accent:button.dataset.accent});
}));
$('accent-picker').addEventListener('input',()=>applyAppearance({...preferences,accent:$('accent-picker').value}));
$('accent-picker').addEventListener('change',perform(()=>savePreferences({accent:$('accent-picker').value})));
$('accent-hex').addEventListener('change',perform(()=>{
  const accent=$('accent-hex').value.trim();
  if (!/^#[0-9a-f]{6}$/i.test(accent)) { $('accent-error').textContent='请输入 # 开头的六位颜色值'; return; }
  $('accent-error').textContent=''; return savePreferences({accent:accent.toLowerCase()});
}));
$('motion-toggle').addEventListener('click',perform(()=>savePreferences({motion:!preferences.motion})));
$('zoom-range').addEventListener('input',()=>applyAppearance({...preferences,zoom:Number($('zoom-range').value)}));
$('zoom-range').addEventListener('change',perform(()=>savePreferences({zoom:Number($('zoom-range').value)})));
$('zoom-reset').addEventListener('click',perform(()=>savePreferences({zoom:100})));
systemTheme.addEventListener('change',()=>{if(preferences.theme==='system') changeAppearance(preferences,true);});
reducedMotion.addEventListener('change',()=>applyAppearance(preferences));
setInterval(() => api('ping').catch(() => {}), 30000);
let polling = false;
setInterval(async () => {
  if (document.hidden || polling || model.busy || document.querySelector('dialog[open]')) return;
  polling = true;
  try {
    const state = await api('state');
    if (state.fingerprint !== model.state?.fingerprint) { renderState(state); await loadList(); }
    else if (!preferencePending && JSON.stringify(state.preferences)!==JSON.stringify(preferences)) applyAppearance(state.preferences);
  } catch (error) { report(error); }
  finally { polling = false; }
}, 5000);
reload().catch(report);
