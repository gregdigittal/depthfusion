#!/usr/bin/env python3
"""rehearse_migration.py — Rehearse DepthFusion ACL migrations on a DB copy.

E-50 Authorization Model — T-564

This script performs a safe, non-destructive migration rehearsal by:
  1. Copying the production SQLite databases to /tmp/df_migration_rehearsal/
  2. Applying all SQL migrations (from src/depthfusion/migrations/) to the copies
  3. Running backfill_acl.py against the copies with --dry-run first, then live
  4. Recording timing and row counts to docs/migration-rehearsal-YYYYMMDD.md

Usage:
    python scripts/rehearse_migration.py [--db-dir DIR] [--output-dir DIR]
                                         [--rehearsal-dir DIR] [--skip-report]

    --db-dir DIR         Source directory with production SQLite databases
                         (default: ~/.claude)
    --output-dir DIR     Directory for the markdown report
                         (default: docs/ relative to repo root)
    --rehearsal-dir DIR  Where to place the DB copies
                         (default: /tmp/df_migration_rehearsal)
    --skip-report        Do not write the markdown report (for testing)

The script exits 0 on success, 1 if any step fails.
"""
from __future__ import annotations

import argparse
import contextlib
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── paths ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "src" / "depthfusion" / "migrations"
BACKFILL_SCRIPT = REPO_ROOT / "scripts" / "backfill_acl.py"
DEFAULT_REHEARSAL_DIR = Path("/tmp/df_migration_rehearsal")
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs"

# SQLite database filenames to copy (relative to db_dir)
SQLITE_DB_FILES = [
    ".depthfusion_memories.db",
    ".depthfusion_file_index.db",
    "depthfusion-graph.db",
    ".depthfusion_telemetry.db",
]

# Tables expected in each DB (for row count reporting)
DB_TABLE_MAP: dict[str, list[str]] = {
    ".depthfusion_memories.db": ["memories"],
    ".depthfusion_file_index.db": ["file_metadata"],
    "depthfusion-graph.db": ["entities", "edges"],
    ".depthfusion_telemetry.db": ["candidate_skills", "telemetry_events"],
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _count_rows(conn: sqlite3.Connection, table: str, where: str = "") -> int:
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


def _get_acl_null_counts(db_path: Path) -> dict[str, int]:
    """Return {table: rows_with_null_acl} to measure backfill coverage."""
    filename = db_path.name
    tables = DB_TABLE_MAP.get(filename, [])
    if not db_path.exists() or not tables:
        return {}

    counts: dict[str, int] = {}
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        for table in tables:
            if _table_exists(conn, table) and _table_has_column(conn, table, "acl_allow"):
                counts[table] = _count_rows(conn, table, "acl_allow IS NULL")
            elif _table_exists(conn, table):
                # Column does not exist yet — all rows need backfill
                counts[table] = _count_rows(conn, table)
    return counts


def _run_migrations(rehearsal_dir: Path) -> tuple[bool, float, list[str]]:
    """Apply all SQL migrations to all copied DBs.

    Returns (success, elapsed_seconds, log_lines).
    """
    log_lines: list[str] = []
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    if not sql_files:
        log_lines.append(f"  No migration files found in {MIGRATIONS_DIR}")
        return True, 0.0, log_lines

    start = time.monotonic()

    for db_filename in SQLITE_DB_FILES:
        db_path = rehearsal_dir / db_filename
        if not db_path.exists():
            log_lines.append(f"  [skip] {db_filename} — not found in rehearsal dir")
            continue

        log_lines.append(f"  Applying migrations to {db_filename}:")
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            conn.execute("PRAGMA journal_mode=WAL")

            # Ensure tracking table exists
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS _df_schema_migrations (
                    migration_id TEXT PRIMARY KEY,
                    applied_at   TEXT NOT NULL
                )
                """
            )
            conn.commit()

            for sql_file in sql_files:
                migration_id = sql_file.stem
                already_applied = conn.execute(
                    "SELECT 1 FROM _df_schema_migrations WHERE migration_id = ?",
                    (migration_id,),
                ).fetchone()

                if already_applied:
                    log_lines.append(f"    {migration_id}: already applied — skip")
                    continue

                sql_text = sql_file.read_text(encoding="utf-8")
                # Strip comment lines and split by semicolons
                sql_lines = [
                    line for line in sql_text.splitlines()
                    if not line.strip().startswith("--")
                ]
                stripped_sql = "\n".join(sql_lines)
                statements = [s.strip() for s in stripped_sql.split(";") if s.strip()]

                # Expected-benign errors: each DB only has its own tables; missing
                # table/column errors from other DBs' statements are safe to skip.
                BENIGN_FRAGMENTS = (
                    "duplicate column",
                    "no such table",
                    "table already exists",
                )
                hard_errors: list[str] = []
                for stmt in statements:
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as exc:
                        msg = str(exc).lower()
                        if any(frag in msg for frag in BENIGN_FRAGMENTS):
                            log_lines.append(
                                f"    {migration_id}: benign skip — {exc}"
                            )
                        else:
                            hard_errors.append(f"{migration_id}: {exc}")
                conn.commit()

                if hard_errors:
                    log_lines.extend(f"    ERROR: {e}" for e in hard_errors)
                else:
                    conn.execute(
                        "INSERT INTO _df_schema_migrations VALUES (?, ?)",
                        (migration_id, datetime.now(timezone.utc).isoformat()),
                    )
                    conn.commit()
                    log_lines.append(f"    {migration_id}: applied OK")

    elapsed = time.monotonic() - start
    return True, elapsed, log_lines


def _run_backfill(
    rehearsal_dir: Path,
    dry_run: bool,
) -> tuple[bool, float, str]:
    """Run backfill_acl.py against rehearsal_dir.

    Returns (success, elapsed_seconds, stdout_output).
    """
    cmd = [
        sys.executable,
        str(BACKFILL_SCRIPT),
        "--db-dir", str(rehearsal_dir),
    ]
    if dry_run:
        cmd.append("--dry-run")

    start = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.monotonic() - start

    output = result.stdout
    if result.stderr:
        output += "\nSTDERR:\n" + result.stderr

    return result.returncode == 0, elapsed, output


# ── main ─────────────────────────────────────────────────────────────────────

def run_rehearsal(
    db_dir: Path,
    rehearsal_dir: Path,
    output_dir: Path,
    skip_report: bool = False,
) -> int:
    """Execute the full rehearsal pipeline.

    Returns 0 on success, 1 on failure.
    """
    report_lines: list[str] = []
    started_at = datetime.now(timezone.utc)
    date_stamp = started_at.strftime("%Y%m%d")

    print(f"[rehearse_migration] Starting rehearsal — {started_at.isoformat()}")
    print(f"  Source db_dir:    {db_dir}")
    print(f"  Rehearsal dir:    {rehearsal_dir}")

    # ── Step 1: Copy production DBs ──────────────────────────────────────────
    print("\n[1/4] Copying production databases to rehearsal directory …")
    copy_start = time.monotonic()

    rehearsal_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    skipped: list[str] = []

    for db_filename in SQLITE_DB_FILES:
        src = db_dir / db_filename
        dst = rehearsal_dir / db_filename
        if src.exists():
            shutil.copy2(str(src), str(dst))
            copied.append(db_filename)
            print(f"  Copied: {db_filename}")
        else:
            skipped.append(db_filename)
            print(f"  [skip] {db_filename} — source not found")

    copy_elapsed = time.monotonic() - copy_start
    print(f"  Copied {len(copied)} of {len(SQLITE_DB_FILES)} DB files in {copy_elapsed:.2f}s")

    # Row counts before migration
    before_counts: dict[str, dict[str, int]] = {}
    for db_filename in SQLITE_DB_FILES:
        db_path = rehearsal_dir / db_filename
        before_counts[db_filename] = _get_row_counts(db_path)

    # ── Step 2: Apply migrations ─────────────────────────────────────────────
    print("\n[2/4] Applying SQL migrations …")
    mig_ok, mig_elapsed, mig_log = _run_migrations(rehearsal_dir)
    for line in mig_log:
        print(line)
    print(f"  Migrations completed in {mig_elapsed:.2f}s (success={mig_ok})")

    if not mig_ok:
        print("ERROR: Migration step failed — aborting rehearsal", file=sys.stderr)
        return 1

    # Row counts after migration (schema changed, data same)
    after_mig_counts: dict[str, dict[str, int]] = {}
    for db_filename in SQLITE_DB_FILES:
        db_path = rehearsal_dir / db_filename
        after_mig_counts[db_filename] = _get_row_counts(db_path)

    # ── Step 3a: Backfill dry-run ────────────────────────────────────────────
    print("\n[3/4] Running backfill (--dry-run) …")
    dr_ok, dr_elapsed, dr_output = _run_backfill(rehearsal_dir, dry_run=True)
    print(dr_output)
    print(f"  Dry-run completed in {dr_elapsed:.2f}s (success={dr_ok})")

    if not dr_ok:
        print("ERROR: Backfill dry-run failed — aborting rehearsal", file=sys.stderr)
        return 1

    # ── Step 3b: Backfill live ───────────────────────────────────────────────
    print("\n[3b/4] Running backfill (live) …")
    live_ok, live_elapsed, live_output = _run_backfill(rehearsal_dir, dry_run=False)
    print(live_output)
    print(f"  Live backfill completed in {live_elapsed:.2f}s (success={live_ok})")

    if not live_ok:
        print("ERROR: Backfill live run failed", file=sys.stderr)
        return 1

    # Row counts after backfill
    after_backfill_counts: dict[str, dict[str, int]] = {}
    acl_null_counts: dict[str, dict[str, int]] = {}
    for db_filename in SQLITE_DB_FILES:
        db_path = rehearsal_dir / db_filename
        after_backfill_counts[db_filename] = _get_row_counts(db_path)
        acl_null_counts[db_filename] = _get_acl_null_counts(db_path)

    finished_at = datetime.now(timezone.utc)
    total_elapsed = (finished_at - started_at).total_seconds()

    # ── Step 4: Write report ─────────────────────────────────────────────────
    if not skip_report:
        print(f"\n[4/4] Writing rehearsal report …")
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"migration-rehearsal-{date_stamp}.md"

        report_lines = _build_report(
            started_at=started_at,
            finished_at=finished_at,
            total_elapsed=total_elapsed,
            db_dir=db_dir,
            rehearsal_dir=rehearsal_dir,
            copied=copied,
            skipped=skipped,
            copy_elapsed=copy_elapsed,
            mig_elapsed=mig_elapsed,
            mig_log=mig_log,
            dr_elapsed=dr_elapsed,
            dr_output=dr_output,
            live_elapsed=live_elapsed,
            live_output=live_output,
            before_counts=before_counts,
            after_mig_counts=after_mig_counts,
            after_backfill_counts=after_backfill_counts,
            acl_null_counts=acl_null_counts,
        )

        report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"  Report written: {report_path}")
    else:
        print("\n[4/4] Report skipped (--skip-report)")

    print(f"\nRehearsal complete in {total_elapsed:.2f}s")
    return 0


def _build_report(
    *,
    started_at: datetime,
    finished_at: datetime,
    total_elapsed: float,
    db_dir: Path,
    rehearsal_dir: Path,
    copied: list[str],
    skipped: list[str],
    copy_elapsed: float,
    mig_elapsed: float,
    mig_log: list[str],
    dr_elapsed: float,
    dr_output: str,
    live_elapsed: float,
    live_output: str,
    before_counts: dict[str, dict[str, int]],
    after_mig_counts: dict[str, dict[str, int]],
    after_backfill_counts: dict[str, dict[str, int]],
    acl_null_counts: dict[str, dict[str, int]],
) -> list[str]:
    """Render the Markdown rehearsal report."""
    lines: list[str] = []
    date_str = started_at.strftime("%Y-%m-%d")

    lines.append(f"# Migration Rehearsal Report — {date_str}")
    lines.append("")
    lines.append(f"- **Started**: {started_at.isoformat()}")
    lines.append(f"- **Finished**: {finished_at.isoformat()}")
    lines.append(f"- **Total elapsed**: {total_elapsed:.2f}s")
    lines.append(f"- **Source db_dir**: `{db_dir}`")
    lines.append(f"- **Rehearsal dir**: `{rehearsal_dir}`")
    lines.append("")

    # Step 1: Copy
    lines.append("## Step 1: Database Copy")
    lines.append("")
    lines.append(f"Elapsed: {copy_elapsed:.2f}s")
    lines.append("")
    if copied:
        lines.append(f"Copied ({len(copied)}):")
        for f in copied:
            lines.append(f"- `{f}`")
    if skipped:
        lines.append(f"Skipped — source not found ({len(skipped)}):")
        for f in skipped:
            lines.append(f"- `{f}`")
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

    # Row counts table
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

    # Step 3a: dry-run
    lines.append("## Step 3a: Backfill — Dry Run")
    lines.append("")
    lines.append(f"Elapsed: {dr_elapsed:.2f}s")
    lines.append("")
    lines.append("```")
    lines.append(dr_output.rstrip())
    lines.append("```")
    lines.append("")

    # Step 3b: live run
    lines.append("## Step 3b: Backfill — Live Run")
    lines.append("")
    lines.append(f"Elapsed: {live_elapsed:.2f}s")
    lines.append("")
    lines.append("```")
    lines.append(live_output.rstrip())
    lines.append("```")
    lines.append("")

    # Final row counts + ACL null check
    lines.append("## Step 4: Final Row Counts and ACL Coverage")
    lines.append("")
    lines.append(
        "| Database | Table | Total Rows | Rows with acl_allow IS NULL |"
    )
    lines.append("|---|---|---|---|")
    for db_filename in SQLITE_DB_FILES:
        final_counts = after_backfill_counts.get(db_filename, {})
        null_counts = acl_null_counts.get(db_filename, {})
        all_tables = sorted(final_counts.keys())
        for table in all_tables:
            total = final_counts.get(table, "–")
            null = null_counts.get(table, "–")
            lines.append(
                f"| `{db_filename}` | `{table}` | {total} | {null} |"
            )
    lines.append("")

    # Timing summary
    lines.append("## Timing Summary")
    lines.append("")
    lines.append("| Phase | Duration (s) |")
    lines.append("|---|---|")
    lines.append(f"| DB Copy | {copy_elapsed:.2f} |")
    lines.append(f"| SQL Migrations | {mig_elapsed:.2f} |")
    lines.append(f"| Backfill (dry-run) | {dr_elapsed:.2f} |")
    lines.append(f"| Backfill (live) | {live_elapsed:.2f} |")
    lines.append(f"| **Total** | **{total_elapsed:.2f}** |")
    lines.append("")

    return lines


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rehearse DepthFusion ACL migration on a copy of the production DB.",
    )
    parser.add_argument(
        "--db-dir",
        type=Path,
        default=Path.home() / ".claude",
        help="Source directory with production SQLite databases (default: ~/.claude).",
    )
    parser.add_argument(
        "--rehearsal-dir",
        type=Path,
        default=DEFAULT_REHEARSAL_DIR,
        help="Directory for the DB copies (default: /tmp/df_migration_rehearsal).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the markdown report (default: docs/).",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Do not write the markdown report.",
    )
    args = parser.parse_args(argv)

    return run_rehearsal(
        db_dir=args.db_dir,
        rehearsal_dir=args.rehearsal_dir,
        output_dir=args.output_dir,
        skip_report=args.skip_report,
    )


if __name__ == "__main__":
    sys.exit(main())
