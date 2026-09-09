-- adaptive-training-coach SQLite schema
-- Schema version is managed by training_log.py through PRAGMA user_version.

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
