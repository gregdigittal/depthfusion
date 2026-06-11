#!/usr/bin/env python3
"""backfill_acl.py — Backfill ACL columns on all V1 DepthFusion records.

E-50 Authorization Model, T-561.

Sets acl_allow='["greg"]' and classification='internal' on every existing
record in all six DepthFusion stores that does not yet carry those fields.

Stores handled:
  1. MemoryStore     — SQLite  (~/.claude/.depthfusion_memories.db)
  2. FileIndex       — SQLite  (~/.claude/.depthfusion_file_index.db)
  3. GraphStore      — SQLite  (~/.claude/depthfusion-graph.db)
     (entities + edges tables)
  4. TelemetryStore  — SQLite  (~/.claude/.depthfusion_telemetry.db)
     (candidate_skills + telemetry_events tables)
  5. EventLog        — NDJSON  (~/.claude/shared/depthfusion-events.jsonl)
  6. VectorStore     — ChromaDB metadata  (in-memory list update, no file)

SQLite stores: uses ALTER TABLE … ADD COLUMN (idempotent via IF NOT EXISTS
guard; column already exists → OperationalError is caught and ignored), then
UPDATE … WHERE acl_allow IS NULL to stamp only un-migrated rows.

EventLog (NDJSON): rewrite the file atomically (rename), adding acl_allow and
classification to every line that lacks them.  Idempotent: lines that already
carry the fields are left unchanged.

VectorStore (ChromaDB): updates metadata on every document whose metadata
lacks acl_allow.  Requires chromadb package; skipped with a warning if absent.

Usage:
    python scripts/backfill_acl.py [--dry-run] [--db-dir DIR]

    --dry-run   Print counts of rows/lines/docs to be updated without
                modifying anything.
    --db-dir    Override the directory that contains the SQLite database
                files (default: ~/.claude).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Optional

ACL_ALLOW_DEFAULT = '["greg"]'
CLASSIFICATION_DEFAULT = "internal"


# ── helpers ─────────────────────────────────────────────────────────────────

def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if *column* exists in *table*."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Return True if *table* exists in the database."""
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _sqlite_backfill(
    db_path: Path,
    tables: list[str],
    dry_run: bool,
) -> dict[str, int]:
    """Add ACL columns (idempotent) and backfill rows that lack them.

    In dry-run mode no changes are made — not even schema changes.
    The "count to backfill" for a table that lacks the acl_allow column
    entirely is the total row count (all rows would receive backfill values).

    Returns a dict of {table: rows_that_would_be_or_were_updated}.
    """
    if not db_path.exists():
        print(f"  [skip] {db_path} — file not found")
        return {}

    counts: dict[str, int] = {}
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for table in tables:
            if not _table_exists(conn, table):
                print(f"  {table}: [skip] table not found in {db_path.name}")
                counts[table] = 0
                continue

            has_acl = _table_has_column(conn, table, "acl_allow")

            if has_acl:
                # Column exists — count rows with NULL value.
                cur = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE acl_allow IS NULL"
                )
                count = cur.fetchone()[0]
            else:
                # Column absent — all rows need backfill.
                cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]

            counts[table] = count

            if count == 0:
                print(f"  {table}: 0 rows to backfill (already done)")
                continue

            print(f"  {table}: {count} rows to backfill")

            if dry_run:
                continue  # Do not alter schema or data in dry-run.

            # Apply schema changes and data backfill.
            for col, default in (
                ("acl_allow", ACL_ALLOW_DEFAULT),
                ("classification", CLASSIFICATION_DEFAULT),
            ):
                if not _table_has_column(conn, table, col):
                    conn.execute(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN {col} TEXT DEFAULT '{default}'"
                    )
            conn.commit()

            # Backfill any rows that still have NULL (can occur if DEFAULT
            # was not applied to pre-existing rows by an older SQLite build,
            # or if rows were inserted after ALTER TABLE but before this UPDATE).
            conn.execute(
                f"UPDATE {table} "
                f"SET acl_allow = '{ACL_ALLOW_DEFAULT}', "
                f"    classification = '{CLASSIFICATION_DEFAULT}' "
                f"WHERE acl_allow IS NULL"
            )
            conn.commit()
            print(f"  {table}: backfilled {count} rows")

    return counts


def _ndjson_backfill(path: Path, dry_run: bool) -> int:
    """Backfill acl_allow + classification on NDJSON event log lines.

    Rewrites the file atomically (write to temp → rename).
    Returns the count of lines that would be / were updated.
    """
    if not path.exists():
        print(f"  [skip] {path} — file not found")
        return 0

    updated = 0
    new_lines: list[str] = []

    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.rstrip("\n")
            if not raw.strip():
                new_lines.append("")
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                new_lines.append(raw)  # preserve malformed lines as-is
                continue

            changed = False
            if "acl_allow" not in obj:
                obj["acl_allow"] = ["greg"]
                changed = True
            if "classification" not in obj:
                obj["classification"] = CLASSIFICATION_DEFAULT
                changed = True

            if changed:
                updated += 1

            new_lines.append(json.dumps(obj, ensure_ascii=False))

    print(f"  {path.name}: {updated} lines to backfill")
    if dry_run or updated == 0:
        return updated

    # Atomic replace: write to sibling temp file then rename.
    tmp_path = path.with_suffix(".tmp_backfill")
    try:
        with open(tmp_path, "w", encoding="utf-8") as fout:
            fout.write("\n".join(new_lines))
            if new_lines:
                fout.write("\n")
        tmp_path.replace(path)
    except Exception as exc:
        print(f"  ERROR writing {path}: {exc}", file=sys.stderr)
        tmp_path.unlink(missing_ok=True)
        raise

    print(f"  {path.name}: backfilled {updated} lines")
    return updated


def _chromadb_backfill(persist_dir: Path, dry_run: bool) -> int:
    """Backfill acl_allow + classification in ChromaDB metadata.

    Returns the count of documents updated (or to be updated in dry-run).
    Skips silently if chromadb is not installed.
    """
    try:
        import chromadb  # type: ignore[import]
    except ImportError:
        print("  [skip] chromadb not installed — VectorStore backfill skipped")
        return 0

    if not persist_dir.exists():
        print(f"  [skip] {persist_dir} — directory not found")
        return 0

    client = chromadb.PersistentClient(path=str(persist_dir))
    collections = client.list_collections()

    if not collections:
        print("  ChromaDB: no collections found")
        return 0

    total = 0
    for coll_meta in collections:
        coll = client.get_collection(coll_meta.name)
        results = coll.get(include=["metadatas", "documents", "embeddings"])
        ids = results.get("ids", [])
        metadatas = results.get("metadatas") or [{}] * len(ids)
        to_update_ids: list[str] = []
        updated_meta: list[dict] = []

        for doc_id, meta in zip(ids, metadatas):
            meta = dict(meta or {})
            changed = False
            if "acl_allow" not in meta:
                meta["acl_allow"] = ACL_ALLOW_DEFAULT
                changed = True
            if "classification" not in meta:
                meta["classification"] = CLASSIFICATION_DEFAULT
                changed = True
            if changed:
                to_update_ids.append(doc_id)
                updated_meta.append(meta)

        count = len(to_update_ids)
        total += count
        print(
            f"  ChromaDB collection '{coll_meta.name}': "
            f"{count} documents to backfill"
        )

        if not dry_run and to_update_ids:
            coll.update(ids=to_update_ids, metadatas=updated_meta)
            print(
                f"  ChromaDB collection '{coll_meta.name}': "
                f"backfilled {count} documents"
            )

    return total


# ── main ────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill acl_allow + classification on all V1 DepthFusion records.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts without modifying any data.",
    )
    parser.add_argument(
        "--db-dir",
        type=Path,
        default=Path.home() / ".claude",
        help="Directory containing DepthFusion SQLite files (default: ~/.claude).",
    )
    args = parser.parse_args(argv)

    db_dir: Path = args.db_dir
    dry_run: bool = args.dry_run

    if dry_run:
        print("DRY RUN — no data will be modified\n")

    grand_total = 0

    # ── 1. MemoryStore ──────────────────────────────────────────────────────
    memories_db = db_dir / ".depthfusion_memories.db"
    print(f"[1/6] MemoryStore — {memories_db}")
    counts = _sqlite_backfill(memories_db, ["memories"], dry_run)
    grand_total += sum(counts.values())
    print()

    # ── 2. FileIndex ────────────────────────────────────────────────────────
    file_index_db = db_dir / ".depthfusion_file_index.db"
    print(f"[2/6] FileIndex — {file_index_db}")
    counts = _sqlite_backfill(file_index_db, ["file_metadata"], dry_run)
    grand_total += sum(counts.values())
    print()

    # ── 3. GraphStore ───────────────────────────────────────────────────────
    graph_db = db_dir / "depthfusion-graph.db"
    print(f"[3/6] GraphStore — {graph_db}")
    counts = _sqlite_backfill(graph_db, ["entities", "edges"], dry_run)
    grand_total += sum(counts.values())
    print()

    # ── 4. TelemetryStore ───────────────────────────────────────────────────
    telemetry_db = db_dir / ".depthfusion_telemetry.db"
    print(f"[4/6] TelemetryStore — {telemetry_db}")
    counts = _sqlite_backfill(
        telemetry_db, ["candidate_skills", "telemetry_events"], dry_run
    )
    grand_total += sum(counts.values())
    print()

    # ── 5. EventLog (NDJSON) ────────────────────────────────────────────────
    # Check both canonical paths (config may vary between deployments).
    event_log_candidates = [
        Path.home() / ".claude" / "shared" / "depthfusion-events.jsonl",
        db_dir / "depthfusion_events.jsonl",
        db_dir / "shared" / "depthfusion-events.jsonl",
    ]
    found_event_log = False
    for candidate in event_log_candidates:
        if candidate.exists():
            print(f"[5/6] EventLog — {candidate}")
            updated = _ndjson_backfill(candidate, dry_run)
            grand_total += updated
            found_event_log = True
            print()
            break
    if not found_event_log:
        print(f"[5/6] EventLog — not found (checked {len(event_log_candidates)} paths)")
        print()

    # ── 6. VectorStore (ChromaDB) ───────────────────────────────────────────
    chroma_dir = db_dir / ".depthfusion_vectors"
    print(f"[6/6] VectorStore (ChromaDB) — {chroma_dir}")
    updated = _chromadb_backfill(chroma_dir, dry_run)
    grand_total += updated
    print()

    # ── summary ─────────────────────────────────────────────────────────────
    if dry_run:
        print(
            f"Dry run complete.  {grand_total} record(s) would be backfilled across all stores."
        )
    else:
        print(
            f"Backfill complete.  {grand_total} record(s) updated across all stores."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
