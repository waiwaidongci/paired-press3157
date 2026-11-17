"""HTTP-level checks: roles, queue behaviour, recovery and result payload shape."""

import pytest
from fastapi.testclient import TestClient

from src.services import monitor
from tests.conftest import add_pair, add_study


class FakeRedis:
    def __init__(self):
        self.lists = {}
        self.expiry = {}

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lpop(self, key):
        values = self.lists.get(key) or []
        return values.pop(0) if values else None

    def incr(self, key):
        self.lists[key] = self.lists.get(key, 0) + 1
        return self.lists[key]

    def expire(self, key, seconds):
        return True

    def ping(self):
        return True


@pytest.fixture
def client(monkeypatch):
    from src.middlewares import rate_limit
    from src.routes import research
    from src.utils import security
    from src.models.user import User

    monkeypatch.setattr(rate_limit, 'redis', FakeRedis())

    from src.config.database import Session
    from src.utils.security import hash_password
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer'):
            db.add(User(email=f'{role}@demo.local', role=role,
                        password_hash=hash_password('Research2026!')))
        db.commit()

    from src.main import app

    class RecordingQueue(list):
        pass

    queued = RecordingQueue()
    monkeypatch.setattr(monitor, '_enqueue', lambda look_id: queued.append(look_id))

    with TestClient(app) as test_client:
        test_client.queue = queued
        yield test_client


def token(client, role):
    response = client.post('/auth/login', json={'email': f'{role}@demo.local',
                                                'password': 'Research2026!'})
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


def make_study(db, name='批次'):
    study = add_study(db, name=name)
    add_pair(db, study, f'{name}-1', 3.0, 3.4)
    add_pair(db, study, f'{name}-2', 4.0, 3.6)
    db.commit()
    return study


def test_viewer_cannot_start_trial_but_can_read(client, db):
    study = make_study(db)
    viewer = token(client, 'viewer')
    analyst = token(client, 'analyst')
    started = client.post('/monitor/trials', json={'study_id': study.id}, headers=analyst)
    assert started.status_code == 200, started.text
    trial_id = started.json()['id']
    assert started.json()['alpha'] == 0.05
    assert started.json()['threshold'] == 20.0
    assert client.post('/monitor/trials', json={'study_id': study.id},
                       headers=viewer).status_code == 403
    fetched = client.get(f'/monitor/trials/{trial_id}', headers=viewer)
    assert fetched.status_code == 200
    body = fetched.json()
    assert body['inclusion_scope']['included_count'] == 0
    assert body['stop']['stopped'] is False
    assert body['basis']


def test_look_flow_through_http(client, db):
    study = make_study(db)
    admin = token(client, 'admin')
    trial = client.post('/monitor/trials', json={'study_id': study.id}, headers=admin).json()
    first_look = trial['looks'][0]
    queued = monitor.execute_look(db, first_look['id'])
    assert queued['added'] == 2

    requested = client.post(f"/monitor/trials/{trial['id']}/looks",
                            json={'request_key': 'ui-1'}, headers=admin)
    assert requested.status_code == 200
    look_id = requested.json()['id']
    executed = client.post(f'/monitor/execute/{look_id}', headers=admin)
    assert executed.status_code == 200
    assert executed.json()['status'] == 'completed'
    fetched = client.get(f"/monitor/trials/{trial['id']}", headers=admin).json()
    assert fetched['evidence']['pairs'] == 2
    assert fetched['inclusion_scope']['included_count'] == 2
    assert fetched['looks']
    assert 'decision' in fetched['stop']


def test_duplicate_request_key_returns_the_same_look(client, db):
    study = make_study(db)
    admin = token(client, 'admin')
    trial = client.post('/monitor/trials', json={'study_id': study.id}, headers=admin).json()
    first = client.post(f"/monitor/trials/{trial['id']}/looks", json={'request_key': 'k'},
                        headers=admin).json()
    second = client.post(f"/monitor/trials/{trial['id']}/looks", json={'request_key': 'k'},
                         headers=admin).json()
    assert first['id'] == second['id']


def test_edit_through_http_returns_seal_and_trial_reports_it(client, db):
    study = make_study(db)
    admin = token(client, 'admin')
    trial = client.post('/monitor/trials', json={'study_id': study.id}, headers=admin).json()
    monitor.execute_look(db, trial['looks'][0]['id'])
    from src.models.observation import Observation
    from sqlalchemy import select
    row = db.scalar(select(Observation).where(Observation.source_key == '批次-1'))
    edited = client.put(f'/studies/{study.id}/observations/{row.id}',
                        json={'source_key': '批次-1', 'values': {'before': 3.0, 'after': 1.0}},
                        headers=admin)
    assert edited.status_code == 200
    assert edited.json()['monitor_seal']['reason'] == 'included_observation_changed'
    fetched = client.get(f"/monitor/trials/{trial['id']}", headers=admin).json()
    assert fetched['sealed'] is True
    assert fetched['inclusion_scope']['included_count'] == 2


def test_unknown_trial_is_404(client):
    assert client.get('/monitor/trials/999', headers=token(client, 'admin')).status_code == 404


def test_existing_fixed_endpoint_results_stay_readable(client, db, monkeypatch):
    from src.services import runs as run_service
    from src.models.run import Run

    study = make_study(db)
    admin = token(client, 'admin')

    class FakeRedis:
        pushed = []

        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def from_url(cls, *args, **kwargs):
            return cls()

        def rpush(self, key, value):
            FakeRedis.pushed.append((key, value))
            return len(FakeRedis.pushed)

    monkeypatch.setattr(run_service, 'Redis', FakeRedis, raising=False)

    def fake_execute(session, run_id):
        run = session.get(Run, run_id)
        run.result = {'metrics': {'配对样本量': 2}, 'interpretation': '固定终点分析',
                      'caveat': '说明', 'columns': ['a'], 'rows': [[1]]}
        run.status = 'completed'
        session.commit()

    started = client.post('/runs', json={'study_id': study.id, 'name': '固定终点',
                                         'parameters': {'resamples': 1000}}, headers=admin)
    assert started.status_code == 200, started.text
    run_id = started.json()['id']
    fake_execute(db, run_id)
    fetched = client.get(f'/runs/{run_id}', headers=admin)
    assert fetched.status_code == 200
    assert fetched.json()['result']['interpretation'] == '固定终点分析'
