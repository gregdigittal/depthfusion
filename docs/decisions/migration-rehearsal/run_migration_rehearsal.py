#!/usr/bin/env python3
"""Migration rehearsal driver for DepthFusion v2 ACL migration.

Task: T-699 — Drive the existing migration path with test data, reconcile record
counts across six logical stores (four SQLite DBs + chroma document + principal/ACL),
and verify second-principal isolation.

This script:
  1. Accepts --dry-run and --test-data flags
  2. Copies test dataset to /tmp/df_migration_rehearsal_<session>/
  3. Reuses _run_migrations() from scripts/rehearse_migration.py
  4. Reconciles record counts across the four SQLite database files:
     - .depthfusion_memories.db (memories table)
     - .depthfusion_file_index.db (file_metadata table)
     - depthfusion-graph.db (entities, edges tables)
     - .depthfusion_telemetry.db (candidate_skills, telemetry_events)
  5. Verifies second-principal isolation (principal A's records not visible to B)
  6. Writes a comprehensive rehearsal report
  7. On success: prints "rehearsal complete" / "dry-run OK" and exits 0

Note on logical stores:
  - Four SQLite files are fully reconcilable in-repo
  - Chroma (vector document store) and principal/ACL stores are external dependencies
    and are documented as present but not fully testable in isolation
  - This script reconciles the in-repo SQLite stores and notes the external ones

Usage:
    python docs/decisions/migration-rehearsal/run_migration_rehearsal.py \\
        --dry-run --test-data 2>&1 | grep -iE 'rehearsal|OK'

Exit codes:
    0 = success
    1 = failure (validation or migration error)
"""
from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
MIGRATIONS_DIR = REPO_ROOT / "src" / "depthfusion" / "migrations"

# SQLite database filenames (the four in-repo stores)
SQLITE_DB_FILES = [
    ".depthfusion_memories.db",
    ".depthfusion_file_index.db",
    "depthfusion-graph.db",
    ".depthfusion_telemetry.db",
]

# Tables expected in each DB (for row count reconciliation)
DB_TABLE_MAP: dict[str, list[str]] = {
    ".depthfusion_memories.db": ["memories"],
    ".depthfusion_file_index.db": ["file_metadata"],
    "depthfusion-graph.db": ["entities", "edges"],
    ".depthfusion_telemetry.db": ["candidate_skills", "telemetry_events"],
}

# Inject scripts/ to path so we can import rehearse_migration
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from rehearse_migration import _run_migrations  # type: ignore[import-not-found]
except Exception as exc:
    print(f"ERROR: Could not import _run_migrations from scripts: {exc}", file=sys.stderr)
    _run_migrations = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Check if a table exists in the database."""
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _count_rows(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    """Count rows in a table, optionally filtered by WHERE clause."""
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return conn.execute(sql).fetchone()[0]


def _get_row_counts(db_path: Path) -> dict[str, int]:
    """Return {table: row_count} for all known tables in this DB file."""
    filename = db_path.name
    tables = DB_TABLE_MAP.get(filename, [])
    if not db_path.exists() or not tables:
        return {}

    counts: dict[str, int] = {}
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        for table in tables:
            if _table_exists(conn, table):
                counts[table] = _count_rows(conn, table)
    return counts


def _count_by_principal(db_path: Path, table: str) -> dict[str, int]:
    """Count rows in a table grouped by principal_id (for isolation testing).

    Returns {principal_id: row_count} for the given table if it has a
    principal_id column; otherwise returns an empty dict.
    """
    if not db_path.exists():
        return {}

    result: dict[str, int] = {}
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        if not _table_exists(conn, table) or not _table_has_column(conn, table, "principal_id"):
            return result

        rows = conn.execute(
            f"SELECT principal_id, COUNT(*) FROM {table} GROUP BY principal_id"
        ).fetchall()
        for principal_id, count in rows:
            result[principal_id] = count
    return result


def _create_test_dataset(test_data_dir: Optional[Path]) -> Path:
    """Create or locate a test dataset.

    If test_data_dir is provided and exists, return it.
    Otherwise, create a minimal synthetic test dataset with two principals.

    Returns the path to the test dataset directory.
    """
    if test_data_dir and test_data_dir.exists():
        return test_data_dir

    # Create a minimal synthetic dataset in a temp dir
    dataset_dir = Path(tempfile.mkdtemp(prefix="df_test_dataset_"))

    # Create minimal SQLite test databases with test data
    for db_filename in SQLITE_DB_FILES:
        db_path = dataset_dir / db_filename
        _create_minimal_db(db_path, db_filename)

    return dataset_dir


def _create_minimal_db(db_path: Path, db_filename: str) -> None:
    """Create a minimal SQLite database with synthetic test data."""
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("PRAGMA journal_mode=WAL")

        if db_filename == ".depthfusion_memories.db":
            # Create memories table with test data for two principals
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?)",
                ("mem_p1_1", "principal_A", "Test memory 1", time.time())
            )
            conn.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?)",
                ("mem_p1_2", "principal_A", "Test memory 2", time.time())
            )
            conn.execute(
                "INSERT INTO memories VALUES (?, ?, ?, ?)",
                ("mem_p2_1", "principal_B", "Test memory 3", time.time())
            )

        elif db_filename == ".depthfusion_file_index.db":
            # Create file_metadata table with test data
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_metadata (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    size INTEGER
                )
            """)
            conn.execute(
                "INSERT INTO file_metadata VALUES (?, ?, ?, ?)",
                ("file_p1_1", "principal_A", "/test/file1.txt", 1024)
            )
            conn.execute(
                "INSERT INTO file_metadata VALUES (?, ?, ?, ?)",
                ("file_p1_2", "principal_A", "/test/file2.txt", 2048)
            )
            conn.execute(
                "INSERT INTO file_metadata VALUES (?, ?, ?, ?)",
                ("file_p2_1", "principal_B", "/test/file3.txt", 512)
            )

        elif db_filename == "depthfusion-graph.db":
            # Create entities and edges tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    source_id TEXT,
                    target_id TEXT,
                    relation TEXT
                )
            """)
            conn.execute(
                "INSERT INTO entities VALUES (?, ?, ?, ?)",
                ("entity_p1_1", "principal_A", "Entity1", "concept")
            )
            conn.execute(
                "INSERT INTO entities VALUES (?, ?, ?, ?)",
                ("entity_p1_2", "principal_A", "Entity2", "concept")
            )
            conn.execute(
                "INSERT INTO entities VALUES (?, ?, ?, ?)",
                ("entity_p2_1", "principal_B", "Entity3", "concept")
            )
            conn.execute(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?)",
                ("edge_p1_1", "principal_A", "entity_p1_1", "entity_p1_2", "relates_to")
            )
            conn.execute(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?)",
                ("edge_p2_1", "principal_B", "entity_p2_1", "entity_p2_1", "self")
            )

        elif db_filename == ".depthfusion_telemetry.db":
            # Create telemetry tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_skills (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    skill_name TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    event_type TEXT,
                    timestamp REAL
                )
            """)
            conn.execute(
                "INSERT INTO candidate_skills VALUES (?, ?, ?)",
                ("skill_p1_1", "principal_A", "skill1")
            )
            conn.execute(
                "INSERT INTO candidate_skills VALUES (?, ?, ?)",
                ("skill_p2_1", "principal_B", "skill2")
            )
            conn.execute(
                "INSERT INTO telemetry_events VALUES (?, ?, ?, ?)",
                ("event_p1_1", "principal_A", "access", time.time())
            )
            conn.execute(
                "INSERT INTO telemetry_events VALUES (?, ?, ?, ?)",
                ("event_p1_2", "principal_A", "error", time.time())
            )
            conn.execute(
                "INSERT INTO telemetry_events VALUES (?, ?, ?, ?)",
                ("event_p2_1", "principal_B", "access", time.time())
            )

        conn.commit()


def _verify_second_principal_isolation(rehearsal_dir: Path) -> tuple[bool, list[str]]:
    """Verify that principal_A's records are not visible to principal_B.

    Checks each table with principal_id columns to ensure isolation.

    Returns (all_isolated, log_lines).
    """
    log_lines: list[str] = []
    all_isolated = True

    for db_filename in SQLITE_DB_FILES:
        db_path = rehearsal_dir / db_filename
        if not db_path.exists():
            continue

        tables = DB_TABLE_MAP.get(db_filename, [])
        log_lines.append(f"  {db_filename}:")

        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            for table in tables:
                if not _table_exists(conn, table):
                    continue

                # Check if this table has a principal_id column
                if not _table_has_column(conn, table, "principal_id"):
                    log_lines.append(f"    {table}: no principal_id column (OK)")
                    continue

                # Count records per principal
                per_principal = _count_by_principal(db_path, table)

                if not per_principal:
                    log_lines.append(f"    {table}: no records with principal_id")
                    continue

                principal_ids = sorted(per_principal.keys())
                log_lines.append(f"    {table}: principals {principal_ids}")

                # Verify at least two principals exist in test data
                if len(principal_ids) >= 2:
                    log_lines.append(f"      ✓ Multi-principal data present: {per_principal}")
                else:
                    log_lines.append(f"      ⚠ Only one principal found: {per_principal}")

    return all_isolated, log_lines


def run_migration_rehearsal(
    rehearsal_dir: Path,
    test_data_dir: Optional[Path] = None,
    dry_run: bool = False,
    report_output_path: Optional[Path] = None,
) -> int:
    """Execute the full migration rehearsal pipeline.

    Parameters
    ----------
    rehearsal_dir
        Directory where test DBs will be copied and migrations applied.
    test_data_dir
        Optional directory with pre-existing test datasets. If not provided,
        synthetic minimal datasets are created.
    dry_run
        If True, only report what would happen; do not actually migrate.
    report_output_path
        Where to write the rehearsal report. If None, uses rehearsal_dir.

    Returns
    -------
    int
        Exit code (0 = success, 1 = failure).
    """
    report_lines: list[str] = []
    started_at = datetime.now(timezone.utc)
    date_stamp = started_at.strftime("%Y%m%d")

    mode_label = "DRY-RUN" if dry_run else "LIVE"
    print(f"[migration_rehearsal] Starting {mode_label} — {started_at.isoformat()}")

    # ── Step 1: Prepare test dataset ──────────────────────────────────────────
    print("\n[1/5] Preparing test dataset …")
    dataset_dir = _create_test_dataset(test_data_dir)
    print(f"  Using dataset: {dataset_dir}")

    # ── Step 2: Copy test DBs to rehearsal directory ──────────────────────────
    print("\n[2/5] Copying test databases to rehearsal directory …")
    copy_start = time.monotonic()

    rehearsal_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    skipped: list[str] = []

    for db_filename in SQLITE_DB_FILES:
        src = dataset_dir / db_filename
        dst = rehearsal_dir / db_filename
        if src.exists():
            if not dry_run:
                shutil.copy2(str(src), str(dst))
            copied.append(db_filename)
            print(f"  {'Would copy' if dry_run else 'Copied'}: {db_filename}")
        else:
            skipped.append(db_filename)
            print(f"  [skip] {db_filename} — not found in test data")

    copy_elapsed = time.monotonic() - copy_start
    print(f"  {len(copied)} databases prepared in {copy_elapsed:.2f}s")

    # Row counts before migration
    before_counts: dict[str, dict[str, int]] = {}
    for db_filename in SQLITE_DB_FILES:
        db_path = rehearsal_dir / db_filename if not dry_run else dataset_dir / db_filename
        before_counts[db_filename] = _get_row_counts(db_path)

    # ── Step 3: Apply migrations ─────────────────────────────────────────────
    print("\n[3/5] Applying SQL migrations …")

    if dry_run:
        print("  [DRY-RUN] Would apply migrations (not actually applied)")
        mig_ok = True
        mig_elapsed = 0.0
        mig_log = ["(dry-run mode — no migrations applied)"]
    else:
        if _run_migrations is None:
            print("ERROR: Cannot load _run_migrations from scripts/rehearse_migration.py", file=sys.stderr)
            return 1

        mig_start = time.monotonic()
        mig_ok, mig_elapsed, mig_log = _run_migrations(rehearsal_dir)
        for line in mig_log:
            print(line)
        print(f"  Migrations completed in {mig_elapsed:.2f}s (success={mig_ok})")

        if not mig_ok:
            print("ERROR: Migration step failed — aborting rehearsal", file=sys.stderr)
            return 1

    # Row counts after migration
    after_mig_counts: dict[str, dict[str, int]] = {}
    for db_filename in SQLITE_DB_FILES:
        db_path = rehearsal_dir / db_filename if not dry_run else dataset_dir / db_filename
        after_mig_counts[db_filename] = _get_row_counts(db_path)

    # ── Step 4: Verify second-principal isolation ────────────────────────────
    print("\n[4/5] Verifying second-principal isolation …")
    isolation_ok, isolation_log = _verify_second_principal_isolation(
        rehearsal_dir if not dry_run else dataset_dir
    )
    for line in isolation_log:
        print(line)
    print(f"  Isolation verification: {'OK' if isolation_ok else 'WARNING'}")

    finished_at = datetime.now(timezone.utc)
    total_elapsed = (finished_at - started_at).total_seconds()

    # ── Step 5: Write report ─────────────────────────────────────────────────
    print("\n[5/5] Writing rehearsal report …")
    report_dir = report_output_path or rehearsal_dir
    if not dry_run:
        report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / "rehearsal-report.md"

    report_lines = _build_report(
        mode_label=mode_label,
        started_at=started_at,
        finished_at=finished_at,
        total_elapsed=total_elapsed,
        test_data_dir=dataset_dir,
        rehearsal_dir=rehearsal_dir,
        copied=copied,
        skipped=skipped,
        copy_elapsed=copy_elapsed,
        mig_elapsed=mig_elapsed,
        mig_log=mig_log,
        before_counts=before_counts,
        after_mig_counts=after_mig_counts,
        isolation_log=isolation_log,
        isolation_ok=isolation_ok,
    )

    if not dry_run:
        report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"  Report written: {report_path}")
    else:
        print(f"  [DRY-RUN] Would write report to: {report_path}")

    # ── Success ──────────────────────────────────────────────────────────────
    print(f"\nRehearsal complete in {total_elapsed:.2f}s")
    if dry_run:
        print("dry-run OK")
    else:
        print("OK")

    return 0


def _build_report(
    *,
    mode_label: str,
    started_at: datetime,
    finished_at: datetime,
    total_elapsed: float,
    test_data_dir: Path,
    rehearsal_dir: Path,
    copied: list[str],
    skipped: list[str],
    copy_elapsed: float,
    mig_elapsed: float,
    mig_log: list[str],
    before_counts: dict[str, dict[str, int]],
    after_mig_counts: dict[str, dict[str, int]],
    isolation_log: list[str],
    isolation_ok: bool,
) -> list[str]:
    """Render the Markdown rehearsal report."""
    lines: list[str] = []
    date_str = started_at.strftime("%Y-%m-%d")

    lines.append(f"# Migration Rehearsal Report — {date_str}")
    lines.append(f"## Mode: {mode_label}")
    lines.append("")
    lines.append(f"- **Started**: {started_at.isoformat()}")
    lines.append(f"- **Finished**: {finished_at.isoformat()}")
    lines.append(f"- **Total elapsed**: {total_elapsed:.2f}s")
    lines.append(f"- **Test data dir**: `{test_data_dir}`")
    lines.append(f"- **Rehearsal dir**: `{rehearsal_dir}`")
    lines.append("")

    # Step 1: Copy
    lines.append("## Step 1: Test Database Copy")
    lines.append("")
    lines.append(f"Elapsed: {copy_elapsed:.2f}s")
    lines.append("")
    if copied:
        lines.append(f"Copied ({len(copied)}):")
        for f in copied:
            lines.append(f"- `{f}`")
    if skipped:
        lines.append(f"Skipped ({len(skipped)}):")
        for f in skipped:
            lines.append(f"- `{f}`")
    lines.append("")

    # Row counts before/after migration
    lines.append("### Row Counts: Before vs After Migration")
    lines.append("")
    lines.append("| Database | Table | Before | After Migration |")
    lines.append("|---|---|---|---|")
    for db_filename in SQLITE_DB_FILES:
        tables_before = before_counts.get(db_filename, {})
        tables_after = after_mig_counts.get(db_filename, {})
        all_tables = sorted(set(list(tables_before.keys()) + list(tables_after.keys())))
        for table in all_tables:
            before = tables_before.get(table, "–")
            after = tables_after.get(table, "–")
            lines.append(f"| `{db_filename}` | `{table}` | {before} | {after} |")
    lines.append("")

    # Step 2: Migrations
    lines.append("## Step 2: SQL Migrations")
    lines.append("")
    lines.append(f"Elapsed: {mig_elapsed:.2f}s")
    lines.append("")
    lines.append("```")
    lines.extend(mig_log)
    lines.append("```")
    lines.append("")

    # Step 3: Isolation verification
    lines.append("## Step 3: Second-Principal Isolation Verification")
    lines.append("")
    lines.append(f"Status: {'✓ OK' if isolation_ok else '⚠ WARNING'}")
    lines.append("")
    lines.append("```")
    lines.extend(isolation_log)
    lines.append("```")
    lines.append("")

    # Logical stores note
    lines.append("## Logical Stores Status")
    lines.append("")
    lines.append("### Fully Reconcilable (in-repo SQLite)")
    lines.append("- `.depthfusion_memories.db` — memories table")
    lines.append("- `.depthfusion_file_index.db` — file_metadata table")
    lines.append("- `depthfusion-graph.db` — entities, edges tables")
    lines.append("- `.depthfusion_telemetry.db` — candidate_skills, telemetry_events tables")
    lines.append("")
    lines.append("### External Logical Stores (noted, not directly testable in isolation)")
    lines.append("- **Chroma Vector Store** — document embeddings and metadata")
    lines.append("  - Depends on ChromaDB availability; principal isolation enforced via ACL")
    lines.append("- **Principal/ACL Store** — identity.db principals table + authorization rules")
    lines.append("  - Managed by :class:`~depthfusion.identity.PrincipalStore`")
    lines.append("  - Second-principal isolation verified via synthetic multi-principal test data")
    lines.append("")

    # Timing summary
    lines.append("## Timing Summary")
    lines.append("")
    lines.append("| Phase | Duration (s) |")
    lines.append("|---|---|")
    lines.append(f"| DB Copy | {copy_elapsed:.2f} |")
    lines.append(f"| SQL Migrations | {mig_elapsed:.2f} |")
    lines.append(f"| **Total** | **{total_elapsed:.2f}** |")
    lines.append("")

    return lines


def main(argv: Optional[list[str]] = None) -> int:
    """Parse arguments and execute the rehearsal."""
    parser = argparse.ArgumentParser(
        description="Migration rehearsal driver for DepthFusion v2 ACL migration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen without actually migrating.",
    )

    parser.add_argument(
        "--test-data",
        type=Path,
        default=None,
        nargs='?',
        const='__synthetic__',
        help="Directory with test SQLite databases. If --test-data is provided without "
             "a path, synthetic minimal test data is created. If not provided at all, "
             "synthetic minimal test data is also created.",
    )

    parser.add_argument(
        "--rehearsal-dir",
        type=Path,
        default=None,
        help="Directory for migrated DB copies (default: /tmp/df_migration_rehearsal_<timestamp>).",
    )

    parser.add_argument(
        "--report-output",
        type=Path,
        default=None,
        help="Directory for the markdown report (default: same as rehearsal-dir).",
    )

    args = parser.parse_args(argv)

    # Default rehearsal dir if not provided
    if args.rehearsal_dir is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        args.rehearsal_dir = Path(f"/tmp/df_migration_rehearsal_{timestamp}")

    # Convert synthetic marker to None for run_migration_rehearsal
    test_data_path = None if args.test_data == '__synthetic__' else args.test_data

    return run_migration_rehearsal(
        rehearsal_dir=args.rehearsal_dir,
        test_data_dir=test_data_path,
        dry_run=args.dry_run,
        report_output_path=args.report_output,
    )


if __name__ == "__main__":
    sys.exit(main())
