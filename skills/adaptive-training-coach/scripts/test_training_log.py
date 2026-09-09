#!/usr/bin/env python3
"""Regression tests for training_log.py; uses only the Python standard library."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).with_name("training_log.py")


class TrainingLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="adaptive-coach-test-")
        self.root = Path(self.temp.name)
        self.home = self.root / "hermes-home"
        self.env = {**os.environ, "HERMES_HOME": str(self.home)}
        self.today = datetime.now(timezone.utc).date()
        self.tomorrow = self.today + timedelta(days=1)
        self.run_cli("init")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def payload(self, name: str, value: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def run_cli(self, *args: object, success: bool = True) -> dict:
        result = subprocess.run(
            ["python3", str(SCRIPT), *(str(arg) for arg in args)],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            result.returncode == 0,
            success,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        return json.loads(result.stdout or result.stderr)

    def make_schedule(self) -> tuple[list[dict], int]:
        result = self.run_cli(
            "schedule",
            self.payload(
                "schedule.json",
                {
                    "replace": True,
                    "items": [
                        {
                            "weekday": "monday",
                            "local_time": "18:00",
                            "workout_name": "A",
                            "enabled": False,
                            "timezone": "UTC",
                        },
                        {
                            "weekday": "friday",
                            "local_time": "18:30",
                            "workout_name": "B",
                            "enabled": True,
                            "timezone": "UTC",
                        },
                    ],
                },
            ),
        )
        rows = result["schedule"]
        return rows, next(row["id"] for row in rows if row["workout_name"] == "A")

    def test_private_storage_and_safe_paths(self) -> None:
        db = self.home / "data" / "adaptive-training-coach" / "training.db"
        self.assertEqual(stat.S_IMODE(db.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(db.stat().st_mode), 0o600)
        self.run_cli("--db", self.root / "outside.db", "init", success=False)
        self.run_cli("export", "--out", self.root / "outside-export", success=False)
        result = self.run_cli("export")
        out = Path(result["directory"])
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in out.glob("*.csv")))

    def test_strict_profile_and_schedule_updates(self) -> None:
        result = self.run_cli(
            "profile",
            self.payload("profile.json", {"timezone": "UTC", "safety_reviewed": "false"}),
        )
        self.assertEqual(result["profile"]["safety_reviewed"], 0)
        self.run_cli("profile", self.payload("bad-tz.json", {"timezone": "Mars/Olympus"}), success=False)
        self.run_cli("schedule", self.payload("missing-replace.json", {"items": []}), success=False)
        rows, item_id = self.make_schedule()
        self.assertEqual(len(rows), 2)
        result = self.run_cli(
            "schedule",
            self.payload("partial.json", {"replace": False, "items": [{"id": item_id, "notes": "kept"}]}),
        )
        row = next(value for value in result["schedule"] if value["id"] == item_id)
        self.assertEqual(row["enabled"], 0)
        result = self.run_cli(
            "schedule",
            self.payload("rename.json", {"replace": False, "items": [{"id": item_id, "workout_name": "A2"}]}),
        )
        self.assertEqual(len(result["schedule"]), 2)
        self.assertTrue(any(row["id"] == item_id and row["workout_name"] == "A2" for row in result["schedule"]))

    def test_reports_do_not_lose_existing_values(self) -> None:
        result = self.run_cli(
            "exercise",
            self.payload(
                "exercise.json",
                {
                    "session": {"date": str(self.today), "planned_name": "A", "raw_report": "original"},
                    "exercise": {"name": "Squat", "sets": [{"reps": 5, "load_kg": 20, "rpe": 6}]},
                },
            ),
        )
        session_id = result["session_id"]
        self.assertEqual(result["sets_recorded"], 1)
        self.run_cli(
            "exercise",
            self.payload(
                "mismatch.json",
                {"session": {"date": str(self.tomorrow)}, "exercise": {"name": "Walk"}},
            ),
            success=False,
        )
        self.run_cli(
            "finish",
            self.payload("finish.json", {"raw_report": None, "discomfort_next_day": 3}),
        )
        self.run_cli(
            "checkin",
            self.payload(
                "checkin.json",
                {"date": str(self.today), "session_id": session_id, "kind": "next_day", "notes": "fine"},
            ),
        )
        recent = self.run_cli("recent", "--days", 2)
        session = next(value for value in recent["sessions"] if value["id"] == session_id)
        self.assertEqual(session["raw_report"], "original")
        self.assertEqual(session["discomfort_next_day"], 3)

    def test_invalid_numbers_and_future_sessions(self) -> None:
        self.run_cli(
            "exercise",
            self.payload(
                "negative.json",
                {
                    "session": {"date": str(self.today)},
                    "exercise": {"name": "Bad", "sets": [{"load_kg": -1}]},
                },
            ),
            success=False,
        )
        self.assertIsNone(self.run_cli("open")["active_session"])
        self.run_cli(
            "exercise",
            self.payload(
                "future.json",
                {"session": {"date": str(self.tomorrow)}, "exercise": {"name": "Walk"}},
            ),
        )
        self.run_cli("finish", self.payload("finish-future.json", {}))
        stats_result = self.run_cli("stats", "--days", 7)
        self.assertEqual(stats_result["sessions"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
