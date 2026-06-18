#!/usr/bin/env bash
set -euo pipefail

# migration-rehearsal.sh — Rehearse DepthFusion v2 migration in dry-run mode.
#
# Usage:
#   ./scripts/migration-rehearsal.sh [additional args passed to migrate]
#
# Exits 0 on successful dry-run, non-zero on error.

echo "=== DepthFusion migration rehearsal (dry-run) ==="
echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

python -m depthfusion.cli.migrate v2 --dry-run "$@"

echo ""
echo "=== Rehearsal complete — no changes were written to disk ==="
