# tests/test_install/test_smoke.py
"""Post-install smoke test tests — T-127 / S-42 AC-4.

The smoke test is a self-check run after install. It materialises a
5-file synthetic corpus, queries it via BM25, and asserts that the
known-target document ranks first. These tests verify the smoke test
itself behaves correctly across modes and degrades gracefully under
failure conditions.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from depthfusion.install.smoke import run_smoke_test


class TestRunSmokeTest:
    @pytest.mark.parametrize("mode", ["local", "vps-cpu", "vps-gpu"])
    def test_passes_for_every_mode(self, mode, tmp_path):
        """Smoke test uses the same BM25 path for all three modes — all pass."""
        result = run_smoke_test(mode=mode, corpus_dir=tmp_path)
        assert result.ok is True
        assert result.mode == mode
        assert result.top_hit == "charlie"
        assert result.result_count >= 1

    def test_writes_five_files_to_corpus_dir(self, tmp_path):
        """Corpus is materialised before the query runs."""
        run_smoke_test(mode="local", corpus_dir=tmp_path)
        md_files = list(tmp_path.glob("*.md"))
        assert len(md_files) == 5

    def test_uses_tempdir_when_no_corpus_dir(self):
        """With corpus_dir=None, runs in a tempdir that's cleaned up."""
        result = run_smoke_test(mode="local")
        assert result.ok is True

    def test_never_raises_on_bm25_failure(self, tmp_path, monkeypatch):
        """Any exception inside run_smoke_test returns a failed SmokeResult.

        `tokenize` is imported into `smoke` via a deferred `from … import`
        inside `_run_with_dir()`, so the name binding is local to that
        function frame. Patching the attribute on the source module
        (`depthfusion.retrieval.bm25.tokenize`) works here because the
        deferred import re-reads the attribute on each call.
        """
        def broken_tokenize(text):
            raise RuntimeError("tokeniser exploded")

        monkeypatch.setattr("depthfusion.retrieval.bm25.tokenize", broken_tokenize)
        result = run_smoke_test(mode="local", corpus_dir=tmp_path)
        assert result.ok is False
        assert "exception" in result.reason.lower() or "failed" in result.reason.lower()

    def test_import_failure_surfaces_as_failed_result(self, tmp_path):
        """If BM25 is unimportable (e.g. renamed), smoke test fails cleanly."""
        with patch(
            "depthfusion.install.smoke._run_with_dir",
            side_effect=ImportError("bm25 module moved"),
        ):
            result = run_smoke_test(mode="local", corpus_dir=tmp_path)
        assert result.ok is False
        assert "exception" in result.reason.lower()

    def test_result_is_frozen_dataclass(self, tmp_path):
        """SmokeResult is immutable — callers can't tamper with the verdict."""
        from dataclasses import FrozenInstanceError
        result = run_smoke_test(mode="local", corpus_dir=tmp_path)
        with pytest.raises(FrozenInstanceError):
            result.ok = False  # type: ignore[misc]

    def test_empty_corpus_dir_returns_failure(self, tmp_path):
        """A corpus dir that has no .md files after materialisation is an error."""
        # Materialise files then delete them before BM25 runs.
        # Easier: patch the _run_with_dir to use a different dir.
        empty = tmp_path / "empty"
        empty.mkdir()
        with patch("depthfusion.install.smoke._SMOKE_CORPUS", {}):
            result = run_smoke_test(mode="local", corpus_dir=empty)
        assert result.ok is False
        assert "no .md files" in result.reason or "zero" in result.reason.lower()


# ---------------------------------------------------------------------------
# S-62 / T-197: run_vps_gpu_smoke
# ---------------------------------------------------------------------------

class TestRunVpsGpuSmoke:
    def test_no_gpu_returns_failure(self, monkeypatch):
        """Probe 1 fails when nvidia-smi reports no GPU."""
        from depthfusion.install.gpu_probe import GPUInfo
        from depthfusion.install.smoke import run_vps_gpu_smoke
        monkeypatch.setattr(
            "depthfusion.install.gpu_probe.detect_gpu",
            lambda: GPUInfo(False, "", 0.0, 0, "nvidia-smi not found"),
        )
        result = run_vps_gpu_smoke()
        assert result.ok is False
        assert "GPU probe failed" in result.reason

    def test_no_sentence_transformers_returns_failure(self, monkeypatch):
        """Probe 2 fails when the extras aren't installed."""
        from depthfusion.install.gpu_probe import GPUInfo
        from depthfusion.install.smoke import run_vps_gpu_smoke
        monkeypatch.setattr(
            "depthfusion.install.gpu_probe.detect_gpu",
            lambda: GPUInfo(True, "RTX 4090", 24.0, 1, "ok"),
        )
        import importlib.util as _iu
        original_find_spec = _iu.find_spec
        monkeypatch.setattr(
            _iu, "find_spec",
            lambda name, *a, **kw: (
                None if name == "sentence_transformers"
                else original_find_spec(name, *a, **kw)
            ),
        )
        result = run_vps_gpu_smoke()
        assert result.ok is False
        assert "sentence_transformers not importable" in result.reason

    def test_embed_returns_empty_vector_returns_failure(self, monkeypatch):
        """Probe 3 fails when embed() returns None (e.g., model load failed)."""
        from depthfusion.install.gpu_probe import GPUInfo
        from depthfusion.install.smoke import run_vps_gpu_smoke
        monkeypatch.setattr(
            "depthfusion.install.gpu_probe.detect_gpu",
            lambda: GPUInfo(True, "RTX 4090", 24.0, 1, "ok"),
        )
        # Pretend sentence-transformers is importable
        import importlib.util as _iu
        monkeypatch.setattr(
            _iu, "find_spec", lambda name, *a, **kw: object(),
        )
        # But the backend returns None
        from depthfusion.backends.local_embedding import LocalEmbeddingBackend

        class BrokenBackend:
            def __init__(self):
                pass

            def embed(self, texts):
                return None

        monkeypatch.setattr(
            LocalEmbeddingBackend, "__init__", lambda self: None,
        )
        monkeypatch.setattr(
            LocalEmbeddingBackend, "embed", lambda self, texts: None,
        )
        result = run_vps_gpu_smoke()
        assert result.ok is False
        assert "returned empty" in result.reason

    def test_all_probes_pass_returns_ok(self, monkeypatch):
        """Happy path: GPU + extras + functional embed → ok=True."""
        from depthfusion.install.gpu_probe import GPUInfo
        from depthfusion.install.smoke import run_vps_gpu_smoke
        monkeypatch.setattr(
            "depthfusion.install.gpu_probe.detect_gpu",
            lambda: GPUInfo(True, "RTX 4090", 24.0, 1, "ok"),
        )
        import importlib.util as _iu
        monkeypatch.setattr(
            _iu, "find_spec", lambda name, *a, **kw: object(),
        )
        from depthfusion.backends.local_embedding import LocalEmbeddingBackend
        monkeypatch.setattr(
            LocalEmbeddingBackend, "__init__", lambda self: None,
        )
        monkeypatch.setattr(
            LocalEmbeddingBackend, "embed",
            lambda self, texts: [[0.1, 0.2, 0.3, 0.4, 0.5]],
        )
        result = run_vps_gpu_smoke()
        assert result.ok is True
        assert result.mode == "vps-gpu"
        # result_count = embedding dimensionality (len of the first vector)
        assert result.result_count == 5
