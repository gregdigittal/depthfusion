#!/usr/bin/env python3
"""auto-compress — compress idle session files per DEPTHFUSION_AUTO_COMPRESS_HOURS.

When DEPTHFUSION_AUTO_COMPRESS_HOURS is set, finds all .tmp session files whose
mtime is older than that many hours and compresses them into discovery files.
When the env var is unset, exits silently (manual-only mode).

Usage:
    python3 scripts/auto-compress.py [--dry-run]

Options:
    --dry-run    Print which files would be compressed; make no changes.

Exit codes:
    0  success (or disabled / nothing to do)
    1  unexpected error
    2  partial success (some files failed)

Cron entry template (run every hour, log to file):
    0 * * * * python3 /path/to/scripts/auto-compress.py >> ~/.claude/logs/auto-compress.log 2>&1

Stop hook integration:
    Add at the end of hooks/depthfusion-stop.sh:
        python3 /path/to/scripts/auto-compress.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auto-compress idle DepthFusion sessions")
    parser.add_argument("--dry-run", action="store_true", help="Print without compressing")
    args = parser.parse_args(argv)

    from depthfusion.core.config import DepthFusionConfig
    config = DepthFusionConfig.from_env()

    if config.auto_compress_hours is None:
        return 0

    from depthfusion.capture.compressor import SessionCompressor, idle_sessions

    sessions_dir = Path.home() / ".claude" / "sessions"
    idle = idle_sessions(sessions_dir, config.auto_compress_hours)

    if not idle:
        print(f"auto-compress: nothing idle (threshold={config.auto_compress_hours}h)")
        return 0

    if args.dry_run:
        print(f"auto-compress: dry-run, {len(idle)} idle file(s):")
        for p in idle:
            print(f"  {p.name}")
        return 0

    compressor = SessionCompressor()
    compressed = 0
    errors = 0
    for session_file in idle:
        try:
            out = compressor.compress(session_file)
            if out:
                print(f"auto-compress: {session_file.name} -> {out.name}")
                compressed += 1
            else:
                print(f"auto-compress: skipped {session_file.name} (already done or empty)")
        except Exception as exc:
            print(f"auto-compress: error on {session_file.name}: {exc}", file=sys.stderr)
            errors += 1

    print(f"auto-compress: done — compressed={compressed} skipped={len(idle)-compressed-errors} errors={errors}")

    if errors and compressed == 0:
        return 1
    if errors:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
