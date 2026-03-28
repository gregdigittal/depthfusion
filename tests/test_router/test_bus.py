"""Tests for router/bus.py — InMemoryBus and FileBus.

CRITICAL: CCRS/VA project isolation test — VA must NOT receive CCRS items.
"""


from depthfusion.core.types import ContextItem
from depthfusion.router.bus import FileBus, InMemoryBus


def make_item(
    item_id: str,
    content: str,
    source_agent: str,
    tags: list[str],
) -> ContextItem:
    return ContextItem(
        item_id=item_id,
        content=content,
        source_agent=source_agent,
        tags=tags,
    )


class TestInMemoryBus:
    def test_published_item_retrievable_by_tag(self):
        bus = InMemoryBus()
        item = make_item("i1", "CCRS content", "ccrs-agent", ["ccrs", "agreement_automation"])
        bus.publish(item)
        results = bus.subscribe(["ccrs"])
        assert len(results) == 1
        assert results[0].item_id == "i1"

    def test_item_not_retrieved_if_no_tag_overlap(self):
        bus = InMemoryBus()
        item = make_item("i2", "CCRS content", "ccrs-agent", ["ccrs", "agreement_automation"])
        bus.publish(item)
        results = bus.subscribe(["virtual_analyst", "va"])
        assert results == [], "Item with no tag overlap must not be returned"

    def test_clear_removes_all_items(self):
        bus = InMemoryBus()
        bus.publish(make_item("i3", "content A", "agent-a", ["tag-a"]))
        bus.publish(make_item("i4", "content B", "agent-b", ["tag-b"]))
        bus.clear()
        assert bus.subscribe(["tag-a"]) == []
        assert bus.subscribe(["tag-b"]) == []

    def test_multiple_items_tag_filter(self):
        bus = InMemoryBus()
        bus.publish(make_item("x1", "Python item", "a1", ["python", "depthfusion"]))
        bus.publish(make_item("x2", "JS item", "a2", ["javascript"]))
        results = bus.subscribe(["python"])
        ids = {r.item_id for r in results}
        assert ids == {"x1"}

    def test_subscribe_any_matching_tag(self):
        bus = InMemoryBus()
        bus.publish(make_item("y1", "content", "agent", ["alpha", "beta"]))
        results = bus.subscribe(["beta", "gamma"])
        assert len(results) == 1

    def test_subscribe_empty_tags_returns_empty(self):
        bus = InMemoryBus()
        bus.publish(make_item("z1", "content", "agent", ["tag"]))
        results = bus.subscribe([])
        assert results == []

    # CRITICAL: Project isolation test
    def test_ccrs_item_not_received_by_va_agent(self):
        """CRITICAL: VA agent must NOT receive CCRS items (tag mismatch isolation)."""
        bus = InMemoryBus()
        ccrs_item = make_item(
            "ccrs-001",
            "CCRS agreement automation data",
            "ccrs-agent",
            ["ccrs", "agreement_automation", "dpp"],
        )
        bus.publish(ccrs_item)

        # VA agent subscribes with its own project tags
        va_results = bus.subscribe(["virtual_analyst", "va", "analytics"])
        assert va_results == [], (
            "CRITICAL ISOLATION FAILURE: VA agent received CCRS item with no tag overlap"
        )

    def test_source_agent_filter(self):
        bus = InMemoryBus()
        bus.publish(make_item("a1", "from ccrs", "ccrs-agent", ["shared-tag"]))
        bus.publish(make_item("a2", "from va", "va-agent", ["shared-tag"]))
        # Filtering by source_agent
        results = bus.subscribe(["shared-tag"], source_agent="ccrs-agent")
        ids = {r.item_id for r in results}
        assert ids == {"a1"}, "source_agent filter must exclude other agents"


class TestFileBus:
    def test_publish_persists_to_disk(self, tmp_path):
        bus = FileBus(bus_dir=tmp_path)
        item = make_item("f1", "File bus content", "agent-f", ["file", "test"])
        bus.publish(item)
        # Find the written file
        files = list(tmp_path.glob("*.jsonl"))
        assert len(files) >= 1, "FileBus must persist items to disk"

    def test_subscribe_reads_from_disk(self, tmp_path):
        bus = FileBus(bus_dir=tmp_path)
        item = make_item("f2", "Persistent content", "agent-f", ["persistent"])
        bus.publish(item)

        # Create a fresh bus instance to simulate restart
        bus2 = FileBus(bus_dir=tmp_path)
        results = bus2.subscribe(["persistent"])
        assert len(results) == 1
        assert results[0].item_id == "f2"

    def test_file_bus_tag_filtering(self, tmp_path):
        bus = FileBus(bus_dir=tmp_path)
        bus.publish(make_item("f3", "CCRS item", "ccrs", ["ccrs", "agreement"]))
        bus.publish(make_item("f4", "VA item", "va", ["virtual_analyst"]))

        results = bus.subscribe(["ccrs"])
        ids = {r.item_id for r in results}
        assert ids == {"f3"}, "FileBus must filter by tags correctly"

    def test_file_bus_ccrs_va_isolation(self, tmp_path):
        """CRITICAL: FileBus must also isolate CCRS from VA."""
        bus = FileBus(bus_dir=tmp_path)
        bus.publish(make_item(
            "ccrs-file-001",
            "Sensitive CCRS data",
            "ccrs-agent",
            ["ccrs", "agreement_automation"],
        ))

        bus2 = FileBus(bus_dir=tmp_path)
        va_results = bus2.subscribe(["virtual_analyst", "va"])
        assert va_results == [], "FileBus CRITICAL: VA must not receive CCRS items"

    def test_file_bus_clear_removes_files(self, tmp_path):
        bus = FileBus(bus_dir=tmp_path)
        bus.publish(make_item("f5", "content", "agent", ["tag"]))
        bus.clear()
        results = bus.subscribe(["tag"])
        assert results == []
