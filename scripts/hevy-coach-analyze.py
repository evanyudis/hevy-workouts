#!/usr/bin/env python3
"""
Hevy Coach — generate a workout analysis (historical compare) and deliver it.

Reads the pending file written by hevy-coach-detect.py, fetches the current
workout + recent history, and produces a markdown comparison table.

Delivery (dual mode, configured via config.json):
  - If telegram.bot_token AND telegram.channel_id are set:
      sends a rich message directly via the Telegram Bot API (sendRichMessage)
      so pipe tables render natively. Prints NOTHING on success so the cron
      stays silent (no double delivery).
  - Otherwise:
      prints the markdown to stdout. The harness's default cron delivery then
      handles the message (format may be less rich).

Bodyweight for bodyweight-exercise volume is pulled from the Hevy API
(/v1/body_measurements) as-of each workout's date, so users don't maintain a
manual log.

Usage: python3 hevy-coach-analyze.py
"""

import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hevy_config import BASE_URL, load_config, resolve_path

import requests

cfg = load_config()
HEVY_API_KEY = cfg.get("hevy_api_key", "")
if not HEVY_API_KEY:
    print("ERROR: hevy_api_key not set in config.json or HEVY_API_KEY env", file=sys.stderr)
    sys.exit(1)
HEADERS = {"api-key": HEVY_API_KEY}

QUEUE_FILE = resolve_path(cfg, "pending_file")
TIMEZONE = cfg.get("timezone", "Asia/Jakarta")

# --- Hevy API helpers -----------------------------------------------------

def hevy_get(endpoint, params=None, retries=3):
    """GET with retry + status check. Returns {} on final failure."""
    for attempt in range(retries):
        try:
            resp = requests.get(f"{BASE_URL}{endpoint}", headers=HEADERS, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            return {}
        except (requests.RequestException, ValueError):
            time.sleep(5 * (attempt + 1))
    return {}


def fetch_bodyweight_history():
    """Pull bodyweight as-of dates from the Hevy API (paginated, page starts at 1).
    Returns sorted [(date_str, weight_kg)]. Empty list on failure — analysis
    then treats all sets as weighted (None bodyweight falls back to 0)."""
    measurements = []
    page = 1
    while True:
        # Note: body_measurements caps pageSize at 10 (larger values return 400).
        data = hevy_get("/v1/body_measurements", {"page": page, "pageSize": 10})
        items = data.get("body_measurements", [])
        if not items:
            break
        for m in items:
            if m.get("weight_kg"):
                measurements.append((m.get("date"), float(m["weight_kg"])))
        if page >= (data.get("page_count") or 1):
            break
        page += 1
    return sorted(measurements)


# --- Formatting helpers ---------------------------------------------------

def fmt_date(iso_str):
    try:
        dt = datetime.strptime(iso_str[:10], "%Y-%m-%d")
        return dt.strftime("%-d %b").lstrip("0")
    except Exception:
        return iso_str[:10]


def fmt_short_date(iso_str):
    try:
        return f"{int(iso_str[8:10])}/{int(iso_str[5:7])}"
    except Exception:
        return iso_str[:10]


def short_name(name, maxlen=22):
    return name if len(name) <= maxlen else name[: maxlen - 1] + "…"


def parse_sets(sets_list):
    """(weight_kg|None, reps) for normal+failure sets with reps. Drop sets excluded."""
    return [(s.get("weight_kg"), s["reps"]) for s in sets_list
            if s.get("type") in ("normal", "failure") and s.get("reps")]


def fmt_sets(sets):
    """Collapse consecutive same-weight sets into weight×total_reps blocks.
    [(42,10),(42,10),(51,8)] -> '42×20→51×8'. Bodyweight (None) -> 'bw×20'."""
    blocks = []
    for w, r in sets:
        if blocks and blocks[-1][0] == w:
            blocks[-1] = (w, blocks[-1][1] + r)
        else:
            blocks.append((w, r))
    parts = []
    for w, r in blocks:
        if w is None:
            parts.append(f"bw×{r}")
        else:
            parts.append(f"{int(w) if w == int(w) else w}×{r}")
    return "→".join(parts)


def fmt_sets_short(sets, max_blocks=2):
    """Like fmt_sets but keep only the LAST max_blocks weight blocks."""
    blocks = []
    for w, r in sets:
        if blocks and blocks[-1][0] == w:
            blocks[-1] = (w, blocks[-1][1] + r)
        else:
            blocks.append((w, r))
    blocks = blocks[-max_blocks:]
    parts = []
    for w, r in blocks:
        if w is None:
            parts.append(f"bw×{r}")
        else:
            parts.append(f"{int(w) if w == int(w) else w}×{r}")
    return "→".join(parts)


def muscle_group(title):
    """Coarse muscle-group classifier for the analysis coverage summary."""
    t = title.lower()
    if any(k in t for k in ["squat", "deadlift", "rdl", "goblet", "calf", "hamstring", "glute", "lunge", "leg press", "leg curl", "leg extension", "hip thrust"]):
        return "Legs"
    if any(k in t for k in ["lateral", "shoulder", "rear delt", "reverse fly", "front raise", "upright", "deltoid"]):
        return "Shoulders"
    if any(k in t for k in ["curl", "pushdown", "triceps", "biceps", "skullcrusher", "kickback", "extension"]):
        return "Arms"
    if any(k in t for k in ["crunch", "raise", "wheel", "plank", "twist", "sit", "ab", "pallof", "bird dog", "dead bug", "hollow", "scissor", "mountain", "jackknife", "torso", "v-up", "leg raise"]):
        return "Core"
    if any(k in t for k in ["bench", "chest", "fly", "dip", "press", "pec", "push up"]):
        return "Chest"
    if any(k in t for k in ["row", "pulldown", "pull", "lat", "chin", "face"]):
        return "Back"
    return "Other"


# --- Bodyweight resolution --------------------------------------------------

_BW_HISTORY = fetch_bodyweight_history()


def bw_as_of(iso_str):
    """Bodyweight in effect on a date (latest entry <= date). None if no entry yet."""
    d = iso_str[:10]
    bw = None
    for date_str, kg in _BW_HISTORY:
        if date_str <= d:
            bw = kg
        else:
            break
    return bw


def set_vol(sets, iso_str):
    """Volume kg×reps; bodyweight sets (None weight) use bodyweight as-of date."""
    bw = bw_as_of(iso_str)
    return sum((w if w is not None else (bw or 0)) * r for w, r in sets)


# --- Main -------------------------------------------------------------------

if not QUEUE_FILE.exists():
    print("ERROR: pending file not found — run hevy-coach-detect.py first", file=sys.stderr)
    sys.exit(1)

with open(QUEUE_FILE) as f:
    pending = json.load(f)

w = pending["workout"]
wid = w["workout_id"]
title = w["title"]
cur_date = fmt_date(w["start_time"])

cur = hevy_get(f"/v1/workouts/{wid}")

# Custom workout detection: workouts started from a routine template carry a
# routine_id; freeform sessions have routine_id = None.
is_custom = cur.get("routine_id") is None

# Per-exercise history index from recent workouts (ANY routine title), compared
# by template_id so exercises carried across routines still show prior sessions.
all_w = hevy_get("/v1/workouts", {"page": 0, "page_size": 50}).get("workouts", [])
hist = defaultdict(list)  # template_id -> [{"date","iso","sets"}], most recent first
for w2 in sorted(all_w, key=lambda x: x.get("start_time", ""), reverse=True):
    if w2["id"] == wid:
        continue
    for e2 in w2.get("exercises", []):
        tid2 = e2.get("exercise_template_id")
        if tid2:
            ps = parse_sets(e2.get("sets", []))
            if ps:
                hist[tid2].append({"date": fmt_date(w2["start_time"]), "iso": w2["start_time"], "sets": ps})

lines = []
lines.append("## Workout Summary")
lines.append(f"{title}")
lines.append(f"{cur_date} | {w['duration_minutes']} min")
lines.append(f"{w['total_sets']} sets | {w['total_reps']} reps | {int(w['total_volume_kg']):,} kg volume")
lines.append("")
lines.append(f"## Performance: {title}")

if is_custom:
    headers = ["Δ", "EXERCISE", cur_date]
else:
    headers = ["Δ", "EXERCISE", cur_date, "Prev 1", "Prev 2"]
lines.append("| " + " | ".join(headers) + " |")
lines.append("|" + "|".join(["---"] + ["---"] + [":---:"] * (len(headers) - 2)) + "|")

rows = []
action_items = []
up = down = flat = new_ex = 0

for cex in cur.get("exercises", []):
    tid = cex.get("exercise_template_id", "")
    if not tid:
        continue

    cs = parse_sets(cex.get("sets", []))
    if not cs:
        continue

    cur_str = fmt_sets(cs)
    ex_name = cex.get("title", "")

    # Pure bodyweight exercise (all sets weight None) → compare total REPS, not
    # volume. BW drop (fat loss) shouldn't flag as regression.
    is_bw = all(w is None for w, _ in cs)

    prev_hits = hist.get(tid, [])[:2]
    prev_strs = []
    prev_vol = 0
    prev_reps = 0
    for i, ph in enumerate(prev_hits):
        prev_strs.append(f"{fmt_sets_short(ph['sets'])} ({fmt_short_date(ph['iso'])})")
        if i == 0:
            prev_vol = set_vol(ph["sets"], ph.get("iso", ""))
            prev_reps = sum(r for _, r in ph["sets"])

    cur_vol = set_vol(cs, w["start_time"])
    cur_reps = sum(r for _, r in cs)

    if not prev_strs:
        delta = "🆕"
        new_ex += 1
        action_items.append(f"- [ ] {ex_name}: new movement, build consistency first — once {cur_str} is clean across the board, add weight next session")
    elif is_bw:
        if cur_reps > prev_reps:
            delta = "+🔥"; up += 1
            action_items.append(f"- [ ] {ex_name}: reps up ({cur_reps} vs {prev_reps}) — keep it up, push 1-2 more reps")
        elif cur_reps < prev_reps:
            delta = "-⬇"; down += 1
            action_items.append(f"- [ ] {ex_name}: reps down ({cur_reps} vs {prev_reps}) — check fatigue/recovery, target returning to {prev_strs[0]}")
        else:
            delta = "="; flat += 1
            action_items.append(f"- [ ] {ex_name}: reps flat at {cur_reps} — try adding 1-2 reps or extra weight")
    elif cur_vol > prev_vol * 1.02:
        delta = "+🔥"; up += 1
        action_items.append(f"- [ ] {ex_name}: volume up ({cur_vol:,} vs {int(prev_vol):,} kg) — keep it up, push 1-2 more reps if there's anything left")
    elif cur_vol < prev_vol * 0.98:
        delta = "-⬇"; down += 1
        action_items.append(f"- [ ] {ex_name}: volume down ({cur_vol:,} vs {int(prev_vol):,} kg) — check fatigue/load, target returning to {prev_strs[0]}")
    else:
        delta = "="; flat += 1
        action_items.append(f"- [ ] {ex_name}: flat at {cur_str} — try adding weight or 1-2 more reps for progressive overload")

    rows.append({
        "delta": delta, "name": ex_name, "cur": cur_str, "prev": prev_strs[:2],
        "vol": cur_vol, "prev_vol": prev_vol if prev_hits else None,
        "reps": cur_reps, "prev_reps": prev_reps if prev_hits else None,
        "is_bw": is_bw, "group": muscle_group(ex_name),
    })

for r in rows:
    if is_custom:
        row = [r["delta"], short_name(r["name"]), f"**{r['cur']}**"]
    else:
        row = [r["delta"], short_name(r["name"]), f"**{r['cur']}**"] + r["prev"]
    while len(row) < len(headers):
        row.append("—")
    lines.append("| " + " | ".join(row) + " |")

lines.append("")
if is_custom:
    lines.append("### Session Analysis")
    if not rows:
        lines.append("- No analyzable exercises (all warmup/empty).")
    else:
        cov = Counter(r["group"] for r in rows)
        cov_txt = ", ".join(f"{g} {n} set" + ("s" if n != 1 else "") for g, n in cov.most_common())
        lines.append(f"**Coverage:** {cov_txt}")
        lines.append("")
        for r in rows:
            name = r["name"]; cur = r["cur"]
            if r["delta"] == "🆕":
                lines.append(f"- 🆕 **{name}**: {cur} — new movement, no baseline yet")
            elif r["is_bw"]:
                if r["reps"] > r["prev_reps"]:
                    lines.append(f"- **{name}**: {cur} — reps up ({r['reps']} vs {r['prev_reps']})")
                elif r["reps"] < r["prev_reps"]:
                    lines.append(f"- **{name}**: {cur} — reps down ({r['reps']} vs {r['prev_reps']})")
                else:
                    lines.append(f"- **{name}**: {cur} — stable ({r['reps']} reps)")
            else:
                if r["prev_vol"] is None:
                    lines.append(f"- **{name}**: {cur} — no baseline volume yet")
                else:
                    pct = (r["vol"] - r["prev_vol"]) / r["prev_vol"] * 100
                    arrow = "+" if pct >= 0 else ""
                    trend = "up" if pct > 2 else ("down" if pct < -2 else "stable")
                    lines.append(f"- **{name}**: {cur} — volume {trend} ({arrow}{pct:.0f}% vs prev {r['prev'][0]})")
        lines.append("")
        total_vol = int(w.get("total_volume_kg") or 0)
        new_count = sum(1 for r in rows if r["delta"] == "🆕")
        up_count = sum(1 for r in rows if r["delta"] == "+🔥")
        down_count = sum(1 for r in rows if r["delta"] == "-⬇")
        obs_parts = []
        if up_count: obs_parts.append(f"{up_count} up")
        if down_count: obs_parts.append(f"{down_count} down")
        if new_count: obs_parts.append(f"{new_count} new")
        obs_txt = ", ".join(obs_parts) if obs_parts else "all stable"
        lines.append(f"**Session:** {w['total_sets']} sets | {w['total_reps']} reps | {total_vol:,} kg | {w['duration_minutes']} min ({obs_txt})")
        lines.append("")
        lines.append("Note: custom workout (not a routine template), so there's no specific progression target. Focus: movement consistency + RIR 1-2 effort.")
else:
    lines.append("### Action Items")
    lines.extend(action_items)
    lines.append("")

# Dynamic coach verdict — honest, based on actual deltas
parts = []
if up: parts.append(f"{up} up")
if down: parts.append(f"{down} down")
if flat: parts.append(f"{flat} flat")
if new_ex: parts.append(f"{new_ex} new")
summary = ", ".join(parts) if parts else "all same"

if up > 0 and down == 0 and flat == 0:
    verdict = f"> Great session — every exercise up ({summary}). Keep the momentum, add weight to the ones that were clean so it's not a one-off."
elif down > 0 and up == 0 and flat == 0:
    verdict = f"> Everything down ({summary}). This is rarely strength loss — more often fatigue or poor recovery. Don't panic, check sleep & food, back to normal next session."
elif up > down:
    verdict = f"> Mixed but mostly up ({summary}). Progress is there, but something dipped — check the -⬇ exercises above, likely fatigue spillover from a previous exercise."
elif down > up:
    verdict = f"> Mixed but mostly down ({summary}). Likely fatigue, or added weight on an early exercise sapped energy for the rest. Lower expectations on early sets if needed."
else:
    verdict = f"> Mostly flat ({summary}). The stimulus has adapted — time for progressive overload: add weight or reps on the flat exercises."

lines.append(verdict)
output = "\n".join(lines)

# --- Delivery -------------------------------------------------------------

telegram = cfg.get("telegram", {})
bot_token = telegram.get("bot_token", "")
channel_id = telegram.get("channel_id", "")
thread_id = telegram.get("thread_id")

if bot_token and channel_id:
    # Rich delivery via Bot API — prints nothing on success (silent cron).
    import urllib.error
    import urllib.request

    _payload = {
        "chat_id": int(channel_id) if str(channel_id).lstrip("-").isdigit() else channel_id,
        "rich_message": {"markdown": output},
    }
    if thread_id:
        _payload["message_thread_id"] = int(thread_id)
    _url = f"https://api.telegram.org/bot{bot_token}/sendRichMessage"
    _req = urllib.request.Request(
        _url, data=json.dumps(_payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(_req, timeout=20) as resp:
            resp.read()
        sys.exit(0)
    except urllib.error.HTTPError as e:
        body = e.read().decode() if hasattr(e, "read") else ""
        print(f"ERROR sendRichMessage HTTP {e.code}: {body[:200]}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"ERROR sendRichMessage: {e}", file=sys.stderr)
        sys.exit(3)
else:
    # No Telegram config — print to stdout so the harness's default cron
    # delivery handles the message.
    print(output)
    sys.exit(0)
