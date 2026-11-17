import {DOMAIN} from '../constants/domain.js';
import {store} from '../stores/research.js';
import {esc} from '../utils/format.js';
import {runList} from '../components/common/run-list.js';
import {table} from '../components/common/table.js';
import {trialState} from '../components/common/monitor-summary.js';
export function render(){const active=store.trials.filter(t=>!t.sealed);return `<div class="eyebrow">FIELD NOTES / ${DOMAIN.direction}</div><section class="hero"><div><h1>${DOMAIN.title}</h1><p class="lead">${DOMAIN.scenario}</p><a class="button" href="#analysis">发起新的研究 →</a></div><aside><span class="folio">${DOMAIN.id.slice(-2)}</span><p>${DOMAIN.algorithm}</p><small>固定种子42 · 真实Python计算 · 输入快照可追溯 · 连续监测锁定α=0.05</small></aside></section><section class="strip"><span>${store.studies.length} 个${DOMAIN.study_label}</span><span>${store.studies.reduce((n,s)=>n+s.row_count,0)} 条业务观测</span><span>${store.runs.length} 次固定终点计算</span><span>${active.length} 项监测进行中</span></section>${active.length?`<h2>连续监测 <small>累计证据与纳入范围</small></h2>${table(['试验 / 批次','状态','累计证据','已纳入','停止判定'],active.map(t=>[{html:`<a href="#results/trial-${t.id}">#${t.id} ${esc(t.study_name)}</a>`},{html:trialState(t)},esc(t.evidence?.evidence_label),`${t.included_count} 对`,t.stop?.stopped?`第 ${t.stop.stop_seq} 对停止`:'继续监测']))}`:''}<h2>研究进度 <small>从来源到复核的同一条链路</small></h2>${runList()}<h2>来源索引</h2>${table([DOMAIN.study_label,'来源文件','观测数'],store.studies.map(s=>[s.name,s.source_filename,s.row_count]))}`;}
export function bind(){}
