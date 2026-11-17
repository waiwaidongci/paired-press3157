import {esc,fmt} from '../../utils/format.js';
import {table} from './table.js';

const stateLabel={included:'已纳入',changed:'纳入后已修改',deleted:'纳入后已删除'};

export function evidenceSummary(trial){
  const e=trial.evidence||{};
  const scope=trial.inclusion_scope||{};
  return table(['累计证据','停止判定','纳入范围'],[
    [
      {html:`<strong>M = ${esc(e.evidence_label??'1')}</strong><br><small>log e = ${esc(fmt(e.log_e))} · 门槛 1/α = ${esc(fmt(trial.threshold))}</small><br><small>α 启动时锁定 ${esc(fmt(trial.alpha))} · ${esc(trial.method||'')}</small>`},
      {html:trial.stop?.stopped?`<span class="status completed">已停止</span><br><small>第 ${esc(trial.stop.stop_seq)} 对时触发</small>`:'<span class="status">继续监测</span>'},
      {html:`<small>已纳入 ${esc(scope.included_count??trial.seq??0)} 对，序号 ${esc(scope.included_first_seq??0)}–${esc(scope.included_last_seq??0)}；批次共 ${esc(scope.study_observation_count??'—')} 条记录；待纳入完整 ${esc(scope.pending_complete_count??0)} 条、不完整 ${esc(scope.pending_incomplete_count??0)} 条${scope.after_stop_pending?`；停止后另有 ${esc(scope.after_stop_pending)} 条不再纳入`:''}</small>`},
    ],
  ]);
}

export function trialState(trial){
  if(trial.sealed) return `<span class="status failed">已封存</span>`;
  if(trial.stop?.stopped) return `<span class="status completed">已停止纳入</span>`;
  return `<span class="status running">监测中</span>`;
}

export function inclusionTable(trial){
  const rows=(trial.inclusions||[]).map(item=>[
    item.seq,item.source_key,
    {html:`<small>原 ${esc(fmt(item.values?.before))} / 新 ${esc(fmt(item.values?.after))}</small>`},
    item.sign>0?'+':item.sign<0?'−':'0',
    fmt(item.log_increment),
    item.evidence_label,
    stateLabel[item.current_state]||item.current_state,
  ]);
  return table(['序号','纸样编号','冻结数值','符号','本次增量 log e','累计 M','状态'],rows);
}

export function scopeNote(trial){
  const scope=trial.inclusion_scope;
  if(!scope) return '';
  const pending=(scope.excluded_not_yet_included||[]).slice(0,40);
  return `<details><summary>纳入范围明细（未纳入 ${(scope.excluded_not_yet_included||[]).length} 条）</summary><p><small>纳入序号 ${scope.included_first_seq}–${scope.included_last_seq}，按登记顺序；不完整记录不会进入证据。${pending.length?'未纳入：'+esc(pending.join('、')):'所有完整纸样均已纳入。'}</small></p></details>`;
}
