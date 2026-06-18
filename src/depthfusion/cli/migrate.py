"""Schema + config migration CLI for DepthFusion (``depthfusion migrate``).

E-63 T-693 — v1 → v2 migration command.

This is a **new** CLI module, distinct from
:mod:`depthfusion.install.migrate` (the Tier 1 → Tier 2 ChromaDB indexer).
It performs two things for the ``v2`` sub-command:

1. **Schema migration** — applies the SQL migrations in
   ``src/depthfusion/migrations/`` to the SQLite stores, reusing the
   battle-tested runner in ``scripts/rehearse_migration.py`` rather than
   duplicating that logic here.
2. **Config translation** — rewrites the legacy ``~/.claude/depthfusion.env``
   file, renaming a small set of v1 environment keys to their v2
   equivalents (a deterministic, idempotent mapping).

A ``--dry-run`` flag reports the full plan (which migrations would apply and
which config keys would be renamed) **without writing anything** — no DB is
copied or mutated, and the env file is left untouched.

Usage::

    depthfusion migrate v2 [--dry-run] [--db-dir DIR] [--env-file PATH]
                           [--rehearsal-dir DIR]

The DB directory defaults to ``DEPTHFUSION_DATA_DIR`` (falling back to
``~/.claude``), matching the convention used elsewhere in the codebase.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Reuse the rehearsal migration runner instead of duplicating it.
#
# scripts/ is not an installable package, so it is imported by path the same
# way tests/test_migration_rehearsal.py does.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from rehearse_migration import (  # type: ignore[import-not-found]
        SQLITE_DB_FILES,
        _run_migrations,
    )
except Exception:  # pragma: no cover - defensive: scripts dir missing/renamed
    SQLITE_DB_FILES = [
        ".depthfusion_memories.db",
        ".depthfusion_file_index.db",
        "depthfusion-graph.db",
        ".depthfusion_telemetry.db",
    ]
    _run_migrations = None  # type: ignore[assignment]

_MIGRATIONS_DIR = _REPO_ROOT / "src" / "depthfusion" / "migrations"

# ---------------------------------------------------------------------------
# v1 → v2 config key translation map.
#
# Each entry renames a legacy v1 env key to its v2 equivalent. The mapping is
# deterministic and idempotent: applying it twice is a no-op.
# ---------------------------------------------------------------------------
V1_TO_V2_CONFIG_KEYS: dict[str, str] = {
    "DEPTHFUSION_RERANKER": "DEPTHFUSION_RERANKER_ENABLED",
    "DEPTHFUSION_TIER_AUTO_PROMOTE": "DEPTHFUSION_TIER_AUTOPROMOTE",
    "DEPTHFUSION_GRAPH": "DEPTHFUSION_GRAPH_ENABLED",
    "DEPTHFUSION_HAIKU": "DEPTHFUSION_HAIKU_ENABLED",
    "DEPTHFUSION_EMBEDDING": "DEPTHFUSION_EMBEDDING_BACKEND",
}


# ---------------------------------------------------------------------------
# Plan data structures
# ---------------------------------------------------------------------------


@dataclass
class MigrationPlan:
    """A computed plan describing what a migration *would* do.

    The plan is produced without mutating any state, so it can be printed in
    ``--dry-run`` mode and reused for the live run.
    """

    db_dir: Path
    env_file: Path
    pending_migrations: list[str] = field(default_factory=list)
    present_dbs: list[str] = field(default_factory=list)
    config_renames: dict[str, str] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        return bool(self.pending_migrations or self.config_renames)

    def render(self, *, dry_run: bool) -> str:
        """Return a human-readable description of this plan."""
        prefix = "[DRY-RUN] " if dry_run else ""
        lines: list[str] = []
        lines.append("DepthFusion v2 migration plan")
        lines.append(f"  DB directory:  {self.db_dir}")
        lines.append(f"  Config file:   {self.env_file}")
        lines.append("")

        # Schema migrations
        lines.append("Schema migrations:")
        if not self.present_dbs:
            lines.append("  (no SQLite databases found — nothing to migrate)")
        elif self.pending_migrations:
            for db in self.present_dbs:
                lines.append(f"  database: {db}")
            for mig in self.pending_migrations:
                lines.append(f"  {prefix}would apply: {mig}")
        else:
            lines.append("  (all migrations already applied)")
        lines.append("")

        # Config translation
        lines.append("Config translation:")
        if not self.config_renames:
            lines.append("  (no legacy v1 keys to rename)")
        else:
            for old, new in self.config_renames.items():
                lines.append(f"  {prefix}would rename: {old} -> {new}")
        lines.append("")

        if dry_run:
            lines.append("Dry run — no changes were written.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_db_dir() -> Path:
    return Path(os.environ.get("DEPTHFUSION_DATA_DIR", "~/.claude")).expanduser()


def _default_env_file(db_dir: Path) -> Path:
    return db_dir / "depthfusion.env"


def _applied_migration_ids(db_path: Path) -> set[str]:
    """Return the set of migration ids already applied to *db_path*."""
    if not db_path.exists():
        return set()
    with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='_df_schema_migrations'"
        )
        if cur.fetchone() is None:
            return set()
        rows = conn.execute(
            "SELECT migration_id FROM _df_schema_migrations"
        ).fetchall()
    return {row[0] for row in rows}


def _compute_pending_migrations(db_dir: Path) -> tuple[list[str], list[str]]:
    """Return (present_db_files, pending_migration_ids).

    A migration is *pending* if it has not been recorded as applied in at
    least one present database.  This is intentionally conservative: it lists
    a migration as pending unless every present DB has it recorded.
    """
    present = [name for name in SQLITE_DB_FILES if (db_dir / name).exists()]
    if not present:
        return [], []

    all_migration_ids = sorted(p.stem for p in _MIGRATIONS_DIR.glob("*.sql"))
    if not all_migration_ids:
        return present, []

    pending: list[str] = []
    for mig in all_migration_ids:
        applied_everywhere = all(
            mig in _applied_migration_ids(db_dir / name) for name in present
        )
        if not applied_everywhere:
            pending.append(mig)
    return present, pending


def _parse_env_file(env_file: Path) -> list[tuple[str, str]]:
    """Parse *env_file* into an ordered list of (key, raw_line) pairs.

    Comment and blank lines are represented with an empty key so the file can
    be faithfully re-rendered.
    """
    if not env_file.exists():
        return []
    parsed: list[tuple[str, str]] = []
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            parsed.append(("", line))
            continue
        key = stripped.split("=", 1)[0].strip()
        parsed.append((key, line))
    return parsed


def _compute_config_renames(env_file: Path) -> dict[str, str]:
    """Return {old_key: new_key} for legacy keys present in *env_file*.

    Only keys that actually appear in the file and whose v2 target is not
    already present are reported — keeps the operation idempotent.
    """
    parsed = _parse_env_file(env_file)
    present_keys = {key for key, _ in parsed if key}
    renames: dict[str, str] = {}
    for old, new in V1_TO_V2_CONFIG_KEYS.items():
        if old in present_keys and new not in present_keys:
            renames[old] = new
    return renames


def _apply_config_renames(env_file: Path, renames: dict[str, str]) -> None:
    """Rewrite *env_file* applying *renames* to matching keys in place."""
    if not renames or not env_file.exists():
        return
    out_lines: list[str] = []
    for key, raw_line in _parse_env_file(env_file):
        if key and key in renames:
            new_key = renames[key]
            out_lines.append(raw_line.replace(key, new_key, 1))
        else:
            out_lines.append(raw_line)
    env_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def _apply_schema_migrations(db_dir: Path, rehearsal_dir: Path) -> tuple[bool, list[str]]:
    """Copy present DBs to *rehearsal_dir* and apply migrations there.

    Mirrors the rehearsal approach (never touches production DBs in-place
    during the migration step) and reuses ``_run_migrations``.

    Returns (success, log_lines).
    """
    if _run_migrations is None:  # pragma: no cover - defensive
        return False, ["ERROR: rehearse_migration runner unavailable"]

    rehearsal_dir.mkdir(parents=True, exist_ok=True)
    for name in SQLITE_DB_FILES:
        src = db_dir / name
        if src.exists():
            shutil.copy2(str(src), str(rehearsal_dir / name))

    ok, _elapsed, log = _run_migrations(rehearsal_dir)
    return ok, log


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------


def build_plan(db_dir: Path, env_file: Path) -> MigrationPlan:
    """Compute the v2 migration plan without mutating any state."""
    present, pending = _compute_pending_migrations(db_dir)
    renames = _compute_config_renames(env_file)
    return MigrationPlan(
        db_dir=db_dir,
        env_file=env_file,
        pending_migrations=pending,
        present_dbs=present,
        config_renames=renames,
    )


def cmd_v2(
    *,
    dry_run: bool,
    db_dir: Path | None = None,
    env_file: Path | None = None,
    rehearsal_dir: Path | None = None,
) -> int:
    """Run (or plan) the v1 → v2 schema + config migration.

    Returns
    -------
    int
        Exit code (0 = success).
    """
    db_dir = db_dir or _default_db_dir()
    env_file = env_file or _default_env_file(db_dir)

    plan = build_plan(db_dir, env_file)
    print(plan.render(dry_run=dry_run))

    if dry_run:
        # Reporting only — write nothing.
        return 0

    if not plan.has_changes:
        print("Nothing to migrate — already up to date.")
        return 0

    # Apply schema migrations (on a staged copy, like the rehearsal flow).
    if plan.pending_migrations:
        staging = rehearsal_dir or (db_dir / ".df_v2_migration")
        ok, log = _apply_schema_migrations(db_dir, staging)
        for line in log:
            print(line)
        if not ok:
            print("ERROR: schema migration failed.", file=sys.stderr)
            return 1
        # Promote the migrated copies back over the originals.
        for name in SQLITE_DB_FILES:
            staged = staging / name
            if staged.exists():
                shutil.copy2(str(staged), str(db_dir / name))
        print("Schema migrations applied.")

    # Apply config translation in place.
    if plan.config_renames:
        _apply_config_renames(env_file, plan.config_renames)
        print(f"Config translated: {len(plan.config_renames)} key(s) renamed.")

    print("v2 migration complete.")
    return 0


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


_USAGE = (
    "Usage:\n"
    "  depthfusion migrate v2 [--dry-run] [--db-dir DIR] [--env-file PATH]\n"
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="depthfusion migrate",
        description="DepthFusion schema + config migration.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    v2 = sub.add_parser(
        "v2",
        help="Migrate v1 schema + config to v2.",
        description=(
            "Apply pending SQL schema migrations and translate the legacy "
            "v1 config to v2. Use --dry-run to preview without writing."
        ),
    )
    v2.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the planned changes without writing anything.",
    )
    v2.add_argument(
        "--db-dir",
        type=Path,
        default=None,
        help="Directory with the SQLite databases "
        "(default: $DEPTHFUSION_DATA_DIR or ~/.claude).",
    )
    v2.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to the depthfusion.env config file "
        "(default: <db-dir>/depthfusion.env).",
    )
    v2.add_argument(
        "--rehearsal-dir",
        type=Path,
        default=None,
        help="Staging directory for migrated DB copies "
        "(default: <db-dir>/.df_v2_migration).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* and dispatch to the appropriate sub-command.

    Parameters
    ----------
    argv:
        Argument list (excluding the program name). Defaults to
        :data:`sys.argv[1:]` when *None*.

    Returns
    -------
    int
        Exit code.
    """
    args = list(argv if argv is not None else sys.argv[1:])

    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        return 0

    sub = args[0]
    if sub != "v2":
        print(f"Error: unknown sub-command {sub!r}.", file=sys.stderr)
        print("Available: v2", file=sys.stderr)
        return 2

    parser = _build_parser()
    ns = parser.parse_args(args)

    return cmd_v2(
        dry_run=ns.dry_run,
        db_dir=ns.db_dir,
        env_file=ns.env_file,
        rehearsal_dir=ns.rehearsal_dir,
    )


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "MigrationPlan",
    "V1_TO_V2_CONFIG_KEYS",
    "build_plan",
    "cmd_v2",
    "main",
]
