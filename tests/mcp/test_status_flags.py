"""S-221: depthfusion_status must report all effective config flags."""
import json

from depthfusion.core.config import DepthFusionConfig
from depthfusion.mcp.tools.system import _tool_status


def _status(cfg: DepthFusionConfig) -> dict:
    return json.loads(_tool_status(cfg))


class TestStatusFlags:
    def test_back_compat_keys_present(self):
        cfg = DepthFusionConfig()
        s = _status(cfg)
        for key in ("depthfusion", "enabled_tools", "rlm_enabled", "router_enabled",
                    "session_enabled", "fusion_enabled"):
            assert key in s, f"missing back-compat key: {key}"

    def test_effective_flags_block_present(self):
        s = _status(DepthFusionConfig())
        assert "effective_flags" in s
        ef = s["effective_flags"]
        assert "on_by_default" in ef
        assert "behind_flag" in ef
        assert "backends" in ef

    def test_rogue_gates_appear_in_behind_flag(self):
        s = _status(DepthFusionConfig())
        behind = s["effective_flags"]["behind_flag"]
        assert "fusion_gates_enabled" in behind, "fusion_gates_enabled missing from behind_flag"
        assert "cognitive_scoring_enabled" in behind, "cognitive_scoring_enabled missing from behind_flag"

    def test_rogue_gates_reflect_config_values(self):
        cfg = DepthFusionConfig(fusion_gates_enabled=True, cognitive_scoring_enabled=True)
        s = _status(cfg)
        behind = s["effective_flags"]["behind_flag"]
        assert behind["fusion_gates_enabled"] is True
        assert behind["cognitive_scoring_enabled"] is True

    def test_on_by_default_contains_core_flags(self):
        s = _status(DepthFusionConfig())
        on = s["effective_flags"]["on_by_default"]
        for flag in ("fusion_enabled", "session_enabled", "rlm_enabled", "router_enabled"):
            assert flag in on, f"expected {flag} in on_by_default"

    def test_flag_count_matches_config_field_count(self):
        import dataclasses
        cfg = DepthFusionConfig()
        s = _status(cfg)
        ef = s["effective_flags"]
        status_bool_count = len(ef["on_by_default"]) + len(ef["behind_flag"])
        # Count boolean fields in the dataclass (excluding skipped ones)
        _SKIP = {
            "ambient_skip_tools", "skillforge_api_url", "skillforge_api_token",
            "skillforge_recursive_skill_id", "bus_file_dir", "api_token",
            "mcp_http_token", "gemma_url", "gemma_model", "event_log",
            "reranker_backend", "extractor_backend", "linker_backend",
            "summariser_backend", "embedding_backend", "decision_extractor_backend",
            "bus_backend",
        }
        dataclass_bool_count = sum(
            1 for f in dataclasses.fields(cfg)
            if isinstance(getattr(cfg, f.name), bool) and f.name not in _SKIP
        )
        assert status_bool_count == dataclass_bool_count, (
            f"Status reports {status_bool_count} boolean flags but config has {dataclass_bool_count}. "
            "A new bool field was likely added to DepthFusionConfig without updating SKIP_FIELDS in system.py."
        )
