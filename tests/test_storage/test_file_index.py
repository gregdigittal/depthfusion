"""Tests for FileMetadataIndex (SQLite-backed file metadata cache)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from depthfusion.storage.file_index import FileMetadataIndex


@pytest.fixture()
def index(tmp_path: Path) -> FileMetadataIndex:
    """Return a FileMetadataIndex backed by a temp DB file."""
    db = tmp_path / "test_file_index.db"
    idx = FileMetadataIndex(db_path=db)
    yield idx
    idx.close()


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    """Create a small sample file and return its path."""
    f = tmp_path / "sample.md"
    f.write_text("hello depthfusion\n")
    return f


# ---------------------------------------------------------------------------
# 1. New file is stale (not in cache)
# ---------------------------------------------------------------------------

def test_new_file_is_stale(index: FileMetadataIndex, sample_file: Path) -> None:
    assert index.is_stale(sample_file) is True


# ---------------------------------------------------------------------------
# 2. After update(), is_stale() returns False for an unchanged file
# ---------------------------------------------------------------------------

def test_after_update_not_stale(index: FileMetadataIndex, sample_file: Path) -> None:
    index.update(sample_file)
    assert index.is_stale(sample_file) is False


# ---------------------------------------------------------------------------
# 3. After file mtime changes, is_stale() returns True
# ---------------------------------------------------------------------------

def test_stale_after_mtime_change(index: FileMetadataIndex, sample_file: Path) -> None:
    index.update(sample_file)
    assert index.is_stale(sample_file) is False

    # Bump mtime into the future
    future = time.time() + 10
    os.utime(sample_file, (future, future))

    assert index.is_stale(sample_file) is True


# ---------------------------------------------------------------------------
# 4. get() returns correct metadata after update()
# ---------------------------------------------------------------------------

def test_get_returns_correct_metadata(index: FileMetadataIndex, sample_file: Path) -> None:
    index.update(
        sample_file,
        project="proj-alpha",
        importance=0.8,
        salience=0.6,
        pinned=True,
    )
    meta = index.get(sample_file)
    assert meta is not None
    assert meta["file_path"] == str(sample_file)
    assert meta["project"] == "proj-alpha"
    assert meta["importance"] == pytest.approx(0.8)
    assert meta["salience"] == pytest.approx(0.6)
    assert meta["pinned"] is True
    assert meta["size"] == sample_file.stat().st_size
    assert meta["mtime"] == pytest.approx(sample_file.stat().st_mtime)


# ---------------------------------------------------------------------------
# 5. remove() makes get() return None
# ---------------------------------------------------------------------------

def test_remove_clears_entry(index: FileMetadataIndex, sample_file: Path) -> None:
    index.update(sample_file)
    assert index.get(sample_file) is not None

    index.remove(sample_file)
    assert index.get(sample_file) is None


# ---------------------------------------------------------------------------
# 6. list_project() returns only entries for the given project
# ---------------------------------------------------------------------------

def test_list_project_filters_correctly(index: FileMetadataIndex, tmp_path: Path) -> None:
    f_alpha = tmp_path / "alpha.md"
    f_alpha.write_text("alpha content")
    f_beta = tmp_path / "beta.md"
    f_beta.write_text("beta content")
    f_alpha2 = tmp_path / "alpha2.md"
    f_alpha2.write_text("alpha content 2")

    index.update(f_alpha, project="alpha")
    index.update(f_beta, project="beta")
    index.update(f_alpha2, project="alpha")

    alpha_entries = index.list_project("alpha")
    assert len(alpha_entries) == 2
    paths = {e["file_path"] for e in alpha_entries}
    assert str(f_alpha) in paths
    assert str(f_alpha2) in paths
    assert str(f_beta) not in paths

    beta_entries = index.list_project("beta")
    assert len(beta_entries) == 1
    assert beta_entries[0]["file_path"] == str(f_beta)


# ---------------------------------------------------------------------------
# 7. purge_missing() removes entries for deleted files and returns correct count
# ---------------------------------------------------------------------------

def test_purge_missing(index: FileMetadataIndex, tmp_path: Path) -> None:
    f_keep = tmp_path / "keep.md"
    f_keep.write_text("keep")
    f_gone1 = tmp_path / "gone1.md"
    f_gone1.write_text("gone1")
    f_gone2 = tmp_path / "gone2.md"
    f_gone2.write_text("gone2")

    index.update(f_keep)
    index.update(f_gone1)
    index.update(f_gone2)

    # Delete two of the files
    f_gone1.unlink()
    f_gone2.unlink()

    removed = index.purge_missing()
    assert removed == 2

    assert index.get(f_keep) is not None
    assert index.get(f_gone1) is None
    assert index.get(f_gone2) is None


# ---------------------------------------------------------------------------
# 8. update(compute_hash=True) stores a non-None content_hash
# ---------------------------------------------------------------------------

def test_compute_hash_stores_sha256(index: FileMetadataIndex, sample_file: Path) -> None:
    index.update(sample_file, compute_hash=True)
    meta = index.get(sample_file)
    assert meta is not None
    assert meta["content_hash"] is not None
    # SHA-256 hex digest is always 64 characters
    assert len(meta["content_hash"]) == 64


# ---------------------------------------------------------------------------
# Bonus: purge_missing() on empty DB returns 0
# ---------------------------------------------------------------------------

def test_purge_missing_empty_db(index: FileMetadataIndex) -> None:
    assert index.purge_missing() == 0


# ---------------------------------------------------------------------------
# Bonus: is_stale() returns True for a non-existent file
# ---------------------------------------------------------------------------

def test_is_stale_nonexistent_file(index: FileMetadataIndex, tmp_path: Path) -> None:
    ghost = tmp_path / "ghost.md"
    assert index.is_stale(ghost) is True


# ---------------------------------------------------------------------------
# Bonus: update without hash leaves content_hash as None
# ---------------------------------------------------------------------------

def test_no_hash_by_default(index: FileMetadataIndex, sample_file: Path) -> None:
    index.update(sample_file)
    meta = index.get(sample_file)
    assert meta is not None
    assert meta["content_hash"] is None
