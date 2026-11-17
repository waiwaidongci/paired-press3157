import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_TMP = Path(tempfile.mkdtemp(prefix='paired-press-tests-'))
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_TMP / "test.db"}')
os.environ.setdefault('REDIS_URL', '')
os.environ.setdefault('JWT_SECRET', 'test-secret')

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from src.config.database import Base, Session, engine  # noqa: E402
from src.models import audit, monitor, observation, review, run, study, user  # noqa: E402,F401
from src.models.observation import Observation  # noqa: E402
from src.models.study import Study  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    with Session() as session:
        yield session


def add_study(db, name='批次'):
    study = Study(name=name, source_filename='field-notes.csv')
    db.add(study)
    db.flush()
    return study


def add_pair(db, study, key, before, after):
    row = Observation(study_id=study.id, source_key=key,
                      values={'before': before, 'after': after})
    db.add(row)
    db.flush()
    return row


def all_inclusions(db, trial_id):
    from src.models.monitor import MonitorInclusion
    return list(db.scalars(
        select(MonitorInclusion)
        .where(MonitorInclusion.trial_id == trial_id)
        .order_by(MonitorInclusion.seq)
    ))


def inclusion_rows(db, trial_id):
    return [
        {'seq': item.seq, 'sign': item.sign, 'log_e': item.log_e,
         'observation_id': item.observation_id, 'source_key': item.source_key}
        for item in all_inclusions(db, trial_id)
    ]
