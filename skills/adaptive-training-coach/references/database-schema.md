# SQLite database schema

## Persistence contract

SQLite is the mandatory persistent source of truth for this skill. The database lives at:

```text
$HERMES_HOME/data/adaptive-training-coach/training.db
```

If `HERMES_HOME` is unset, the effective path is:

```text
~/.hermes/data/adaptive-training-coach/training.db
```

The directory is created with mode `0700`; the database and CSV exports use mode `0600`.
`training_log.py` blocks `--db` and `--out` paths outside this data directory.

Do not keep the current profile, schedule, workout state, exercise results, or check-ins only in
conversation history, Markdown notes, cron prompts, temporary JSON, or agent memory. Those may be
inputs or presentation layers, but a record is durable only after the relevant command returns
`{"status": "ok"}`.

## Schema version

Schema version: **1**. For a new database, `training_log.py init` sets `PRAGMA user_version = 1`
and returns the same value as `schema_version`. Version 1 refuses to open a database whose
`user_version` is greater than 1, preventing an older script from silently downgrading newer data.

The schema is created idempotently with `CREATE TABLE IF NOT EXISTS`. Any future incompatible
change must increment both the frontmatter version and `PRAGMA user_version`, add an explicit
migration before changing existing tables, update this document, and add a regression test.

## Relationships

```text
profile (singleton)

schedule (independent recurring plan; cron_job_id links to Hermes cron)

sessions 1 ─── * exercises 1 ─── * exercise_sets
    │
    └──── 0..* checkins
```

Deletion behavior:

- deleting a session cascades to its exercises and sets;
- deleting an exercise cascades to its sets;
- deleting a session keeps check-ins but sets `checkins.session_id` to `NULL`.

## Command-to-table mapping

- `profile` writes `profile`.
- `schedule` writes `schedule`.
- `exercise` creates or reuses an active row in `sessions`, then writes `exercises` and
  `exercise_sets` in one transaction.
- `update-exercise` updates `exercises` and/or `exercise_sets`.
- `finish` updates the active `sessions` row to `completed`.
- `update-session` corrects a `sessions` row.
- `checkin` writes `checkins`; a morning or next-day check-in with an explicit discomfort value
  also updates `sessions.discomfort_next_day`.
- `open`, `recent`, `stats`, `profile-show`, and `schedule-show` are read-only.
- `export` reads all tables and writes private CSV files.

## Invariants

- `profile` has at most one row (`id = 1`).
- only one session may have `status = 'in_progress'`.
- dates use `YYYY-MM-DD`.
- time zones use IANA names such as `Europe/Moscow`.
- ratings and RPE are in the range `0..10`.
- weights, repetitions, duration, distance, heart rate, and sleep cannot be negative.
- `(weekday, local_time, workout_name)` is unique in `schedule`.
- exercise positions are unique inside a session.
- set numbers are unique inside an exercise.
- `raw_report` preserves the user's original wording; corrections append rather than overwrite it.

## SQL DDL

This DDL mirrors `scripts/training_log.py::connect` and is the schema reference for version 1:

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    goal TEXT,
    target_date TEXT,
    experience TEXT,
    current_activity TEXT,
    preferred_activities TEXT,
    equipment TEXT,
    limitations TEXT,
    preferred_days_times TEXT,
    session_minutes REAL,
    coaching_style TEXT,
    timezone TEXT,
    safety_reviewed INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday TEXT NOT NULL,
    local_time TEXT NOT NULL,
    workout_name TEXT NOT NULL,
    duration_min REAL,
    timezone TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    cron_job_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(weekday, local_time, workout_name)
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    local_datetime TEXT,
    planned_name TEXT,
    status TEXT NOT NULL DEFAULT 'in_progress',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_min REAL,
    session_rpe REAL,
    energy_before REAL,
    energy_after REAL,
    discomfort_before REAL,
    discomfort_during REAL,
    discomfort_after REAL,
    discomfort_next_day REAL,
    sleep_hours REAL,
    stress REAL,
    new_symptoms TEXT,
    notes TEXT,
    raw_report TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_session
    ON sessions(status) WHERE status = 'in_progress';
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);

CREATE TABLE IF NOT EXISTS exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    variant TEXT,
    total_reps REAL,
    duration_min REAL,
    distance_m REAL,
    rpe REAL,
    discomfort REAL,
    sensations TEXT,
    notes TEXT,
    UNIQUE(session_id, position)
);

CREATE INDEX IF NOT EXISTS idx_exercises_session ON exercises(session_id);

CREATE TABLE IF NOT EXISTS exercise_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
    set_number INTEGER NOT NULL,
    reps REAL,
    load_kg REAL,
    bodyweight INTEGER,
    machine_setting TEXT,
    duration_seconds REAL,
    distance_m REAL,
    speed_kmh REAL,
    incline_pct REAL,
    heart_rate_avg_bpm REAL,
    heart_rate_end_bpm REAL,
    rpe REAL,
    discomfort REAL,
    sensations TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(exercise_id, set_number)
);

CREATE INDEX IF NOT EXISTS idx_sets_exercise ON exercise_sets(exercise_id);

CREATE TABLE IF NOT EXISTS checkins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    date TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'general',
    energy REAL,
    soreness REAL,
    discomfort REAL,
    sleep_hours REAL,
    stress REAL,
    new_symptoms TEXT,
    notes TEXT,
    raw_report TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date);

PRAGMA user_version = 1;
```
