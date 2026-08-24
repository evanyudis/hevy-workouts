# Hevy Workouts

Turn your [Hevy](https://www.hevy.com/) workout data into an automated AI
coach. A skill + scripts that run on **Hermes Agent** (primary) and **OpenClaw**
(and any harness that can run a Python script on a schedule), fetching your gym
sessions and producing a clean historical-comparison report after every
workout.

Everything is **fully configurable** — no hardcoded routines, exercise
templates, or personal data. It adapts to your Hevy account, your routines,
and your delivery channel.

## Features

- **Skill for AI agents** — your agent can answer anything about your training
  ("what did I lift last week?", "compare my bench press", "how's my split?")
  straight from the Hevy API.
- **Automated coach** — a detect script polls Hevy every 15 minutes; when a new
  workout lands, an analyze script builds a **historical-comparison table**
  (per-exercise deltas vs your previous sessions) and delivers it to Telegram
  (or stdout).
- **Harness-agnostic** — installs on Hermes or OpenClaw. Cron scheduling is
  automatic on Hermes, documented for others.
- **Bodyweight from the API** — bodyweight exercises (`bw×N`) use your weight
  as-of each session date from `/v1/body_measurements`. No manual logs.
- **Zero cost polling** — the detect cron is `no_agent` (pure script, no LLM
  tokens); only analysis generates output.

## Install

```bash
git clone https://github.com/evanyudis/hevy-workouts.git
cd hevy-workouts
./install.sh                 # auto-detects hermes | openclaw
```

Or force a harness:

```bash
./install.sh --harness hermes
./install.sh --harness openclaw
```

The installer:
1. Prompts for your **Hevy API key** (see below) and optional **Telegram**
   delivery config
2. Writes `config.json` and copies scripts to the harness location
3. Installs the skill
4. On Hermes: registers the two cron jobs and wires the trigger command

You need **Hevy Pro** and an API key from
**https://hevy.com/settings?developer**.

### Install via your AI agent

Want your agent (Hermes, OpenClaw, Claude Code, Codex, etc.) to install and set
this up for you? Paste this prompt into it:

```text
Install the Hevy Workouts repo for me. This turns my Hevy gym data into an
automated AI coach (skill + scripts).

STEPS:
1. Clone it:
   git clone https://github.com/evanyudis/hevy-workouts.git
2. Read the README.md, SKILL.md, and references/hevy-api.md first.
3. Figure out what harness I'm running (Hermes Agent = `hermes` binary,
   OpenClaw = `openclaw`/`claw` binary). Run ./install.sh --harness <auto|hermes|openclaw>.
4. It will prompt for my Hevy API key (I'll paste it — it's at
   https://hevy.com/settings?developer, requires Hevy Pro). If I also want
   Telegram delivery, I'll provide bot token + channel ID. Otherwise skip.
5. After install, verify it works:
   - Run the detect script once:  python3 <install_dir>/hevy-coach-detect.py
   - Confirm it polls the Hevy API without errors.
   - On Hermes: confirm the two cron jobs were registered
     (`hermes cron list`) and the trigger_command is set in config.json.

Then use the installed skill whenever I ask about my workouts: "analyze my
workout", "compare my bench press", "what did I lift last week", "how's my
split", etc. Pull data live from the Hevy API — don't guess.

IMPORTANT: All personal config (API key, routines, exercise templates, delivery
channel) comes from config.json / env vars — never hardcode anything. The
scripts are harness-agnostic and adapt to whatever routines I have in Hevy.
```

Note: the Hevy API is available only to **Hevy Pro** subscribers. Get your key
at **https://hevy.com/settings?developer**. On OpenClaw / non-Hermes harnesses,
the cron scheduling is manual — see `references/hevy-api.md`.

## Configuration

`config.json` is written by the installer. Env-var fallbacks exist for power
users (`HEVY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `HEVY_COACH_CHANNEL`,
`HEVY_COACH_THREAD_ID`, `HEVY_COACH_TRIGGER`). See `config.example.json`.

```json
{
  "hevy_api_key": "uuid-from-hevy.com/settings?developer",
  "telegram": {
    "bot_token": "",
    "channel_id": "",
    "thread_id": null
  },
  "coach": {
    "enabled": true,
    "poll_interval_minutes": 15,
    "trigger_command": "hermes cron run <analyze_job_id>"
  },
  "timezone": "Asia/Jakarta"
}
```

Leave `telegram` empty to deliver analysis to stdout instead (the harness's
default cron delivery handles it).

## Project Layout

```
hevy-workouts/
├── SKILL.md                    # agent skill (agnostic template)
├── install.sh                  # installer: harness detect, config, cron
├── config.example.json         # config template
├── scripts/
│   ├── hevy_config.py          # shared config loader (file + env fallback)
│   ├── hevy-coach-detect.py    # polls Hevy, silent if nothing new
│   └── hevy-coach-analyze.py   # builds + delivers the comparison report
└── references/
    └── hevy-api.md             # endpoint reference + API key tutorial
```

## Requirements

- Python 3 with `requests`
- Hevy Pro account + API key
- Hermes or OpenClaw (for the skill + cron automation)

## License

MIT
