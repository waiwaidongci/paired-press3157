import {DOMAIN} from '../constants/domain.js';
import {store,load} from '../stores/research.js';
import {runs,monitor} from '../api/research.js';
import {studySelect} from '../components/common/study-select.js';
import {runList} from '../components/common/run-list.js';
import {table} from '../components/common/table.js';
import {bindForm} from '../hooks/forms.js';
import {esc,fmt} from '../utils/format.js';
import {trialState} from '../components/common/monitor-summary.js';

const writable=()=>localStorage.getItem('research-role')!=='viewer';
const activeTrial=()=>store.trials.find(t=>!t.sealed&&(!store.studyId||t.study_id===store.studyId))||null;

export function render(){
  const selected=store.studyId;
  const trials=store.trials.filter(t=>!selected||t.study_id===selected);
  const active=activeTrial();
  const study=store.studies.find(s=>s.id===selected);
  return `<div class="eyebrow">02 / METHOD WORKSHEET</div><h1>${DOMAIN.field_label}</h1><p class="lead">${DOMAIN.algorithm}</p><div class="method-note">${DOMAIN.note}</div>
  <h2>固定终点分析 <small>原有方法保持不变</small></h2>
  ${writable()?`<form id="run-form" class="worksheet">${studySelect()}<label>本次研究名称<input name="name" required minlength="2" value="${esc(DOMAIN.names[0])} · 核算"></label><div class="inline-form">${Object.entries(DOMAIN.defaults).map(([key,value])=>`<label>重采样次数 (200–20000)<input name="${key}" type="number" step="any" value="${value}" required></label>`).join('')}</div><p>提交时冻结全部观测与参数，生成 SHA256。后台 worker 独立执行。</p><button type="submit">冻结输入并排队计算 →</button></form>`:'<p class="method-note">观察员可阅读研究记录，分析师与负责人可以发起计算。</p>'}
  <h2>连续监测 <small>启动锁定双侧 5% 门槛，任意停看仍控制全程误报</small></h2>
  <div class="method-note"><strong>方法依据</strong><p>零假设下配对差独立且关于零对称，差值符号即独立公平的 ±1。按 Beta(1/2,1/2) 预测概率下注得到非负鞅 M<sub>n</sub>；Ville 不等式保证对任意查看次数和任意停时 P(存在 n 使 M<sub>n</sub> ≥ 1/α) ≤ α。启动时锁定 α = 0.05、门槛 M = 20，查看多少次都不会抬高误报。</p><small>每次查看只纳入累计新增的完整纸样，同一纸样重试不重复计入；已纳入纸样被修改或删除后试验封存，改后数据不续入旧证据。监测不阻塞新纸样登记，多个查看沿同一累计序列推进，中断后可恢复，晚到执行不能倒退证据或解除封存。</small></div>
  ${active?`<div class="worksheet"><h3>试验 #${active.id} ${esc(active.study_name)} ${trialState(active)}</h3><p><small>已纳入 ${esc(active.included_count)} 对 · M = ${esc(active.evidence?.evidence_label)} · 锁定门槛 1/α = ${esc(fmt(active.threshold))}${active.stop?.stopped?' · 已在第 '+esc(active.stop.stop_seq)+' 对触发停止':''}</small></p><div class="toolbar"><a class="button" href="#results/trial-${active.id}">打开监测结果 →</a>${writable()?`${active.stop?.stopped?'':'<button id="look-button">查看累计新增纸样</button>'}<button class="text" id="seal-button">封存本试验</button>`:''}</div></div>`:writable()?`<div class="worksheet"><p>该${DOMAIN.study_label}${study?'「'+esc(study.name)+'」':''}尚未启动连续监测试验。</p><button id="trial-button" ${study?'':'disabled'}>启动连续监测（锁定 α = 0.05）</button></div>`:'<p class="method-note">尚无未封存的连续监测试验。</p>'}
  ${trials.filter(t=>t.sealed).length?`<h3>已封存试验</h3>${table(['试验','批次','已纳入','封存原因','结果'],trials.filter(t=>t.sealed).map(t=>[`#${t.id}`,t.study_name,t.included_count,t.sealed_reason==='manual_archive'?'负责人手动归档':'纳入纸样被修改或删除',{html:`<a href="#results/trial-${t.id}">查看 →</a>`}]))}`:''}
  <h2>计算记录</h2>${runList()}`;
}

export function bind(refresh){
  bindForm('run-form',async data=>{
    const parameters=Object.fromEntries(Object.keys(DOMAIN.defaults).map(k=>[k,+data[k]]));
    const r=await runs.create({study_id:+data.study_id,name:data.name,parameters});
    store.runId=r.id;await load();location.hash='results/'+r.id;
  });
  const sel=document.getElementById('study-select');
  if(sel)sel.onchange=async()=>{store.studyId=+sel.value;await load();refresh();};
  const trialButton=document.getElementById('trial-button');
  if(trialButton)trialButton.onclick=async()=>{try{trialButton.disabled=true;const t=await monitor.start(store.studyId);await load();location.hash='results/trial-'+t.id;}catch(e){document.getElementById('notice').textContent=e.message;trialButton.disabled=false;}};
  const lookButton=document.getElementById('look-button');
  if(lookButton)lookButton.onclick=async()=>{const active=activeTrial();if(!active)return;try{lookButton.disabled=true;const key='look-'+Date.now()+'-'+Math.random().toString(36).slice(2,8);await monitor.look(active.id,key);await load();location.hash='results/trial-'+active.id;}catch(e){document.getElementById('notice').textContent=e.message;lookButton.disabled=false;}};
  const sealButton=document.getElementById('seal-button');
  if(sealButton)sealButton.onclick=async()=>{const active=activeTrial();if(!active)return;try{await monitor.seal(active.id);await load();refresh();}catch(e){document.getElementById('notice').textContent=e.message;}};
}
