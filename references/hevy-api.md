# Hevy API Reference

Official docs: https://api.hevyapp.com/docs/

Base URL: `https://api.hevyapp.com`

Auth header on every request: `api-key: <key>`

---

## Getting a Hevy API Key (Tutorial)

The Hevy API is currently available **only to Hevy Pro subscribers**.

1. **Subscribe to Hevy Pro** if you haven't already. The API won't issue a key
   on the free plan.
2. Open the Hevy web app and go to:
   **https://hevy.com/settings?developer**
3. You'll see your API key there (a UUID). Copy it.
4. Keep it secret — it's scoped to your account and grants full read/write
   access to your Hevy data.
5. Provide it to the Hevy Coach installer (or set `HEVY_API_KEY`).

### Verify your key

```bash
curl -s -H "api-key: YOUR_KEY" https://api.hevyapp.com/v1/user/info
```

Expected: a JSON object with your `name` and profile `url`. A `401` means the
key is wrong or not active.

---

## Endpoints

| Method | Path | Notes |
|--------|------|-------|
| GET | `/v1/user/info` | Current user profile |
| GET | `/v1/workouts/count` | Total workout count |
| GET | `/v1/workouts` | Paginated workouts. `page` 0-indexed, `page_size` snake_case |
| GET | `/v1/workouts/{workoutId}` | Single workout detail |
| POST | `/v1/workouts` | Create a workout |
| GET | `/v1/workouts/events` | Workout change events (streaming) |
| GET | `/v1/exercise_history/{exerciseTemplateId}` | Per-exercise history |
| GET | `/v1/exercise_templates` | Exercise templates. `page`/`page_size` |
| POST | `/v1/exercise_templates` | Create custom exercise template |
| GET | `/v1/routines` | Routine templates. `page`/`page_size` |
| GET | `/v1/routines/{routineId}` | Single routine |
| POST | `/v1/routines` | Create routine |
| PUT | `/v1/routines/{routineId}` | Replace routine (see PUT quirks in SKILL.md) |
| GET | `/v1/body_measurements` | Body weight/composition history |
| GET/PUT | `/v1/body_measurements/{date}` | Body measurement for a date |
| POST | `/v1/body_measurements` | Add a body measurement |
| GET | `/v1/routine_folders` | Routine folders |

---

## Pagination Gotchas

**Workouts** (`/v1/workouts`, `/v1/exercise_templates`, `/v1/routines`):
- page is **0-indexed**
- parameter is **`page_size`** (snake_case)
- response has `page_count`

**Body measurements** (`/v1/body_measurements`):
- page is **1-indexed**
- parameter is **`pageSize`** (camelCase)
- **pageSize caps at 10** — values > 10 return HTTP 400
- response has `page_count`

---

## Routine PUT Quirks

Creating/updating routines via PUT rejects these fields (HTTP 400):
`exercises[].index`, `exercises[].title`, `exercises[].notes`,
`exercises[].superset_id`, `exercises[].sets[].index`,
`exercises[].sets[].rest_seconds`, `routine.folder_id`, `routine.description`.

Valid exercise payload:
```json
{
  "exercise_template_id": "07B38369",
  "sets": [
    {"type": "normal", "weight_kg": 18, "reps": 10,
     "distance_meters": null, "duration_seconds": null, "custom_metric": null}
  ],
  "rest_seconds": 90
}
```

Routine limit: **4 max** (all accounts, including Pro).

---

## Cron Setup (OpenClaw / non-Hermes harnesses)

The Hevy Coach install script registers both cron jobs automatically on
Hermes. On other harnesses (OpenClaw, etc.) you set up scheduling yourself:

1. **Detect job** — run `hevy-coach-detect.py` every 15 minutes. It exits
   silently (no output, exit 0) when there's no new workout, and writes a
   pending file + fires `coach.trigger_command` when there is.
2. **Analyze job** — run `hevy-coach-analyze.py` on demand. Point
   `coach.trigger_command` at whatever command fires this on new workouts.

Example for a generic system cron (every 15 min):
```cron
*/15 * * * *  cd /path/to/hevy-coach && python3 hevy-coach-detect.py
```

The analyze script delivers to Telegram (if configured) or prints to stdout.
