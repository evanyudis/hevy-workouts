---
name: hevy-workouts
description: Fetch workout data from Hevy App via REST API. Use when the user asks about gym workouts, exercises, sets/reps, workout history, training patterns, or wants an automated workout analysis from Hevy.
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [hevy, fitness, workout, gym, tracking, coach]
    related_skills: []
---

# Hevy Workouts Skill

Fetch and analyze gym workout data from the Hevy App via its REST API. Ships
with two optional automation scripts (`hevy-coach-detect.py` and
`hevy-coach-analyze.py`) that turn it into a self-running workout coach that
reports progress after every session.

**Works on any agent harness.** The skill + scripts are harness-agnostic:
everything personal is read from `config.json` (with env-var fallbacks), so it
adapts to any user's routines, exercise templates, and delivery channel.

## Auth

- Base URL: `https://api.hevyapp.com`
- Header: `api-key: <key>`
- Requires **Hevy Pro**. Get your key at **https://hevy.com/settings?developer**
- Key comes from `config.json` (`hevy_api_key`) or `HEVY_API_KEY` env var

See `references/hevy-api.md` for the full endpoint reference and API-key setup
tutorial.

## Config

The skill and scripts read config from `config.json` (found next to the
scripts, or in `~/.hevy/config.json`), with env-var fallbacks. See
`config.example.json` in the repo root.

```json
{
  "hevy_api_key": "uuid-from-hevy.com/settings?developer",
  "telegram": {
    "bot_token": "",
    "channel_id": "",
    "thread_id": null
  },
  "paths": {
    "pending_file": "hevy_pending.json",
    "last_workout_file": "hevy_last_workout.txt",
    "routines_file": "hevy_routines.json"
  },
  "coach": {
    "enabled": true,
    "poll_interval_minutes": 15,
    "trigger_command": ""
  },
  "timezone": "Asia/Jakarta"
}
```

- `telegram.*`: optional. When `bot_token` + `channel_id` are set, the analyze
  script sends a rich message directly (pipe tables render natively). When
  empty, it prints to stdout and the harness handles delivery.
- `coach.trigger_command`: fired by the detect script to run the analysis step.
  Harness-specific (Hermes: `hermes cron run <analyze_job_id>`; OpenClaw:
  whatever your scheduled-task trigger is).

## Endpoints

### User Info
```
GET /v1/user/info
```
Returns user profile (name, url).

### Workout Count
```
GET /v1/workouts/count
```
Returns total workout count.

### List Workouts (paginated)
```
GET /v1/workouts?page=0&page_size=10
```
Returns paginated list. `page_count` in response for pagination. Note: workouts
use snake_case `page_size` (0-indexed page); body_measurements uses camelCase
`pageSize` (1-indexed page).

### Single Workout Detail
```
GET /v1/workouts/{workoutId}
```
Full workout with all exercises and sets.

### Exercise History
```
GET /v1/exercise_history/{exerciseTemplateId}
```
History for a specific exercise across all workouts.

### Routines
```
GET /v1/routines?page=0&page_size=10
```
Saved routine templates.

### Routine Detail
```
GET /v1/routines/{routineId}
```
Full routine with exercises.

### Exercise Templates
```
GET /v1/exercise_templates?page=0&page_size=100
```
Available exercise templates (paginated). Search by `title.lower()` to get the
template ID.

### Body Measurements
```
GET /v1/body_measurements?page=1&pageSize=10
```
Bodyweight + body composition history. **pageSize caps at 10** (larger values
return 400). Used by the analyze script for bodyweight-exercise volume.

## Routine PUT — API Quirks (Critical)

When creating or updating routines via PUT, these fields cause 400 errors:
- `exercises[].index` — NOT allowed
- `exercises[].title` — NOT allowed
- `exercises[].notes` — NOT allowed
- `exercises[].superset_id` — NOT allowed
- `exercises[].sets[].index` — NOT allowed
- `exercises[].sets[].rest_seconds` — NOT allowed
- `routine.folder_id` — NOT allowed
- `routine.description` — NOT allowed

**Correct exercise structure for PUT:**
```python
{
    "exercise_template_id": "07B38369",
    "sets": [
        {"type": "normal", "weight_kg": 18, "reps": 10,
         "distance_meters": None, "duration_seconds": None, "custom_metric": None}
    ],
    "rest_seconds": 90
}
```

**Correct routine structure for PUT:**
```python
{"routine": {"title": "Routine Name", "exercises": [exercise, ...]}}
```

**Replacing a routine in-place:** To replace an existing routine's exercises,
use PUT with the routine's UUID. The API accepts the same routine ID with
completely different exercises. Cleanest approach when hitting the routine limit.

**Routine limit:** 4 routines max — applies to ALL accounts (including Pro).
API returns `"routine-limit-exceeded"`. Must DELETE existing first or UPDATE
in-place via PUT.

**Failure sets:** Users may mark sets as `"type": "failure"` when pushing reps
to actual failure. When mirroring a workout back into a routine template,
PRESERVE the failure set type — the routine PUT accepts
`{"type": "failure", ...}` per set.

## Data Schema

### Workout
```json
{
  "id": "uuid",
  "title": "Upper Body Push Focus",
  "routine_id": "uuid",
  "description": "",
  "start_time": "2026-04-12T05:03:14+00:00",
  "end_time": "2026-04-12T06:14:43+00:00",
  "exercises": [Exercise]
}
```

### Exercise
```json
{
  "index": 0,
  "title": "EZ Bar Biceps Curl",
  "notes": "",
  "exercise_template_id": "01A35BF9",
  "sets": [Set]
}
```

### Set
```json
{
  "index": 0,
  "type": "normal|warmup|drop|failure",
  "weight_kg": 20,
  "reps": 12,
  "distance_meters": null,
  "duration_seconds": null,
  "rpe": null
}
```

## Analysis Workflow

When the user asks to "analyze workout" or similar, **auto-pull without asking
confirmation**. Execute directly, then present analysis.

### Full Analysis Pattern
1. Pull latest workout (page_size=1, most recent by start_time)
2. Check bodyweight via `/v1/body_measurements` as-of the session date
3. Check full history (page_size=10-20) for workout frequency and split consistency
4. For hypertrophy focus: 8-12 rep range, progressive overload when a target is hit clean
5. For strength focus: 4-6 rep range, heavier loads, slower progression

### Historical Compare Workflow (Performance Over Time)

When the user asks about performance, progress, or "compare with previous" — OR
when auto-analysis cron fires after a workout — run this:

1. **Get current workout** — latest by `start_time`
2. **Build per-exercise history index** — scan recent workouts (page_size=50,
   ANY routine title); index by `exercise_template_id`, most recent first
3. **For each exercise in current workout**, take its own 2 most recent prior
   sessions (cross-routine)
4. **Compare working-set metrics** (skip warmup/drop sets): volume load
   (kg×reps), max weight, total reps per exercise
5. **Format output** — Historical Compare Table with `Prev 1`/`Prev 2` columns
   showing `sets (date)` per exercise

**Matching uses per-exercise template_id index, NOT routine-title matching:**
exercises are compared by `exercise_template_id` across ALL recent workouts, so
exercises carried between routine splits still show prior sessions. Genuinely
new exercises show 🆕.

### Historical Compare Table

| Δ | EXERCISE | 8 Aug | Prev 1 | Prev 2 |
|---|---|---|---|---|
| +🔥 | Incline DB Press | **20×24** | 18×24 (14 Jul) | 17.5×28 (22 Jun) |
| -⬇ | Lat Pulldown | **42×30** | 42×20→51×16 (1 Aug) | 42×30→51×7 (23 Jul) |

- Delta column first (leftmost) — visible without scroll on narrow Telegram
- **Bold current session values**
- Delta: `+🔥` (naik), `-⬇` (turun), `=` (flat), `🆕` (no prior history)
- Show weight and total reps (e.g. 20×24 = 20kg × 24 total reps across working sets)
- Prev columns are PER-EXERCISE: `sets (date)` of that exercise's 2 most recent prior sessions, from any routine

### Output Format (Telegram)

Write plain markdown directly — no wrapper, no code fence, no special container. Just headings, pipe tables, task lists, and blockquotes.

```
## Workout Summary
[Title]
[YYYY-MM-DD] | [Duration] min
[X] sets | [Y] reps | [Z]kg volume

## Performance: [Routine Name]
| Δ | EXERCISE | [Latest] | [Prev-1] | [Prev-2] |
|---|---|---|---|---|
| +🔥 | Incline DB Press | **20×24** | 18×24 | 17.5×28 |
| = | Triceps Pushdown | **36×34** | 32.5×36 | 18×38 |
| -⬇ | Face Pull | **42×24** | 51×30 | 51×30 |

- [ ] Action item 1
- [ ] Action item 2

> Coach verdict — 2-3 sentences, direct, no fluff.
```

**Rules:**
- Do NOT add Underloaded/Overloaded/Highlights/Exercise Breakdown sections
- Do NOT use STATUS column or bullet lists for exercises
- Current session values MUST be bolded
- If no historical data available (first session of a type), skip the compare table and just give coaching
- Action Items are GENERATED DYNAMICALLY per exercise (volume delta + concrete next target)
- Verdict is DYNAMIC based on actual deltas. Never a canned line.
- New exercises (no history) get delta 🆕, not "=".
- Pure bodyweight exercises (all sets weight None) compare total REPS, not volume — bodyweight drop from fat loss shouldn't flag as regression.
- Custom workouts (routine_id is None) use a 3-column table + observational analysis instead of prescriptive action items.

## Automated Coach — Two-Cron Architecture

**Architecture:** Detect (no_agent, zero cost) → trigger → Analysis (no_agent,
direct rich delivery).

```
Hevy API (poll every 15 min)
    ↓
hevy-coach-detect.py (no_agent cron)
    → silent exit if no new workout (0 tokens)
    ↓ (new workout detected — writes pending.json, fires trigger_command)
    ↓ (background, via subprocess.Popen)
trigger_command (Hermes: hermes cron run <analyze_job_id>)
    → runs hevy-coach-analyze.py
    → fetches current + 2 previous same-exercise workouts
    → emits Historical Compare Table markdown
    → rich-delivers to Telegram OR prints to stdout (harness delivery)
    → empty stdout = silent cron (no double delivery)
```

### Cron 1: Detect (no_agent, zero token cost)
- Schedule: `*/15 * * * *` (every 15 min)
- no_agent: true, silent exit (no stdout output)
- Script: `hevy-coach-detect.py`
- Fires `coach.trigger_command` (non-blocking) when a new workout is found

### Cron 2: Analysis (no_agent, direct delivery)
- Schedule: `0 0 29 2 *` (leap day, never naturally fires — triggered by detect)
- no_agent: true
- Script: `hevy-coach-analyze.py`
- Empty stdout on success (when Telegram configured)

### Scripts
- `hevy-coach-detect.py` — polls Hevy API, dedup via state file. Silent exit if
  no new workout. On new: writes pending + fires trigger_command via
  `subprocess.Popen` (non-blocking), silent exit. Includes bodyweight exercises
  and failure sets in pending data; drop sets excluded. **API resilience:**
  retries 3× on 429/5xx/JSON-decode errors with backoff; on final failure
  returns None and exits silently — never crashes the cron with an alert.
- `hevy-coach-analyze.py` — reads pending file, fetches current + recent
  workouts, emits the Historical Compare Table as markdown. Formatting:
  `fmt_sets()` collapses consecutive same-weight sets into `weight×total_reps`
  blocks joined by `→`. Bodyweight sets (weight None) render as `bw×N`, using
  the bodyweight as-of that session date from `/v1/body_measurements`. Delta
  logic: weighted exercises compare volume (kg×reps, ±2% tolerance); PURE
  bodyweight exercises compare total REPS instead — bodyweight drop from fat
  loss must not flag as regression. Delivers via Telegram `sendRichMessage`
  when configured, else prints to stdout.

## Notes
- Workouts: pagination starts at page 0, snake_case `page_size`
- Body measurements: pagination starts at page 1, camelCase `pageSize`, capped at 10
- Latest workout pull: page_size=1, take first result
- Timestamps are ISO 8601; convert to local timezone
- `weight_kg` can be null for bodyweight exercises
- Filter out warmup/drop/failure sets when calculating working-set metrics for comparison tables

## Common Pitfalls
- Forgetting the `api-key` header on every request
- Using `pageSize` on workouts (snake_case there) or `page_size` on body_measurements (camelCase there)
- Using pageSize > 10 on body_measurements (returns 400)
- Sending disallowed fields (`index`, `title`, `notes`, `superset_id`, etc.) in routine PUT — 400 error
- Hitting the 4-routine limit
- Not filtering out warmup/drop sets before computing comparison metrics

## Verification Checklist
- [ ] API key present in config.json or HEVY_API_KEY
- [ ] `/v1/user/info` returns the correct user
- [ ] Latest workout pull returns the expected most-recent session
- [ ] Historical compare matches by exercise_template_id (cross-routine)
- [ ] Bodyweight exercises use bodyweight as-of session date
- [ ] Detect script exits silent on no-new-workout (dedup works)
