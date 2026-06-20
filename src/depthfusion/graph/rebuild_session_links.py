#!/usr/bin/env python3
# src/depthfusion/graph/rebuild_session_links.py
"""Backfill PRECEDED_BY edges derived from VPS event entities.

Usage:
    python -m depthfusion.graph.rebuild_session_links [--dry-run | --apply]

    --dry-run  (default) Print what would be added; write nothing.
    --apply             Upsert session entities + PRECEDED_BY edges.

Designed to be idempotent — safe to run multiple times.  Each call checks
which sessions already have outgoing PRECEDED_BY edges and skips them.

S-212.
"""
from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill PRECEDED_BY edges from VPS event entities"
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Print what would be added (default)",
    )
    mode_group.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Actually upsert entities and edges",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )

    from depthfusion.graph.store import get_store
    from depthfusion.graph.session_entity_linker import (
        get_unlinked_sessions,
        link_and_upsert,
    )

    graph_store = get_store()

    sessions = get_unlinked_sessions(graph_store)
    if not sessions:
        print("No unlinked sessions found — nothing to do.")
        return 0

    result = link_and_upsert(sessions, graph_store, dry_run=args.dry_run)

    if result["dry_run"]:
        print(
            f"Would add {result['edges_added']} PRECEDED_BY edge(s) across "
            f"{result['sessions']} session(s). Dry run — no changes written."
        )
    else:
        print(
            f"Added {result['edges_added']} PRECEDED_BY edge(s) across "
            f"{result['sessions']} session(s). Done."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
