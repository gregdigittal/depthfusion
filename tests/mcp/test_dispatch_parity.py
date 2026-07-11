"""S-219: Every TOOL_SCHEMAS key must be dispatchable and every dispatch branch
must have a schema.  Prevents silent tool registration without a dispatcher
(the recommend_model class of bug).
"""


def test_all_schema_tools_are_dispatchable():
    """TOOL_SCHEMAS ⊆ DISPATCHABLE — no schema-registered tool can be undispatchable."""
    from depthfusion.mcp.server import DISPATCHABLE
    from depthfusion.mcp.tools._registry import TOOL_SCHEMAS

    missing = set(TOOL_SCHEMAS.keys()) - DISPATCHABLE
    assert not missing, (
        f"Tools in TOOL_SCHEMAS but NOT in DISPATCHABLE (would raise ValueError): {missing}\n"
        "Add an elif branch in server._dispatch_tool() AND add the name to DISPATCHABLE."
    )


def test_all_dispatchable_tools_have_schemas():
    """DISPATCHABLE ⊆ TOOL_SCHEMAS — no dispatch branch without a schema (dead code)."""
    from depthfusion.mcp.server import DISPATCHABLE
    from depthfusion.mcp.tools._registry import TOOL_SCHEMAS

    orphan = DISPATCHABLE - set(TOOL_SCHEMAS.keys())
    assert not orphan, (
        f"Tools in DISPATCHABLE but NOT in TOOL_SCHEMAS (dispatch dead-code): {orphan}\n"
        "Add a schema entry in tools/_registry.py TOOL_SCHEMAS."
    )


def test_dispatchable_matches_tools_dict():
    """Every dispatchable tool must also appear in TOOLS (has a description)."""
    from depthfusion.mcp.server import DISPATCHABLE
    from depthfusion.mcp.tools._registry import TOOLS

    missing = DISPATCHABLE - set(TOOLS.keys())
    assert not missing, (
        f"Tools in DISPATCHABLE but NOT in TOOLS (no description): {missing}"
    )


def test_removing_recommend_model_branch_breaks_parity():
    """Sanity: if DISPATCHABLE is wrong this test fails, proving it's load-bearing."""
    from depthfusion.mcp.server import DISPATCHABLE
    assert "depthfusion_recommend_model" in DISPATCHABLE, (
        "depthfusion_recommend_model must be in DISPATCHABLE — this was the original dispatch bug"
    )
