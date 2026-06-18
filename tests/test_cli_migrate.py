"""tests/test_cli_migrate.py — Unit tests for src/depthfusion/cli/migrate.py.

E-63 T-693: ``depthfusion migrate v2`` schema + config migration CLI.

These tests are fully self-contained: they build fixture SQLite DBs and env
files in ``tmp_path`` and never touch the real production state. No external
services are required.
"""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from depthfusion.cli import migrate

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_memories_db(path: Path) -> None:
    """Create a minimal pre-migration .depthfusion_memories.db (no acl_allow)."""
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, body TEXT)")
        conn.executemany(
            "INSERT OR IGNORE INTO memories VALUES (?, ?)",
            [("m1", "one"), ("m2", "two")],
        )
        conn.commit()


@pytest.fixture()
def db_dir(tmp_path: Path) -> Path:
    """A temp DB directory with a single pre-migration memories DB."""
    d = tmp_path / "data"
    d.mkdir()
    _make_memories_db(d / ".depthfusion_memories.db")
    return d


@pytest.fixture()
def env_file(tmp_path: Path) -> Path:
    """A legacy v1 env file with keys that should be renamed by the migration."""
    p = tmp_path / "data" / "depthfusion.env"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "# legacy v1 config\n"
        "DEPTHFUSION_RERANKER=true\n"
        "DEPTHFUSION_GRAPH=true\n"
        "DEPTHFUSION_MODE=vps-gpu\n",  # unmapped key — must be preserved verbatim
        encoding="utf-8",
    )
    return p


# ── Plan computation ──────────────────────────────────────────────────────────


class TestBuildPlan:
    def test_detects_pending_migrations(self, db_dir: Path, tmp_path: Path) -> None:
        plan = migrate.build_plan(db_dir, tmp_path / "missing.env")
        # A fresh DB has no _df_schema_migrations table → migrations are pending.
        assert plan.present_dbs == [".depthfusion_memories.db"]
        assert plan.pending_migrations, "expected at least one pending migration"
        assert plan.has_changes is True

    def test_detects_config_renames(self, db_dir: Path, env_file: Path) -> None:
        plan = migrate.build_plan(db_dir, env_file)
        assert plan.config_renames == {
            "DEPTHFUSION_RERANKER": "DEPTHFUSION_RERANKER_ENABLED",
            "DEPTHFUSION_GRAPH": "DEPTHFUSION_GRAPH_ENABLED",
        }

    def test_empty_db_dir_has_no_migrations(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        plan = migrate.build_plan(empty, tmp_path / "missing.env")
        assert plan.present_dbs == []
        assert plan.pending_migrations == []


# ── Dry-run: produces a plan and changes nothing ────────────────────────────────


class TestDryRun:
    def test_dry_run_returns_zero_and_prints_plan(
        self, db_dir: Path, env_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = migrate.cmd_v2(dry_run=True, db_dir=db_dir, env_file=env_file)
        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert "would apply" in out
        assert "would rename: DEPTHFUSION_RERANKER -> DEPTHFUSION_RERANKER_ENABLED" in out
        assert "no changes were written" in out.lower()

    def test_dry_run_does_not_modify_db(self, db_dir: Path, env_file: Path) -> None:
        db_path = db_dir / ".depthfusion_memories.db"
        before = db_path.read_bytes()

        migrate.cmd_v2(dry_run=True, db_dir=db_dir, env_file=env_file)

        after = db_path.read_bytes()
        assert before == after, "dry-run must not mutate the database file"

        # No acl_allow column should have been added.
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            cur = conn.execute("PRAGMA table_info(memories)")
            cols = {row[1] for row in cur.fetchall()}
        assert "acl_allow" not in cols

    def test_dry_run_does_not_modify_env_file(
        self, db_dir: Path, env_file: Path
    ) -> None:
        before = env_file.read_text(encoding="utf-8")
        migrate.cmd_v2(dry_run=True, db_dir=db_dir, env_file=env_file)
        after = env_file.read_text(encoding="utf-8")
        assert before == after, "dry-run must not rewrite the config file"

    def test_dry_run_creates_no_staging_dir(
        self, db_dir: Path, env_file: Path
    ) -> None:
        migrate.cmd_v2(dry_run=True, db_dir=db_dir, env_file=env_file)
        assert not (db_dir / ".df_v2_migration").exists()


# ── Live run: applies changes ───────────────────────────────────────────────────


class TestLiveRun:
    def test_live_run_renames_config_keys(self, db_dir: Path, env_file: Path) -> None:
        rc = migrate.cmd_v2(dry_run=False, db_dir=db_dir, env_file=env_file)
        assert rc == 0
        content = env_file.read_text(encoding="utf-8")
        assert "DEPTHFUSION_RERANKER_ENABLED=true" in content
        assert "DEPTHFUSION_GRAPH_ENABLED=true" in content
        # Unmapped key preserved; legacy bare keys gone.
        assert "DEPTHFUSION_MODE=vps-gpu" in content
        assert "DEPTHFUSION_RERANKER=true" not in content
        assert "DEPTHFUSION_GRAPH=true" not in content

    def test_live_run_applies_schema_migration(
        self, db_dir: Path, env_file: Path
    ) -> None:
        migrate.cmd_v2(dry_run=False, db_dir=db_dir, env_file=env_file)
        db_path = db_dir / ".depthfusion_memories.db"
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            cur = conn.execute("PRAGMA table_info(memories)")
            cols = {row[1] for row in cur.fetchall()}
        assert "acl_allow" in cols, "schema migration should add acl_allow"

    def test_live_run_is_idempotent(self, db_dir: Path, env_file: Path) -> None:
        assert migrate.cmd_v2(dry_run=False, db_dir=db_dir, env_file=env_file) == 0
        # Second run: nothing left to do.
        rc2 = migrate.cmd_v2(dry_run=False, db_dir=db_dir, env_file=env_file)
        assert rc2 == 0
        plan = migrate.build_plan(db_dir, env_file)
        assert plan.config_renames == {}


# ── Dispatch via main(argv) ─────────────────────────────────────────────────────


class TestMainDispatch:
    def test_v2_subcommand_dispatches(
        self, db_dir: Path, env_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = migrate.main(
            ["v2", "--dry-run", "--db-dir", str(db_dir), "--env-file", str(env_file)]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "v2 migration plan" in out
        assert "DRY-RUN" in out

    def test_no_args_prints_usage(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = migrate.main([])
        assert rc == 0
        assert "Usage:" in capsys.readouterr().out

    def test_help_flag_prints_usage(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = migrate.main(["--help"])
        assert rc == 0
        assert "depthfusion migrate v2" in capsys.readouterr().out

    def test_unknown_subcommand_returns_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = migrate.main(["bogus"])
        assert rc == 2
        assert "unknown sub-command" in capsys.readouterr().err

    def test_v2_default_dirs_do_not_crash(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Point DEPTHFUSION_DATA_DIR at an empty temp dir so the default-path
        # branch runs without touching real state.
        empty = tmp_path / "empty_default"
        empty.mkdir()
        monkeypatch.setenv("DEPTHFUSION_DATA_DIR", str(empty))
        rc = migrate.main(["v2", "--dry-run"])
        assert rc == 0
        assert "no SQLite databases found" in capsys.readouterr().out
