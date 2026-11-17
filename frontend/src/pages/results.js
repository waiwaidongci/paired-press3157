import {store} from '../stores/research.js';
import {runs,monitor} from '../api/research.js';
import {esc,fmt} from '../utils/format.js';
import {table} from '../components/common/table.js';
import {status} from '../components/common/status.js';
import {runList} from '../components/common/run-list.js';
import {download} from '../utils/download.js';
import {evidenceSummary,trialState,inclusionTable,scopeNote} from '../components/common/monitor-summary.js';

const writable=()=>localStorage.getItem('research-role')!=='viewer';

export async function render(key){
  if(!key)return `<div class="eyebrow">03 / RESULTS ARCHIVE</div><h1>计算结果与解释</h1><p>选择一次固定终点运行或连续监测试验查看冻结输入、算法结果与业务解释。</p>${runList()}${trialList()}`;
  if(String(key).startsWith('trial-'))return renderTrial(+String(key).slice(6));
  return renderRun(key);
}

function trialList(){
  if(!store.trials.length)return '';
  return `<h2>连续监测试验</h2>${table(['试验 / 批次','状态','累计证据','已纳入','停止判定'],store.trials.map(t=>[{html:`<a href="#results/trial-${t.id}">#${t.id} ${esc(t.study_name)}</a>`},{html:trialState(t)},esc(t.evidence?.evidence_label),`${t.included_count} 对`,t.stop?.stopped?`第 ${t.stop.stop_seq} 对停止`:'继续监测']))}`;
}

async function renderTrial(id){
  const t=await monitor.get(id);
  store.trialId=t.id;store.studyId=t.study_id;
  const scope=t.inclusion_scope||{};
  const pending=(t.looks||[]).filter(l=>l.status==='queued'||l.status==='running');
  store.pendingLooks=pending.length;
  return `<div class="eyebrow">03 / CONTINUOUS MONITORING · TRIAL ${t.id}</div><h1>${esc(t.study_name)} ${trialState(t)}</h1>
  <p>启动 ${esc(t.created_at)} · α 启动时锁定 ${esc(fmt(t.alpha))} · 门槛 1/α = ${esc(fmt(t.threshold))}${t.sealed?` · 封存于 ${esc(t.sealed_at)}`:''}</p>
  <section class="result-intro"><strong>${esc(t.stop?.decision||'继续监测')}</strong><p>${esc(t.method)}</p><p><small>${esc(t.basis)}</small></p></section>
  <h2>累计证据、停止判定与纳入范围</h2>${evidenceSummary(t)}
  <h2>纳入范围</h2>
  <p><small>纳入第 ${esc(scope.included_first_seq)}–${esc(scope.included_last_seq)} 对，按登记顺序；批次共 ${esc(scope.study_observation_count)} 条记录，待纳入完整 ${esc(scope.pending_complete_count)} 条、不完整 ${esc(scope.pending_incomplete_count)} 条${scope.after_stop_pending?`，停止判定后 ${esc(scope.after_stop_pending)} 条不再进入证据`:''}。每行冻结当时数值与 SHA256；纳入后修改或删除的纸样标记为“已修改/已删除”，旧证据不变。</small></p>
  ${inclusionTable(t)}${scopeNote(t)}
  <h2>查看记录 <small>沿同一累计序列推进，重试不重复纳入</small></h2>
  ${table(['查看','请求键','状态','新增','序号区间','执行时 M','判定 / 说明'],(t.looks||[]).map(l=>[`#${l.id}`,l.request_key?esc(l.request_key):'启动时自动',l.status,l.added?l.added+' 对':'0',`${l.seq_before}→${l.seq_after}`,esc(l.log_e_after!=null?fmt(Math.exp(Math.min(l.log_e_after,700))):'—'),{html:`<small>${esc(l.message||l.error||'')}</small>`}]))}
  ${pending.length&&writable()?`<div class="toolbar">${pending.map(l=>`<button class="text" data-resume="${l.id}">恢复查看 #${l.id}</button>`).join(' ')}</div>`:''}
  ${t.sealed?`<p class="method-note">试验已封存：<strong>${esc({included_observation_changed:'已纳入纸样修改',included_observation_deleted:'已纳入纸样删除',manual_archive:'负责人手动归档'}[t.sealed_reason]||t.sealed_reason||'已封存')}</strong>。改后数据不续入旧证据；如需继续研究，请在分析执行页启动新试验。</p>`:''}
  ${writable()&&!t.sealed?`<div class="toolbar">${t.stop?.stopped?'':'<button id="trial-look">查看累计新增纸样</button>'}<button class="text" id="trial-seal">封存本试验</button></div>`:''}
  <p><a class="button secondary" href="#analysis">返回分析执行页 →</a></p>`;
}

async function renderRun(id){
  const r=await runs.get(id);store.runId=r.id;
  const trialLink=store.trials.filter(t=>t.study_id===r.study_id).slice(0,1).map(t=>`<a class="button secondary" href="#results/trial-${t.id}">查看同批次连续监测 →</a>`).join('');
  return `<div class="eyebrow">03 / RESULTS ARCHIVE · RUN ${r.id}</div><h1>${esc(r.name)} ${status(r.status)}</h1><div class="checksum">INPUT SHA256 ${r.checksum}</div><p>快照 ${r.snapshot.length} 条 · ${esc(r.created_at)} · ${r.review?'负责人已复核':'尚未复核'}</p>${r.status==='failed'?`<div class="error">计算失败：${esc(r.error)}。请到来源页修复数据，然后创建新运行。</div>`:r.result?`<section class="result-intro"><strong>${esc(r.result.interpretation)}</strong><p>${esc(r.result.caveat)}</p></section><h2>核心数值</h2>${table(['业务指标','计算值'],Object.entries(r.result.metrics).map(([k,v])=>[k,fmt(v)]))}<h2>${esc(r.result.table_title||'计算明细')}</h2>${table(r.result.columns,r.result.rows)}${r.result.chart?`<h2>${esc(r.result.chart.title)}</h2><div class="chart">${r.result.chart.points.map(([x,y])=>`<div class="chart-row"><span>${esc(fmt(x))}</span><i style="width:${Math.max(1,Math.min(100,Number(y)*100))}%"></i><b>${esc(fmt(y))}</b></div>`).join('')}</div>`:''}<div class="toolbar"><button id="export">下载核算 CSV</button><a class="button secondary" href="#review/${r.id}">打开业务复核 →</a>${trialLink}</div>`:'<p class="method-note">任务正在队列中。此页每3秒更新，结果写入数据库后展示。</p>'}<details><summary>本次冻结的参数与源记录</summary><pre>${esc(JSON.stringify({parameters:r.parameters,snapshot:r.snapshot},null,2))}</pre></details>`;
}

export function bind(){
  const exportButton=document.getElementById('export');
  if(exportButton)exportButton.onclick=async()=>{try{download(await runs.export(store.runId),'research-'+store.runId+'.csv');}catch(e){document.getElementById('notice').textContent=e.message;}};
  const look=document.getElementById('trial-look');
  if(look)look.onclick=async()=>{try{look.disabled=true;const key='look-'+Date.now()+'-'+Math.random().toString(36).slice(2,8);await monitor.look(store.trialId,key);}catch(e){document.getElementById('notice').textContent=e.message;}finally{setTimeout(()=>location.reload(),300);}};
  const seal=document.getElementById('trial-seal');
  if(seal)seal.onclick=async()=>{try{await monitor.seal(store.trialId);location.reload();}catch(e){document.getElementById('notice').textContent=e.message;}};
  document.querySelectorAll('[data-resume]').forEach(el=>el.onclick=async()=>{try{await monitor.resume(+el.dataset.resume);setTimeout(()=>location.reload(),300);}catch(e){document.getElementById('notice').textContent=e.message;}});
}
