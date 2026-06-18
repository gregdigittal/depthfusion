"""Tests for S-112 — structured observation fields on ContextItem / retrieve_context.

AC-2: retrieve_context returns facts/concepts/files_read/files_modified when
      present on a stored MemoryObject.
AC-5: describe_capabilities output lists 'structured_fields' under
      supported_features.publish_context.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

from depthfusion.mcp.server import _tool_describe_capabilities, _tool_retrieve_context

# ---------------------------------------------------------------------------
# AC-5: describe_capabilities
# ---------------------------------------------------------------------------


def test_describe_capabilities_lists_structured_fields():
    """S-112 AC-5: describe_capabilities must advertise structured_fields."""
    result = json.loads(_tool_describe_capabilities())
    supported = result.get("supported_features", {})
    assert "publish_context" in supported, (
        "describe_capabilities must include 'supported_features.publish_context'"
    )
    assert "structured_fields" in supported["publish_context"], (
        "describe_capabilities must list 'structured_fields' in supported_features.publish_context"
    )


# ---------------------------------------------------------------------------
# AC-2: retrieve_context returns structured fields when stored
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> object:
    cfg = types.SimpleNamespace()
    cfg.memory_store_path = tmp_path / "test_memories.db"
    return cfg


def _write_memory_with_structured_fields(store_path: Path) -> str:
    """Insert a MemoryObject with facts/concepts/files_read/files_modified and return its ID."""
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory_object import (
        MemoryConfidence,
        MemoryObject,
        MemoryStatus,
        MemoryType,
    )
    from depthfusion.storage.memory_store import MemoryStore

    store = MemoryStore(store_path)
    mem_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    obj = MemoryObject(
        id=mem_id,
        project_id="test-proj",
        type=MemoryType.OPERATIONAL,
        status=MemoryStatus.ACTIVE,
        content="Deployed authentication service to production",
        summary="auth service deployed",
        confidence=MemoryConfidence(score=0.9),
        extra={
            "facts": ["auth service deployed", "uses JWT tokens"],
            "concepts": ["authentication", "deployment"],
            "files_read": ["src/auth/config.py"],
            "files_modified": ["src/auth/service.py"],
            "acl_allow": ["test-proj"],
        },
        created_at=now,
        updated_at=now,
    )
    store.upsert(obj)
    return mem_id


def test_retrieve_context_includes_structured_fields(tmp_path):
    """S-112 AC-2: retrieve_context response includes facts/concepts/files_* when non-empty."""
    cfg = _make_config(tmp_path)
    _write_memory_with_structured_fields(cfg.memory_store_path)

    result = json.loads(_tool_retrieve_context(
        {"project_id": "test-proj", "query": "auth deployment", "top_k": 5},
        cfg,
    ))

    assert result["count"] >= 1, "Expected at least one memory returned"
    memories = result["memories"]
    mem = memories[0]

    assert "facts" in mem, "retrieve_context must include 'facts' field when present"
    assert "authentication" in " ".join(mem.get("concepts", [])) or "deployment" in " ".join(mem.get("concepts", []))
    assert "files_read" in mem, "retrieve_context must include 'files_read' field when present"
    assert "files_modified" in mem, "retrieve_context must include 'files_modified' field when present"

    assert "auth service deployed" in mem["facts"]
    assert "src/auth/service.py" in mem["files_modified"]


def test_retrieve_context_omits_empty_structured_fields(tmp_path):
    """S-112 AC-2: retrieve_context omits structured fields keys when they are empty."""
    import uuid
    from datetime import datetime, timezone

    from depthfusion.core.memory_object import (
        MemoryConfidence,
        MemoryObject,
        MemoryStatus,
        MemoryType,
    )
    from depthfusion.storage.memory_store import MemoryStore

    cfg = _make_config(tmp_path)
    store = MemoryStore(cfg.memory_store_path)
    now = datetime.now(timezone.utc)
    obj = MemoryObject(
        id=str(uuid.uuid4()),
        project_id="plain-proj",
        type=MemoryType.OPERATIONAL,
        status=MemoryStatus.ACTIVE,
        content="Plain observation with no structured fields",
        summary="plain observation",
        confidence=MemoryConfidence(score=0.8),
        extra={"acl_allow": ["plain-proj"]},
        created_at=now,
        updated_at=now,
    )
    store.upsert(obj)

    result = json.loads(_tool_retrieve_context(
        {"project_id": "plain-proj", "query": "plain observation", "top_k": 5},
        cfg,
    ))

    assert result["count"] >= 1
    mem = result["memories"][0]
    # Empty structured fields must not appear as keys
    for field in ("facts", "concepts", "files_read", "files_modified"):
        assert field not in mem, (
            f"retrieve_context must omit '{field}' key when field is empty"
        )
