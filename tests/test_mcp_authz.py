"""T-580: MCP authz test suite — per-tool allow/deny matrix.

Tests
-----
For every one of the 30 MCP tools:

* **allow** — a principal whose role grants the required capability calls the
  tool via ``_handle_tools_call`` and receives ``isError: False``.
* **deny (wrong capability)** — a principal whose role does NOT grant the
  required capability is rejected with ``isError: True`` and the error text
  contains "Authorization denied".
* **deny (no principal)** — ``principal=None`` is always rejected.

Additional unit tests:

* ``check_tool_access`` raises ``AuthorizationError`` for ``None`` principal.
* ``check_tool_access`` raises ``ValueError`` for unknown tool names.
* ``TOOL_CAPABILITIES`` covers all 30 tools registered in ``TOOLS``.
* ``_process_request`` propagates the principal to ``_handle_tools_call``.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from depthfusion.authz.capability_check import AuthorizationError
from depthfusion.authz.roles import ROLE_CAPABILITIES, Capability, Role
from depthfusion.identity.models import Principal
from depthfusion.mcp.authz import (
    TOOL_CAPABILITIES,
    check_tool_access,
    requires_capability,
)
from depthfusion.mcp.server import (
    _handle_tools_call,
    _process_request,
)
from depthfusion.mcp.tools._registry import TOOLS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_principal(
    principal_id: str = "user-1",
    groups: list[str] | None = None,
) -> Principal:
    return Principal(
        principal_id=principal_id,
        upn=f"{principal_id}@example.com",
        display_name=principal_id,
        groups=groups or [],
    )


def make_config() -> MagicMock:
    """Return a minimal config stub that enables all tools."""
    cfg = MagicMock()
    # Enable every feature flag so no tool is excluded by config
    cfg.router_enabled = True
    cfg.graph_enabled = True
    cfg.cognitive_retrieval = True
    cfg.decision_memory = True
    cfg.operational_memory = True
    return cfg


def _role_that_grants(capability: Capability) -> Role:
    """Return the *lowest-privilege* role that grants *capability*."""
    # Preference order: OWNER > ADMIN > MEMBER > VIEWER
    for role in (Role.VIEWER, Role.MEMBER, Role.ADMIN, Role.OWNER):
        if capability in ROLE_CAPABILITIES[role]:
            return role
    return Role.OWNER  # fallback — should always hit OWNER


def _role_that_denies(capability: Capability) -> Role | None:
    """Return a role that does NOT grant *capability*, or None if all roles grant it."""
    for role in (Role.VIEWER, Role.MEMBER, Role.ADMIN):
        if capability not in ROLE_CAPABILITIES[role]:
            return role
    return None  # owner has all; if even viewer grants it there's nothing lower


def _principal_with_role(role: Role) -> Principal:
    return make_principal("test-user", groups=[role.value])


# ---------------------------------------------------------------------------
# T-579 annotation coverage
# ---------------------------------------------------------------------------


class TestToolCapabilitiesCoverage:
    """Every registered tool must have an entry in TOOL_CAPABILITIES."""

    def test_all_tools_annotated(self) -> None:
        missing = [t for t in TOOLS if t not in TOOL_CAPABILITIES]
        assert missing == [], (
            f"Tools missing capability annotations: {missing}\n"
            "Add them to depthfusion/mcp/authz.py TOOL_CAPABILITIES."
        )

    def test_no_extra_entries(self) -> None:
        """TOOL_CAPABILITIES should not reference unknown tools."""
        extra = [t for t in TOOL_CAPABILITIES if t not in TOOLS]
        assert extra == [], (
            f"TOOL_CAPABILITIES references unknown tools: {extra}"
        )

    def test_count_is_29(self) -> None:
        """There must be exactly 31 tools annotated (29 original + recommend_model E-64 + describe_capabilities S-76)."""
        assert len(TOOL_CAPABILITIES) == 31, (
            f"Expected 31 annotated tools, got {len(TOOL_CAPABILITIES)}. "
            f"TOOL_CAPABILITIES keys: {list(TOOL_CAPABILITIES.keys())}"
        )


# ---------------------------------------------------------------------------
# T-578 principal binding — check_tool_access unit tests
# ---------------------------------------------------------------------------


class TestCheckToolAccess:
    def test_none_principal_raises(self) -> None:
        """Unauthenticated call (principal=None) must always be rejected."""
        with pytest.raises(AuthorizationError) as exc_info:
            check_tool_access("depthfusion_status", None)
        assert "anonymous" in str(exc_info.value).lower() or "authentication" in str(exc_info.value).lower()

    def test_unknown_tool_raises_value_error(self) -> None:
        principal = make_principal(groups=["owner"])
        with pytest.raises(ValueError, match="no capability annotation"):
            check_tool_access("nonexistent_tool_xyz", principal)

    def test_principal_with_required_capability_passes(self) -> None:
        cap = Capability.READ_OWN_RECORDS
        role = _role_that_grants(cap)
        principal = _principal_with_role(role)
        # Should not raise
        check_tool_access("depthfusion_status", principal)

    def test_principal_without_required_capability_raises(self) -> None:
        # MANAGE_SETTINGS is OWNER-only in our matrix
        role = Role.VIEWER
        principal = _principal_with_role(role)
        # depthfusion_register_project requires MANAGE_SETTINGS
        with pytest.raises(AuthorizationError):
            check_tool_access("depthfusion_register_project", principal)


# ---------------------------------------------------------------------------
# requires_capability decorator
# ---------------------------------------------------------------------------


class TestRequiresCapabilityDecorator:
    def test_annotates_function(self) -> None:
        @requires_capability(Capability.READ_OWN_RECORDS)
        def my_tool() -> str:
            return "ok"

        assert my_tool._required_capability == Capability.READ_OWN_RECORDS  # type: ignore[attr-defined]

    def test_decorated_function_still_callable(self) -> None:
        @requires_capability(Capability.WRITE_OWN_RECORDS)
        def do_thing(x: int) -> int:
            return x + 1

        assert do_thing(5) == 6

    def test_annotation_survives_wraps(self) -> None:
        @requires_capability(Capability.MANAGE_SETTINGS)
        def admin_tool() -> None:
            pass

        assert hasattr(admin_tool, "_required_capability")
        assert admin_tool._required_capability == Capability.MANAGE_SETTINGS  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# T-580 per-tool allow / deny matrix via _handle_tools_call
# ---------------------------------------------------------------------------


class TestHandleToolsCallAuthz:
    """For every tool: allow with correct role, deny with wrong role, deny with None."""

    def _stub_dispatch(self, tool_name: str, arguments: dict, config: Any, principal: Any) -> str:
        """Stub that bypasses actual tool logic — returns a success marker."""
        return '{"ok": true}'

    def _call_tool(
        self,
        tool_name: str,
        principal: Principal | None,
    ) -> dict:
        config = make_config()
        with patch(
            "depthfusion.mcp.server._dispatch_tool",
            side_effect=self._stub_dispatch,
        ):
            return _handle_tools_call(tool_name, {}, config, principal)

    @pytest.mark.parametrize("tool_name", list(TOOL_CAPABILITIES.keys()))
    def test_allow_with_required_capability(self, tool_name: str) -> None:
        """Principal with the required capability receives isError=False."""
        cap = TOOL_CAPABILITIES[tool_name]
        role = _role_that_grants(cap)
        principal = _principal_with_role(role)
        result = self._call_tool(tool_name, principal)
        assert result["isError"] is False, (
            f"Tool '{tool_name}' should be allowed for role '{role.value}' "
            f"(cap={cap.value}), but got: {result}"
        )

    @pytest.mark.parametrize("tool_name", list(TOOL_CAPABILITIES.keys()))
    def test_deny_without_capability(self, tool_name: str) -> None:
        """Principal missing the required capability receives isError=True."""
        cap = TOOL_CAPABILITIES[tool_name]
        deny_role = _role_that_denies(cap)
        if deny_role is None:
            pytest.skip(f"No role denies {cap.value} — all roles grant it")

        principal = _principal_with_role(deny_role)
        result = self._call_tool(tool_name, principal)
        assert result["isError"] is True, (
            f"Tool '{tool_name}' should be denied for role '{deny_role.value}' "
            f"(cap={cap.value}), but got isError=False."
        )
        text = result["content"][0]["text"]
        assert "Authorization denied" in text, (
            f"Expected 'Authorization denied' in error text for tool '{tool_name}', "
            f"got: {text!r}"
        )

    @pytest.mark.parametrize("tool_name", list(TOOL_CAPABILITIES.keys()))
    def test_deny_no_principal(self, tool_name: str) -> None:
        """Unauthenticated call (principal=None) is always rejected."""
        result = self._call_tool(tool_name, None)
        assert result["isError"] is True, (
            f"Tool '{tool_name}' should be denied for anonymous principal, "
            f"but got isError=False."
        )
        text = result["content"][0]["text"]
        assert "Authorization denied" in text, (
            f"Expected 'Authorization denied' in error text for tool '{tool_name}', "
            f"got: {text!r}"
        )


# ---------------------------------------------------------------------------
# T-578 — _process_request propagates principal
# ---------------------------------------------------------------------------


class TestProcessRequestPropagatesPrincipal:
    """_process_request must pass principal down to _handle_tools_call."""

    def test_principal_propagated_to_handle_tools_call(self) -> None:
        principal = make_principal("user-99", groups=["owner"])
        config = make_config()
        captured: list[Principal | None] = []

        def fake_handle(
            tool_name: str,
            arguments: dict,
            cfg: Any,
            p: Principal | None = None,
        ) -> dict:
            captured.append(p)
            return {"isError": False, "content": [{"type": "text", "text": "ok"}]}

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "depthfusion_status", "arguments": {}},
        }

        with patch("depthfusion.mcp.server._handle_tools_call", side_effect=fake_handle):
            _process_request(request, config, principal=principal)

        assert len(captured) == 1
        assert captured[0] is principal

    def test_none_principal_propagated(self) -> None:
        config = make_config()
        captured: list[Principal | None] = []

        def fake_handle(
            tool_name: str,
            arguments: dict,
            cfg: Any,
            p: Principal | None = None,
        ) -> dict:
            captured.append(p)
            return {"isError": True, "content": [{"type": "text", "text": "Authorization denied: ..."}]}

        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "depthfusion_status", "arguments": {}},
        }

        with patch("depthfusion.mcp.server._handle_tools_call", side_effect=fake_handle):
            _process_request(request, config, principal=None)

        assert len(captured) == 1
        assert captured[0] is None

    def test_tools_list_does_not_require_principal(self) -> None:
        """tools/list must work without authentication (discovery is unauthenticated)."""
        config = make_config()
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/list",
            "params": {},
        }
        response = _process_request(request, config, principal=None)
        assert "result" in response
        assert "tools" in response["result"]
