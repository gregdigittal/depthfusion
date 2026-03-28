"""Tests for router/publisher.py and router/subscriber.py."""
from depthfusion.core.types import ContextItem
from depthfusion.router.bus import InMemoryBus
from depthfusion.router.publisher import ContextPublisher
from depthfusion.router.subscriber import ContextSubscriber


class TestContextPublisher:
    def test_publish_returns_context_item(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="test-agent")
        item = pub.publish("Hello world", tags=["test", "hello"])
        assert isinstance(item, ContextItem)

    def test_publish_item_has_correct_content(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="test-agent")
        item = pub.publish("My content", tags=["tag1"])
        assert item.content == "My content"

    def test_publish_item_has_correct_source_agent(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="ccrs-agent")
        item = pub.publish("CCRS data", tags=["ccrs"])
        assert item.source_agent == "ccrs-agent"

    def test_publish_item_has_correct_tags(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="agent")
        item = pub.publish("data", tags=["alpha", "beta"])
        assert set(item.tags) == {"alpha", "beta"}

    def test_published_item_available_on_bus(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="agent")
        pub.publish("Bus content", tags=["bus-tag"])
        results = bus.subscribe(["bus-tag"])
        assert len(results) == 1

    def test_publish_kwargs_stored_in_metadata(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="agent")
        item = pub.publish("data", tags=["t"], priority="high")
        assert item.priority == "high"

    def test_item_id_is_unique(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="agent")
        item1 = pub.publish("data1", tags=["t"])
        item2 = pub.publish("data2", tags=["t"])
        assert item1.item_id != item2.item_id


class TestContextSubscriber:
    def test_query_returns_matching_items(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="pub-agent")
        pub.publish("Python content", tags=["python", "code"])

        sub = ContextSubscriber(bus=bus, agent_name="sub-agent")
        results = sub.query(["python"])
        assert len(results) == 1

    def test_query_no_match_returns_empty(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="pub-agent")
        pub.publish("CCRS content", tags=["ccrs"])

        sub = ContextSubscriber(bus=bus, agent_name="va-agent")
        results = sub.query(["virtual_analyst"])
        assert results == []

    def test_query_returns_list_of_context_items(self):
        bus = InMemoryBus()
        pub = ContextPublisher(bus=bus, source_agent="agent")
        pub.publish("content", tags=["tag"])

        sub = ContextSubscriber(bus=bus, agent_name="consumer")
        results = sub.query(["tag"])
        for item in results:
            assert isinstance(item, ContextItem)

    def test_ccrs_publisher_va_subscriber_isolation(self):
        """VA subscriber must not receive CCRS publisher content."""
        bus = InMemoryBus()
        ccrs_pub = ContextPublisher(bus=bus, source_agent="ccrs-agent")
        ccrs_pub.publish("Sensitive agreement data", tags=["ccrs", "agreement_automation"])

        va_sub = ContextSubscriber(bus=bus, agent_name="virtual-analyst-agent")
        results = va_sub.query(["virtual_analyst", "va", "analytics"])
        assert results == [], "VA subscriber must not receive CCRS items"
