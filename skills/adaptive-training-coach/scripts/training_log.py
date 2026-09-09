#!/usr/bin/env python3
"""Local journal for the adaptive-training-coach Hermes skill."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DEFAULT_DIR = HERMES_HOME / "data" / "adaptive-training-coach"
DEFAULT_DB = DEFAULT_DIR / "training.db"
DEFAULT_EXPORT_DIR = DEFAULT_DIR / "exports"

PROFILE_FIELDS = [
    "goal",
    "target_date",
    "experience",
    "current_activity",
    "preferred_activities",
    "equipment",
    "limitations",
    "preferred_days_times",
    "session_minutes",
    "coaching_style",
    "timezone",
    "safety_reviewed",
    "notes",
]
SCHEDULE_FIELDS = [
    "weekday",
    "local_time",
    "workout_name",
    "duration_min",
    "timezone",
    "enabled",
    "cron_job_id",
    "notes",
]
SESSION_FIELDS = [
    "date",
    "local_datetime",
    "planned_name",
    "status",
    "started_at",
    "finished_at",
    "duration_min",
    "session_rpe",
    "energy_before",
    "energy_after",
    "discomfort_before",
    "discomfort_during",
    "discomfort_after",
    "discomfort_next_day",
    "sleep_hours",
    "stress",
    "new_symptoms",
    "notes",
    "raw_report",
]
EXERCISE_FIELDS = [
    "position",
    "name",
    "variant",
    "total_reps",
    "duration_min",
    "distance_m",
    "rpe",
    "discomfort",
    "sensations",
    "notes",
]
SET_FIELDS = [
    "set_number",
    "reps",
    "load_kg",
    "bodyweight",
    "machine_setting",
    "duration_seconds",
    "distance_m",
    "speed_kmh",
    "incline_pct",
    "heart_rate_avg_bpm",
    "heart_rate_end_bpm",
    "rpe",
    "discomfort",
    "sensations",
    "notes",
]
CHECKIN_FIELDS = [
    "session_id",
    "date",
    "kind",
    "energy",
    "soreness",
    "discomfort",
    "sleep_hours",
    "stress",
    "new_symptoms",
    "notes",
    "raw_report",
]
WEEKDAYS = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}
RATING_FIELDS = {
    "session_rpe",
    "energy_before",
    "energy_after",
    "discomfort_before",
    "discomfort_during",
    "discomfort_after",
    "discomfort_next_day",
    "stress",
    "rpe",
    "discomfort",
    "energy",
    "soreness",
}
NONNEGATIVE_FIELDS = {
    "session_minutes",
    "duration_min",
    "total_reps",
    "distance_m",
    "reps",
    "load_kg",
    "duration_seconds",
    "speed_kmh",
    "heart_rate_avg_bpm",
    "heart_rate_end_bpm",
    "sleep_hours",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def output(payload: dict[str, Any], *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2),
        file=sys.stderr if error else sys.stdout,
    )


def load_payload(path: str) -> dict[str, Any]:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("JSON payload must be an object")
    return value


def validate_ratings(payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if key in RATING_FIELDS and value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a number from 0 to 10") from exc
            if not 0 <= number <= 10:
                raise ValueError(f"{key} must be from 0 to 10")


def validate_nonnegative(payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if key in NONNEGATIVE_FIELDS and value is not None:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be a non-negative number") from exc
            if number < 0:
                raise ValueError(f"{key} must be a non-negative number")
    if payload.get("set_number") is not None and int(payload["set_number"]) < 1:
        raise ValueError("set_number must be at least 1")


def as_bool(value: Any, field: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if value in (0, 1):
        return int(value)
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return int(value.lower() == "true")
    raise ValueError(f"{field} must be a boolean")


def validate_date(value: Any, field: str = "date") -> None:
    try:
        datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD format") from exc


def validate_timezone(value: Any, field: str = "timezone") -> None:
    if value in (None, ""):
        return
    try:
        ZoneInfo(str(value))
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"{field} must be an IANA timezone") from exc


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def ensure_safe_path(path: Path, field: str) -> Path:
    root = DEFAULT_DIR.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{field} must stay inside {root}")
    return resolved


def report_window(conn: sqlite3.Connection, days: int) -> tuple[str, str]:
    if days < 1:
        raise ValueError("days must be at least 1")
    row = conn.execute("SELECT timezone FROM profile WHERE id=1").fetchone()
    timezone_name = row["timezone"] if row and row["timezone"] else None
    today = datetime.now(ZoneInfo(timezone_name)).date() if timezone_name else datetime.now().astimezone().date()
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    ensure_private_directory(db_path.parent)
    conn = sqlite3.connect(db_path)
    db_path.chmod(0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version > 1:
        conn.close()
        raise ValueError(
            f"Database schema version {current_version} is newer than supported version 1"
        )
    conn.executescript(
        """
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
        """
    )
    if current_version == 0:
        conn.execute("PRAGMA user_version = 1")
    conn.commit()
    return conn


def filtered(payload: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {key: payload[key] for key in fields if key in payload}


def upsert_profile(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    validate_nonnegative(payload)
    values = filtered(payload, PROFILE_FIELDS)
    if not values:
        raise ValueError("No valid profile fields")
    if "safety_reviewed" in values:
        values["safety_reviewed"] = as_bool(values["safety_reviewed"], "safety_reviewed")
    if "timezone" in values:
        validate_timezone(values["timezone"])
    now = utc_now()
    existing = conn.execute("SELECT 1 FROM profile WHERE id = 1").fetchone()
    with conn:
        if existing:
            assignments = ",".join(f"{key}=?" for key in values)
            conn.execute(
                f"UPDATE profile SET {assignments},updated_at=? WHERE id=1",
                [*values.values(), now],
            )
        else:
            columns = ["id", *values, "created_at", "updated_at"]
            conn.execute(
                f"INSERT INTO profile ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                [1, *values.values(), now, now],
            )
    return dict(conn.execute("SELECT * FROM profile WHERE id=1").fetchone())


def replace_schedule(conn: sqlite3.Connection, payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("schedule payload requires an items array")
    if "replace" not in payload:
        raise ValueError("schedule payload requires explicit replace: true or false")
    replace = bool(as_bool(payload["replace"], "replace"))
    normalized: list[tuple[int | None, dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each schedule item must be an object")
        item_id = item.get("id")
        if item_id is not None:
            try:
                item_id = int(item_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("schedule item id must be an integer") from exc
        row = filtered(item, SCHEDULE_FIELDS)
        validate_nonnegative(row)
        existing = None
        if item_id is not None and not replace:
            existing = conn.execute("SELECT * FROM schedule WHERE id=?", (item_id,)).fetchone()
            if not existing:
                raise ValueError(f"Unknown schedule id: {item_id}")
        effective = dict(existing) if existing else {}
        effective.update(row)
        for required in ("weekday", "local_time", "workout_name"):
            if not effective.get(required):
                raise ValueError(f"Each schedule item requires {required}")
        weekday = str(effective["weekday"]).lower()
        if weekday not in WEEKDAYS:
            raise ValueError(f"Unknown weekday: {weekday}")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(effective["local_time"])):
            raise ValueError(f"Invalid local_time: {effective['local_time']}")
        if "weekday" in row:
            row["weekday"] = weekday
        if "timezone" in row:
            validate_timezone(row["timezone"], "schedule.timezone")
        if "enabled" in row:
            row["enabled"] = as_bool(row["enabled"], "enabled")
        elif item_id is None:
            row["enabled"] = 1
        normalized.append((item_id, row))

    now = utc_now()
    with conn:
        if replace:
            conn.execute("DELETE FROM schedule")
        for item_id, row in normalized:
            if item_id is not None and not replace:
                if row:
                    assignments = ",".join(f"{key}=?" for key in row)
                    conn.execute(
                        f"UPDATE schedule SET {assignments},updated_at=? WHERE id=?",
                        [*row.values(), now, item_id],
                    )
                else:
                    conn.execute("UPDATE schedule SET updated_at=? WHERE id=?", (now, item_id))
            else:
                values = dict(row)
                if item_id is not None:
                    values = {"id": item_id, **values}
                columns = [*values, "created_at", "updated_at"]
                conn.execute(
                    f"INSERT INTO schedule ({','.join(columns)}) "
                    f"VALUES ({','.join('?' for _ in columns)})",
                    [*values.values(), now, now],
                )
    return schedule_rows(conn)


def schedule_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM schedule ORDER BY "
        "CASE weekday "
        "WHEN 'monday' THEN 1 WHEN 'tuesday' THEN 2 WHEN 'wednesday' THEN 3 "
        "WHEN 'thursday' THEN 4 WHEN 'friday' THEN 5 WHEN 'saturday' THEN 6 "
        "WHEN 'sunday' THEN 7 END, local_time"
    ).fetchall()
    return [dict(row) for row in rows]


def active_session(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sessions WHERE status='in_progress' ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


def session_id(payload: dict[str, Any]) -> str:
    if payload.get("id"):
        return str(payload["id"])
    source = str(payload.get("local_datetime") or utc_now())
    compact = "".join(ch for ch in source if ch.isdigit())[:14]
    return f"workout-{compact}"


def create_session(conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
    if not payload.get("date"):
        raise ValueError("Starting a workout requires session.date")
    validate_date(payload["date"], "session.date")
    validate_ratings(payload)
    validate_nonnegative(payload)
    sid = session_id(payload)
    now = utc_now()
    values = filtered(payload, SESSION_FIELDS)
    values["status"] = "in_progress"
    values["started_at"] = payload.get("started_at") or payload.get("local_datetime") or now
    columns = ["id", *values, "created_at", "updated_at"]
    conn.execute(
        f"INSERT INTO sessions ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        [sid, *values.values(), now, now],
    )
    return sid


def append_text(old: str | None, new: str | None) -> str | None:
    if not new:
        return old
    return f"{old}\n\n{new}" if old else new


def add_sets(conn: sqlite3.Connection, exercise_id: int, items: list[dict[str, Any]]) -> int:
    maximum = conn.execute(
        "SELECT COALESCE(MAX(set_number),0) FROM exercise_sets WHERE exercise_id=?",
        (exercise_id,),
    ).fetchone()[0]
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError("Each set must be an object")
        validate_ratings(item)
        validate_nonnegative(item)
        row = filtered(item, SET_FIELDS)
        row["set_number"] = item.get("set_number") or maximum + index
        if "bodyweight" in row:
            row["bodyweight"] = as_bool(row["bodyweight"], "bodyweight")
        columns = ["exercise_id", *row, "created_at"]
        conn.execute(
            f"INSERT INTO exercise_sets ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            [exercise_id, *row.values(), utc_now()],
        )
    return len(items)


def record_exercise(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    exercise = payload.get("exercise")
    if not isinstance(exercise, dict) or not exercise.get("name"):
        raise ValueError("Payload requires exercise.name")
    validate_ratings(exercise)
    validate_nonnegative(exercise)
    session_payload = payload.get("session") or {}
    if not isinstance(session_payload, dict):
        raise ValueError("session must be an object")
    with conn:
        active = active_session(conn)
        started = active is None
        if active is not None:
            checks = {
                "id": session_payload.get("id"),
                "date": session_payload.get("date"),
                "planned_name": session_payload.get("planned_name"),
            }
            for field, incoming in checks.items():
                if incoming is not None and active[field] is not None and str(incoming) != str(active[field]):
                    raise ValueError(
                        f"Active workout {active['id']} has {field}={active[field]!r}, "
                        f"but the report has {incoming!r}; finish or correct the active workout first"
                    )
        sid = create_session(conn, session_payload) if started else str(active["id"])
        position = exercise.get("position")
        if position is None:
            position = conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM exercises WHERE session_id=?",
                (sid,),
            ).fetchone()[0]
        row = filtered(exercise, EXERCISE_FIELDS)
        row["position"] = position
        columns = ["session_id", *row]
        cursor = conn.execute(
            f"INSERT INTO exercises ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            [sid, *row.values()],
        )
        if cursor.lastrowid is None:
            raise sqlite3.DatabaseError("Exercise insert returned no id")
        exercise_id = int(cursor.lastrowid)
        count = add_sets(conn, exercise_id, exercise.get("sets") or [])
        old = conn.execute("SELECT raw_report FROM sessions WHERE id=?", (sid,)).fetchone()[0]
        conn.execute(
            "UPDATE sessions SET raw_report=?,updated_at=? WHERE id=?",
            (append_text(old, payload.get("raw_report")), utc_now(), sid),
        )
    return {
        "session_id": sid,
        "session_started": started,
        "exercise_id": exercise_id,
        "position": position,
        "sets_recorded": count,
    }


def update_exercise(conn: sqlite3.Connection, exercise_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    current = conn.execute(
        "SELECT session_id FROM exercises WHERE id=?", (exercise_id,)
    ).fetchone()
    if not current:
        raise ValueError(f"Unknown exercise id: {exercise_id}")
    exercise = payload.get("exercise") or {}
    if not isinstance(exercise, dict):
        raise ValueError("exercise must be an object")
    validate_ratings(exercise)
    validate_nonnegative(exercise)
    changes = filtered(exercise, [field for field in EXERCISE_FIELDS if field != "position"])
    set_updates = payload.get("sets") or []
    with conn:
        if changes:
            assignments = ",".join(f"{key}=?" for key in changes)
            conn.execute(
                f"UPDATE exercises SET {assignments} WHERE id=?",
                [*changes.values(), exercise_id],
            )
        for item in set_updates:
            if not isinstance(item, dict) or item.get("set_number") is None:
                raise ValueError("Each set update requires set_number")
            validate_ratings(item)
            validate_nonnegative(item)
            update = filtered(item, [field for field in SET_FIELDS if field != "set_number"])
            if not update:
                continue
            assignments = ",".join(f"{key}=?" for key in update)
            cursor = conn.execute(
                f"UPDATE exercise_sets SET {assignments} "
                "WHERE exercise_id=? AND set_number=?",
                [*update.values(), exercise_id, item["set_number"]],
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Unknown set {item['set_number']} for exercise {exercise_id}")
        if payload.get("raw_report"):
            old = conn.execute(
                "SELECT raw_report FROM sessions WHERE id=?", (current["session_id"],)
            ).fetchone()[0]
            conn.execute(
                "UPDATE sessions SET raw_report=?,updated_at=? WHERE id=?",
                (append_text(old, payload["raw_report"]), utc_now(), current["session_id"]),
            )
    return {
        "session_id": current["session_id"],
        "exercise_id": exercise_id,
        "exercise_updated": bool(changes),
        "sets_updated": len(set_updates),
    }


def finish_session(conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
    current = active_session(conn)
    if not current:
        raise ValueError("No workout is currently in progress")
    validate_ratings(payload)
    validate_nonnegative(payload)
    changes = filtered(payload, SESSION_FIELDS)
    changes.pop("status", None)
    raw_report = changes.pop("raw_report", None)
    changes["status"] = "completed"
    changes["finished_at"] = payload.get("finished_at") or payload.get("local_datetime") or utc_now()
    changes["updated_at"] = utc_now()
    if raw_report:
        changes["raw_report"] = append_text(current["raw_report"], raw_report)
    assignments = ",".join(f"{key}=?" for key in changes)
    with conn:
        conn.execute(
            f"UPDATE sessions SET {assignments} WHERE id=?",
            [*changes.values(), current["id"]],
        )
    return str(current["id"])


def update_session(
    conn: sqlite3.Connection, sid: str, payload: dict[str, Any]
) -> dict[str, Any]:
    validate_ratings(payload)
    validate_nonnegative(payload)
    changes = filtered(payload, SESSION_FIELDS)
    changes.pop("status", None)
    if not changes:
        raise ValueError("No valid session fields to update")
    if "date" in changes:
        validate_date(changes["date"], "date")
    if "raw_report" in changes:
        current = conn.execute("SELECT raw_report FROM sessions WHERE id=?", (sid,)).fetchone()
        if not current:
            raise ValueError(f"Unknown session id: {sid}")
        changes["raw_report"] = append_text(current["raw_report"], changes["raw_report"])
    changes["updated_at"] = utc_now()
    assignments = ",".join(f"{key}=?" for key in changes)
    with conn:
        cursor = conn.execute(
            f"UPDATE sessions SET {assignments} WHERE id=?",
            [*changes.values(), sid],
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Unknown session id: {sid}")
    return {"session_id": sid, "updated": True}


def record_checkin(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("date"):
        raise ValueError("checkin requires date")
    validate_date(payload["date"], "checkin.date")
    validate_ratings(payload)
    validate_nonnegative(payload)
    values = filtered(payload, CHECKIN_FIELDS)
    values.setdefault("kind", "general")
    if values.get("session_id") and not conn.execute(
        "SELECT 1 FROM sessions WHERE id=?", (values["session_id"],)
    ).fetchone():
        raise ValueError(f"Unknown session id: {values['session_id']}")
    columns = [*values, "created_at"]
    with conn:
        cursor = conn.execute(
            f"INSERT INTO checkins ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            [*values.values(), utc_now()],
        )
        if (
            values.get("session_id")
            and values.get("kind") in {"next_day", "morning"}
            and "discomfort" in values
            and values["discomfort"] is not None
        ):
            conn.execute(
                "UPDATE sessions SET discomfort_next_day=?,updated_at=? WHERE id=?",
                (values.get("discomfort"), utc_now(), values["session_id"]),
            )
    if cursor.lastrowid is None:
        raise sqlite3.DatabaseError("Check-in insert returned no id")
    return {"checkin_id": int(cursor.lastrowid), "session_id": values.get("session_id")}


def write_csv(path: Path, rows: list[sqlite3.Row]) -> None:
    ensure_private_directory(path.parent)
    if not rows:
        path.write_text("", encoding="utf-8")
        path.chmod(0o600)
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
    path.chmod(0o600)


def export_csv(conn: sqlite3.Connection, out_dir: Path) -> dict[str, Any]:
    queries = {
        "profile": "SELECT * FROM profile ORDER BY id",
        "schedule": "SELECT * FROM schedule ORDER BY id",
        "sessions": "SELECT * FROM sessions ORDER BY date,started_at,id",
        "exercises": (
            "SELECT e.*,s.date,s.planned_name,s.status FROM exercises e "
            "JOIN sessions s ON s.id=e.session_id ORDER BY s.date,e.position"
        ),
        "sets": (
            "SELECT x.*,e.session_id,e.position AS exercise_position,e.name,s.date "
            "FROM exercise_sets x JOIN exercises e ON e.id=x.exercise_id "
            "JOIN sessions s ON s.id=e.session_id "
            "ORDER BY s.date,e.position,x.set_number"
        ),
        "checkins": "SELECT * FROM checkins ORDER BY date,id",
    }
    counts: dict[str, int] = {}
    for name, query in queries.items():
        rows = conn.execute(query).fetchall()
        write_csv(out_dir / f"{name}.csv", rows)
        counts[name] = len(rows)
    return {"directory": str(out_dir), "rows": counts}


def stats(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    start_date, end_date = report_window(conn, days)
    base = dict(
        conn.execute(
            """
            SELECT COUNT(*) AS sessions,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) AS in_progress,
                   ROUND(AVG(session_rpe),2) AS avg_rpe,
                   ROUND(AVG(discomfort_before),2) AS avg_discomfort_before,
                   ROUND(AVG(discomfort_after),2) AS avg_discomfort_after,
                   ROUND(AVG(discomfort_next_day),2) AS avg_discomfort_next_day,
                   ROUND(AVG(energy_before),2) AS avg_energy_before,
                   ROUND(AVG(energy_after),2) AS avg_energy_after,
                   ROUND(AVG(sleep_hours),2) AS avg_sleep_hours
            FROM sessions WHERE date BETWEEN ? AND ?
            """,
            (start_date, end_date),
        ).fetchone()
    )
    base["days"] = days
    base["enabled_schedule_items"] = conn.execute(
        "SELECT COUNT(*) FROM schedule WHERE enabled=1"
    ).fetchone()[0]
    base["exercise_rows"] = conn.execute(
        "SELECT COUNT(*) FROM exercises e JOIN sessions s ON s.id=e.session_id "
        "WHERE s.date BETWEEN ? AND ?",
        (start_date, end_date),
    ).fetchone()[0]
    base["set_rows"] = conn.execute(
        "SELECT COUNT(*) FROM exercise_sets x "
        "JOIN exercises e ON e.id=x.exercise_id "
        "JOIN sessions s ON s.id=e.session_id "
        "WHERE s.date BETWEEN ? AND ?",
        (start_date, end_date),
    ).fetchone()[0]
    current = active_session(conn)
    base["active_session"] = dict(current) if current else None
    return base


def recent(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    start_date, end_date = report_window(conn, days)
    session_rows = conn.execute(
        "SELECT * FROM sessions WHERE date BETWEEN ? AND ? "
        "ORDER BY date DESC,started_at DESC,id DESC",
        (start_date, end_date),
    ).fetchall()
    sessions: list[dict[str, Any]] = []
    for session in session_rows:
        item = dict(session)
        exercise_rows = conn.execute(
            "SELECT * FROM exercises WHERE session_id=? ORDER BY position,id",
            (session["id"],),
        ).fetchall()
        exercises: list[dict[str, Any]] = []
        for exercise in exercise_rows:
            exercise_item = dict(exercise)
            exercise_item["sets"] = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM exercise_sets WHERE exercise_id=? "
                    "ORDER BY set_number,id",
                    (exercise["id"],),
                ).fetchall()
            ]
            exercises.append(exercise_item)
        item["exercises"] = exercises
        sessions.append(item)
    checkins = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM checkins WHERE date BETWEEN ? AND ? ORDER BY date DESC,id DESC",
            (start_date, end_date),
        ).fetchall()
    ]
    return {"days": days, "sessions": sessions, "checkins": checkins}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", type=Path, default=DEFAULT_DB)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    profile = commands.add_parser("profile")
    profile.add_argument("json_file")
    commands.add_parser("profile-show")
    schedule = commands.add_parser("schedule")
    schedule.add_argument("json_file")
    commands.add_parser("schedule-show")
    exercise = commands.add_parser("exercise")
    exercise.add_argument("json_file")
    update_ex = commands.add_parser("update-exercise")
    update_ex.add_argument("exercise_id", type=int)
    update_ex.add_argument("json_file")
    finish = commands.add_parser("finish")
    finish.add_argument("json_file")
    update = commands.add_parser("update-session")
    update.add_argument("session_id")
    update.add_argument("json_file")
    checkin = commands.add_parser("checkin")
    checkin.add_argument("json_file")
    commands.add_parser("open")
    stat = commands.add_parser("stats")
    stat.add_argument("--days", type=int, default=7)
    recent_parser = commands.add_parser("recent")
    recent_parser.add_argument("--days", type=int, default=14)
    export = commands.add_parser("export")
    export.add_argument("--out", type=Path, default=DEFAULT_EXPORT_DIR)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        db_path = ensure_safe_path(args.db, "--db")
        conn = connect(db_path)
        if args.command == "init":
            schema_version = conn.execute("PRAGMA user_version").fetchone()[0]
            result: dict[str, Any] = {"db": str(db_path), "schema_version": schema_version}
        elif args.command == "profile":
            result = {"profile": upsert_profile(conn, load_payload(args.json_file))}
        elif args.command == "profile-show":
            row = conn.execute("SELECT * FROM profile WHERE id=1").fetchone()
            result = {"profile": dict(row) if row else None}
        elif args.command == "schedule":
            result = {"schedule": replace_schedule(conn, load_payload(args.json_file))}
        elif args.command == "schedule-show":
            result = {"schedule": schedule_rows(conn)}
        elif args.command == "exercise":
            result = record_exercise(conn, load_payload(args.json_file))
        elif args.command == "update-exercise":
            result = update_exercise(conn, args.exercise_id, load_payload(args.json_file))
        elif args.command == "finish":
            sid = finish_session(conn, load_payload(args.json_file))
            result = {"session_id": sid, "completed": True}
        elif args.command == "update-session":
            result = update_session(conn, args.session_id, load_payload(args.json_file))
        elif args.command == "checkin":
            result = record_checkin(conn, load_payload(args.json_file))
        elif args.command == "open":
            row = active_session(conn)
            result = {"active_session": dict(row) if row else None}
        elif args.command == "stats":
            result = stats(conn, args.days)
        elif args.command == "recent":
            result = recent(conn, args.days)
        elif args.command == "export":
            result = export_csv(conn, ensure_safe_path(args.out, "--out"))
        else:
            raise ValueError(f"Unsupported command: {args.command}")
        output({"status": "ok", **result})
        return 0
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        output({"status": "error", "error": str(exc)}, error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
