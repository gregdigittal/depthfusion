"""tests/test_migration_rehearsal.py — Unit tests for scripts/rehearse_migration.py.

T-564: Migration rehearsal tests.

All tests use fixture DBs — never the real production databases.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Generator

import pytest

# Ensure the scripts directory is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from rehearse_migration import (  # noqa: E402  (import after sys.path update)
    _count_rows,
    _get_acl_null_counts,
    _get_row_counts,
    _run_backfill,
    _run_migrations,
    _table_exists,
    _table_has_column,
    run_rehearsal,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_memories_db(path: Path) -> None:
    """Create a minimal .depthfusion_memories.db fixture."""
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id   TEXT PRIMARY KEY,
                body TEXT
            )
            """
        )
        conn.executemany(
            "INSERT OR IGNORE INTO memories VALUES (?, ?)",
            [("m1", "memory one"), ("m2", "memory two"), ("m3", "memory three")],
        )
        conn.commit()


def _make_file_index_db(path: Path) -> None:
    """Create a minimal .depthfusion_file_index.db fixture."""
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS file_metadata (
                path TEXT PRIMARY KEY,
                mtime REAL
            )
            """
        )
        conn.executemany(
            "INSERT OR IGNORE INTO file_metadata VALUES (?, ?)",
            [("/a/b.py", 1.0), ("/c/d.py", 2.0)],
        )
        conn.commit()


def _make_graph_db(path: Path) -> None:
    """Create a minimal depthfusion-graph.db fixture."""
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entities (
                id   TEXT PRIMARY KEY,
                name TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS edges (
                id  INTEGER PRIMARY KEY AUTOINCREMENT,
                src TEXT,
                dst TEXT
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO entities VALUES ('e1', 'entity one')")
        conn.execute("INSERT INTO edges (src, dst) VALUES ('e1', 'e2')")
        conn.commit()


def _make_telemetry_db(path: Path) -> None:
    """Create a minimal .depthfusion_telemetry.db fixture."""
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candidate_skills (
                id TEXT PRIMARY KEY,
                score REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT
            )
            """
        )
        conn.execute("INSERT OR IGNORE INTO candidate_skills VALUES ('s1', 0.9)")
        conn.execute("INSERT INTO telemetry_events (event_type) VALUES ('recall')")
        conn.commit()


@pytest.fixture()
def fixture_db_dir(tmp_path: Path) -> Path:
    """Return a temp directory populated with all four fixture SQLite DBs."""
    _make_memories_db(tmp_path / ".depthfusion_memories.db")
    _make_file_index_db(tmp_path / ".depthfusion_file_index.db")
    _make_graph_db(tmp_path / "depthfusion-graph.db")
    _make_telemetry_db(tmp_path / ".depthfusion_telemetry.db")
    return tmp_path


@pytest.fixture()
def rehearsal_dir(tmp_path: Path) -> Path:
    """Return a fresh temporary directory for rehearsal copies."""
    d = tmp_path / "rehearsal"
    d.mkdir()
    return d


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    """Return a temporary output directory for reports."""
    d = tmp_path / "docs"
    d.mkdir()
    return d


# ── Helper function tests ─────────────────────────────────────────────────────

class TestTableHelpers:
    """Tests for the _table_exists and _table_has_column helpers."""

    def test_table_exists_true(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.execute("CREATE TABLE foo (id TEXT)")
            assert _table_exists(conn, "foo") is True

    def test_table_exists_false(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.execute("CREATE TABLE foo (id TEXT)")
            assert _table_exists(conn, "bar") is False

    def test_table_has_column_true(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.execute("CREATE TABLE foo (id TEXT, value TEXT)")
            assert _table_has_column(conn, "foo", "value") is True

    def test_table_has_column_false(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.execute("CREATE TABLE foo (id TEXT)")
            assert _table_has_column(conn, "foo", "missing_col") is False

    def test_count_rows_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.execute("CREATE TABLE foo (id TEXT)")
            assert _count_rows(conn, "foo") == 0

    def test_count_rows_with_where(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.execute("CREATE TABLE foo (id TEXT, acl_allow TEXT)")
            conn.execute("INSERT INTO foo VALUES ('1', NULL)")
            conn.execute("INSERT INTO foo VALUES ('2', '[\"greg\"]')")
            conn.commit()
            assert _count_rows(conn, "foo", "acl_allow IS NULL") == 1


class TestGetRowCounts:
    """Tests for the _get_row_counts helper."""

    def test_returns_correct_counts(self, fixture_db_dir: Path) -> None:
        counts = _get_row_counts(fixture_db_dir / ".depthfusion_memories.db")
        assert counts == {"memories": 3}

    def test_missing_db_returns_empty(self, tmp_path: Path) -> None:
        counts = _get_row_counts(tmp_path / "nonexistent.db")
        assert counts == {}

    def test_graph_db_counts_both_tables(self, fixture_db_dir: Path) -> None:
        counts = _get_row_counts(fixture_db_dir / "depthfusion-graph.db")
        assert "entities" in counts
        assert "edges" in counts
        assert counts["entities"] == 1
        assert counts["edges"] == 1


class TestGetAclNullCounts:
    """Tests for the _get_acl_null_counts helper."""

    def test_no_acl_column_returns_total_count(self, fixture_db_dir: Path) -> None:
        # Before migration, no acl_allow column — all rows count as "null"
        counts = _get_acl_null_counts(fixture_db_dir / ".depthfusion_memories.db")
        assert counts == {"memories": 3}

    def test_with_acl_column_and_nulls(self, tmp_path: Path) -> None:
        db = tmp_path / ".depthfusion_memories.db"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.execute(
                "CREATE TABLE memories (id TEXT PRIMARY KEY, acl_allow TEXT)"
            )
            conn.execute("INSERT INTO memories VALUES ('1', NULL)")
            conn.execute("INSERT INTO memories VALUES ('2', '[\"greg\"]')")
            conn.commit()
        counts = _get_acl_null_counts(db)
        assert counts == {"memories": 1}

    def test_all_acl_populated_returns_zero(self, tmp_path: Path) -> None:
        db = tmp_path / ".depthfusion_memories.db"
        with contextlib.closing(sqlite3.connect(str(db))) as conn:
            conn.execute(
                "CREATE TABLE memories (id TEXT PRIMARY KEY, acl_allow TEXT)"
            )
            conn.execute("INSERT INTO memories VALUES ('1', '[\"greg\"]')")
            conn.commit()
        counts = _get_acl_null_counts(db)
        assert counts == {"memories": 0}


# ── Migration application tests ───────────────────────────────────────────────

class TestRunMigrations:
    """Tests for _run_migrations."""

    def test_adds_acl_columns(
        self, fixture_db_dir: Path, rehearsal_dir: Path
    ) -> None:
        """Migrations must add acl_allow + classification to memories table."""
        import shutil
        src = fixture_db_dir / ".depthfusion_memories.db"
        dst = rehearsal_dir / ".depthfusion_memories.db"
        shutil.copy2(str(src), str(dst))

        ok, elapsed, log = _run_migrations(rehearsal_dir)
        assert ok is True

        with contextlib.closing(sqlite3.connect(str(dst))) as conn:
            assert _table_has_column(conn, "memories", "acl_allow")
            assert _table_has_column(conn, "memories", "classification")

    def test_migration_is_idempotent(
        self, fixture_db_dir: Path, rehearsal_dir: Path
    ) -> None:
        """Running migrations twice must not fail or duplicate rows."""
        import shutil
        src = fixture_db_dir / ".depthfusion_memories.db"
        dst = rehearsal_dir / ".depthfusion_memories.db"
        shutil.copy2(str(src), str(dst))

        ok1, _, _ = _run_migrations(rehearsal_dir)
        ok2, _, log2 = _run_migrations(rehearsal_dir)
        assert ok1 is True
        assert ok2 is True

        # Second run should log "already applied"
        combined = "\n".join(log2)
        assert "already applied" in combined or "skip" in combined.lower()

    def test_creates_tracking_table(
        self, fixture_db_dir: Path, rehearsal_dir: Path
    ) -> None:
        """_df_schema_migrations table must be created by the migration runner."""
        import shutil
        src = fixture_db_dir / ".depthfusion_memories.db"
        dst = rehearsal_dir / ".depthfusion_memories.db"
        shutil.copy2(str(src), str(dst))

        _run_migrations(rehearsal_dir)

        with contextlib.closing(sqlite3.connect(str(dst))) as conn:
            assert _table_exists(conn, "_df_schema_migrations")

    def test_skips_missing_db(self, tmp_path: Path) -> None:
        """Missing DB files are skipped without causing failure."""
        ok, _, log = _run_migrations(tmp_path)
        assert ok is True

    def test_returns_false_on_hard_error(
        self, rehearsal_dir: Path, tmp_path: Path
    ) -> None:
        """A non-benign OperationalError must cause _run_migrations to return False."""
        import shutil as _shutil
        from unittest.mock import patch

        # Inject a bad SQL file with a syntax error (message contains neither
        # "no such table", "duplicate column", nor "table already exists", so it
        # is treated as a *hard* error by the benign-fragment filter).
        bad_sql = rehearsal_dir / "bad_sql_for_test.sql"
        bad_sql.write_text("INVALID_SYNTAX_HARD_ERROR_FOR_TESTING;\n", encoding="utf-8")

        # Seed one DB so the runner has something to open.
        db_path = rehearsal_dir / ".depthfusion_memories.db"
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            conn.execute("CREATE TABLE memories (id TEXT PRIMARY KEY)")
            conn.commit()

        import rehearse_migration as rm

        original_migrations_dir = rm.MIGRATIONS_DIR
        try:
            # Point MIGRATIONS_DIR at our rehearsal_dir which has the bad SQL.
            rm.MIGRATIONS_DIR = rehearsal_dir
            ok, _elapsed, log = _run_migrations(rehearsal_dir)
        finally:
            rm.MIGRATIONS_DIR = original_migrations_dir
            bad_sql.unlink(missing_ok=True)

        assert ok is False, "Expected False when a non-benign OperationalError occurs"
        assert any("ERROR:" in line for line in log), (
            "Expected at least one ERROR line in the log"
        )


# ── Backfill invocation tests ─────────────────────────────────────────────────

class TestRunBackfill:
    """Tests for _run_backfill."""

    def test_dry_run_exits_zero(
        self, fixture_db_dir: Path, rehearsal_dir: Path
    ) -> None:
        import shutil
        for db_name in [".depthfusion_memories.db"]:
            shutil.copy2(
                str(fixture_db_dir / db_name),
                str(rehearsal_dir / db_name),
            )
        ok, elapsed, output = _run_backfill(rehearsal_dir, dry_run=True)
        assert ok is True
        assert "DRY RUN" in output

    def test_live_run_exits_zero(
        self, fixture_db_dir: Path, rehearsal_dir: Path
    ) -> None:
        import shutil
        for db_name in [".depthfusion_memories.db"]:
            shutil.copy2(
                str(fixture_db_dir / db_name),
                str(rehearsal_dir / db_name),
            )
        ok, elapsed, output = _run_backfill(rehearsal_dir, dry_run=False)
        assert ok is True
        assert "complete" in output.lower() or "backfill" in output.lower()

    def test_backfill_populates_acl_columns(
        self, fixture_db_dir: Path, rehearsal_dir: Path
    ) -> None:
        import shutil
        src = fixture_db_dir / ".depthfusion_memories.db"
        dst = rehearsal_dir / ".depthfusion_memories.db"
        shutil.copy2(str(src), str(dst))

        # Run migrations first so the columns exist
        _run_migrations(rehearsal_dir)

        # Run live backfill
        ok, _, output = _run_backfill(rehearsal_dir, dry_run=False)
        assert ok is True

        # Verify all rows now have acl_allow populated
        with contextlib.closing(sqlite3.connect(str(dst))) as conn:
            null_count = _count_rows(conn, "memories", "acl_allow IS NULL")
            assert null_count == 0


# ── Full rehearsal pipeline test ──────────────────────────────────────────────

class TestRunRehearsal:
    """End-to-end tests for the run_rehearsal() function."""

    def test_full_pipeline_exits_zero(
        self,
        fixture_db_dir: Path,
        tmp_path: Path,
        output_dir: Path,
    ) -> None:
        rehearsal_dir = tmp_path / "rehearsal_full"
        exit_code = run_rehearsal(
            db_dir=fixture_db_dir,
            rehearsal_dir=rehearsal_dir,
            output_dir=output_dir,
            skip_report=False,
        )
        assert exit_code == 0

    def test_report_file_is_written(
        self,
        fixture_db_dir: Path,
        tmp_path: Path,
        output_dir: Path,
    ) -> None:
        from datetime import datetime, timezone

        rehearsal_dir = tmp_path / "rehearsal_report"
        run_rehearsal(
            db_dir=fixture_db_dir,
            rehearsal_dir=rehearsal_dir,
            output_dir=output_dir,
            skip_report=False,
        )
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        report_path = output_dir / f"migration-rehearsal-{date_stamp}.md"
        assert report_path.exists(), f"Expected report at {report_path}"

    def test_report_contains_timing_section(
        self,
        fixture_db_dir: Path,
        tmp_path: Path,
        output_dir: Path,
    ) -> None:
        from datetime import datetime, timezone

        rehearsal_dir = tmp_path / "rehearsal_timing"
        run_rehearsal(
            db_dir=fixture_db_dir,
            rehearsal_dir=rehearsal_dir,
            output_dir=output_dir,
            skip_report=False,
        )
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        report_path = output_dir / f"migration-rehearsal-{date_stamp}.md"
        content = report_path.read_text(encoding="utf-8")
        assert "Timing Summary" in content
        assert "Total" in content

    def test_report_contains_row_count_table(
        self,
        fixture_db_dir: Path,
        tmp_path: Path,
        output_dir: Path,
    ) -> None:
        from datetime import datetime, timezone

        rehearsal_dir = tmp_path / "rehearsal_row_counts"
        run_rehearsal(
            db_dir=fixture_db_dir,
            rehearsal_dir=rehearsal_dir,
            output_dir=output_dir,
            skip_report=False,
        )
        date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        report_path = output_dir / f"migration-rehearsal-{date_stamp}.md"
        content = report_path.read_text(encoding="utf-8")
        assert "Row Counts" in content
        assert "memories" in content

    def test_skip_report_does_not_write_file(
        self,
        fixture_db_dir: Path,
        tmp_path: Path,
        output_dir: Path,
    ) -> None:
        rehearsal_dir = tmp_path / "rehearsal_noreport"
        run_rehearsal(
            db_dir=fixture_db_dir,
            rehearsal_dir=rehearsal_dir,
            output_dir=output_dir,
            skip_report=True,
        )
        # No markdown report should have been written
        md_files = list(output_dir.glob("migration-rehearsal-*.md"))
        assert len(md_files) == 0

    def test_missing_source_dbs_does_not_crash(
        self,
        tmp_path: Path,
        output_dir: Path,
    ) -> None:
        """An empty source db_dir must not crash — it completes with 0 copies."""
        empty_dir = tmp_path / "empty_source"
        empty_dir.mkdir()
        rehearsal_dir = tmp_path / "rehearsal_empty"

        exit_code = run_rehearsal(
            db_dir=empty_dir,
            rehearsal_dir=rehearsal_dir,
            output_dir=output_dir,
            skip_report=True,
        )
        assert exit_code == 0

    def test_all_dbs_are_copied_to_rehearsal_dir(
        self,
        fixture_db_dir: Path,
        tmp_path: Path,
        output_dir: Path,
    ) -> None:
        rehearsal_dir = tmp_path / "rehearsal_copies"
        run_rehearsal(
            db_dir=fixture_db_dir,
            rehearsal_dir=rehearsal_dir,
            output_dir=output_dir,
            skip_report=True,
        )
        for db_name in [
            ".depthfusion_memories.db",
            ".depthfusion_file_index.db",
            "depthfusion-graph.db",
            ".depthfusion_telemetry.db",
        ]:
            assert (rehearsal_dir / db_name).exists(), f"Missing copy: {db_name}"

    def test_production_dbs_are_not_modified(
        self,
        fixture_db_dir: Path,
        tmp_path: Path,
        output_dir: Path,
    ) -> None:
        """The source DB must not have acl_allow added (only the copy should)."""
        src_memories = fixture_db_dir / ".depthfusion_memories.db"

        # Capture row count before rehearsal
        before = _get_row_counts(src_memories)

        rehearsal_dir = tmp_path / "rehearsal_prod_check"
        run_rehearsal(
            db_dir=fixture_db_dir,
            rehearsal_dir=rehearsal_dir,
            output_dir=output_dir,
            skip_report=True,
        )

        after = _get_row_counts(src_memories)
        assert before == after, "Production DB row counts changed during rehearsal"

        # Column must NOT have been added to the source
        with contextlib.closing(sqlite3.connect(str(src_memories))) as conn:
            assert not _table_has_column(conn, "memories", "acl_allow"), (
                "acl_allow column was added to the production DB — must only touch the copy"
            )
