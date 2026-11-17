"""Behaviour of continuous monitoring: accumulation, sealing, retries, recovery."""

import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.models.monitor import MonitorInclusion, MonitorLook, MonitorTrial
from src.models.observation import Observation
from src.services import monitor, studies
from src.services.audits import log
from tests.conftest import add_pair, add_study, all_inclusions


def test_evidence_is_the_replay_of_the_included_sign_sequence(db):
    study = add_study(db)
    signs = [0.3, -0.2, 0.5, 0.1, -0.4, 0.7, 0.2, -0.1, 0.6, 0.9]
    for index, difference in enumerate(signs):
        add_pair(db, study, f'X-{index}', 2.0, 2.0 + difference)
    add_pair(db, study, 'X-tie', 2.0, 2.0)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    detail = monitor.get_trial(db, trial['id'])
    sequence = [row['sign'] for row in detail['inclusions']]
    replayed = monitor.sequential.replay(sequence)
    assert detail['evidence']['pairs'] == len(sequence) == 11
    assert abs(replayed[-1]['log_e'] - detail['evidence']['log_e']) < 1e-9
    assert abs(sum(row['log_increment'] for row in detail['inclusions'])
               - detail['evidence']['log_e']) < 1e-9
    assert abs(monitor.sequential.closed_form_log_e(
        sum(1 for sign in sequence if sign > 0),
        sum(1 for sign in sequence if sign < 0),
    ) - detail['evidence']['log_e']) < 1e-9
    tie = [row for row in detail['inclusions'] if row['values']['after'] == 2.0][0]
    assert tie['sign'] == 0 and tie['log_increment'] == 0.0


class _NullRedis:
    @classmethod
    def from_url(cls, *args, **kwargs):
        return cls()

    def rpush(self, *args, **kwargs):
        return 1


def run_look(db, trial_id, key=None):
    look = monitor.create_look(db, trial_id, key, 'analyst@demo.local')
    return monitor.execute_look(db, look['id'])


def test_trial_locks_threshold_and_rejects_second_open_trial(db):
    study = add_study(db)
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    assert trial['alpha'] == 0.05
    assert trial['threshold'] == 20.0
    assert trial['seq'] == 0
    assert trial['sealed'] is False
    with pytest.raises(ValueError):
        monitor.create_trial(db, study.id, 'admin@demo.local')


def test_each_look_only_includes_new_complete_pairs(db):
    study = add_study(db)
    add_pair(db, study, 'P-1', 3.0, 3.4)
    add_pair(db, study, 'P-2', 4.0, 3.6)
    add_pair(db, study, 'P-3', 5.0, 5.1)
    db.add(Observation(study_id=study.id, source_key='P-4', values={'before': 2.0}))
    db.commit()

    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    first = run_look(db, trial['id'])
    assert first['status'] == 'completed'
    assert first['added'] == 3
    assert first['seq_after'] == 3
    detail = monitor.get_trial(db, trial['id'])
    assert detail['inclusion_scope']['included_source_keys'] == ['P-1', 'P-2', 'P-3']
    assert detail['inclusion_scope']['pending_incomplete_count'] == 1
    assert detail['inclusion_scope']['pending_complete_count'] == 0

    add_pair(db, study, 'P-5', 6.0, 6.5)
    add_pair(db, study, 'P-6', 7.0, 6.2)
    db.commit()
    second = run_look(db, trial['id'])
    assert second['added'] == 2
    assert second['seq_before'] == 3
    assert second['seq_after'] == 5
    assert [row.seq for row in all_inclusions(db, trial['id'])] == [1, 2, 3, 4, 5]

    third = run_look(db, trial['id'])
    assert third['added'] == 0
    assert third['seq_after'] == 5


def test_retrying_the_same_look_does_not_duplicate_inclusions(db):
    study = add_study(db)
    for index in range(4):
        add_pair(db, study, f'R-{index}', 2.0, 2.2)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    look = monitor.create_look(db, trial['id'], 'look-1', 'analyst@demo.local')
    assert monitor.execute_look(db, look['id'])['added'] == 4
    again = monitor.execute_look(db, look['id'])
    assert again['status'] == 'completed'
    assert len(all_inclusions(db, trial['id'])) == 4
    # A new look with a repeated request key resolves to the same durable look
    duplicate = monitor.create_look(db, trial['id'], 'look-1', 'analyst@demo.local')
    assert duplicate['id'] == look['id']
    assert len(all_inclusions(db, trial['id'])) == 4


def test_two_stale_executions_do_not_include_pairs_twice(db):
    study = add_study(db)
    for index in range(3):
        add_pair(db, study, f'S-{index}', 3.0, 3.3)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    first = monitor.create_look(db, trial['id'], 'a', 'analyst@demo.local')
    second = monitor.create_look(db, trial['id'], 'b', 'analyst@demo.local')
    monitor.execute_look(db, first['id'])
    monitor.execute_look(db, second['id'])
    detail = monitor.get_trial(db, trial['id'])
    assert detail['seq'] == 3
    assert len(all_inclusions(db, trial['id'])) == 3
    assert len({row.observation_id for row in all_inclusions(db, trial['id'])}) == 3


def test_concurrent_version_conflict_replays_from_fresh_state(db, monkeypatch):
    study = add_study(db)
    for index in range(6):
        add_pair(db, study, f'C-{index}', 4.0, 4.0 + 0.2 * index)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    look = monitor.create_look(db, trial['id'], 'conflict', 'analyst@demo.local')

    original = monitor.seq if hasattr(monitor, 'seq') else None

    from src.services import monitor as module

    real_apply = module._apply
    calls = {'count': 0}

    def racing_apply(session, item):
        calls['count'] += 1
        if calls['count'] == 1:
            # Someone else committed an identical new look first.
            rival = monitor.create_look(session, trial['id'], 'rival', 'analyst@demo.local')
            module.execute_look(session, rival['id'])
        return real_apply(session, item)

    monkeypatch.setattr(module, '_apply', racing_apply)
    result = monitor.execute_look(db, look['id'])
    assert result['status'] == 'completed'
    detail = monitor.get_trial(db, trial['id'])
    assert detail['seq'] == 6
    assert len(all_inclusions(db, trial['id'])) == 6
    assert len({row.observation_id for row in all_inclusions(db, trial['id'])}) == 6
    assert calls['count'] >= 2


def test_stop_freezes_inclusion_scope(db):
    study = add_study(db)
    for index in range(10):
        add_pair(db, study, f'T-{index}', 1.0, 2.0)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    result = run_look(db, trial['id'])
    assert result['added'] == 7
    detail = monitor.get_trial(db, trial['id'])
    assert detail['stop']['stopped'] is True
    assert detail['stop']['stop_seq'] == 7
    assert detail['evidence']['pairs'] == 7
    assert detail['inclusion_scope']['after_stop_pending'] == 3
    assert detail['stop']['decision'].startswith('停止判定')

    add_pair(db, study, 'T-10', 1.0, 2.0)
    db.commit()
    with pytest.raises(ValueError):
        monitor.create_look(db, trial['id'], None, 'analyst@demo.local')
    before = detail['evidence']['log_e']
    assert monitor.get_trial(db, trial['id'])['evidence']['log_e'] == before


def test_queueing_is_not_blocked_by_monitoring_and_runs_still_work(db, monkeypatch):
    """Registering a new pair never needs the monitoring lock."""
    study = add_study(db)
    add_pair(db, study, 'Q-1', 2.0, 2.4)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    studies.save_observation(
        db, study.id,
        type('Payload', (), {'source_key': 'Q-2', 'values': {'before': 1.0, 'after': 1.4}})(),
        'analyst@demo.local',
    )
    detail = monitor.get_trial(db, trial['id'])
    assert detail['inclusion_scope']['pending_complete_count'] == 1
    assert detail['evidence']['pairs'] == 1
    # A fixed-endpoint run over the same study is independent of the trial.
    from src.services import runs
    monkeypatch.setattr(runs, 'Redis', _NullRedis, raising=False)
    created = runs.create(
        db, type('Run', (), {'study_id': study.id, 'name': '固定终点', 'parameters': {}})(),
        type('User', (), {'email': 'analyst@demo.local', 'role': 'analyst'})(),
    )
    assert created['snapshot']
    assert created['status'] == 'queued'


def test_editing_an_included_pair_seals_and_does_not_extend_evidence(db):
    study = add_study(db)
    for index in range(4):
        add_pair(db, study, f'E-{index}', 3.0, 3.2)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    detail = monitor.get_trial(db, trial['id'])
    evidence = detail['evidence']['log_e']
    included = all_inclusions(db, trial['id'])
    frozen = included[1]

    row = db.scalar(select(Observation).where(Observation.source_key == 'E-1'))
    saved = studies.save_observation(
        db, study.id,
        type('Payload', (), {'source_key': 'E-1', 'values': {'before': 3.0, 'after': 0.5}},
             )(),
        'analyst@demo.local', row.id,
    )
    sealed = saved['monitor_seal']
    assert sealed['reason'] == 'included_observation_changed'
    detail = monitor.get_trial(db, trial['id'])
    assert detail['sealed'] is True
    assert detail['evidence']['log_e'] == evidence
    assert detail['evidence']['pairs'] == 4
    kept = [item for item in all_inclusions(db, trial['id']) if item.seq == frozen.seq][0]
    assert kept.values == frozen.values
    assert kept.values_sha256 == frozen.values_sha256
    changed_row = [row for row in detail['inclusions'] if row['seq'] == frozen.seq][0]
    assert changed_row['current_state'] == 'changed'
    assert changed_row['values'] == frozen.values

    add_pair(db, study, 'E-9', 2.0, 2.9)
    db.commit()
    with pytest.raises(ValueError):
        monitor.create_look(db, trial['id'], None, 'analyst@demo.local')
    assert monitor.get_trial(db, trial['id'])['evidence']['log_e'] == evidence


def test_renaming_an_included_pair_seals(db):
    study = add_study(db)
    add_pair(db, study, 'N-1', 2.0, 2.3)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    row = db.scalar(select(Observation).where(Observation.source_key == 'N-1'))
    saved = studies.save_observation(
        db, study.id,
        type('Payload', (), {'source_key': 'N-1-renamed', 'values': {'before': 2.0, 'after': 2.3}})(),
        'analyst@demo.local', row.id,
    )
    assert saved['monitor_seal']['reason'] == 'included_observation_changed'
    assert monitor.get_trial(db, trial['id'])['sealed'] is True


def test_saving_identical_values_does_not_seal(db):
    study = add_study(db)
    add_pair(db, study, 'I-1', 2.0, 2.3)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    row = db.scalar(select(Observation).where(Observation.source_key == 'I-1'))
    saved = studies.save_observation(
        db, study.id,
        type('Payload', (), {'source_key': 'I-1', 'values': {'before': 2.0, 'after': 2.3}})(),
        'analyst@demo.local', row.id,
    )
    assert 'monitor_seal' not in saved
    assert monitor.get_trial(db, trial['id'])['sealed'] is False


def test_deleting_an_included_pair_seals(db):
    study = add_study(db)
    add_pair(db, study, 'D-1', 1.0, 1.5)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    row = db.scalar(select(Observation).where(Observation.source_key == 'D-1'))
    deleted = studies.delete_observation(db, study.id, row.id, 'analyst@demo.local')
    assert deleted['monitor_seal']['reason'] == 'included_observation_deleted'
    assert monitor.get_trial(db, trial['id'])['sealed'] is True


def test_new_pairs_do_not_seal(db):
    study = add_study(db)
    add_pair(db, study, 'A-1', 1.0, 1.2)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    studies.save_observation(
        db, study.id,
        type('Payload', (), {'source_key': 'A-2', 'values': {'before': 1.0, 'after': 1.1}})(),
        'analyst@demo.local',
    )
    assert monitor.get_trial(db, trial['id'])['sealed'] is False


def test_deleting_a_not_yet_included_pair_does_not_seal(db):
    study = add_study(db)
    add_pair(db, study, 'B-1', 1.0, 1.2)
    extra = Observation(study_id=study.id, source_key='B-2', values={'before': 1.0})
    db.add(extra)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    studies.delete_observation(db, study.id, extra.id, 'analyst@demo.local')
    assert monitor.get_trial(db, trial['id'])['sealed'] is False


def test_interrupted_running_look_can_resume_without_losing_or_duplicating(db):
    study = add_study(db)
    for index in range(3):
        add_pair(db, study, f'W-{index}', 5.0, 5.1)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    look = monitor.create_look(db, trial['id'], 'crash', 'analyst@demo.local')
    # Simulate a worker that died right after marking the look running.
    look_obj = db.get(MonitorLook, look['id'])
    look_obj.status = 'running'
    db.commit()
    requeued = monitor.requeue_unfinished(db, client=None)
    assert look['id'] in requeued
    detail = monitor.get_trial(db, trial['id'])
    assert detail['sealed'] is False
    resumed = monitor.execute_look(db, look['id'])
    assert resumed['status'] == 'completed'
    assert resumed['added'] == 3


def test_late_execution_cannot_rewind_evidence(db):
    study = add_study(db)
    for index in range(3):
        add_pair(db, study, f'L-{index}', 6.0, 6.1)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    stale = monitor.create_look(db, trial['id'], 'stale', 'analyst@demo.local')
    monitor.execute_look(db, stale['id'])
    for index in range(3, 6):
        add_pair(db, study, f'L-{index}', 6.0, 6.1)
    db.commit()
    fresh = run_look(db, trial['id'])
    assert fresh['added'] == 3
    evidence = monitor.get_trial(db, trial['id'])['evidence']['log_e']
    # Re-delivering the already completed stale look changes nothing.
    assert monitor.execute_look(db, stale['id'])['status'] == 'completed'
    assert monitor.get_trial(db, trial['id'])['evidence']['log_e'] == evidence


def test_late_execution_cannot_lift_a_seal(db):
    study = add_study(db)
    add_pair(db, study, 'Z-1', 2.0, 2.2)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    queued = monitor.create_look(db, trial['id'], 'late', 'analyst@demo.local')
    monitor.execute_look(db, trial['id'] and monitor.create_look(db, trial['id'], 'seed', 'x')['id'])
    row = db.scalar(select(Observation).where(Observation.source_key == 'Z-1'))
    studies.save_observation(
        db, study.id,
        type('Payload', (), {'source_key': 'Z-1', 'values': {'before': 2.0, 'after': 0.1}})(),
        'analyst@demo.local', row.id,
    )
    assert monitor.get_trial(db, trial['id'])['sealed'] is True
    log_e = monitor.get_trial(db, trial['id'])['evidence']['log_e']
    executed = monitor.execute_look(db, queued['id'])
    assert executed['added'] == 0
    detail = monitor.get_trial(db, trial['id'])
    assert detail['sealed'] is True
    assert detail['evidence']['log_e'] == log_e


def test_seal_is_detected_by_a_look_if_the_editor_bypassed_the_service(db):
    study = add_study(db)
    add_pair(db, study, 'M-1', 2.0, 2.4)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    row = db.scalar(select(Observation).where(Observation.source_key == 'M-1'))
    row.values = {'before': 2.0, 'after': 0.4}
    db.commit()
    result = run_look(db, trial['id'])
    assert result['added'] == 0
    detail = monitor.get_trial(db, trial['id'])
    assert detail['sealed'] is True
    assert detail['evidence']['pairs'] == 1


def test_manual_seal_allows_a_new_trial_but_keeps_old_evidence(db):
    study = add_study(db)
    add_pair(db, study, 'V-1', 3.0, 3.5)
    db.commit()
    trial = monitor.create_trial(db, study.id, 'admin@demo.local')
    run_look(db, trial['id'])
    old = monitor.get_trial(db, trial['id'])['evidence']['pairs']
    monitor.seal_trial(db, trial['id'], 'admin@demo.local')
    with pytest.raises(ValueError):
        monitor.seal_trial(db, trial['id'], 'admin@demo.local')
    fresh = monitor.create_trial(db, study.id, 'admin@demo.local')
    assert fresh['seq'] == 0
    assert monitor.get_trial(db, trial['id'])['evidence']['pairs'] == old
    assert monitor.get_trial(db, trial['id'])['sealed'] is True
