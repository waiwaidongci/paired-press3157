"""Continuous monitoring of accumulating matched pairs.

Design rules implemented here:

* A trial locks alpha = 0.05 and the evidence threshold M = 20 when it is
  created; the threshold never moves.
* Every look includes exactly the complete pairs that have not yet been
  included, in observation-id order.  Re-running a look, retrying a failed
  look or delivering a stale execution can never include a pair twice
  (unique constraints plus an optimistic version counter).
* If an already included pair is edited or deleted, the trial is sealed.
  The new values are not appended to the old evidence; the inclusion
  snapshot is preserved.
* A late execution may not move the cumulative sequence backwards and may
  not clear the seal.  Both are enforced by conditional UPDATE statements.
"""

import os

from redis import Redis
from sqlalchemy import func, select, update as sql_update
from sqlalchemy.exc import IntegrityError, OperationalError

from src.models.monitor import MonitorInclusion, MonitorLook, MonitorTrial
from src.models.observation import Observation
from src.models.study import Study
from src.services import sequential
from src.services.audits import log
from src.utils.serializers import checksum, now, record

MAX_ATTEMPTS = 8
QUEUE_NAME = 'monitor-queue'


class ConcurrentUpdate(Exception):
    """Another execution advanced the trial; recompute from fresh state."""


def _trial_or_404(db, trial_id):
    trial = db.get(MonitorTrial, trial_id)
    if not trial:
        raise LookupError('连续监测试验不存在')
    return trial


def _open_trial(db, study_id):
    return db.scalar(
        select(MonitorTrial)
        .where(MonitorTrial.study_id == study_id, MonitorTrial.sealed.is_(False))
        .order_by(MonitorTrial.id.desc())
    )


def lock_open_trial(db, study_id):
    """Lock the study's open trial before changing an observation.

    Taking the trial lock first and the observation lock second gives the
    editor and the monitoring executor the same lock order (trial then
    observations), so the two paths cannot deadlock and an edit can never slip
    between a look's completeness check and its commit.
    """
    return db.scalar(
        select(MonitorTrial)
        .where(MonitorTrial.study_id == study_id, MonitorTrial.sealed.is_(False))
        .order_by(MonitorTrial.id.desc())
        .with_for_update()
    )


def canonical_values(values):
    """Normalize a pair for hashing so 2 and 2.0 are the same observation."""
    if not isinstance(values, dict):
        return values
    normalized = {}
    for key, value in values.items():
        if isinstance(value, bool):
            normalized[key] = value
        elif isinstance(value, (int, float)):
            normalized[key] = float(value)
        else:
            normalized[key] = value
    return normalized


def values_digest(values):
    return checksum(canonical_values(values))


def paired_difference(values):
    """Return after - before for a complete, finite pair, else raise."""
    if not isinstance(values, dict) or 'before' not in values or 'after' not in values:
        raise ValueError('配对字段不完整')
    try:
        before = float(values['before'])
        after = float(values['after'])
    except (TypeError, ValueError):
        raise ValueError('配对数值不合法')
    if not (before == before and after == after):  # NaN check
        raise ValueError('配对数值不合法')
    if before in (float('inf'), float('-inf')) or after in (float('inf'), float('-inf')):
        raise ValueError('配对数值不合法')
    return after - before


def _evidence(trial):
    view = sequential.evidence_view(float(trial.log_e))
    view['pairs'] = int(trial.seq)
    view['ties'] = int(trial.ties)
    view['positive_signs'] = int(trial.positive)
    view['negative_signs'] = int(trial.nonzero - trial.positive)
    view['alpha_locked'] = float(trial.alpha)
    return view


def _stop_text(seq, log_e, threshold, alpha):
    evidence = sequential.evidence_view(log_e)
    return (
        f'停止判定：累计第 {seq} 对完整纸样时双侧e过程证据 M={evidence["evidence_label"]} '
        f'达到启动时锁定的 1/α={threshold:g}（α={alpha:g}）。'
        '该判定由预先锁定的双侧门槛触发；此后登记或尚未纳入的数据不再进入本试验证据。'
    )


def _continue_text(seq):
    return f'继续监测：累计 {seq} 对完整纸样，证据尚未达到锁定门槛。'


def _seal(db, trial, reason, detail):
    """Seal a trial for good.  Returns the decision text or None if already sealed."""
    if trial.sealed:
        return None
    evidence = sequential.evidence_view(float(trial.log_e))
    text = (
        f'试验已封存：{detail}。已纳入的 {trial.seq} 对纸样证据保留在封存时的状态'
        f'（M={evidence["evidence_label"]}），修改后的数据不续入旧证据。'
    )
    decision = f'{trial.decision} {text}' if trial.decision else text
    result = db.execute(
        sql_update(MonitorTrial)
        .where(MonitorTrial.id == trial.id, MonitorTrial.sealed.is_(False))
        .values(
            sealed=True,
            status='sealed',
            sealed_at=now(),
            sealed_reason=reason,
            decision=decision,
            version=MonitorTrial.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None
    db.expire(trial)
    return text


def seal_trial(db, trial_id, user, reason='manual_archive'):
    """Archive a trial manually (e.g. a stopped trial that should be reopened)."""
    trial = _trial_or_404(db, trial_id)
    text = _seal(db, trial, reason, '负责人手动归档')
    if text is None:
        raise ValueError('试验已封存，不能重复封存')
    log(db, user, 'monitor.seal', f'{trial.id}: {reason}')
    db.commit()
    return get_trial(db, trial_id)

def seal_study_trial(db, study_id, observation_id, reason, detail, user=None):
    """Seal the open trial of a study if the given observation was included.

    The caller must already hold the trial lock (``lock_open_trial``) so the
    check-then-seal happens under the same lock a look takes before appending.
    A rename is a change of evidence identity too: the frozen source key no
    longer matches the row.
    """
    trial = _open_trial(db, study_id)
    if not trial:
        return None
    included = db.scalar(
        select(MonitorInclusion).where(
            MonitorInclusion.trial_id == trial.id,
            MonitorInclusion.observation_id == observation_id,
        )
    )
    if not included:
        return None
    text = _seal(db, trial, reason, detail)
    if text is None:
        return None
    log(db, user if user is not None else 'monitor', 'monitor.seal',
        f'{trial.id}: {reason} ({detail})')
    return {
        'trial_id': trial.id,
        'reason': reason,
        'detail': detail,
        'decision': text,
    }


def create_trial(db, study_id, user):
    study = db.get(Study, study_id)
    if not study:
        raise LookupError('业务批次不存在')
    if _open_trial(db, study_id):
        raise ValueError('该批次已有未封存的连续监测试验；封存后才能启动新试验')
    trial = MonitorTrial(
        study_id=study_id,
        status='open',
        alpha=sequential.ALPHA,
        threshold=sequential.THRESHOLD,
        method=sequential.METHOD,
        basis=sequential.BASIS,
        seq=0,
        log_e=0.0,
        nonzero=0,
        positive=0,
        ties=0,
        version=0,
        sealed=False,
        created_at=now(),
    )
    db.add(trial)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ValueError('该批次已有未封存的连续监测试验；封存后才能启动新试验')
    log(db, user, 'monitor.start', f'{trial.id} / study {study_id} / alpha 0.05')
    look = _new_look(db, trial, request_key=None)
    db.commit()
    _enqueue(look.id)
    return get_trial(db, trial.id)


def _new_look(db, trial, request_key):
    look = MonitorLook(
        trial_id=trial.id,
        request_key=request_key,
        status='queued',
        added=0,
        seq_before=trial.seq,
        seq_after=trial.seq,
        log_e_after=trial.log_e,
        decision='等待执行',
        created_at=now(),
    )
    db.add(look)
    db.flush()
    return look


def _new_look(db, trial, request_key):
    look = MonitorLook(
        trial_id=trial.id,
        request_key=request_key,
        status='queued',
        added=0,
        seq_before=trial.seq,
        seq_after=trial.seq,
        log_e_after=trial.log_e,
        decision='等待执行',
        created_at=now(),
    )
    db.add(look)
    db.flush()
    return look


def _create_queued_look(db, trial, request_key, user):
    look = _new_look(db, trial, request_key)
    log(db, user, 'monitor.look', f'{trial.id} / look {look.id}')
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if not request_key:
            raise
        existing = db.scalar(
            select(MonitorLook).where(
                MonitorLook.trial_id == trial.id,
                MonitorLook.request_key == request_key,
            )
        )
        if existing:
            return record(existing)
        raise
    _enqueue(look.id)
    return record(look)


def create_look(db, trial_id, request_key, user):
    trial = _trial_or_404(db, trial_id)
    # A look is only a request: the executor re-checks the seal and the stop
    # decision under the trial lock, so a look that races with a seal or a
    # stop adds nothing and cannot rewind the evidence.  Rejecting the obvious
    # cases here just gives the UI a clear message.
    if trial.sealed:
        raise ValueError('试验已封存，不能新增查看；如需继续研究请启动新试验')
    if trial.stop_seq is not None:
        raise ValueError('停止判定已触发，不能再新增查看')
    if request_key:
        existing = db.scalar(
            select(MonitorLook).where(
                MonitorLook.trial_id == trial.id,
                MonitorLook.request_key == request_key,
            )
        )
        if existing:
            return record(existing)
    return _create_queued_look(db, trial, request_key, user)


def _enqueue(look_id):
    url = os.environ.get('REDIS_URL')
    if not url:
        return False
    try:
        Redis.from_url(url).rpush(QUEUE_NAME, look_id)
        return True
    except Exception:
        # The look is durably queued in the database.  A worker restart (or an
        # explicit resume call) re-enqueues it; nothing is lost.
        return False


def requeue_unfinished(db, client=None):
    """Requeue looks orphaned by a worker restart.  Never resets a seal.

    A look is durable in the database; the Redis list is only a delivery hint.
    IDs already waiting in the list are skipped, so a periodic sweep is cheap
    and does not duplicate work.  Re-execution of a completed look is a no-op,
    and a stale execution cannot rewind the cumulative evidence.
    """
    looks = list(db.scalars(
        select(MonitorLook).where(MonitorLook.status.in_(['queued', 'running']))
    ))
    client = client or (Redis.from_url(os.environ['REDIS_URL']) if os.environ.get('REDIS_URL') else None)
    waiting = set()
    if client:
        try:
            waiting = {int(value) for value in client.lrange(QUEUE_NAME, 0, -1)}
        except Exception:
            waiting = set()
    pushed = []
    for look in looks:
        if look.id in waiting:
            continue
        if client:
            client.rpush(QUEUE_NAME, look.id)
        pushed.append(look.id)
    return pushed


def resume_look(db, look_id, user):
    look = db.get(MonitorLook, look_id)
    if not look:
        raise LookupError('查看记录不存在')
    if look.status == 'completed':
        return record(look)
    look.status = 'queued'
    look.error = ''
    log(db, user, 'monitor.resume', look.id)
    db.commit()
    _enqueue(look.id)
    return record(look)


def _finish_look(db, look, trial, added, message, decision):
    look.status = 'completed'
    look.added = added
    look.seq_before = int(look.seq_before)
    look.seq_after = int(trial.seq)
    look.log_e_after = float(trial.log_e)
    look.message = message
    look.decision = decision
    look.completed_at = now()


def _load_inclusions(db, trial):
    return list(db.scalars(
        select(MonitorInclusion)
        .where(MonitorInclusion.trial_id == trial.id)
        .order_by(MonitorInclusion.seq)
    ))


def _apply(db, look):
    trial = db.scalar(
        select(MonitorTrial).where(MonitorTrial.id == look.trial_id).with_for_update()
    )
    if not trial:
        raise LookupError('连续监测试验不存在')
    look.seq_before = int(trial.seq)
    if trial.sealed:
        _finish_look(
            db, look, trial, 0,
            '试验已封存，本次查看不纳入任何纸样；封存时的证据保持不变。',
            trial.decision or '试验已封存。'
        )
        return
    if trial.stop_seq is not None or trial.status == 'stopped':
        _finish_look(
            db, look, trial, 0,
            '停止判定已触发，本次查看不纳入任何新纸样。',
            trial.decision or _stop_text(trial.stop_seq or trial.seq, trial.log_e,
                                         trial.threshold, trial.alpha),
        )
        return

    inclusions = _load_inclusions(db, trial)
    included = {item.observation_id: item for item in inclusions}

    # Lock every observation of the study while reading.  An edit/delete of an
    # included row takes the same lock, so either the inclusion is visible to
    # the editor (which then seals) or the changed values are visible here.
    observations = list(db.scalars(
        select(Observation)
        .where(Observation.study_id == trial.study_id)
        .order_by(Observation.id)
        .with_for_update()
    ))
    current = {item.id: item for item in observations}

    drift = []
    for item in inclusions:
        row = current.get(item.observation_id)
        if row is None:
            drift.append(f'#{item.source_key} 已删除')
        elif values_digest(row.values) != item.values_sha256 or row.source_key != item.source_key:
            drift.append(f'#{item.source_key} 已修改')
    if drift:
        detail = '、'.join(drift[:5]) + ('等' if len(drift) > 5 else '')
        text = _seal(db, trial, 'included_observation_changed', f'已纳入纸样 {detail}')
        db.flush()
        refreshed = db.get(MonitorTrial, trial.id)
        _finish_look(
            db, look, refreshed, 0,
            f'检测到已纳入纸样发生变化（{detail}），试验已封存，本次不追加证据。',
            text or refreshed.decision or '试验已封存。',
        )
        return

    candidates = []
    incomplete = 0
    for row in observations:
        if row.id in included:
            continue
        try:
            difference = paired_difference(row.values)
        except ValueError:
            incomplete += 1
            continue
        candidates.append((row, difference))

    skipped = f'；跳过 {incomplete} 条不完整记录' if incomplete else ''
    if not candidates:
        decision = trial.decision or _continue_text(trial.seq)
        _finish_look(
            db, look, trial, 0,
            f'本次查看没有新增完整纸样（累计 {trial.seq} 对）{skipped}。',
            decision,
        )
        return

    state = sequential.initial_state()
    state.update(
        seq=int(trial.seq),
        log_e=float(trial.log_e),
        nonzero=int(trial.nonzero),
        positive=int(trial.positive),
        ties=int(trial.ties),
    )
    added = 0
    deferred = 0
    for row, difference in candidates:
        state = sequential.advance(state, difference)
        db.add(MonitorInclusion(
            trial_id=trial.id,
            look_id=look.id,
            observation_id=row.id,
            source_key=row.source_key,
            seq=state['seq'],
            values=row.values,
            values_sha256=values_digest(row.values),
            sign=state['sign'],
            log_increment=state['log_increment'],
            log_e=state['log_e'],
            included_at=now(),
        ))
        added += 1
        if sequential.crossed(state['log_e']):
            # The evidence stops exactly at the first crossing.  Pairs later in
            # this look stay outside the trial: including them after the
            # decision would blur the inclusion scope, and Ville's inequality
            # already holds at this stopping time.
            deferred = len(candidates) - added
            break
    db.flush()

    values = dict(
        seq=state['seq'],
        log_e=state['log_e'],
        nonzero=state['nonzero'],
        positive=state['positive'],
        ties=state['ties'],
        version=MonitorTrial.version + 1,
    )
    stopped = sequential.crossed(state['log_e'])
    if stopped:
        values.update(
            status='stopped',
            stop_seq=state['seq'],
            decision=_stop_text(state['seq'], state['log_e'], trial.threshold, trial.alpha),
        )
    result = db.execute(
        sql_update(MonitorTrial)
        .where(
            MonitorTrial.id == trial.id,
            MonitorTrial.version == trial.version,
            MonitorTrial.sealed.is_(False),
            MonitorTrial.stop_seq.is_(None),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ConcurrentUpdate()
    db.flush()
    db.expire(trial)
    trial = db.get(MonitorTrial, trial.id)
    decision = trial.decision or _continue_text(trial.seq)
    deferred_note = f'；停止判定后另有 {deferred} 对不再纳入' if deferred else ''
    _finish_look(
        db, look, trial, added,
        f'本次纳入 {added} 对新增完整纸样（累计 {trial.seq} 对）{skipped}{deferred_note}。',
        decision,
    )


def execute_look(db, look_id):
    """Execute (or resume) one durable look.  Safe to call repeatedly."""
    look = db.get(MonitorLook, look_id)
    if not look:
        raise LookupError('查看记录不存在')
    if look.status == 'completed':
        return record(look)
    look.status = 'running'
    look.error = ''
    db.commit()

    last_error = None
    for _ in range(MAX_ATTEMPTS):
        try:
            _apply(db, look)
            db.commit()
            return record(db.get(MonitorLook, look_id))
        except (ConcurrentUpdate, IntegrityError, OperationalError) as error:
            last_error = error
            db.rollback()
            look = db.get(MonitorLook, look_id)
            if not look or look.status == 'completed':
                return record(look) if look else None
            look.status = 'running'
            db.commit()
    look = db.get(MonitorLook, look_id)
    look.status = 'failed'
    look.error = f'并发冲突，已放弃本次查看：{last_error}'
    look.completed_at = now()
    db.commit()
    return record(look)


def observation_scope(db, trial, observations, inclusions):
    by_id = {item.observation_id: item for item in inclusions}
    current = {item.id: item for item in observations}
    rows = []
    for item in inclusions:
        row = current.get(item.observation_id)
        if row is None:
            state = 'deleted'
        elif values_digest(row.values) != item.values_sha256 or row.source_key != item.source_key:
            state = 'changed'
        else:
            state = 'included'
        rows.append({
            'seq': item.seq,
            'observation_id': item.observation_id,
            'source_key': item.source_key,
            'values': item.values,
            'values_sha256': item.values_sha256,
            'sign': item.sign,
            'log_increment': item.log_increment,
            'log_e': item.log_e,
            'evidence_label': sequential.evidence_view(item.log_e)['evidence_label'],
            'included_at': item.included_at,
            'look_id': item.look_id,
            'current_state': state,
        })
    pending = [item for item in observations if item.id not in by_id]
    pending_complete = 0
    pending_incomplete = 0
    for item in pending:
        try:
            paired_difference(item.values)
            pending_complete += 1
        except ValueError:
            pending_incomplete += 1
    return rows, {
        'included_count': len(rows),
        'included_first_seq': rows[0]['seq'] if rows else 0,
        'included_last_seq': rows[-1]['seq'] if rows else 0,
        'included_source_keys': [row['source_key'] for row in rows],
        'observation_id_min': min((row['observation_id'] for row in rows), default=None),
        'observation_id_max': max((row['observation_id'] for row in rows), default=None),
        'study_observation_count': len(observations),
        'pending_complete_count': pending_complete,
        'pending_incomplete_count': pending_incomplete,
        'excluded_not_yet_included': [item.source_key for item in pending],
    }


def get_trial(db, trial_id):
    trial = _trial_or_404(db, trial_id)
    study = db.get(Study, trial.study_id)
    inclusions = _load_inclusions(db, trial)
    observations = list(db.scalars(
        select(Observation)
        .where(Observation.study_id == trial.study_id)
        .order_by(Observation.id)
    ))
    rows, scope = observation_scope(db, trial, observations, inclusions)
    scope['after_stop_pending'] = 0
    if trial.stop_seq is not None:
        scope['after_stop_pending'] = sum(
            1 for item in observations
            if item.id not in {row['observation_id'] for row in rows}
        )
    looks = list(db.scalars(
        select(MonitorLook)
        .where(MonitorLook.trial_id == trial.id)
        .order_by(MonitorLook.id.desc())
    ))
    data = record(trial)
    data['study_name'] = study.name if study else ''
    data['evidence'] = _evidence(trial)
    data['stop'] = {
        'stopped': trial.stop_seq is not None,
        'stop_seq': trial.stop_seq,
        'alpha': trial.alpha,
        'threshold': trial.threshold,
        'decision': trial.decision,
    }
    data['inclusion_scope'] = scope
    data['inclusions'] = rows
    data['looks'] = [record(item) for item in looks[:20]]
    data['pending_looks'] = sum(1 for item in looks if item.status in ('queued', 'running'))
    data['seal'] = {
        'sealed': trial.sealed,
        'sealed_at': trial.sealed_at,
        'reason': trial.sealed_reason,
    }
    return data


def list_trials(db, study_id=None):
    query = select(MonitorTrial).order_by(MonitorTrial.id.desc())
    if study_id:
        query = query.where(MonitorTrial.study_id == study_id)
    result = []
    for trial in db.scalars(query):
        study = db.get(Study, trial.study_id)
        count = db.scalar(
            select(func.count()).select_from(MonitorInclusion)
            .where(MonitorInclusion.trial_id == trial.id)
        )
        data = record(trial)
        data['study_name'] = study.name if study else ''
        data['evidence'] = _evidence(trial)
        data['included_count'] = int(count or 0)
        data['stop'] = {
            'stopped': trial.stop_seq is not None,
            'stop_seq': trial.stop_seq,
            'decision': trial.decision,
        }
        result.append(data)
    return result
