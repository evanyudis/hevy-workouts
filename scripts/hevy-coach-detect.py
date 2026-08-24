#!/usr/bin/env python3
"""
Hevy Coach — detect new workouts, prepare for analysis.

Polls the Hevy API for the most recent workout. If it differs from the last
analyzed one (tracked in a state file), writes a pending file and fires the
configured trigger command (which runs the analysis step).

The trigger command is fully configurable via config.json / env vars, so this
script is harness-agnostic:
  - Hermes:   `hermes cron run <analyze_job_id>`
  - OpenClaw: whatever your scheduled-task trigger is

Silent exit (no stdout) when nothing new — safe for zero-cost polling cron.

Usage: python3 hevy-coach-detect.py
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# import shared config loader (same dir)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hevy_config import BASE_URL, load_config, resolve_path

import requests

cfg = load_config()
HEVY_API_KEY = cfg.get("hevy_api_key", "")
if not HEVY_API_KEY:
    print("ERROR: hevy_api_key not set in config.json or HEVY_API_KEY env", file=sys.stderr)
    sys.exit(1)

HEADERS = {"api-key": HEVY_API_KEY}
LAST_ID_FILE = resolve_path(cfg, "last_workout_file")
QUEUE_FILE = resolve_path(cfg, "pending_file")
ROUTINES_FILE = resolve_path(cfg, "routines_file")
TRIGGER = cfg.get("coach", {}).get("trigger_command", "").strip()


def hevy_get(endpoint, params=None, retries=3):
    """GET with status check + retry. Hevy occasionally returns empty/non-JSON
    bodies (5xx/429) — crash silently instead of alerting cron. Returns None on
    final failure."""
    for attempt in range(retries):
        try:
            resp = requests.get(f"{BASE_URL}{endpoint}", headers=HEADERS, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            # 4xx other than 429 = real error, don't retry
            return None
        except (requests.RequestException, ValueError):
            time.sleep(5 * (attempt + 1))
    return None


def fetch_routines():
    """Cache current routines for reference."""
    data = hevy_get("/v1/routines", {"page": 0, "page_size": 10})
    if not data:
        return []
    routines = []
    for r in data.get("routines", []):
        exercises = []
        for ex in r.get("exercises", []):
            sets_info = []
            for s in ex.get("sets", []):
                if s.get("weight_kg") and s.get("reps"):
                    sets_info.append(f"{s['weight_kg']}kg×{s['reps']}")
            exercises.append({
                "title": ex.get("title"),
                "template_id": ex.get("exercise_template_id"),
                "sets_summary": ", ".join(sets_info) if sets_info else " bodyweight"
            })
        routines.append({
            "id": r.get("id"),
            "title": r.get("title"),
            "exercises": exercises
        })
    return routines


def get_latest_hevy_workout():
    """Fetch most recent Hevy workout."""
    data = hevy_get("/v1/workouts", {"page": 0, "page_size": 1})
    if not data:
        return None
    workouts = data.get("workouts", [])
    if not workouts:
        return None
    w = workouts[0]

    exercises_data = []
    total_sets = 0
    total_reps = 0
    total_volume = 0.0

    for ex in w.get("exercises", []):
        ex_sets = 0
        ex_reps = 0
        ex_volume = 0.0
        for s in ex.get("sets", []):
            # Count normal + failure sets with reps — weight_kg may be None for
            # bodyweight exercises. Drop sets excluded: partial reps after
            # failure would double-count volume.
            if s.get("type") in ("normal", "failure") and s.get("reps"):
                ex_sets += 1
                ex_reps += s.get("reps", 0)
                ex_volume += (s.get("weight_kg") or 0) * s.get("reps", 0)
                total_sets += 1
                total_reps += s.get("reps", 0)
                total_volume += (s.get("weight_kg") or 0) * s.get("reps", 0)

        if ex_sets > 0:
            exercises_data.append({
                "title": ex.get("title"),
                "template_id": ex.get("exercise_template_id"),
                "sets": ex_sets,
                "reps": ex_reps,
                "avg_weight": round(ex_volume / ex_reps, 1) if ex_reps > 0 else 0,
                "volume_kg": round(ex_volume, 1)
            })

    return {
        "workout_id": w.get("id"),
        "title": w.get("title"),
        "start_time": w.get("start_time"),
        "duration_minutes": calculate_duration(w.get("start_time"), w.get("end_time")),
        "exercise_count": len(exercises_data),
        "total_sets": total_sets,
        "total_reps": total_reps,
        "total_volume_kg": round(total_volume, 1),
        "exercises": exercises_data
    }


def calculate_duration(start_iso, end_iso):
    if not start_iso or not end_iso:
        return 0
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return round((end - start).seconds / 60)
    except Exception:
        return 0


def read_last_id():
    if LAST_ID_FILE.exists():
        return LAST_ID_FILE.read_text().strip()
    return None


def write_last_id(workout_id):
    LAST_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_ID_FILE.write_text(str(workout_id))


def main():
    workout = get_latest_hevy_workout()
    if not workout:
        sys.exit(0)  # Silent

    current_id = workout.get("workout_id")
    last_id = read_last_id()

    if current_id == last_id:
        sys.exit(0)  # Silent, already analyzed

    # New workout — save to pending
    routines = fetch_routines()
    pending = {
        "workout": workout,
        "routines": routines,
        "detected_at": datetime.utcnow().isoformat()
    }

    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_FILE, "w") as f:
        json.dump(pending, f, indent=2)

    write_last_id(current_id)

    if TRIGGER:
        # Non-blocking — the analysis step owns its own delivery
        subprocess.Popen(
            TRIGGER,
            shell=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    # Silent exit — the analysis step handles all delivery
    sys.exit(0)


if __name__ == "__main__":
    main()
