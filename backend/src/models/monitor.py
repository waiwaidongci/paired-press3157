from sqlalchemy import (Boolean, Column, Float, Index, Integer, JSON, String,
                        Text, ForeignKey, UniqueConstraint, text)
from src.config.database import Base

class MonitorTrial(Base):
    __tablename__ = 'monitor_trials'
    __table_args__ = (
        Index(
            'uq_monitor_open_study', 'study_id', unique=True,
            postgresql_where=text('sealed = false'),
            sqlite_where=text('sealed = 0'),
        ),
    )
    id = Column(Integer, primary_key=True)
    study_id = Column(Integer, ForeignKey('studies.id'), nullable=False)
    status = Column(String(20), default='open', nullable=False)
    alpha = Column(Float, default=0.05, nullable=False)
    threshold = Column(Float, default=20.0, nullable=False)
    method = Column(String(200), nullable=False)
    basis = Column(Text, nullable=False)
    seq = Column(Integer, default=0, nullable=False)
    log_e = Column(Float, default=0.0, nullable=False)
    nonzero = Column(Integer, default=0, nullable=False)
    positive = Column(Integer, default=0, nullable=False)
    ties = Column(Integer, default=0, nullable=False)
    version = Column(Integer, default=0, nullable=False)
    sealed = Column(Boolean, default=False, nullable=False)
    sealed_at = Column(String(40), default='')
    sealed_reason = Column(String(80), default='')
    stop_seq = Column(Integer, nullable=True)
    decision = Column(Text, default='')
    created_at = Column(String(40), nullable=False)

class MonitorInclusion(Base):
    __tablename__ = 'monitor_inclusions'
    __table_args__ = (
        UniqueConstraint('trial_id', 'observation_id', name='uq_monitor_observation'),
        UniqueConstraint('trial_id', 'seq', name='uq_monitor_seq'),
    )
    id = Column(Integer, primary_key=True)
    trial_id = Column(Integer, ForeignKey('monitor_trials.id'), nullable=False)
    look_id = Column(Integer, nullable=True)
    observation_id = Column(Integer, nullable=False)
    source_key = Column(String(100), nullable=False)
    seq = Column(Integer, nullable=False)
    values = Column(JSON, nullable=False)
    values_sha256 = Column(String(64), nullable=False)
    sign = Column(Integer, nullable=False)
    log_increment = Column(Float, nullable=False)
    log_e = Column(Float, nullable=False)
    included_at = Column(String(40), nullable=False)

class MonitorLook(Base):
    __tablename__ = 'monitor_looks'
    __table_args__ = (
        UniqueConstraint('trial_id', 'request_key', name='uq_monitor_request'),
    )
    id = Column(Integer, primary_key=True)
    trial_id = Column(Integer, ForeignKey('monitor_trials.id'), nullable=False)
    request_key = Column(String(120), nullable=True)
    status = Column(String(20), default='queued', nullable=False)
    added = Column(Integer, default=0, nullable=False)
    seq_before = Column(Integer, default=0, nullable=False)
    seq_after = Column(Integer, default=0, nullable=False)
    log_e_after = Column(Float, default=0.0, nullable=False)
    decision = Column(Text, default='')
    message = Column(Text, default='')
    error = Column(Text, default='')
    created_at = Column(String(40), nullable=False)
    completed_at = Column(String(40), default='')
