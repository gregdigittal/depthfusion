#!/usr/bin/env bash
#
# Install DepthFusion research tools — mode-agnostic.
#
# Installs:
#   (b) Session-history prompt miner — runs once, produces initial corpus
#   (c) Weekly autonomous regression monitor — systemd --user timer
#
# Prerequisites (checked, not installed):
#   * DepthFusion already installed (via pip + `python -m depthfusion.install.install`)
#   * systemd --user available (for the weekly timer)
#   * Python 3.10+ in PATH (for the miner + weekly scripts)
#
# Safe to run multiple times — each step detects existing state and skips.
# Does NOT install DepthFusion itself; the quickstart guide handles that
# so mode selection is explicit per host.
#
# Usage:
#   bash scripts/install-research-tools.sh            # full install
#   bash scripts/install-research-tools.sh --skip-miner  # timer only
#   bash scripts/install-research-tools.sh --dry-run  # show plan, no changes
#
# Exit codes:
#   0 = success (or nothing needed)
#   1 = prerequisite failed (user action required)
#   2 = systemd unit install failed (user may want to re-run or investigate)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DRY_RUN=false
SKIP_MINER=false
MINER_OUT_DIR="$HOME/.local/share/depthfusion/corpus"
UNIT_DIR="$HOME/.config/systemd/user"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --skip-miner) SKIP_MINER=true ;;
    -h|--help)
      sed -n '3,25p' "$0" | sed 's/^# //; s/^#//'
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg" >&2
      echo "Use --help for options." >&2
      exit 1
      ;;
  esac
done

# --- helpers ---

say() { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()  { printf '\033[32m ✓\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m ⚠\033[0m %s\n' "$*" >&2; }
err() { printf '\033[31m ✗\033[0m %s\n' "$*" >&2; }

run() {
  if $DRY_RUN; then
    printf '   (would run) %s\n' "$*"
  else
    "$@"
  fi
}

# --- prerequisite checks ---

say "Prerequisite checks"

if ! command -v python3 >/dev/null 2>&1; then
  err "python3 not in PATH. Install Python 3.10+ and re-run."
  exit 1
fi

if ! python3 -c "import depthfusion" 2>/dev/null; then
  err "depthfusion module not importable. Run:"
  err "  pip install 'depthfusion[vps-cpu]'   # or [vps-gpu]"
  err "  python -m depthfusion.install.install"
  err "then re-run this script."
  exit 1
fi
# Use importlib.metadata.version — robust across editable installs and
# doesn't require the package to export __version__ on the top-level module.
df_version=$(python3 -c "
try:
    from importlib.metadata import version, PackageNotFoundError
    try:
        print(version('depthfusion'))
    except PackageNotFoundError:
        print('(dev, not installed via pip)')
except Exception:
    print('(unknown)')
" 2>/dev/null)
ok "depthfusion module importable (v$df_version)"

# systemd --user check (the weekly timer requires it)
SYSTEMD_AVAILABLE=true
if ! command -v systemctl >/dev/null 2>&1; then
  SYSTEMD_AVAILABLE=false
  warn "systemctl not found — weekly timer will be skipped."
  warn "You can still run scripts/ciqs_weekly.py manually or via cron."
elif ! systemctl --user status >/dev/null 2>&1; then
  SYSTEMD_AVAILABLE=false
  warn "systemd --user not usable (no user session bus) — weekly timer will be skipped."
  warn "On headless VPSes, run: sudo loginctl enable-linger $USER  then re-run."
else
  ok "systemd --user available"
fi

# --- (c) weekly timer install ---

if $SYSTEMD_AVAILABLE; then
  say "Installing weekly regression monitor (systemd --user timer)"

  run mkdir -p "$UNIT_DIR"
  src_service="$REPO_ROOT/scripts/ciqs-weekly.service"
  src_timer="$REPO_ROOT/scripts/ciqs-weekly.timer"
  dst_service="$UNIT_DIR/ciqs-weekly.service"
  dst_timer="$UNIT_DIR/ciqs-weekly.timer"

  if [[ ! -f "$src_service" || ! -f "$src_timer" ]]; then
    err "Unit files missing from $REPO_ROOT/scripts/ — is this a complete checkout?"
    exit 2
  fi

  # Idempotent copy: only overwrite if content differs
  for pair in "$src_service:$dst_service" "$src_timer:$dst_timer"; do
    src="${pair%:*}"; dst="${pair#*:}"
    if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then
      ok "$(basename "$dst") already installed and up to date"
    else
      run cp "$src" "$dst"
      ok "Installed $(basename "$dst")"
    fi
  done

  run systemctl --user daemon-reload
  run systemctl --user enable --now ciqs-weekly.timer
  ok "Timer enabled and active"

  if ! $DRY_RUN; then
    next_fire=$(systemctl --user list-timers ciqs-weekly.timer --no-pager 2>/dev/null | awk 'NR==2 {print $1, $2}' || echo "unknown")
    ok "Next scheduled run: $next_fire"
  fi
else
  warn "Skipping weekly timer install (see prerequisite notes above)."
  warn "Fallback cron entry you can add manually:"
  warn "  0 6 * * 1 cd $REPO_ROOT && python3 scripts/ciqs_weekly.py --out \$HOME/.local/share/depthfusion/weekly-reports/weekly-\$(date +\\%F).md"
fi

# --- (b) initial session-mining pass ---

if $SKIP_MINER; then
  say "Skipping session mining (--skip-miner)"
else
  say "Running initial session-mining pass"

  sessions_dir="$HOME/.claude/projects"
  if [[ ! -d "$sessions_dir" ]]; then
    warn "Session dir $sessions_dir does not exist — skipping miner."
    warn "Once you've used Claude Code on this host, re-run:"
    warn "  python3 $REPO_ROOT/scripts/mine_session_prompts.py --out $MINER_OUT_DIR/corpus-\$(date +%F).jsonl"
  else
    run mkdir -p "$MINER_OUT_DIR"
    out_file="$MINER_OUT_DIR/corpus-$(date +%F).jsonl"
    if [[ -f "$out_file" ]]; then
      ok "Corpus for today already exists: $out_file (skipping re-mine)"
    elif $DRY_RUN; then
      printf '   (would run) python3 %s/scripts/mine_session_prompts.py --out %s\n' \
        "$REPO_ROOT" "$out_file"
    else
      python3 "$REPO_ROOT/scripts/mine_session_prompts.py" \
        --sessions-dir "$sessions_dir" \
        --out "$out_file" \
        -v 2>&1 | tail -20
      n_prompts=$(wc -l < "$out_file" 2>/dev/null || echo 0)
      ok "Mined $n_prompts prompts -> $out_file"
    fi
  fi
fi

# --- summary ---

cat <<EOF

$(printf '\033[32m✓\033[0m') Research tools install complete.

Next steps:
  * Monitor the weekly timer:      systemctl --user list-timers ciqs-weekly.timer
  * Read a weekly report:          ls ~/.local/share/depthfusion/weekly-reports/
  * Re-mine prompts (recommended
    monthly for fresh corpus):     bash $0 --skip-miner=false
  * Compare two modes (later):     python3 $REPO_ROOT/scripts/ciqs_compare.py --help

If you change DepthFusion versions, re-run this script — it's idempotent.
EOF
