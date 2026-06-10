"""depthfusion MCP tool implementations — shared server state and infrastructure helpers."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from depthfusion.router.bus import ContextBus, FileBus, InMemoryBus

logger = logging.getLogger("depthfusion.mcp.server")

# Module-level ContextBus cache (S-78). Patchable for tests:
#   patch.object(mcp_server, "_get_context_bus", return_value=test_bus, create=True)
_BUS_INSTANCE: ContextBus | None = None



def _get_context_bus(config: Any = None) -> ContextBus:
    """Return the process-wide ContextBus, lazily constructed from config.

    The instance is cached on first call to avoid rebuilding FileBus's hash
    index on every MCP request. Tests should patch this function directly via
    ``unittest.mock.patch.object(..., create=True)`` rather than mutate the
    cache. ``config`` may be ``None`` — defaults are used (file backend at
    ``~/.claude/context-bus``).
    """
    global _BUS_INSTANCE
    if _BUS_INSTANCE is not None:
        return _BUS_INSTANCE

    backend = getattr(config, "bus_backend", "file")
    bus_dir_str = getattr(config, "bus_file_dir", "~/.claude/context-bus")
    if backend == "memory":
        _BUS_INSTANCE = InMemoryBus()
    else:
        _BUS_INSTANCE = FileBus(bus_dir=Path(bus_dir_str).expanduser())
    return _BUS_INSTANCE

_HNSW_STORE: Any = None  # depthfusion.retrieval.hnsw_store.HNSWStore | None
_HNSW_INIT_ATTEMPTED: bool = False
_HNSW_SHUTDOWN_REGISTERED: bool = False
_HNSW_LOCK = threading.Lock()

def _hnsw_enabled() -> bool:
    return os.environ.get("DEPTHFUSION_HNSW_ENABLED", "false").lower() in (
        "true",
        "1",
        "yes",
    )

def _register_hnsw_shutdown() -> None:
    """Install SIGTERM/SIGINT handlers that flush the index on graceful exit."""
    global _HNSW_SHUTDOWN_REGISTERED
    if _HNSW_SHUTDOWN_REGISTERED:
        return
    try:
        import signal as _signal

        def _hnsw_shutdown_handler(signum, frame):  # type: ignore[no-redef]
            store = _HNSW_STORE
            if store is not None:
                try:
                    store.save()
                    logger.info("[hnsw] index persisted on shutdown")
                except Exception as exc:  # noqa: BLE001 — best-effort flush
                    logger.warning("[hnsw] failed to persist on shutdown: %s", exc)

        # Only install handlers in the main thread (signal API restriction).
        if threading.current_thread() is threading.main_thread():
            _signal.signal(_signal.SIGTERM, _hnsw_shutdown_handler)
            _signal.signal(_signal.SIGINT, _hnsw_shutdown_handler)
            _HNSW_SHUTDOWN_REGISTERED = True
    except (ValueError, OSError) as exc:
        # signal() raises ValueError outside the main thread or when running
        # under restricted environments — degrade silently.
        logger.debug("[hnsw] shutdown handler not installed: %s", exc)

def _get_hnsw_store() -> Any:
    """Return the process-wide HNSWStore (lazily constructed), or None.

    Returns None when ``DEPTHFUSION_HNSW_ENABLED`` is falsey or when the
    store could not be initialised (missing hnswlib, embedding-model load
    failure, etc.). The init attempt is only made once per process — on
    subsequent calls a failed init still returns None without retrying.
    """
    global _HNSW_STORE, _HNSW_INIT_ATTEMPTED
    if not _hnsw_enabled():
        return None
    if _HNSW_INIT_ATTEMPTED:
        return _HNSW_STORE

    with _HNSW_LOCK:
        if _HNSW_INIT_ATTEMPTED:
            return _HNSW_STORE
        _HNSW_INIT_ATTEMPTED = True
        try:
            from depthfusion.retrieval.hnsw_store import HNSWStore

            index_path_raw = os.environ.get(
                "DEPTHFUSION_HNSW_INDEX_PATH",
                "~/.agent-mc/depthfusion/hnsw.bin",
            )
            model_name = (
                os.environ.get("DEPTHFUSION_EMBEDDING_MODEL", "").strip()
                or "all-MiniLM-L6-v2"
            )
            store = HNSWStore(
                index_path=Path(index_path_raw).expanduser(),
                model_name=model_name,
            )
            if not getattr(store, "hnsw_ready", False):
                logger.info("[hnsw] store not ready — falling back to BM25-only")
                _HNSW_STORE = None
                return None
            _HNSW_STORE = store
            _register_hnsw_shutdown()
            logger.info("[hnsw] store initialised (model=%s)", model_name)
            return _HNSW_STORE
        except Exception as exc:  # noqa: BLE001 — graceful degrade
            logger.info("[hnsw] init failed (%s) — falling back to BM25-only", exc)
            _HNSW_STORE = None
            return None

_fabric_store = None

def _get_fabric_store():
    """Lazy singleton EventStore for MCP tool calls (sync init, async methods)."""
    global _fabric_store
    if _fabric_store is None:
        from depthfusion.core.event_store import EventStore, RedisStreamBackend
        from depthfusion.graph.store import get_store

        graph = get_store()
        redis_url = os.getenv("DEPTHFUSION_REDIS_URL", "")
        stream = RedisStreamBackend(redis_url) if redis_url else None
        _fabric_store = EventStore(graph=graph, stream=stream)
    return _fabric_store
