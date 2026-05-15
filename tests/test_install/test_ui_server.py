"""Tests for the guided web install wizard backend (S-110 T-372)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from depthfusion.install import ui_server
from depthfusion.install.gpu_probe import AppleSiliconInfo, GPUInfo
from depthfusion.install.ui_server import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset server-side session state before and after each test."""
    ui_server._reset_state()
    yield
    ui_server._reset_state()


# ---------------------------------------------------------------------------
# 1. Hardware endpoint
# ---------------------------------------------------------------------------

def test_hardware_endpoint_returns_recommended_mode():
    with (
        patch("depthfusion.install.ui_server.detect_apple_silicon") as mock_apple,
        patch("depthfusion.install.ui_server.detect_gpu") as mock_gpu,
    ):
        mock_apple.return_value = AppleSiliconInfo(
            has_apple_silicon=False, chip_name="", memory_gb=0.0,
            reason="not apple silicon",
        )
        mock_gpu.return_value = GPUInfo(
            has_gpu=True, gpu_name="Tesla T4", vram_gb=16.0, device_count=1,
            reason="detected 1 GPU(s); primary: Tesla T4 (16.0 GB VRAM)",
        )
        r = client.get("/install/api/hardware")

    assert r.status_code == 200
    data = r.json()
    assert data["recommended_mode"] == "vps-gpu"
    assert "nvidia_gpu" in data
    assert "apple_silicon" in data
    assert data["nvidia_gpu"]["detected"] is True
    assert data["apple_silicon"]["detected"] is False


# ---------------------------------------------------------------------------
# 2. Step 1 — mode options
# ---------------------------------------------------------------------------

def test_step1_returns_mode_options():
    r = client.get("/install/api/steps/1")
    assert r.status_code == 200
    data = r.json()
    ids = {m["id"] for m in data["mode_options"]}
    assert ids == {"local", "vps-cpu", "vps-gpu", "mac-mlx"}
    assert "recommended_mode" in data


# ---------------------------------------------------------------------------
# 3. Step 3 — local mode has no required extras
# ---------------------------------------------------------------------------

def test_step3_deps_local_has_no_required_extras():
    client.post("/install/api/steps/1", json={"mode": "local"})
    r = client.get("/install/api/steps/3")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "local"
    assert data["deps"] == []


# ---------------------------------------------------------------------------
# 4. Step 3 — vps-cpu lists anthropic as required
# ---------------------------------------------------------------------------

def test_step3_deps_vps_cpu_lists_anthropic():
    client.post("/install/api/steps/1", json={"mode": "vps-cpu"})
    r = client.get("/install/api/steps/3")
    assert r.status_code == 200
    data = r.json()
    names = [d["name"] for d in data["deps"]]
    assert "anthropic" in names
    # Verify the dep has the correct shape
    anthropic_dep = next(d for d in data["deps"] if d["name"] == "anthropic")
    assert anthropic_dep["required"] is True
    assert "installed" in anthropic_dep
    assert "required_version" in anthropic_dep


# ---------------------------------------------------------------------------
# 5. Step 4 — env var values must not appear in the response
# ---------------------------------------------------------------------------

def test_step4_env_write_does_not_echo_key_in_response():
    client.post("/install/api/steps/1", json={"mode": "vps-cpu"})
    fake_key = "sk-ant-fake-key-xxxxxxxxxxxxxxxxxxx"
    with patch("depthfusion.install.install.write_env_from_dict"):
        r = client.post(
            "/install/api/steps/4",
            json={"ANTHROPIC_API_KEY": fake_key},
        )
    assert r.status_code == 200
    # The key VALUE must not appear anywhere in the response
    assert fake_key not in r.text
    # But the key NAME is returned (written_keys list)
    data = r.json()
    assert "ANTHROPIC_API_KEY" in data["written_keys"]
    assert data["next_step"] == 5


# ---------------------------------------------------------------------------
# 6. Step 5 — hooks diff has correct shape
# ---------------------------------------------------------------------------

def test_step5_hooks_diff_returns_correct_shape():
    client.post("/install/api/steps/1", json={"mode": "local"})
    with patch("depthfusion.install.install.get_hooks_diff") as mock_diff:
        mock_diff.return_value = {
            "settings_json_path": "/home/user/.claude/settings.json",
            "hooks_dir": "/home/user/.claude/hooks",
            "hooks_to_register": [
                {"event": "PreCompact", "script": "/home/user/.claude/hooks/depthfusion-pre-compact.sh", "already_registered": False},
                {"event": "PostCompact", "script": "/home/user/.claude/hooks/depthfusion-post-compact.sh", "already_registered": False},
            ],
            "env_path": "/home/user/.claude/depthfusion.env",
        }
        r = client.get("/install/api/steps/5")

    assert r.status_code == 200
    data = r.json()
    assert "diff" in data
    diff = data["diff"]
    assert "settings_json_path" in diff
    assert "hooks_dir" in diff
    assert "hooks_to_register" in diff
    assert "env_path" in diff
    assert len(diff["hooks_to_register"]) == 2


# ---------------------------------------------------------------------------
# 7. Apply endpoint returns dry_run: true
# ---------------------------------------------------------------------------

def test_apply_runs_dry_run():
    client.post("/install/api/steps/1", json={"mode": "local"})
    with patch("depthfusion.install.install.install_local") as mock_install:
        mock_install.return_value = None
        r = client.post("/install/api/install/apply")

    assert r.status_code == 200
    data = r.json()
    assert data["dry_run"] is True
    assert data["mode"] == "local"
    assert "summary" in data
    mock_install.assert_called_once()
    # dry_run=True must have been passed
    call_kwargs = mock_install.call_args
    assert call_kwargs.kwargs.get("dry_run") is True


# ---------------------------------------------------------------------------
# 8. Finish endpoint calls the correct install function with dry_run=False
# ---------------------------------------------------------------------------

def test_finish_calls_installer():
    client.post("/install/api/steps/1", json={"mode": "local"})
    with patch("depthfusion.install.install.install_local") as mock_install:
        mock_install.return_value = None
        r = client.post("/install/api/install/finish")

    assert r.status_code == 200
    data = r.json()
    assert data["dry_run"] is False
    assert data["mode"] == "local"
    mock_install.assert_called_once()
    # dry_run=False must have been passed
    call_kwargs = mock_install.call_args
    assert call_kwargs.kwargs.get("dry_run") is False
