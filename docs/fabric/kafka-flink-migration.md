# Kafka + Flink Migration Guide

This guide covers swapping `RedisStreamBackend` for a `KafkaFlinkBackend` when
your agent fleet grows beyond what a single Redis instance can serve.

---

## When to migrate

| Signal | Recommended action |
|--------|-------------------|
| Single Redis instance; ≤ 50 concurrent agents | Keep `RedisStreamBackend` |
| > 50 concurrent agents or cross-datacenter fan-out | Migrate to Kafka |
| Need CEP (pattern detection across agent streams) | Add Flink job on top of Kafka |
| Need message retention > Redis `maxmemory` allows | Migrate to Kafka |

Redis Streams work well up to ~50 concurrent publishers and ~500K events/day.
Beyond that, Kafka's partitioned log model eliminates the single-writer bottleneck.

---

## KafkaFlinkBackend (not yet shipped — operator guide only)

The `KafkaFlinkBackend` is documented here for operators planning a future
migration. The implementation ships when E-47 is scoped. Until then, use
`RedisStreamBackend` and this document as the interface contract.

### Interface contract

```python
import asyncio
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer

class KafkaFlinkBackend:
    """StreamBackend backed by Kafka topics.

    Topic naming mirrors Redis channel naming:
    depthfusion.stream.{project_slug}
    (dots instead of colons because Kafka topic names can't contain colons)
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        group_id: str = "depthfusion-fabric",
    ) -> None:
        self._servers = bootstrap_servers
        self._group_id = group_id
        self._producer: AIOKafkaProducer | None = None

    def _topic(self, channel: str) -> str:
        return channel.replace(":", ".")

    async def _ensure_producer(self) -> AIOKafkaProducer:
        if self._producer is None:
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._servers,
                value_serializer=lambda v: json.dumps(v).encode(),
            )
            await self._producer.start()
        return self._producer

    async def publish(self, channel: str, payload: dict) -> str:
        producer = await self._ensure_producer()
        result = await producer.send_and_wait(self._topic(channel), payload)
        return f"{result.topic}:{result.partition}:{result.offset}"

    async def subscribe(
        self,
        channels: list[str],
        since_id: str = "$",
    ):
        topics = [self._topic(c) for c in channels]
        consumer = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self._servers,
            group_id=self._group_id,
            value_deserializer=lambda v: json.loads(v.decode()),
            auto_offset_reset="latest" if since_id == "$" else "earliest",
        )
        await consumer.start()
        try:
            async for msg in consumer:
                entry_id = f"{msg.topic}:{msg.partition}:{msg.offset}"
                yield entry_id, msg.value
        finally:
            await consumer.stop()

    async def read_since(
        self,
        channel: str,
        since_id: str = "0",
        count: int = 100,
    ) -> list[tuple[str, dict]]:
        # Kafka doesn't support random-offset reads without a consumer group seek.
        # For bulk replay, use the Flink job to query the compacted topic.
        raise NotImplementedError(
            "Use the Flink CEP job for bulk replay queries. "
            "KafkaFlinkBackend.read_since() is not implemented."
        )

    async def close(self) -> None:
        if self._producer:
            await self._producer.stop()
            self._producer = None
```

### Wiring it in

```python
from depthfusion.core.event_store import EventStore
from depthfusion.graph.store import get_store

graph = get_store()
stream = KafkaFlinkBackend(bootstrap_servers="kafka:9092")
store = EventStore(graph=graph, stream=stream)
```

Pass this `store` to the FastAPI app via dependency injection (or set
`depthfusion.api.events._event_store = store` before the server starts).

---

## Flink CEP: convergence signal

The Flink job detects when multiple agents have published overlapping memory
sets within a short time window — the "convergence signal" that indicates
shared understanding has emerged.

### Pattern definition (Flink CEP pseudo-code)

```java
Pattern<EventEntity, ?> convergence = Pattern
    .<EventEntity>begin("first")
    .where(new SimpleCondition<EventEntity>() {
        @Override
        public boolean filter(EventEntity e) {
            return "AGENT_PUBLISHED".equals(e.eventType);
        }
    })
    .followedByAny("second")
    .where(new SimpleCondition<EventEntity>() {
        @Override
        public boolean filter(EventEntity e) {
            // Different agent, same memory_ref overlap
            return "AGENT_PUBLISHED".equals(e.eventType) &&
                   !e.agentId.equals(/* first.agentId */ "");
        }
    })
    .within(Time.minutes(30));
```

When the pattern fires, the Flink job emits a `CONVERGENCE_SIGNAL` event to
a dedicated topic. DepthFusion can subscribe to this topic and create a
`DERIVED_FROM` edge in the knowledge graph connecting the two EventEntities.

### Infrastructure

```yaml
# docker-compose excerpt (loopback binds per infra-exposure.md)
services:
  kafka:
    image: confluentinc/cp-kafka:7.6.0
    ports:
      - "127.0.0.1:9092:9092"
    environment:
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://127.0.0.1:9092
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"

  flink-jobmanager:
    image: apache/flink:1.19-scala_2.12
    ports:
      - "127.0.0.1:8081:8081"  # Flink Web UI — loopback only
    command: jobmanager
```

---

## Migration checklist

- [ ] Deploy Kafka and verify `depthfusion.stream.{project}` topics are auto-created
- [ ] Install `aiokafka>=0.9` (`pip install aiokafka`)
- [ ] Implement `KafkaFlinkBackend` using the contract above
- [ ] Run the existing E-46 integration test suite against the new backend (`pytest tests/test_integration/test_events_api.py`)
- [ ] Deploy Flink job for CEP convergence signal (optional)
- [ ] Update `DEPTHFUSION_REDIS_URL` env var to unset (or remove) to stop the Redis backend
- [ ] Monitor `fabric_seed` latency — Kafka topic scan is O(1) vs Redis linear key scan; expect improvement at scale
