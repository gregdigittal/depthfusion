# tests/test_capture/test_dedup.py
"""Embedding-based dedup tests — CM-2 / S-49 / T-151.

≥ 6 tests required by S-49 AC-3.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from depthfusion.capture.dedup import (
    _SUPERSEDED_SUFFIX,
    dedup_against_corpus,
    extract_project,
    find_duplicates,
    load_discovery_corpus,
    supersede,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_discovery(dir_: Path, name: str, project: str, body: str) -> Path:
    """Write a discovery file with minimal frontmatter. Returns the path."""
    content = (
        "---\n"
        f"project: {project}\n"
        "type: decisions\n"
        "---\n"
        "\n"
        f"{body}\n"
    )
    p = dir_ / name
    p.write_text(content, encoding="utf-8")
    return p


def _mk_backend(vectors_by_text: dict[str, list[float]]) -> MagicMock:
    """Mock backend whose embed(texts) returns vectors_by_text[text] in order.

    Missing texts return a default orthogonal vector so shape matches.
    """
    backend = MagicMock()

    def embed(texts: list[str]) -> list[list[float]]:
        out = []
        for i, t in enumerate(texts):
            # Match by content prefix (first 80 chars) to make lookup robust
            matched = None
            for key, vec in vectors_by_text.items():
                if key in t:
                    matched = vec
                    break
            if matched is None:
                # default: an orthogonal vector seeded by index
                default = [0.0] * 4
                default[i % 4] = 1.0
                out.append(default)
            else:
                out.append(matched)
        return out

    backend.embed.side_effect = embed
    return backend


# ---------------------------------------------------------------------------
# extract_project
# ---------------------------------------------------------------------------

class TestExtractProject:
    def test_reads_project_frontmatter(self):
        content = "---\nproject: depthfusion\ntype: decisions\n---\n\nbody"
        assert extract_project(content) == "depthfusion"

    def test_returns_none_when_no_project(self):
        content = "---\ntype: decisions\n---\n\nbody"
        assert extract_project(content) is None

    def test_returns_none_for_empty_content(self):
        assert extract_project("") is None


# ---------------------------------------------------------------------------
# load_discovery_corpus
# ---------------------------------------------------------------------------

class TestLoadDiscoveryCorpus:
    def test_returns_empty_when_dir_missing(self, tmp_path):
        corpus = load_discovery_corpus(tmp_path / "nonexistent")
        assert corpus == []

    def test_loads_markdown_files(self, tmp_path):
        _mk_discovery(tmp_path, "a.md", "p1", "body a")
        _mk_discovery(tmp_path, "b.md", "p2", "body b")
        corpus = load_discovery_corpus(tmp_path)
        assert len(corpus) == 2
        names = {p.name for p, _, _ in corpus}
        assert names == {"a.md", "b.md"}

    def test_skips_superseded_files(self, tmp_path):
        _mk_discovery(tmp_path, "a.md", "p1", "body a")
        (tmp_path / "b.md.superseded").write_text("old", encoding="utf-8")
        corpus = load_discovery_corpus(tmp_path)
        assert len(corpus) == 1
        assert corpus[0][0].name == "a.md"

    def test_excludes_given_path(self, tmp_path):
        a = _mk_discovery(tmp_path, "a.md", "p1", "body a")
        _mk_discovery(tmp_path, "b.md", "p2", "body b")
        corpus = load_discovery_corpus(tmp_path, exclude=a)
        assert len(corpus) == 1
        assert corpus[0][0].name == "b.md"

    def test_respects_window_size(self, tmp_path):
        for i in range(10):
            _mk_discovery(tmp_path, f"f{i}.md", f"p{i}", f"body {i}")
        corpus = load_discovery_corpus(tmp_path, window_size=3)
        assert len(corpus) == 3

    def test_extracts_project_from_frontmatter(self, tmp_path):
        _mk_discovery(tmp_path, "a.md", "myapp", "body")
        corpus = load_discovery_corpus(tmp_path)
        assert corpus[0][2] == "myapp"


# ---------------------------------------------------------------------------
# find_duplicates
# ---------------------------------------------------------------------------

class TestFindDuplicates:
    def test_flags_high_similarity_pair(self, tmp_path):
        new_content = "---\nproject: p1\n---\nUse redis for caching"
        existing = _mk_discovery(tmp_path, "old.md", "p1", "Use redis for caching")
        corpus = [(existing, existing.read_text(), "p1")]

        embeddings = [
            [1.0, 0.0, 0.0],  # new
            [0.99, 0.01, 0.0],  # old — very similar
        ]
        dupes = find_duplicates(
            new_path=tmp_path / "new.md",
            new_content=new_content,
            corpus=corpus,
            embeddings=embeddings,
            threshold=0.92,
        )
        assert len(dupes) == 1
        assert dupes[0][0] == existing
        assert dupes[0][1] >= 0.92

    def test_ignores_low_similarity(self, tmp_path):
        new_content = "---\nproject: p1\n---\nUse redis"
        existing = _mk_discovery(tmp_path, "old.md", "p1", "Use postgres")
        corpus = [(existing, existing.read_text(), "p1")]

        embeddings = [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],  # orthogonal
        ]
        dupes = find_duplicates(
            new_path=tmp_path / "new.md",
            new_content=new_content,
            corpus=corpus,
            embeddings=embeddings,
            threshold=0.92,
        )
        assert dupes == []

    def test_project_scoped_comparison(self, tmp_path):
        """Semantic dup with different project does NOT supersede."""
        new_content = "---\nproject: projectA\n---\nUse redis"
        existing = _mk_discovery(tmp_path, "old.md", "projectB", "Use redis")
        corpus = [(existing, existing.read_text(), "projectB")]

        embeddings = [
            [1.0, 0.0],
            [1.0, 0.0],  # identical vectors but different projects
        ]
        dupes = find_duplicates(
            new_path=tmp_path / "new.md",
            new_content=new_content,
            corpus=corpus,
            embeddings=embeddings,
            threshold=0.92,
        )
        assert dupes == []

    def test_missing_frontmatter_never_deduped(self, tmp_path):
        """Strict project-scoping: files without `project:` frontmatter
        are never deduped — even against each other. Conservative choice
        (false-negative < false-positive in dedup cost).
        """
        new_content = "raw body, no frontmatter"
        existing = tmp_path / "old.md"
        existing.write_text("another raw body", encoding="utf-8")
        # project=None on both sides
        corpus = [(existing, existing.read_text(), None)]

        embeddings = [
            [1.0, 0.0],
            [1.0, 0.0],  # identical vectors, both projectless
        ]
        dupes = find_duplicates(
            new_path=tmp_path / "new.md",
            new_content=new_content,
            corpus=corpus,
            embeddings=embeddings,
            threshold=0.92,
        )
        assert dupes == []

    def test_projectless_existing_not_matched_by_projectful_new(self, tmp_path):
        """A new file with project:X does NOT supersede a projectless old file."""
        new_content = "---\nproject: X\n---\nbody"
        existing = tmp_path / "old.md"
        existing.write_text("body", encoding="utf-8")
        corpus = [(existing, existing.read_text(), None)]

        embeddings = [[1.0, 0.0], [1.0, 0.0]]
        dupes = find_duplicates(
            new_path=tmp_path / "new.md",
            new_content=new_content,
            corpus=corpus,
            embeddings=embeddings,
            threshold=0.92,
        )
        assert dupes == []

    def test_embedding_length_mismatch_returns_empty(self, tmp_path):
        """Defensive: if embeddings count ≠ corpus+1, no crash."""
        existing = _mk_discovery(tmp_path, "old.md", "p1", "body")
        corpus = [(existing, existing.read_text(), "p1")]
        # Only 1 embedding but corpus+1 = 2 expected
        dupes = find_duplicates(
            new_path=tmp_path / "new.md",
            new_content="body",
            corpus=corpus,
            embeddings=[[1.0, 0.0]],
        )
        assert dupes == []

    def test_sorts_by_descending_similarity(self, tmp_path):
        e1 = _mk_discovery(tmp_path, "e1.md", "p1", "body 1")
        e2 = _mk_discovery(tmp_path, "e2.md", "p1", "body 2")
        corpus = [
            (e1, e1.read_text(), "p1"),
            (e2, e2.read_text(), "p1"),
        ]
        embeddings = [
            [1.0, 0.0],  # new
            [0.95, 0.05],  # e1 — very similar
            [0.99, 0.01],  # e2 — more similar
        ]
        dupes = find_duplicates(
            new_path=tmp_path / "new.md",
            new_content="---\nproject: p1\n---\nbody",
            corpus=corpus,
            embeddings=embeddings,
            threshold=0.92,
        )
        assert [d[0].name for d in dupes] == ["e2.md", "e1.md"]


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------

class TestSupersede:
    def test_renames_with_superseded_suffix(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("content", encoding="utf-8")
        result = supersede(p)
        assert result is not None
        assert result.name == "a.md" + _SUPERSEDED_SUFFIX
        assert result.exists()
        assert not p.exists()

    def test_returns_none_when_file_missing(self, tmp_path):
        assert supersede(tmp_path / "missing.md") is None

    def test_idempotent_when_target_exists(self, tmp_path):
        p = tmp_path / "a.md"
        p.write_text("original", encoding="utf-8")
        # Pre-existing superseded target
        (tmp_path / ("a.md" + _SUPERSEDED_SUFFIX)).write_text("prev", encoding="utf-8")
        result = supersede(p)
        assert result is not None
        # Original still exists because target was pre-existing
        assert p.exists()
        # Pre-existing superseded content is preserved
        assert result.read_text() == "prev"


# ---------------------------------------------------------------------------
# dedup_against_corpus (integration)
# ---------------------------------------------------------------------------

class TestDedupAgainstCorpus:
    def test_supersedes_near_duplicate_in_same_project(self, tmp_path):
        old = _mk_discovery(
            tmp_path, "2026-04-01-p1-decisions.md", "p1", "Use redis OLDMARKER",
        )
        new = _mk_discovery(
            tmp_path, "2026-04-20-p1-decisions.md", "p1", "Use redis NEWMARKER",
        )
        backend = _mk_backend({
            "NEWMARKER": [1.0, 0.0, 0.0],
            "OLDMARKER": [0.99, 0.01, 0.0],
        })

        superseded = dedup_against_corpus(
            new, backend=backend, output_dir=tmp_path, threshold=0.90,
        )
        assert len(superseded) == 1
        assert superseded[0] == old
        assert (tmp_path / (old.name + _SUPERSEDED_SUFFIX)).exists()

    def test_no_op_when_backend_returns_none(self, tmp_path):
        """NullBackend / missing sentence-transformers → graceful no-op."""
        _mk_discovery(tmp_path, "a.md", "p1", "body a")
        new = _mk_discovery(tmp_path, "b.md", "p1", "body b")
        backend = MagicMock()
        backend.embed.return_value = None

        superseded = dedup_against_corpus(new, backend=backend, output_dir=tmp_path)
        assert superseded == []
        assert not any(p.name.endswith(_SUPERSEDED_SUFFIX) for p in tmp_path.iterdir())

    def test_no_op_when_corpus_empty(self, tmp_path):
        new = _mk_discovery(tmp_path, "b.md", "p1", "body b")
        backend = MagicMock()  # should never be called
        superseded = dedup_against_corpus(new, backend=backend, output_dir=tmp_path)
        assert superseded == []
        backend.embed.assert_not_called()

    def test_no_op_when_new_file_missing(self, tmp_path):
        backend = MagicMock()
        superseded = dedup_against_corpus(
            tmp_path / "nonexistent.md", backend=backend, output_dir=tmp_path,
        )
        assert superseded == []
        backend.embed.assert_not_called()

    def test_backend_exception_returns_empty(self, tmp_path):
        """Embedding failures must never propagate — dedup is best-effort."""
        _mk_discovery(tmp_path, "a.md", "p1", "body a")
        new = _mk_discovery(tmp_path, "b.md", "p1", "body b")
        backend = MagicMock()
        backend.embed.side_effect = RuntimeError("CUDA OOM")
        superseded = dedup_against_corpus(new, backend=backend, output_dir=tmp_path)
        assert superseded == []

    def test_threshold_env_var_override(self, tmp_path, monkeypatch):
        """DEPTHFUSION_DEDUP_THRESHOLD lets operators tune without code change."""
        old = _mk_discovery(tmp_path, "a.md", "p1", "body a")
        new = _mk_discovery(tmp_path, "b.md", "p1", "body b")
        backend = _mk_backend({
            "body a": [0.8, 0.6],
            "body b": [1.0, 0.0],
        })
        # Default 0.92 would NOT dedupe these (cos ≈ 0.8). Lower threshold via env.
        monkeypatch.setenv("DEPTHFUSION_DEDUP_THRESHOLD", "0.70")
        superseded = dedup_against_corpus(new, backend=backend, output_dir=tmp_path)
        assert old in superseded

    def test_different_projects_not_deduped(self, tmp_path):
        """Identical embeddings across different projects → no dedup."""
        _mk_discovery(tmp_path, "a.md", "projectA", "body a")
        new = _mk_discovery(tmp_path, "b.md", "projectB", "body b")
        backend = _mk_backend({
            "body a": [1.0, 0.0],
            "body b": [1.0, 0.0],
        })
        superseded = dedup_against_corpus(new, backend=backend, output_dir=tmp_path)
        assert superseded == []
