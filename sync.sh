#!/usr/bin/env bash
# DepthFusion context sync — bidirectional rsync between local and VPS
#
# Usage:
#   ./sync.sh                    # bidirectional sync (pull then push)
#   ./sync.sh --push             # local → VPS only
#   ./sync.sh --pull             # VPS → local only
#   ./sync.sh --dry-run          # preview without writing
#   ./sync.sh --discoveries-only # sync shared/discoveries/ only (skip memory)

set -euo pipefail

VPS_HOST="gregmorris@77.42.45.197"
VPS_CLAUDE_DIR="/home/gregmorris/.claude"
LOCAL_CLAUDE_DIR="$HOME/.claude"
DISCOVERIES_DIR="shared/discoveries/"

DIRECTION="--both"
DRY_RUN=""
DISCOVERIES_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --push|--pull|--both) DIRECTION="$arg" ;;
        --dry-run)            DRY_RUN="--dry-run" ;;
        --discoveries-only)   DISCOVERIES_ONLY=1 ;;
        *)                    echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

RSYNC_OPTS=(-avz --update --ignore-existing --exclude=".depthfusion-*" --exclude="*.tmp" --exclude="depthfusion.env" -e "ssh -o ConnectTimeout=5")
[[ -n "$DRY_RUN" ]] && RSYNC_OPTS+=("$DRY_RUN")
MEMORY_RSYNC_OPTS=("${RSYNC_OPTS[@]}" "--exclude=MEMORY.md")

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

# Collect all memory/ subdirs under ~/.claude/projects/
get_memory_dirs() {
    find "$LOCAL_CLAUDE_DIR/projects" -maxdepth 2 -type d -name "memory" 2>/dev/null || true
}

sync_discoveries_pull() {
    echo -e "${YELLOW}▶${NC} Pulling discoveries VPS → local"
    mkdir -p "$LOCAL_CLAUDE_DIR/$DISCOVERIES_DIR"
    rsync "${RSYNC_OPTS[@]}" "$VPS_HOST:$VPS_CLAUDE_DIR/$DISCOVERIES_DIR" "$LOCAL_CLAUDE_DIR/$DISCOVERIES_DIR"
    local count; count=$(ls "$LOCAL_CLAUDE_DIR/$DISCOVERIES_DIR" 2>/dev/null | wc -l | tr -d ' ')
    echo -e "${GREEN}✓${NC} Discoveries pulled ($count local files)"
}

sync_discoveries_push() {
    echo -e "${YELLOW}▶${NC} Pushing discoveries local → VPS"
    rsync "${RSYNC_OPTS[@]}" "$LOCAL_CLAUDE_DIR/$DISCOVERIES_DIR" "$VPS_HOST:$VPS_CLAUDE_DIR/$DISCOVERIES_DIR"
    echo -e "${GREEN}✓${NC} Discoveries pushed"
}

sync_memory_pull() {
    local dirs; dirs=$(get_memory_dirs)
    [[ -z "$dirs" ]] && echo "  (no local memory/ dirs found)" && return
    local total=0
    while IFS= read -r dir; do
        local rel="${dir#"$LOCAL_CLAUDE_DIR/"}"
        echo -e "${YELLOW}▶${NC} Pulling memory VPS → local: $rel"
        mkdir -p "$dir"
        rsync "${MEMORY_RSYNC_OPTS[@]}" "$VPS_HOST:$VPS_CLAUDE_DIR/$rel/" "$dir/" 2>/dev/null || true
        local count; count=$(ls "$dir" 2>/dev/null | wc -l | tr -d ' ')
        total=$((total + count))
    done <<< "$dirs"
    echo -e "${GREEN}✓${NC} Memory pulled ($total total files across all projects)"
}

sync_memory_push() {
    local dirs; dirs=$(get_memory_dirs)
    [[ -z "$dirs" ]] && echo "  (no local memory/ dirs found)" && return
    while IFS= read -r dir; do
        local rel="${dir#"$LOCAL_CLAUDE_DIR/"}"
        echo -e "${YELLOW}▶${NC} Pushing memory local → VPS: $rel"
        rsync "${MEMORY_RSYNC_OPTS[@]}" "$dir/" "$VPS_HOST:$VPS_CLAUDE_DIR/$rel/" 2>/dev/null || true
    done <<< "$dirs"
    echo -e "${GREEN}✓${NC} Memory pushed"
}

do_pull() {
    sync_discoveries_pull
    [[ $DISCOVERIES_ONLY -eq 0 ]] && sync_memory_pull
}

do_push() {
    sync_discoveries_push
    [[ $DISCOVERIES_ONLY -eq 0 ]] && sync_memory_push
}

case "$DIRECTION" in
    --push) do_push ;;
    --pull) do_pull ;;
    --both) do_pull; do_push ;;
    *)      echo "Usage: $0 [--push|--pull|--both|--dry-run|--discoveries-only]"; exit 1 ;;
esac

echo -e "\n${GREEN}Sync complete.${NC}"
