#!/usr/bin/env bash
# ============================================================================
# Hevy Coach — installer for Hermes / OpenClaw
#
# Sets up the Hevy Coach workflow:
#   1. Detects (or accepts via --harness) the target agent harness
#   2. Copies scripts + config to the harness's shared/scripts location
#   3. Prompts for Hevy API key + optional Telegram delivery
#   4. (Hermes) registers the two cron jobs and wires the trigger command
#   5. (OpenClaw) installs skill + scripts; cron setup is manual (see docs)
#
# Usage:
#   ./install.sh                     # auto-detect harness
#   ./install.sh --harness hermes    # force Hermes
#   ./install.sh --harness openclaw  # force OpenClaw
#   ./install.sh --dry-run           # show what would happen, don't write
# ============================================================================
set -euo pipefail

# --- paths ---------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_SRC="$SCRIPT_DIR/scripts"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

HARNESS="auto"
DRY_RUN=0

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    echo
    echo "Options:"
    echo "  --harness auto|hermes|openclaw   Target harness (default: auto-detect)"
    echo "  --dry-run                        Print actions without executing"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --harness) HARNESS="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

# --- color/log helpers ---------------------------------------------------
c_green="\033[0;32m"; c_yellow="\033[0;33m"; c_cyan="\033[0;36m"; c_red="\033[0;31m"; c_reset="\033[0m"
info()  { echo -e "${c_cyan}[hevy]${c_reset} $*"; }
ok()    { echo -e "${c_green}[hevy]${c_reset} ✓ $*"; }
warn()  { echo -e "${c_yellow}[hevy]${c_reset} ⚠ $*"; }
err()   { echo -e "${c_red}[hevy]${c_reset} ✗ $*" >&2; }

run() {
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo -e "${c_yellow}[dry-run]${c_reset} $*"
    else
        "$@"
    fi
}

# --- harness detection ---------------------------------------------------
detect_harness() {
    if command -v hermes >/dev/null 2>&1; then
        echo "hermes"
    elif command -v openclaw >/dev/null 2>&1 || command -v claw >/dev/null 2>&1; then
        echo "openclaw"
    else
        echo "unknown"
    fi
}

if [[ "$HARNESS" == "auto" ]]; then
    HARNESS="$(detect_harness)"
    info "Auto-detected harness: ${HARNESS}"
fi

case "$HARNESS" in
    hermes)
        # Hermes cron --script requires paths under $HERMES_HOME/scripts/.
        # Put the scripts (and their config.json) in a subdir there.
        TARGET_DIR="$HERMES_HOME/scripts/hevy-coach"
        SKILLS_DIR="$HERMES_HOME/skills"
        ;;
    openclaw)
        OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
        TARGET_DIR="$OPENCLAW_HOME/shared/hevy-coach"
        SKILLS_DIR="${OPENCLAW_SKILLS_DIR:-$OPENCLAW_HOME/skills}"
        ;;
    *)
        err "Unknown harness '$HARNESS'. Use --harness hermes|openclaw, or make sure hermes/openclaw is in PATH."
        exit 1
        ;;
esac

# --- requirements --------------------------------------------------------
command -v python3 >/dev/null 2>&1 || { err "python3 is required."; exit 1; }
python3 -c "import requests" >/dev/null 2>&1 || {
    warn "Python 'requests' module not found — installing..."
    run pip3 install requests --quiet
}

# --- collect config ------------------------------------------------------
info "Hevy API key (get it at https://hevy.com/settings?developer — requires Hevy Pro)"
read -r -p "  API key: " HEVY_API_KEY
if [[ -z "$HEVY_API_KEY" ]]; then
    err "Hevy API key is required."
    exit 1
fi

echo
read -r -p "Set up Telegram delivery? (y/n) [n]: " SETUP_TG
SETUP_TG="${SETUP_TG:-n}"
BOT_TOKEN=""; CHANNEL_ID=""; THREAD_ID=""
if [[ "$SETUP_TG" =~ ^[Yy]$ ]]; then
    echo "  (Get bot token from @BotFather, channel ID from your channel/topic)"
    read -r -p "  Bot token: " BOT_TOKEN
    read -r -p "  Channel/chat ID (e.g. -1001234567890): " CHANNEL_ID
    read -r -p "  Topic/thread ID (optional, press Enter to skip): " THREAD_ID
fi

# --- write config --------------------------------------------------------
info "Writing config.json..."
CONFIG_FILE="$TARGET_DIR/config.json"
if [[ "$DRY_RUN" -eq 1 ]]; then
    echo -e "${c_yellow}[dry-run]${c_reset} would write $CONFIG_FILE"
else
    mkdir -p "$TARGET_DIR"
    THREAD_JSON="${THREAD_ID:-null}"
    cat > "$CONFIG_FILE" <<EOF
{
  "hevy_api_key": "$HEVY_API_KEY",
  "telegram": {
    "bot_token": "$BOT_TOKEN",
    "channel_id": "$CHANNEL_ID",
    "thread_id": $THREAD_JSON
  },
  "paths": {
    "pending_file": "$TARGET_DIR/hevy_pending.json",
    "last_workout_file": "$TARGET_DIR/hevy_last_workout.txt",
    "routines_file": "$TARGET_DIR/hevy_routines.json"
  },
  "coach": {
    "enabled": true,
    "poll_interval_minutes": 15,
    "trigger_command": ""
  },
  "timezone": "$(cat /etc/timezone 2>/dev/null || echo 'Asia/Jakarta')"
}
EOF
    ok "config.json written to $CONFIG_FILE"
fi

# --- copy scripts --------------------------------------------------------
info "Copying scripts to $TARGET_DIR..."
run cp "$SCRIPTS_SRC/hevy_config.py" "$SCRIPTS_SRC/hevy-coach-detect.py" "$SCRIPTS_SRC/hevy-coach-analyze.py" "$TARGET_DIR/"
run chmod +x "$TARGET_DIR/hevy-coach-detect.py" "$TARGET_DIR/hevy-coach-analyze.py"

# --- install skill -------------------------------------------------------
info "Installing skill to $SKILLS_DIR..."
run mkdir -p "$SKILLS_DIR/hevy-workouts"
run cp "$SCRIPT_DIR/SKILL.md" "$SKILLS_DIR/hevy-workouts/SKILL.md"
run mkdir -p "$SKILLS_DIR/hevy-workouts/references"
if [[ -f "$SCRIPT_DIR/references/hevy-api.md" ]]; then
    run cp "$SCRIPT_DIR/references/hevy-api.md" "$SKILLS_DIR/hevy-workouts/references/hevy-api.md"
fi

# --- Hermes cron setup ---------------------------------------------------
if [[ "$HARNESS" == "hermes" ]]; then
    info "Registering Hermes cron jobs..."
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo -e "${c_yellow}[dry-run]${c_reset} hermes cron create '*/15 * * * *' --name 'Hevy Coach Detect' --no-agent --script hevy-coach-detect.py"
        echo -e "${c_yellow}[dry-run]${c_reset} hermes cron create '0 0 29 2 *' --name 'Hevy Coach Analyze' --no-agent --script hevy-coach-analyze.py"
        echo -e "${c_yellow}[dry-run]${c_reset} hermes cron run <analyze_id> (set as trigger_command)"
    else
        # Detect job — polls every 15 min, silent exit, zero LLM tokens
        DETECT_OUTPUT="$(hermes cron create "*/15 * * * *" \
            --name "Hevy Coach Detect" \
            --no-agent \
            --script "$TARGET_DIR/hevy-coach-detect.py" 2>&1 || true)"
        echo "$DETECT_OUTPUT" | grep -E "created|ID|id:" >&2 || true
        ANALYZE_ID=""
        # Analyze job — never naturally fires (leap-day schedule); detect triggers it
        ANALYZE_OUTPUT="$(hermes cron create "0 0 29 2 *" \
            --name "Hevy Coach Analyze" \
            --no-agent \
            --script "$TARGET_DIR/hevy-coach-analyze.py" 2>&1 || true)"
        echo "$ANALYZE_OUTPUT" | grep -E "created|ID|id:" >&2 || true

        # Extract analyze job ID from output to build the trigger command
        ANALYZE_ID="$(echo "$ANALYZE_OUTPUT" | grep -oE '[a-f0-9]{12}' | head -1 || true)"
        if [[ -n "$ANALYZE_ID" ]]; then
            TRIGGER="hermes cron run $ANALYZE_ID"
            if [[ "$DRY_RUN" -eq 1 ]]; then
                echo -e "${c_yellow}[dry-run]${c_reset} would set trigger_command='$TRIGGER'"
            else
                python3 - "$CONFIG_FILE" "$TRIGGER" <<'PYEOF'
import json, sys
path, trigger = sys.argv[1], sys.argv[2]
cfg = json.load(open(path))
cfg.setdefault("coach", {})["trigger_command"] = trigger
json.dump(cfg, open(path, "w"), indent=2)
PYEOF
                ok "trigger_command set to: $TRIGGER"
            fi
        else
            warn "Could not read analyze job ID from output. Set coach.trigger_command manually in $CONFIG_FILE"
        fi
        ok "Hermes cron jobs created."
    fi
fi

# --- OpenClaw note -------------------------------------------------------
if [[ "$HARNESS" == "openclaw" ]]; then
    warn "OpenClaw cron scheduling is manual — see references/hevy-api.md 'Cron setup' section."
    warn "Set coach.trigger_command in $CONFIG_FILE to fire the analyze script on new workouts."
fi

echo
ok "Hevy Coach installed for $HARNESS."
echo "  Next: test with 'python3 $TARGET_DIR/hevy-coach-detect.py'"
if [[ "$HARNESS" == "hermes" ]]; then
    echo "  Polling runs every 15 min via the 'Hevy Coach Detect' cron."
fi
