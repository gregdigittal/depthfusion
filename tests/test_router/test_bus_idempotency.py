"""Tests for S-78 — publish_context idempotency by content_hash.

Covers AC-1..AC-7 of S-78.

Test classes:
    TestContextItemContentHash       — AC-2, AC-5, AC-6 (data layer)
    TestInMemoryBusIdempotency       — AC-3, AC-4 (in-memory bus)
    TestFileBusIdempotency           — AC-3, AC-4, AC-6, AC-7 (persistent bus + intra-process concurrency)
    TestFileBusCrossProcess          — AC-7 cross-process flock path (consensus-driven coverage)
    TestFileBusRobustness            — torn-write recovery, malformed-row handling (consensus-driven)
    TestMcpPublishContextToolShape   — AC-1, AC-4 (MCP tool wiring)
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import threading
from pathlib import Path
from unittest.mock import patch

from depthfusion.core.types import ContextItem
from depthfusion.router.bus import FileBus, InMemoryBus


# Module-level helper for cross-process test — must be top-level for pickling.
def _cp_publish_worker(bus_dir: str, item_id: str, content: str, result_path: str) -> None:
    """Spawn-safe helper: instantiate FileBus, publish one item, write result JSON."""
    from depthfusion.core.types import ContextItem  # re-import in subprocess
    from depthfusion.router.bus import FileBus

    bus = FileBus(bus_dir=Path(bus_dir))
    item = ContextItem(item_id=item_id, content=content, source_agent="a", tags=["t"])
    result = bus.publish(item)
    Path(result_path).write_text(json.dumps(result), encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-2, AC-5, AC-6 — ContextItem.content_hash semantics
# ---------------------------------------------------------------------------

class TestContextItemContentHash:
    def test_content_hash_auto_derived_when_absent(self):
        """AC-2: omitting content_hash triggers sha256 auto-derivation."""
        item = ContextItem(
            item_id="i1",
            content="hello world",
            source_agent="a",
            tags=["t"],
        )
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert item.content_hash == expected

    def test_content_hash_excludes_tags(self):
        """AC-5: tag-only differences must produce the same hash."""
        a = ContextItem(item_id="a", content="same payload", source_agent="x", tags=["t1"])
        b = ContextItem(item_id="b", content="same payload", source_agent="x", tags=["t2", "t3"])
        assert a.content_hash == b.content_hash

    def test_content_hash_excludes_metadata(self):
        """AC-5: metadata differences must not affect the hash."""
        a = ContextItem(item_id="a", content="payload", source_agent="x", tags=["t"])
        b = ContextItem(
            item_id="b", content="payload", source_agent="x", tags=["t"],
            metadata={"trace_id": "abc"},
        )
        assert a.content_hash == b.content_hash

    def test_content_hash_byte_sensitive(self):
        """AC-5: 1-character content difference produces a different hash."""
        a = ContextItem(item_id="a", content="hello", source_agent="x", tags=[])
        b = ContextItem(item_id="b", content="hellp", source_agent="x", tags=[])
        assert a.content_hash != b.content_hash

    def test_content_hash_whitespace_sensitive(self):
        """AC-5: whitespace differences produce a different hash."""
        a = ContextItem(item_id="a", content="hello world", source_agent="x", tags=[])
        b = ContextItem(item_id="b", content="hello  world", source_agent="x", tags=[])
        assert a.content_hash != b.content_hash

    def test_explicit_empty_hash_preserved(self):
        """AC-6: callers may pass content_hash='' to mark a legacy/no-hash item."""
        item = ContextItem(
            item_id="legacy",
            content="payload",
            source_agent="x",
            tags=[],
            content_hash="",
        )
        assert item.content_hash == ""

    def test_explicit_precomputed_hash_preserved(self):
        """Callers passing a non-empty content_hash must have it preserved verbatim."""
        canned = "deadbeef" * 8  # 64-char hex stand-in
        item = ContextItem(
            item_id="x",
            content="payload",
            source_agent="a",
            tags=[],
            content_hash=canned,
        )
        assert item.content_hash == canned


# ---------------------------------------------------------------------------
# AC-3, AC-4 — InMemoryBus idempotency
# ---------------------------------------------------------------------------

class TestInMemoryBusIdempotency:
    def test_first_publish_returns_published_not_deduped(self):
        bus = InMemoryBus()
        item = ContextItem(item_id="i1", content="payload", source_agent="a", tags=["t"])
        result = bus.publish(item)
        assert result == {"published": True, "item_id": "i1", "deduped": False}

    def test_repeat_publish_returns_original_item_id(self):
        """AC-3 + AC-4: identical-content second publish dedupes;
        response carries the ORIGINAL item_id, not the retry's."""
        bus = InMemoryBus()
        first = ContextItem(item_id="orig", content="payload", source_agent="a", tags=["t"])
        bus.publish(first)
        retry = ContextItem(item_id="retry", content="payload", source_agent="a", tags=["t"])
        result = bus.publish(retry)
        assert result == {"published": True, "item_id": "orig", "deduped": True}
        assert len(bus.subscribe(["t"])) == 1

    def test_one_char_diff_creates_new_item(self):
        """AC-5: 1-char content difference is a distinct item."""
        bus = InMemoryBus()
        a = ContextItem(item_id="a", content="hello", source_agent="x", tags=["t"])
        b = ContextItem(item_id="b", content="hellp", source_agent="x", tags=["t"])
        bus.publish(a)
        result = bus.publish(b)
        assert result["deduped"] is False
        assert result["item_id"] == "b"
        assert len(bus.subscribe(["t"])) == 2

    def test_tag_only_diff_still_dedupes(self):
        """AC-5: same content + different tags still dedupes."""
        bus = InMemoryBus()
        a = ContextItem(item_id="a", content="same", source_agent="x", tags=["t1"])
        b = ContextItem(item_id="b", content="same", source_agent="x", tags=["t2"])
        bus.publish(a)
        result = bus.publish(b)
        assert result == {"published": True, "item_id": "a", "deduped": True}


# ---------------------------------------------------------------------------
# AC-3, AC-4, AC-6, AC-7 — FileBus persistence + dedup + concurrency
# ---------------------------------------------------------------------------

class TestFileBusIdempotency:
    def test_first_publish_persists_and_returns_dict(self, tmp_path):
        bus = FileBus(bus_dir=tmp_path)
        item = ContextItem(item_id="f1", content="data", source_agent="a", tags=["t"])
        result = bus.publish(item)
        assert result == {"published": True, "item_id": "f1", "deduped": False}

    def test_repeat_publish_dedupes_against_persisted_row(self, tmp_path):
        """AC-3, AC-6: a fresh FileBus instance must dedupe against rows
        already on disk — the index is rebuilt from bus.jsonl on init."""
        bus = FileBus(bus_dir=tmp_path)
        first = ContextItem(item_id="orig", content="data", source_agent="a", tags=["t"])
        bus.publish(first)

        bus2 = FileBus(bus_dir=tmp_path)  # simulate restart
        retry = ContextItem(item_id="retry", content="data", source_agent="a", tags=["t"])
        result = bus2.publish(retry)
        assert result == {"published": True, "item_id": "orig", "deduped": True}
        # Disk should hold exactly one row.
        assert len(bus2.subscribe(["t"])) == 1

    def test_legacy_row_not_matched_for_dedup(self, tmp_path):
        """AC-6: pre-existing rows lacking content_hash never match for dedup.

        Hand-craft a bus.jsonl row in the v0.5.x schema (no content_hash key)
        and confirm a new publish of identical content stores rather than dedupes.
        """
        bus_file = tmp_path / "bus.jsonl"
        legacy = {
            "item_id": "legacy",
            "content": "old payload",
            "source_agent": "a",
            "tags": ["t"],
            "priority": "normal",
            "ttl_seconds": None,
            "metadata": {},
            # NOTE: no content_hash key — legacy schema
        }
        bus_file.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

        bus = FileBus(bus_dir=tmp_path)
        new = ContextItem(item_id="new", content="old payload", source_agent="a", tags=["t"])
        result = bus.publish(new)
        assert result["deduped"] is False, (
            "Legacy rows lacking content_hash must never match for dedup (AC-6)"
        )
        ids = {r.item_id for r in bus.subscribe(["t"])}
        assert ids == {"legacy", "new"}

    def test_concurrent_publish_no_double_insert(self, tmp_path):
        """AC-7: concurrent identical-content publishes from N threads
        must produce exactly one stored row and N-1 dedupe responses,
        all pointing to the same item_id."""
        bus = FileBus(bus_dir=tmp_path)
        N = 10
        results: list[dict] = []
        results_lock = threading.Lock()

        def worker(i: int) -> None:
            item = ContextItem(
                item_id=f"r{i}", content="shared", source_agent="a", tags=["t"]
            )
            r = bus.publish(item)
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stored = [r for r in results if not r["deduped"]]
        deduped = [r for r in results if r["deduped"]]
        assert len(stored) == 1, f"Expected 1 stored, got {len(stored)}: {stored}"
        assert len(deduped) == N - 1
        original_id = stored[0]["item_id"]
        assert all(d["item_id"] == original_id for d in deduped), (
            "All dedupe responses must reference the original stored item_id"
        )
        # Disk must have exactly one row.
        all_results = FileBus(bus_dir=tmp_path).subscribe(["t"])
        assert len(all_results) == 1

    def test_large_content_persists_and_dedupes(self, tmp_path):
        """AC-7: content > 1MB hashes and persists; repeat dedupes."""
        large = "x" * 1_100_000  # ~1.05 MB
        bus = FileBus(bus_dir=tmp_path)
        first = ContextItem(item_id="big1", content=large, source_agent="a", tags=["t"])
        r1 = bus.publish(first)
        assert r1["deduped"] is False
        assert r1["item_id"] == "big1"

        retry = ContextItem(item_id="big2", content=large, source_agent="a", tags=["t"])
        r2 = bus.publish(retry)
        assert r2 == {"published": True, "item_id": "big1", "deduped": True}


# ---------------------------------------------------------------------------
# AC-7 cross-process — fcntl.flock path (the consensus-driven coverage gap)
#
# On Linux, fcntl.flock between threads of the SAME process does not contend
# (separate open-file-descriptions in the same PID), so the intra-process
# threading test above passes solely on threading.Lock. A regression that
# removed the flock would not break that test. This class exercises the
# actual cross-process flock guarantee using independent processes.
# ---------------------------------------------------------------------------

class TestFileBusCrossProcess:
    def test_cross_process_concurrent_publish_no_double_insert(self, tmp_path):
        """AC-7 cross-process: 3 independent processes publishing identical
        content via fcntl.flock must produce exactly one stored row + 2
        dedupe responses, all referencing the same original item_id."""
        ctx = mp.get_context("fork")
        result_files = [tmp_path / f"r{i}.json" for i in range(3)]
        procs = [
            ctx.Process(
                target=_cp_publish_worker,
                args=(str(tmp_path), f"p{i}", "shared content", str(result_files[i])),
            )
            for i in range(3)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=15)
            assert not p.is_alive(), f"process {p} timed out"
            assert p.exitcode == 0, f"process {p} exited with {p.exitcode}"

        results = [json.loads(rf.read_text(encoding="utf-8")) for rf in result_files]
        stored = [r for r in results if not r["deduped"]]
        deduped = [r for r in results if r["deduped"]]
        assert len(stored) == 1, (
            f"Cross-process flock failed: {len(stored)} stored, expected exactly 1: {results}"
        )
        assert len(deduped) == 2
        original_id = stored[0]["item_id"]
        assert all(d["item_id"] == original_id for d in deduped), (
            "All cross-process dedup responses must reference the original stored item_id"
        )
        # Disk has exactly one row.
        all_rows = FileBus(bus_dir=tmp_path).subscribe(["t"])
        assert len(all_rows) == 1


# ---------------------------------------------------------------------------
# Robustness — torn-write recovery and malformed-row handling
# (Consensus-driven: addresses Codex's bus.py:171 + bus.py:121 findings.)
# ---------------------------------------------------------------------------

class TestFileBusRobustness:
    def test_torn_write_does_not_concatenate_with_next_publish(self, tmp_path):
        """A prior crash leaves an unterminated JSON fragment in bus.jsonl.
        The next publish() must NOT concatenate onto it — the new record
        must land on its own line and remain readable to subscribe()."""
        bus_file = tmp_path / "bus.jsonl"
        # Simulate a torn write: a fragment with no trailing newline.
        bus_file.write_text('{"item_id": "torn", "content":', encoding="utf-8")

        bus = FileBus(bus_dir=tmp_path)
        item = ContextItem(item_id="recovery", content="post-crash payload", source_agent="a", tags=["t"])
        result = bus.publish(item)
        assert result["deduped"] is False
        assert result["item_id"] == "recovery"

        # subscribe() must find the new row (the torn fragment is silently skipped).
        results = bus.subscribe(["t"])
        assert len(results) == 1
        assert results[0].item_id == "recovery"
        assert results[0].content == "post-crash payload"

    def test_malformed_dict_row_does_not_crash_init(self, tmp_path):
        """A JSON-decodable but structurally invalid row (e.g., a list, or a
        dict missing item_id) must not crash FileBus.__init__ or publish()."""
        bus_file = tmp_path / "bus.jsonl"
        bus_file.write_text(
            "[1, 2, 3]\n"  # JSON-valid but not a dict
            '{"content_hash": "deadbeef"}\n'  # dict with content_hash but no item_id
            '{"item_id": "good", "content": "ok", "source_agent": "a", "tags": ["t"], '
            '"priority": "normal", "ttl_seconds": null, "metadata": {}, '
            '"content_hash": "abcd1234"}\n',
            encoding="utf-8",
        )
        # Init must succeed despite the two malformed lines.
        bus = FileBus(bus_dir=tmp_path)
        # Publishing a fresh item also works.
        item = ContextItem(item_id="new", content="fresh", source_agent="a", tags=["t"])
        result = bus.publish(item)
        assert result["deduped"] is False

    def test_clear_under_flock_does_not_orphan_concurrent_publishes(self, tmp_path):
        """clear() must hold flock so a sibling-process publish cannot write
        to an orphaned inode. We can't easily race two real processes here,
        but we verify the post-clear state is consistent: file exists, is
        empty, hash index is empty, and a subsequent publish lands cleanly."""
        bus = FileBus(bus_dir=tmp_path)
        bus.publish(ContextItem(item_id="pre", content="x", source_agent="a", tags=["t"]))
        bus.clear()
        # File must still exist (truncate, not unlink) so future flock targets
        # the same inode that any existing-fd-holding process is using.
        assert (tmp_path / "bus.jsonl").exists()
        assert (tmp_path / "bus.jsonl").read_bytes() == b""
        # Subsequent publish must be a fresh first-publish (not a stale dedup).
        result = bus.publish(ContextItem(item_id="post", content="x", source_agent="a", tags=["t"]))
        assert result == {"published": True, "item_id": "post", "deduped": False}


# ---------------------------------------------------------------------------
# AC-1, AC-4 — MCP tool wiring + response shape
# ---------------------------------------------------------------------------

class TestMcpPublishContextToolShape:
    """Patch ``_get_context_bus`` at module scope to inject a test bus.

    ``_get_context_bus`` is a real module-level function in ``depthfusion.mcp.server``
    (added by S-78 Task 4). ``patch.object`` without ``create=True`` is the right
    call: if a future refactor renames or removes the function, the patch site
    raises ``AttributeError`` immediately and these tests fail loudly — which is
    what we want. ``create=True`` would silently fabricate a phantom attribute
    and let the tests pass for the wrong reason (cubic 2026-04-30 review caught
    that the original ``create=True`` rationale in the consensus report was
    inverted).
    """

    def test_tool_returns_published_item_id_deduped_keys(self, tmp_path):
        from depthfusion.mcp import server as mcp_server

        bus = FileBus(bus_dir=tmp_path)
        with patch.object(mcp_server, "_get_context_bus", return_value=bus):
            payload = {
                "item": {
                    "item_id": "mcp1",
                    "content": "hello mcp",
                    "source_agent": "test-agent",
                    "tags": ["t"],
                }
            }
            raw = mcp_server._tool_publish_context(payload)

        result = json.loads(raw)
        assert {"published", "item_id", "deduped"} <= set(result.keys()), (
            f"AC-4: response must include published, item_id, deduped — got {result}"
        )
        assert result["published"] is True
        assert result["item_id"] == "mcp1"
        assert result["deduped"] is False

    def test_tool_dedup_response_returns_original_item_id(self, tmp_path):
        from depthfusion.mcp import server as mcp_server

        bus = FileBus(bus_dir=tmp_path)
        with patch.object(mcp_server, "_get_context_bus", return_value=bus):
            first_payload = {
                "item": {
                    "item_id": "first",
                    "content": "same content",
                    "source_agent": "a",
                    "tags": ["t"],
                }
            }
            mcp_server._tool_publish_context(first_payload)

            retry_payload = {
                "item": {
                    "item_id": "retry",
                    "content": "same content",
                    "source_agent": "a",
                    "tags": ["t"],
                }
            }
            raw = mcp_server._tool_publish_context(retry_payload)

        result = json.loads(raw)
        assert result == {"published": True, "item_id": "first", "deduped": True}, (
            f"AC-4: dedup response must return original item_id — got {result}"
        )
