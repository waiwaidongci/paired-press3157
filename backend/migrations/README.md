Schema v1 is defined by the SQLAlchemy models and created at startup via Base.metadata.create_all. For this local small application no destructive migration is run. Back up named db-data before modifying schemas.

Schema additions for continuous monitoring (created automatically on startup for existing databases; additive only, no destructive change):

- `monitor_trials`: one row per trial. Locks `alpha` (0.05) and `threshold` (20) at creation, keeps the cumulative state (`seq`, `log_e`, `nonzero`, `positive`, `ties`, `version`), and records `sealed`, `sealed_at`, `sealed_reason`, `stop_seq`, `decision`. A partial unique index allows at most one unsealed trial per study (PostgreSQL: `WHERE sealed = false`; SQLite: `WHERE sealed = 0`).
- `monitor_inclusions`: the frozen evidence rows. `UNIQUE(trial_id, observation_id)` prevents a pair from ever being counted twice; `UNIQUE(trial_id, seq)` prevents two inclusions from claiming the same cumulative position. Each row stores the frozen `values` and `values_sha256` plus its `sign`, `log_increment` and cumulative `log_e`.
- `monitor_looks`: durable look requests. `UNIQUE(trial_id, request_key)` makes a retried request idempotent. `status` uses queued/running/completed/failed; queued and running rows are re-enqueued after a worker restart without touching the seal.

Rollback: drop the three `monitor_*` tables. Existing studies, observations, fixed-endpoint runs and reviews are untouched.
