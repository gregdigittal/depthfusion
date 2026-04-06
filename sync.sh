#!/usr/bin/env bash
# DepthFusion context sync — bidirectional rsync between local and VPS
#
# Usage:
#   ./sync.sh              # bidirectional sync (pull then push)
#   ./sync.sh --push       # local → VPS only
#   ./sync.sh --pull       # VPS → local only
#   ./sync.sh --dry-run    # preview without writing

set -euo pipefail

VPS_HOST="gregmorris@77.42.45.197"
VPS_CLAUDE_DIR="/home/gregmorris/.claude"
LOCAL_CLAUDE_DIR="$HOME/.claude"
SYNC_DIR="shared/discoveries/"

DIRECTION="${1:---both}"
DRY_RUN=""
[[ "$DIRECTION" == "--dry-run" ]] && DRY_RUN="--dry-run" && DIRECTION="--both"
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN="--dry-run"

RSYNC_OPTS=(
    -avz
    --update
    --ignore-existing
    --exclude=".depthfusion-*"
    --exclude="*.tmp"
    --exclude="depthfusion.env"
    -e "ssh -o ConnectTimeout=5"
)
[[ -n "$DRY_RUN" ]] && RSYNC_OPTS+=("$DRY_RUN")

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

sync_pull() {
    echo -e "${YELLOW}▶${NC} Pulling VPS → local"
    mkdir -p "$LOCAL_CLAUDE_DIR/$SYNC_DIR"
    rsync "${RSYNC_OPTS[@]}" "$VPS_HOST:$VPS_CLAUDE_DIR/$SYNC_DIR" "$LOCAL_CLAUDE_DIR/$SYNC_DIR"
    local count=$(ls "$LOCAL_CLAUDE_DIR/$SYNC_DIR" 2>/dev/null | wc -l | tr -d ' ')
    echo -e "${GREEN}✓${NC} Pull complete ($count local discoveries)"
}

sync_push() {
    echo -e "${YELLOW}▶${NC} Pushing local → VPS"
    rsync "${RSYNC_OPTS[@]}" "$LOCAL_CLAUDE_DIR/$SYNC_DIR" "$VPS_HOST:$VPS_CLAUDE_DIR/$SYNC_DIR"
    echo -e "${GREEN}✓${NC} Push complete"
}

case "$DIRECTION" in
    --push)    sync_push ;;
    --pull)    sync_pull ;;
    --both)    sync_pull; sync_push ;;
    --dry-run) DRY_RUN="--dry-run"; RSYNC_OPTS+=("$DRY_RUN"); sync_pull; sync_push ;;
    *)         echo "Usage: $0 [--push|--pull|--both|--dry-run]"; exit 1 ;;
esac

echo -e "\n${GREEN}Sync complete.${NC}"
