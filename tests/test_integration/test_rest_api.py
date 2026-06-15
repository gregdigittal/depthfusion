"""REST API integration tests — Task 10 / E-31 / S-100."""
from __future__ import annotations

import pytest


def _install_fake_principal(app):
    from depthfusion.api.auth import _require_principal_dep
    from depthfusion.identity.models import Principal
    fake = Principal(principal_id="greg", upn="greg@test.local")
    original = dict(app.dependency_overrides)
    app.dependency_overrides[_require_principal_dep] = lambda: fake
    return original


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))
    from importlib import reload

    import depthfusion.api.rest as rest_module
    reload(rest_module)
    original = _install_fake_principal(rest_module.app)
    from fastapi.testclient import TestClient
    yield TestClient(rest_module.app)
    rest_module.app.dependency_overrides.clear()
    rest_module.app.dependency_overrides.update(original)


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cognitive_state_endpoint(client):
    response = client.get("/v1/cognitive-state?project_id=proj-test")
    assert response.status_code == 200
    data = response.json()
    assert "total_memories" in data
    assert "feature_flags" in data


def test_memories_endpoint_returns_list(client):
    response = client.get("/v1/memories?project_id=proj-test")
    assert response.status_code == 200
    data = response.json()
    assert "memories" in data
    assert "count" in data
    assert isinstance(data["memories"], list)


def test_api_binds_loopback_by_default(monkeypatch):
    monkeypatch.delenv("DEPTHFUSION_API_PUBLIC", raising=False)
    from importlib import reload

    import depthfusion.api.rest as rest_module
    reload(rest_module)
    assert rest_module.get_bind_host() == "127.0.0.1"


def test_api_public_bind_requires_token(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_API_PUBLIC", "1")
    monkeypatch.setenv("DEPTHFUSION_API_TOKEN", "")
    from importlib import reload

    import depthfusion.api.rest as rest_module
    reload(rest_module)
    with pytest.raises(ValueError, match="DEPTHFUSION_API_TOKEN"):
        rest_module.validate_public_bind_config()


def test_api_public_bind_with_token_does_not_raise(monkeypatch):
    monkeypatch.setenv("DEPTHFUSION_API_PUBLIC", "1")
    monkeypatch.setenv("DEPTHFUSION_API_TOKEN", "s3cret-t0ken")
    monkeypatch.setenv("DEPTHFUSION_QUERY_API_KEY", "query-key-123")
    from importlib import reload

    import depthfusion.api.rest as rest_module
    reload(rest_module)
    rest_module.validate_public_bind_config()  # must not raise
