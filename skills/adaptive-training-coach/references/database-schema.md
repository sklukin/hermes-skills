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

## Executable schema

The single executable source of truth for all tables, columns, indexes, foreign keys, and
constraints is `scripts/schema.sql`. `scripts/training_log.py` contains no embedded DDL: it reads
and executes that file during initialization and connection.

When the schema changes, update `scripts/schema.sql` first, then update this behavioral reference,
the `SCHEMA_VERSION` constant, migration logic, and regression tests in the same commit. Do not
copy the full DDL back into this Markdown file or into Python.
