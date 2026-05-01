#!/usr/bin/env python3
"""Cron-friendly decay job for DepthFusion discovery files — S-71.

Reads configuration from environment variables, calls ``apply_decay``, and
prints a human-readable summary to stdout.  Exits 0 on success, non-zero on
unexpected error.

Usage
-----
Run daily (e.g. via cron or a systemd timer):

    python scripts/decay-job.py

Optional env vars (see ``core/config.py`` for full list):
    DEPTHFUSION_DECAY_RATE_HIGH          (default 0.01 — 1%/day)
    DEPTHFUSION_DECAY_RATE_MID           (default 0.02 — 2%/day)
    DEPTHFUSION_DECAY_RATE_LOW           (default 0.05 — 5%/day)
    DEPTHFUSION_HARD_ARCHIVE_THRESHOLD   (default 0.05)

The discovery directory defaults to ``~/.claude/shared/discoveries/``.
Override by setting ``DEPTHFUSION_DISCOVERY_DIR``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    # Allow tests / operators to redirect the discovery directory via env.
    discovery_dir_raw = os.environ.get("DEPTHFUSION_DISCOVERY_DIR", "").strip()
    discovery_dir = Path(discovery_dir_raw).expanduser() if discovery_dir_raw else None

    try:
        from depthfusion.capture.decay import apply_decay
    except ImportError as exc:
        print(f"ERROR: could not import depthfusion: {exc}", file=sys.stderr)
        return 1

    try:
        summary = apply_decay(discovery_dir=discovery_dir)
    except Exception as exc:  # noqa: BLE001 — top-level guard
        print(f"ERROR: apply_decay raised unexpectedly: {exc}", file=sys.stderr)
        return 1

    # Human-readable summary
    print("DepthFusion decay job complete.")
    print(f"  Files found:              {summary.total}")
    print(f"  Skipped (pinned):         {summary.skipped_pinned}")
    print(f"  Skipped (already decayed today): {summary.skipped_already_decayed}")
    print(f"  Decayed:                  {summary.decayed}")
    print(f"  Archived (hard threshold): {summary.archived}")
    if summary.errors:
        print(f"  Errors ({len(summary.errors)}):", file=sys.stderr)
        for path, msg in summary.errors.items():
            print(f"    {path}: {msg}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
