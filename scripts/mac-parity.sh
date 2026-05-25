#!/usr/bin/env bash
# mac-parity.sh — bring a mac-mlx DepthFusion install in line with the
# canonical 21-tool set (adds missing E-31 env vars, then reloads service).
#
# Usage:  bash scripts/mac-parity.sh [--dry-run]
#
# What it does:
#   1. Locates the DepthFusion REST plist in ~/Library/LaunchAgents/
#   2. Adds DEPTHFUSION_COGNITIVE_RETRIEVAL, DEPTHFUSION_DECISION_MEMORY,
#      and DEPTHFUSION_OPERATIONAL_MEMORY=true if absent
#   3. Unloads and reloads the plist so launchd picks up the changes
#
# Safe to re-run — idempotent; skips vars already present.

set -euo pipefail

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "[dry-run] No changes will be written."
fi

# Try known plist names in order of preference
PLIST=""
for candidate in \
  "$HOME/Library/LaunchAgents/com.depthfusion.rest.plist" \
  "$HOME/Library/LaunchAgents/com.depthfusion.mcp.plist"; do
  if [[ -f "$candidate" ]]; then
    PLIST="$candidate"
    break
  fi
done

if [[ -z "$PLIST" ]]; then
  echo "ERROR: no DepthFusion plist found in ~/Library/LaunchAgents/"
  echo "       Checked: com.depthfusion.rest.plist, com.depthfusion.mcp.plist"
  echo "       Install the service first (docs/install/mac-mlx-quickstart.md step 4)."
  exit 1
fi
echo "Using plist: $PLIST"

# --- plist editing via Python plistlib (XML-safe, no sed fragility) ---------

PYTHON="$(command -v python3 || command -v python)"
if [[ -z "$PYTHON" ]]; then
  echo "ERROR: python3 not found — cannot edit plist safely."
  exit 1
fi

MISSING_VARS=()
for var in DEPTHFUSION_COGNITIVE_RETRIEVAL DEPTHFUSION_DECISION_MEMORY DEPTHFUSION_OPERATIONAL_MEMORY; do
  if ! "$PYTHON" - "$PLIST" "$var" <<'EOF'
import sys, plistlib
plist_path, key = sys.argv[1], sys.argv[2]
with open(plist_path, "rb") as f:
    pl = plistlib.load(f)
env = pl.get("EnvironmentVariables", {})
sys.exit(0 if key in env else 1)
EOF
  then
    MISSING_VARS+=("$var")
  fi
done

if [[ ${#MISSING_VARS[@]} -eq 0 ]]; then
  echo "All E-31 env vars already present in $PLIST — nothing to do."
else
  echo "Adding missing vars: ${MISSING_VARS[*]}"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[dry-run] Would add: ${MISSING_VARS[*]}"
  else
    # Build the new vars as a JSON-safe Python literal and patch via plistlib
    VARS_JSON="[$(printf '"%s",' "${MISSING_VARS[@]}" | sed 's/,$//')]"

    "$PYTHON" - "$PLIST" "$VARS_JSON" <<'EOF'
import sys, json, plistlib
plist_path = sys.argv[1]
keys = json.loads(sys.argv[2])

with open(plist_path, "rb") as f:
    pl = plistlib.load(f)

env = pl.setdefault("EnvironmentVariables", {})
for key in keys:
    env[key] = "true"

with open(plist_path, "wb") as f:
    plistlib.dump(pl, f, fmt=plistlib.FMT_XML, sort_keys=True)

print(f"Wrote {len(keys)} new var(s) to {plist_path}")
EOF
    echo "Plist updated."
  fi
fi

# --- reload service ----------------------------------------------------------

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[dry-run] Would unload/load $PLIST"
  exit 0
fi

echo "Reloading DepthFusion REST service..."
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load   "$PLIST"
echo "Service reloaded."

echo ""
echo "Verification (allow ~5 s for startup):"
sleep 5
curl -sf http://127.0.0.1:7300/health && echo "" || echo "WARNING: health check failed — check 'tail -30 /tmp/depthfusion-rest.log'"
