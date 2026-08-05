"""Integration tests for GET /query/aggregate?type=file_diffs — E-73 S-254 / T-864.

Real-collaborator style, deliberately end-to-end: a real ``git init`` working
tree under ``tmp_path``, a real ``JSONGraphStore``, a real ``EventStore``, a real
filesystem checkpoint store, and the real FastAPI app via ``TestClient``. The
diffs asserted on here are produced by the actual ``git`` binary and travel the
whole way — collector → ``base64(gzip(...))`` → checkpoint JSON on disk →
``list_checkpoints`` → ``query_file_diffs`` → HTTP JSON.

Why that matters: a suite that hand-wrote the encoded payloads (as
test_rest_query_checkpoints.py's ``_seed`` does, correctly, for its own purpose)
would keep passing if the decode order were reversed, if ``metadata`` were
dropped from ``to_dict()``, or if the read path started constructing an
``EventStore``. The assertions here fail on each of those.

Run explicitly (norecursedirs excludes this directory from the default run):
    python -m pytest tests/test_integration/test_rest_query_file_diffs.py -q
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import subprocess
from pathlib import Path

import pytest

PROJECT = "depthfusion"
TRACKED = "tracked.py"
FIRST_LINE = "value = 'T-864 first change'"
SECOND_LINE = "value = 'T-864 second change'"

_EXPECTED_ITEM_KEYS = {"checkpoint_id", "session_id", "project_slug", "created_at", "diff"}


def _install_fake_principal(app):
    """Override _require_principal_dep with a no-op that returns a test principal.

    Without this, _UnconfiguredPrincipalDep raises 503 for every protected route
    when DEPTHFUSION_JWKS_URI / OIDC_ISSUER / OIDC_AUDIENCE are absent (always
    true in the test environment).  Returns the original overrides so callers
    can restore state.
    """
    from depthfusion.api.auth import _require_principal_dep
    from depthfusion.identity.models import Principal

    fake = Principal(principal_id="greg", upn="greg@test.local")
    original = dict(app.dependency_overrides)
    app.dependency_overrides[_require_principal_dep] = lambda: fake
    return original


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in *repo*, failing loudly — fixture setup must not degrade."""
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _decode(encoded: str) -> str:
    """base64 → gunzip → text, mirroring the write path exactly."""
    return gzip.decompress(base64.b64decode(encoded)).decode("utf-8", errors="replace")


@pytest.fixture
def checkpoint_dir(tmp_path, monkeypatch) -> Path:
    """Point DEPTHFUSION_CHECKPOINT_DIR at a temp root and return it."""
    root = tmp_path / "checkpoints"
    root.mkdir()
    monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(root))
    return root


@pytest.fixture
def tmp_repo(tmp_path) -> Path:
    """A real git working tree with one committed file, ready to be modified."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@depthfusion.local")
    _git(repo, "config", "user.name", "DepthFusion Test")
    _git(repo, "config", "commit.gpgsign", "false")

    tracked = repo / TRACKED
    tracked.write_text("value = 'original line'\n", encoding="utf-8")
    _git(repo, "add", TRACKED)
    _git(repo, "commit", "-q", "-m", "initial commit")
    return repo


@pytest.fixture
def store(tmp_path, monkeypatch, checkpoint_dir):
    """A real EventStore over a real JSONGraphStore (no MagicMock)."""
    from depthfusion.core.event_store import EventStore, InMemoryStreamBackend
    from depthfusion.graph.store import JSONGraphStore

    monkeypatch.setenv("DEPTHFUSION_LOCK_DIR", str(tmp_path / "locks"))
    graph = JSONGraphStore(path=tmp_path / "graph.json")
    return EventStore(graph=graph, stream=InMemoryStreamBackend())


@pytest.fixture
def client(tmp_path, monkeypatch, checkpoint_dir):
    monkeypatch.setenv("DEPTHFUSION_REST_API", "1")
    monkeypatch.setenv("DEPTHFUSION_EVENT_LOG", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("DEPTHFUSION_MEMORY_STORE", str(tmp_path / "memories.db"))
    monkeypatch.delenv("DEPTHFUSION_QUERY_API_KEY", raising=False)
    from importlib import reload

    import depthfusion.api.rest as rest_module
    reload(rest_module)

    original = _install_fake_principal(rest_module.app)
    from fastapi.testclient import TestClient
    yield TestClient(rest_module.app)
    rest_module.app.dependency_overrides.clear()
    rest_module.app.dependency_overrides.update(original)


def _publish(store, repo: Path, line: str, *, session_id: str, checkpoint_id: str,
             project: str = PROJECT):
    """Write *line* into the tracked file, then publish a real checkpoint.

    ``publish_checkpoint`` is async; the surrounding tests are synchronous
    because ``TestClient`` drives its own portal, so each publish gets its own
    short-lived loop via ``asyncio.run``.
    """
    (repo / TRACKED).write_text(f"{line}\n", encoding="utf-8")
    return asyncio.run(
        store.publish_checkpoint(
            session_id=session_id,
            project_slug=project,
            plan_state=f"T-864 — {line}",
            files_modified=[TRACKED],
            checkpoint_id=checkpoint_id,
            project_path=str(repo),
        )
    )


@pytest.fixture
def two_checkpoints(store, tmp_repo):
    """Two real checkpoints, each carrying a diff for the SAME file.

    Published oldest-first with different working-tree content so the two diffs
    are distinguishable and their chronological order is assertable.
    """
    first = _publish(store, tmp_repo, FIRST_LINE, session_id="sess-a", checkpoint_id="cp-first")
    second = _publish(store, tmp_repo, SECOND_LINE, session_id="sess-b", checkpoint_id="cp-second")

    # Guard the fixture's own premise: without diffs on disk every assertion
    # below would pass vacuously against an empty history.
    assert TRACKED in first.metadata["diffs"]
    assert TRACKED in second.metadata["diffs"]
    assert first.created_at < second.created_at
    return first, second


# ---------------------------------------------------------------------------
# AC-4 / AC-7 — the happy path over HTTP
# ---------------------------------------------------------------------------

def test_file_diffs_returns_two_chronological_diffs(client, two_checkpoints):
    resp = client.get(f"/query/aggregate?type=file_diffs&file={TRACKED}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["file"] == TRACKED
    assert data["count"] == 2
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # Ascending created_at — a chronological history view.
    created = [i["created_at"] for i in data["items"]]
    assert created == sorted(created)
    assert [i["checkpoint_id"] for i in data["items"]] == ["cp-first", "cp-second"]
    assert [i["session_id"] for i in data["items"]] == ["sess-a", "sess-b"]

    # Real decoded diff text, in the right slot.
    assert set(data["items"][0].keys()) == _EXPECTED_ITEM_KEYS
    assert data["items"][0]["diff"].startswith("diff --git")
    assert FIRST_LINE in data["items"][0]["diff"]
    assert SECOND_LINE in data["items"][1]["diff"]
    assert SECOND_LINE not in data["items"][0]["diff"]
    assert all(i["project_slug"] == PROJECT for i in data["items"])


def test_file_diffs_decoded_text_matches_the_stored_encoding(client, two_checkpoints):
    """The HTTP payload really is base64→gunzip of what is on disk."""
    first, _ = two_checkpoints

    resp = client.get(f"/query/aggregate?type=file_diffs&file={TRACKED}")
    item = resp.json()["items"][0]
    assert item["diff"] == _decode(first.metadata["diffs"][TRACKED])


def test_file_diffs_project_scoping(client, two_checkpoints):
    assert client.get(
        f"/query/aggregate?type=file_diffs&file={TRACKED}&project={PROJECT}"
    ).json()["count"] == 2
    assert client.get(
        f"/query/aggregate?type=file_diffs&file={TRACKED}&project=no-such-project"
    ).json()["count"] == 0


def test_file_diffs_unknown_file_returns_empty_history(client, two_checkpoints):
    resp = client.get("/query/aggregate?type=file_diffs&file=src/never/touched.py")
    assert resp.status_code == 200
    assert resp.json() == {
        "items": [], "total": 0, "count": 0, "file": "src/never/touched.py",
    }


# ---------------------------------------------------------------------------
# AC-5 — validation
# ---------------------------------------------------------------------------

def test_file_diffs_without_file_param_is_422(client, two_checkpoints):
    resp = client.get("/query/aggregate?type=file_diffs")
    assert resp.status_code == 422
    # Blank is treated the same as absent rather than as "the empty path".
    assert client.get("/query/aggregate?type=file_diffs&file=%20").status_code == 422


def test_file_diffs_limit_bounds_enforced(client, two_checkpoints):
    base = f"/query/aggregate?type=file_diffs&file={TRACKED}"
    assert client.get(f"{base}&limit=0").status_code == 422
    assert client.get(f"{base}&limit=501").status_code == 422
    assert client.get(f"{base}&limit=1").json()["count"] == 1


def test_limit_bounds_are_not_enforced_on_the_no_type_aggregate(client, two_checkpoints):
    """Review-gate regression guard: `limit` must stay scoped to type=file_diffs.

    `limit` is declared without ge=/le=, and as a *string*, precisely so FastAPI
    neither range-checks nor coerces it on the pre-existing recall-event path.
    Either would 422 an existing caller passing a `limit` this route previously
    ignored as an unknown query param.

    The non-integer cases below are the half the first pass missed and the review
    gate caught: `Optional[int]` made FastAPI reject `?limit=abc` at validation
    time, before the handler body could scope the parameter to type=file_diffs, so
    a previously-200 request became a 422.
    """
    for suffix in ("limit=0", "limit=501", "limit=9999", "limit=-1",
                   "limit=abc", "limit=", "limit=1.5"):
        resp = client.get(f"/query/aggregate?{suffix}")
        assert resp.status_code == 200, (
            f"/query/aggregate?{suffix} must be unaffected by the file_diffs "
            f"limit parsing, got {resp.status_code}"
        )


@pytest.mark.parametrize("bad_limit", ["abc", "1.5", "0x10", " ten "])
def test_non_integer_limit_is_422_on_the_file_diffs_branch(
    client, two_checkpoints, bad_limit,
):
    """Moving `limit` parsing into the branch must not stop it being validated.

    The companion to the test above: declaring `limit` as a string keeps the
    no-type path untouched, but the file_diffs branch — the only place the
    parameter means anything — must still reject a value it cannot parse, rather
    than silently falling back to the default.
    """
    resp = client.get(
        f"/query/aggregate?type=file_diffs&file={TRACKED}&limit={bad_limit}"
    )
    assert resp.status_code == 422
    # And the caller's value is not reflected back (matching the other 422s here).
    assert bad_limit.strip() not in resp.text


def test_blank_limit_on_the_file_diffs_branch_uses_the_default(client, two_checkpoints):
    """`limit=` (present but empty) is "unspecified", not "unparseable"."""
    resp = client.get(f"/query/aggregate?type=file_diffs&file={TRACKED}&limit=")
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_file_diffs_total_is_the_pre_truncation_match_count(client, two_checkpoints):
    """`total` counts every match, `count` only the rows returned.

    They must diverge when `limit` truncates, otherwise a caller cannot tell
    "this file has 1 diff" from "this file has more, and you are seeing 1".
    """
    resp = client.get(f"/query/aggregate?type=file_diffs&file={TRACKED}&limit=1")
    assert resp.status_code == 200
    data = resp.json()

    assert data["count"] == 1
    assert len(data["items"]) == 1
    assert data["total"] == 2


def test_unparseable_since_is_422_not_500(client, two_checkpoints):
    resp = client.get(f"/query/aggregate?type=file_diffs&file={TRACKED}&since=not-a-date")
    assert resp.status_code == 422


def test_unsupported_type_is_rejected(client):
    assert client.get("/query/aggregate?type=nonsense").status_code == 422


# ---------------------------------------------------------------------------
# AC-4 — the pre-existing aggregate behaviour is untouched
# ---------------------------------------------------------------------------

def test_default_aggregate_response_unchanged_without_type(client):
    """type absent → the recall-event aggregate, same shape as before T-864."""
    resp = client.get("/query/aggregate")
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {
        "total_events",
        "total_latency_ms",
        "avg_latency_ms",
        "p95_latency_ms",
        "avg_result_count",
        "modes",
        "config_versions",
    }

    # Explicitly-null type takes the same branch as an absent one.
    assert client.get("/query/aggregate?from=2026-01-01").status_code == 200


# ---------------------------------------------------------------------------
# AC-2 — tz-safe `since` comparison
# ---------------------------------------------------------------------------

def test_naive_since_does_not_raise_and_filters(client, two_checkpoints):
    """A naive bound must be coerced, never compared against aware created_at."""
    first, second = two_checkpoints

    # Naive, no offset — the TypeError case this AC exists for.
    resp = client.get(
        f"/query/aggregate?type=file_diffs&file={TRACKED}&since=2000-01-01T00:00:00"
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 2

    # Date-only is also naive and also valid.
    assert client.get(
        f"/query/aggregate?type=file_diffs&file={TRACKED}&since=2000-01-01"
    ).json()["count"] == 2

    # A bound between the two checkpoints keeps only the newer one. Passed via
    # `params` so the '+00:00' offset is percent-encoded rather than arriving as
    # a space (a raw '+' in a query string decodes to ' ' and would 422).
    data = client.get(
        "/query/aggregate",
        params={"type": "file_diffs", "file": TRACKED, "since": second.created_at},
    ).json()
    assert [i["checkpoint_id"] for i in data["items"]] == ["cp-second"]

    # A bound after both drops everything.
    assert client.get(
        f"/query/aggregate?type=file_diffs&file={TRACKED}&since=2999-01-01T00:00:00Z"
    ).json()["count"] == 0

    # Omitted since means all time.
    assert client.get(f"/query/aggregate?type=file_diffs&file={TRACKED}").json()["count"] == 2
    assert first.created_at < second.created_at


# ---------------------------------------------------------------------------
# AC-1 / AC-3 / AC-6 — query_file_diffs called directly
# ---------------------------------------------------------------------------

def test_query_file_diffs_is_graph_free(two_checkpoints, monkeypatch):
    """AC-1: the read path must not construct an EventStore / GraphBackend.

    Detonating ``graph.store.get_store()`` proves the filesystem read path never
    reaches it (the invariant documented on ``list_checkpoints``).
    """
    import depthfusion.graph.store as graph_store

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("file-diff read path opened a graph backend")

    monkeypatch.setattr(graph_store, "get_store", _boom)

    from depthfusion.api.query import query_file_diffs

    out = query_file_diffs(TRACKED)
    assert out["count"] == 2
    assert [i["checkpoint_id"] for i in out["items"]] == ["cp-first", "cp-second"]


def test_query_file_diffs_bounds_decompressed_size(checkpoint_dir):
    """Review-gate regression guard: a gzip bomb must not be expanded in full.

    Checkpoint JSON is persistent on-disk data this read path does not control,
    so a corrupt or hand-crafted record could hold a payload that inflates to
    gigabytes — and one request decodes up to ``_DIFF_SCAN_CHECKPOINTS`` of them.
    The decode must stop at ``_MAX_DIFF_DECOMPRESSED_BYTES`` and yield a
    truncated string rather than exhausting memory or raising.

    The record is written directly (rather than through ``_publish``, which
    drives real git) because the whole point is a payload the write path would
    never produce.
    """
    import depthfusion.api.query as query_module
    from depthfusion.api.query import query_file_diffs
    from depthfusion.core.event_store import CheckpointRecord

    bomb_path = "src/bomb.py"
    # ~8 MiB of zeros gzips to a few KB but is 8x the 1 MiB read cap.
    payload = base64.b64encode(gzip.compress(b"\0" * (8 << 20))).decode("ascii")

    record = CheckpointRecord(
        checkpoint_id="cp-bomb",
        session_id="sess-bomb",
        project_slug=PROJECT,
        created_at="2026-08-05T12:00:00+00:00",
        plan_state="bomb",
        files_modified=[bomb_path],
        metadata={"diffs": {bomb_path: payload}},
    )
    slug_dir = checkpoint_dir / PROJECT
    slug_dir.mkdir(parents=True, exist_ok=True)
    (slug_dir / "cp-bomb.json").write_text(json.dumps(record.to_dict()), encoding="utf-8")

    out = query_file_diffs(bomb_path)

    assert out["count"] == 1
    decoded = out["items"][0]["diff"]
    assert len(decoded) <= query_module._MAX_DIFF_DECOMPRESSED_BYTES, (
        "decompression must be bounded by _MAX_DIFF_DECOMPRESSED_BYTES"
    )


def test_query_file_diffs_scan_breadth_is_decoupled_from_limit(two_checkpoints, monkeypatch):
    """AC-3: `limit` must not throttle the underlying checkpoint scan.

    Fails if the caller's limit is passed straight through to
    ``list_checkpoints`` — with limit=1 that would scan only the newest
    checkpoint and lose ``cp-first`` entirely.
    """
    import depthfusion.api.query as query_module
    import depthfusion.core.event_store as es

    seen: list[int] = []
    real = es.list_checkpoints

    def _spy(project_slug=None, limit=20, cursor=None):
        seen.append(limit)
        return real(project_slug, limit=limit, cursor=cursor)

    monkeypatch.setattr(es, "list_checkpoints", _spy)

    out = query_module.query_file_diffs(TRACKED, limit=1)
    assert seen and seen[0] >= query_module._DIFF_SCAN_CHECKPOINTS
    assert seen[0] > 1

    # limit applied to matched items after filtering + ascending sort.
    assert out["count"] == 1
    assert out["items"][0]["checkpoint_id"] == "cp-first"


def test_query_file_diffs_scans_every_project_slug_when_unscoped(store, tmp_repo, two_checkpoints):
    """AC-3: project_slug=None enumerates all per-slug directories."""
    from depthfusion.api.query import query_file_diffs

    _publish(
        store, tmp_repo, "value = 'other project'",
        session_id="sess-other", checkpoint_id="cp-other", project="other-project",
    )

    scoped = query_file_diffs(TRACKED, project_slug=PROJECT)
    assert {i["project_slug"] for i in scoped["items"]} == {PROJECT}

    unscoped = query_file_diffs(TRACKED)
    assert unscoped["count"] == 3
    assert {i["project_slug"] for i in unscoped["items"]} == {PROJECT, "other-project"}
    created = [i["created_at"] for i in unscoped["items"]]
    assert created == sorted(created)


def test_undecodable_diff_is_skipped_not_fatal(two_checkpoints, checkpoint_dir, caplog):
    """AC-6: a per-checkpoint decode failure logs and skips the record only."""
    import json
    import logging

    from depthfusion.api.query import query_file_diffs

    path = checkpoint_dir / PROJECT / "cp-second.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["metadata"]["diffs"][TRACKED] = "!!!not-base64-gzip!!!"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="depthfusion.api.query"):
        out = query_file_diffs(TRACKED)

    assert out["count"] == 1
    assert out["items"][0]["checkpoint_id"] == "cp-first"
    assert any("cp-second" in r.getMessage() for r in caplog.records)


def test_records_without_diffs_metadata_are_ignored(store, tmp_repo, checkpoint_dir):
    """A checkpoint with no `metadata["diffs"]` is simply not part of the history."""
    from depthfusion.api.query import query_file_diffs

    # project_path=None → no subprocess, no "diffs" key (pre-T-863 shape).
    asyncio.run(
        store.publish_checkpoint(
            session_id="sess-nodiff",
            project_slug=PROJECT,
            plan_state="no diffs",
            files_modified=[TRACKED],
            checkpoint_id="cp-nodiff",
        )
    )
    assert (checkpoint_dir / PROJECT / "cp-nodiff.json").exists()

    out = query_file_diffs(TRACKED)
    assert out["count"] == 0
    assert out["file"] == TRACKED


def test_route_still_registered_and_gated(client):
    """AC-5: /query/aggregate exists and keeps its require_principal gate."""
    import depthfusion.api.rest as rest_module

    paths = {r.path for r in rest_module.app.routes if hasattr(r, "path")}
    assert "/query/aggregate" in paths

    import inspect

    sig = inspect.signature(rest_module.get_aggregate)
    # Assert the WIRE name, not the Python identifier: the selector is declared
    # `type_` with `alias="type"` so the handler body does not shadow the `type`
    # builtin (matching the existing `from_`/alias="from" convention). Pinning
    # the identifier would forbid that alias idiom for no caller-visible reason.
    assert sig.parameters["type_"].default.alias == "type"
    assert sig.parameters["from_"].default.alias == "from"
    assert sig.parameters["principal"].default is not inspect.Parameter.empty


# ---------------------------------------------------------------------------
# Gate-4 security findings — S-254
# ---------------------------------------------------------------------------

SCOPED_PROJECT = "depthfusion"
OTHER_PROJECT = "other-project"


def _scoped_principal(slug: str):
    """A principal whose only project claim is *slug* (``project:<slug>`` group)."""
    from depthfusion.identity.models import Principal

    return Principal(
        principal_id=f"scoped-to-{slug}",
        upn=f"{slug}@test.local",
        groups=[f"project:{slug}"],
    )


def _install_principal(app, principal):
    """Override the principal dep with *principal*; return the prior overrides."""
    from depthfusion.api.auth import _require_principal_dep

    original = dict(app.dependency_overrides)
    app.dependency_overrides[_require_principal_dep] = lambda: principal
    return original


@pytest.fixture
def two_projects(store, tmp_repo, two_checkpoints):
    """The two ``depthfusion`` checkpoints plus one under ``other-project``."""
    other = _publish(
        store, tmp_repo, "value = 'cross-tenant secret change'",
        session_id="sess-other", checkpoint_id="cp-other", project=OTHER_PROJECT,
    )
    assert TRACKED in other.metadata["diffs"]
    return other


# Checklist 3 — authorization / tenant scoping
# ---------------------------------------------------------------------------

def test_scoped_principal_cannot_read_another_projects_diffs(two_projects):
    """A principal scoped to project A gets no rows for a project-B checkpoint.

    Fails if the project claim is ignored: unscoped, this same query returns all
    three checkpoints (asserted below as the control).
    """
    from depthfusion.api.query import query_file_diffs

    # Control — no claim means unrestricted, and project B really is present.
    unscoped = query_file_diffs(TRACKED)
    assert unscoped["count"] == 3
    assert OTHER_PROJECT in {i["project_slug"] for i in unscoped["items"]}

    scoped = query_file_diffs(TRACKED, principal=_scoped_principal(SCOPED_PROJECT))
    slugs = {i["project_slug"] for i in scoped["items"]}
    assert slugs == {SCOPED_PROJECT}, f"leaked projects: {slugs - {SCOPED_PROJECT}}"
    assert scoped["count"] == 2
    assert scoped["total"] == 2, "`total` must not leak the count of unreadable rows"


def test_scoped_principal_asking_for_a_foreign_project_gets_nothing(two_projects):
    """Naming project B explicitly must not bypass the claim, and must not 403.

    An empty 200 rather than a 403 so the response cannot be used to probe which
    project slugs exist.
    """
    from depthfusion.api.query import query_file_diffs

    out = query_file_diffs(
        TRACKED, project_slug=OTHER_PROJECT, principal=_scoped_principal(SCOPED_PROJECT)
    )
    assert out == {"items": [], "total": 0, "count": 0, "file": TRACKED}


def test_scoped_principal_isolation_holds_over_http(client, two_projects):
    """The same isolation via the real route, with the dep-injected principal."""
    import depthfusion.api.rest as rest_module

    original = _install_principal(rest_module.app, _scoped_principal(SCOPED_PROJECT))
    try:
        body = client.get(f"/query/aggregate?type=file_diffs&file={TRACKED}").json()
        assert {i["project_slug"] for i in body["items"]} == {SCOPED_PROJECT}
        assert body["count"] == 2

        foreign = client.get(
            f"/query/aggregate?type=file_diffs&file={TRACKED}&project={OTHER_PROJECT}"
        )
        assert foreign.status_code == 200
        assert foreign.json()["count"] == 0
    finally:
        rest_module.app.dependency_overrides.clear()
        rest_module.app.dependency_overrides.update(original)


def test_query_checkpoints_enforces_the_same_scope(two_projects):
    """The sibling reader over the same store must not be the weak link.

    ``query_file_diffs`` and ``query_checkpoints`` read the same checkpoint JSON;
    scoping one and not the other would leave the leak fully open through the
    other endpoint. Both go through ``_principal_project_scope``.
    """
    from depthfusion.api.query import query_checkpoints

    assert OTHER_PROJECT in {
        i["project_slug"] for i in query_checkpoints(limit=50)["items"]
    }

    scoped = query_checkpoints(limit=50, principal=_scoped_principal(SCOPED_PROJECT))
    assert {i["project_slug"] for i in scoped["items"]} == {SCOPED_PROJECT}
    assert query_checkpoints(
        project_slug=OTHER_PROJECT, limit=50, principal=_scoped_principal(SCOPED_PROJECT)
    ) == {"items": [], "total": 0, "count": 0}


def test_unclaimed_principal_stays_unrestricted(two_projects):
    """Fail-open on absence: today's principals carry no project claim.

    Treating "no claim" as "no access" would lock every existing caller out of
    its own data, so an empty scope must mean unrestricted — the same contract
    ``retrieval.acl_verifier.verify_acl`` already documents.
    """
    from depthfusion.api.query import query_checkpoints, query_file_diffs
    from depthfusion.identity.models import Principal

    plain = Principal(principal_id="greg", upn="greg@test.local")
    assert query_file_diffs(TRACKED, principal=plain)["count"] == 3
    assert len(query_checkpoints(limit=50, principal=plain)["items"]) == 3


def test_explicit_projects_attribute_is_honoured(two_projects):
    """A principal carrying a `projects` list is confined to it too."""
    from types import SimpleNamespace

    from depthfusion.api.query import _principal_project_scope, query_file_diffs

    svc = SimpleNamespace(principal_id="svc", groups=[], projects=[OTHER_PROJECT])
    assert _principal_project_scope(svc) == {OTHER_PROJECT}

    out = query_file_diffs(TRACKED, principal=svc)
    assert {i["project_slug"] for i in out["items"]} == {OTHER_PROJECT}


# Checklist 4 — `file` param path handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "/etc/shadow",
        "//etc/passwd",
        "../../../etc/passwd",
        "src/../../etc/passwd",
        "app/../../../../root/.ssh/id_rsa",
        "..",
        "../tracked.py",
        "C:\\Windows\\win.ini",
        "\\\\host\\share\\secret",
        "..\\..\\windows\\win.ini",
    ],
)
def test_absolute_and_traversal_file_params_are_rejected(bad):
    """Absolute paths and any `..` segment must be refused, not matched."""
    import pytest as _pytest

    from depthfusion.api.query import query_file_diffs

    with _pytest.raises(ValueError, match="invalid_file"):
        query_file_diffs(bad)


@pytest.mark.parametrize("bad", ["/etc/passwd", "../../etc/passwd", "..\\..\\win.ini"])
def test_bad_file_param_is_422_over_http(client, two_checkpoints, bad):
    """The route maps the rejection to a clean 422, never a 500."""
    resp = client.get("/query/aggregate", params={"type": "file_diffs", "file": bad})
    assert resp.status_code == 422, resp.text
    # And the rejection must not echo the caller's value back.
    assert bad not in resp.text


def test_relative_path_is_normalised_not_rejected(client, two_checkpoints):
    """Normalisation is separator/`.`-folding only, so `./x` still matches `x`."""
    from depthfusion.api.query import _normalise_diff_file_path, query_file_diffs

    assert _normalise_diff_file_path(f"./{TRACKED}") == TRACKED
    assert _normalise_diff_file_path(f".\\{TRACKED}") == TRACKED
    assert _normalise_diff_file_path("a//b/./c.py") == "a/b/c.py"

    out = query_file_diffs(f"./{TRACKED}")
    assert out["count"] == 2
    assert out["file"] == TRACKED

    resp = client.get("/query/aggregate", params={"type": "file_diffs", "file": f"./{TRACKED}"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_file_param_never_reaches_a_subprocess_or_open(two_checkpoints, monkeypatch):
    """The selector is a dict key only — never a path handed to open()/subprocess.

    Detonates ``subprocess.run`` in the event-store module and records every
    ``open()`` target, then asserts the caller-supplied value is nowhere among
    them. Fails the moment someone "helpfully" resolves the param against the
    filesystem.
    """
    import builtins

    import depthfusion.core.event_store as es

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("file-diff read path spawned a subprocess")

    monkeypatch.setattr(es.subprocess, "run", _boom)

    opened: list[str] = []
    real_open = builtins.open

    def _spy_open(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", _spy_open)

    from depthfusion.api.query import query_file_diffs

    sentinel = "src/depthfusion/api/query.py"
    out = query_file_diffs(sentinel)

    assert out["count"] == 0
    assert not any(sentinel in path for path in opened), opened


# Checklist 2 (read side) — secret-bearing selectors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "secret_path",
    [
        ".env",
        ".env.production",
        "certs/server.pem",
        "certs/server.key",
        "config/secrets.yaml",
        "id_rsa",
        "config/credentials.json",
        "certs/bundle.p12",
        "certs/app.keystore",
    ],
)
def test_secret_bearing_selector_returns_empty_even_for_pre_denylist_records(
    checkpoint_dir, secret_path
):
    """A checkpoint written BEFORE the write-side denylist must still not leak.

    The store is append-only, so records captured earlier may already hold a
    credential diff. The read path therefore applies the same predicate: the
    record is seeded directly (bypassing the collector, exactly as legacy data
    would have been) and must not come back.
    """
    from depthfusion.api.query import query_file_diffs
    from depthfusion.core.event_store import CheckpointRecord

    # Deliberately NOT shaped like a real credential. The code under test keys
    # off the PATH (is_secret_bearing_path), never the payload, so this body only
    # has to be a recognisable sentinel for the `leaked not in ...` assertion
    # below. A realistic high-entropy fake would trip the repo's gitleaks gate on
    # every future run while adding nothing to what this test proves.
    leaked = "SENTINEL-CREDENTIAL-BODY-THAT-MUST-NEVER-BE-RETURNED"
    payload = base64.b64encode(gzip.compress(leaked.encode())).decode("ascii")
    record = CheckpointRecord(
        checkpoint_id=f"cp-legacy-{abs(hash(secret_path))}",
        session_id="sess-legacy",
        project_slug=PROJECT,
        created_at="2026-08-01T12:00:00+00:00",
        plan_state="pre-denylist capture",
        files_modified=[secret_path],
        metadata={"diffs": {secret_path: payload}},
    )
    slug_dir = checkpoint_dir / PROJECT
    slug_dir.mkdir(parents=True, exist_ok=True)
    (slug_dir / f"{record.checkpoint_id}.json").write_text(
        json.dumps(record.to_dict()), encoding="utf-8"
    )

    # Premise guard: the record is on disk and would match on an exact key lookup.
    out = query_file_diffs(secret_path)
    assert out == {"items": [], "total": 0, "count": 0, "file": secret_path.replace("\\", "/")}
    assert leaked not in json.dumps(out)


def test_secret_selector_is_empty_over_http(client, checkpoint_dir):
    resp = client.get("/query/aggregate", params={"type": "file_diffs", "file": ".env"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


def test_no_diff_body_reaches_logs_on_the_read_path(two_checkpoints, checkpoint_dir, caplog):
    """A skipped record logs id + path + exception type — never payload bytes."""
    import logging

    from depthfusion.api.query import query_file_diffs

    path = checkpoint_dir / PROJECT / "cp-second.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    poisoned = base64.b64encode(b"not-a-gzip-stream-SENTINEL-BODY").decode("ascii")
    payload["metadata"]["diffs"][TRACKED] = poisoned
    path.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level(logging.DEBUG, logger="depthfusion.api.query"):
        out = query_file_diffs(TRACKED)

    assert out["count"] == 1
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "cp-second" in blob
    assert poisoned not in blob
    assert "SENTINEL-BODY" not in blob
    assert SECOND_LINE not in blob


# Checklist 5 — reflected input in the 422 detail
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert(1)</script>",
        "nonsense",
        "\"'><img src=x onerror=alert(1)>",
        "file_diffs\n\rInjected-Header: 1",
    ],
)
def test_unsupported_type_422_does_not_echo_caller_input(client, hostile):
    """The detail must be a fixed message listing allowed values.

    Fails if the handler goes back to interpolating `type` into the detail.
    """
    resp = client.get("/query/aggregate", params={"type": hostile})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert hostile not in detail
    assert hostile.strip() not in resp.text
    assert "file_diffs" in detail, "the fixed message should still list allowed values"


def test_legacy_no_type_aggregate_is_untouched_by_all_of_the_above(client, two_checkpoints):
    """Invariant guard: the pre-S-254 /query/aggregate path gains no requirement.

    No new required params and no new 422s on the legacy from/to path — the new
    validation lives strictly inside the ``type=file_diffs`` branch.
    """
    for qs in (
        "",
        "?from=2026-01-01",
        "?from=2026-01-01&to=2026-12-31",
        "?limit=9999",
        "?file=/etc/passwd",
        "?file=../../etc/passwd",
        "?since=not-a-date",
        "?project=no-such-project",
    ):
        resp = client.get(f"/query/aggregate{qs}")
        assert resp.status_code == 200, f"/query/aggregate{qs} → {resp.status_code}"
        assert set(resp.json().keys()) == {
            "total_events",
            "total_latency_ms",
            "avg_latency_ms",
            "p95_latency_ms",
            "avg_result_count",
            "modes",
            "config_versions",
        }


def test_oversized_encoded_diff_is_skipped_not_decoded(checkpoint_dir, caplog):
    """Review-gate guard: cap the base64 payload BEFORE b64decode allocates it.

    ``_MAX_DIFF_DECOMPRESSED_BYTES`` bounds gzip *output*, but ``b64decode``
    materialises its whole input first, so a hand-crafted record could allocate
    far more than the gzip bound ever sees. The record must be skipped like any
    other unusable one — not decoded, and not allowed to fail the whole query.
    """
    from depthfusion.api import query as query_module

    slug_dir = checkpoint_dir / PROJECT
    slug_dir.mkdir(parents=True, exist_ok=True)

    oversized = "A" * (query_module._MAX_DIFF_ENCODED_BYTES + 1)
    (slug_dir / "cp-oversized.json").write_text(
        json.dumps(
            {
                "checkpoint_id": "cp-oversized",
                "session_id": "sess-oversized",
                "project_slug": PROJECT,
                "created_at": "2026-07-02T10:00:00+00:00",
                "plan_state": "oversized",
                "files_modified": [TRACKED],
                "metadata": {"diffs": {TRACKED: oversized}},
            }
        ),
        encoding="utf-8",
    )

    # b64decode must never be reached for this record.
    calls: list[int] = []
    real_b64decode = query_module.base64.b64decode

    def _spy(payload, *a, **kw):
        calls.append(len(payload))
        return real_b64decode(payload, *a, **kw)

    query_module.base64.b64decode = _spy
    try:
        with caplog.at_level("WARNING"):
            out = query_module.query_file_diffs(TRACKED)
    finally:
        query_module.base64.b64decode = real_b64decode

    assert all(n <= query_module._MAX_DIFF_ENCODED_BYTES for n in calls), (
        "an over-cap payload reached b64decode"
    )
    assert "cp-oversized" not in [i["checkpoint_id"] for i in out["items"]]
    # The query still succeeds — one bad record is skipped, not fatal.
    assert isinstance(out["items"], list)
    assert any("cp-oversized" in r.getMessage() for r in caplog.records)


def test_pre_change_checkpoint_json_without_metadata_diffs_round_trips(checkpoint_dir):
    """Backward compatibility: absent `metadata.diffs` reads as empty, never raises."""
    from depthfusion.api.query import query_checkpoints, query_file_diffs
    from depthfusion.core.event_store import CheckpointRecord, list_checkpoints

    slug_dir = checkpoint_dir / PROJECT
    slug_dir.mkdir(parents=True, exist_ok=True)

    # (a) no `metadata` key at all — the literal pre-S-254 on-disk shape.
    (slug_dir / "cp-legacy-nometa.json").write_text(
        json.dumps(
            {
                "checkpoint_id": "cp-legacy-nometa",
                "session_id": "sess-legacy",
                "project_slug": PROJECT,
                "created_at": "2026-07-01T10:00:00+00:00",
                "plan_state": "legacy",
                "files_modified": [TRACKED],
                "git_stash_ref": None,
                "context_pct_at_checkpoint": None,
            }
        ),
        encoding="utf-8",
    )
    # (b) `metadata` present but with no "diffs" key.
    (slug_dir / "cp-legacy-nodiffs.json").write_text(
        json.dumps(
            {
                "checkpoint_id": "cp-legacy-nodiffs",
                "session_id": "sess-legacy",
                "project_slug": PROJECT,
                "created_at": "2026-07-02T10:00:00+00:00",
                "plan_state": "legacy",
                "files_modified": [TRACKED],
                "metadata": {"unrelated": True},
            }
        ),
        encoding="utf-8",
    )

    records = list_checkpoints(PROJECT, limit=50)
    ids = {r.checkpoint_id for r in records}
    assert {"cp-legacy-nometa", "cp-legacy-nodiffs"} <= ids, "legacy records were skipped"
    for rec in records:
        assert rec.metadata == {} or isinstance(rec.metadata, dict)
        assert CheckpointRecord.from_dict(rec.to_dict()).metadata == rec.metadata

    assert query_file_diffs(TRACKED)["count"] == 0
    assert len(query_checkpoints(project_slug=PROJECT, limit=50)["items"]) == 2


# ---------------------------------------------------------------------------
# S-254 review gate — write/read diff-key agreement
# ---------------------------------------------------------------------------
# The regression these pin: `files_modified` is populated in
# mcp/session_activity.py from raw tool-call arguments, which in practice are
# ABSOLUTE paths. The writer stored them verbatim as `metadata["diffs"]` keys
# while the reader rejected every absolute selector, so a real checkpoint stored
# keys the reader could never address — 422 for the dashboard's file pills, and
# an empty history forever. One shared canonicaliser (normalise_diff_path) now
# keys both sides.

def test_absolute_files_modified_is_stored_as_a_repo_relative_key(store, tmp_repo):
    """AC: an absolute `files_modified` entry becomes a repo-relative diff key."""
    (tmp_repo / TRACKED).write_text("value = 'absolute path capture'\n", encoding="utf-8")
    record = asyncio.run(
        store.publish_checkpoint(
            session_id="sess-abs",
            project_slug=PROJECT,
            plan_state="absolute path",
            # Exactly what session_activity records from a tool call.
            files_modified=[str(tmp_repo / TRACKED)],
            checkpoint_id="cp-abs",
            project_path=str(tmp_repo),
        )
    )
    diffs = record.metadata["diffs"]
    assert TRACKED in diffs, diffs
    assert str(tmp_repo / TRACKED) not in diffs, "absolute key must not be stored"
    assert "absolute path capture" in _decode(diffs[TRACKED])


def test_absolute_selector_under_the_project_root_resolves_to_the_stored_key(
    store, tmp_repo, monkeypatch
):
    """The reader accepts the same absolute path the tracker recorded."""
    from depthfusion.api.query import query_file_diffs

    (tmp_repo / TRACKED).write_text("value = 'round trip'\n", encoding="utf-8")
    asyncio.run(
        store.publish_checkpoint(
            session_id="sess-rt",
            project_slug=PROJECT,
            plan_state="round trip",
            files_modified=[str(tmp_repo / TRACKED)],
            checkpoint_id="cp-rt",
            project_path=str(tmp_repo),
        )
    )
    monkeypatch.setenv("DEPTHFUSION_PROJECT_PATH", str(tmp_repo))

    out = query_file_diffs(str(tmp_repo / TRACKED))
    assert out["file"] == TRACKED
    assert out["count"] == 1, out
    assert "round trip" in out["items"][0]["diff"]


def test_absolute_selector_outside_the_project_root_is_still_rejected(monkeypatch, tmp_path):
    """Relativisation must not become a way to reach arbitrary absolute paths."""
    import pytest as _pytest

    from depthfusion.api.query import _normalise_diff_file_path

    monkeypatch.setenv("DEPTHFUSION_PROJECT_PATH", str(tmp_path / "repo"))
    for bad in ("/etc/passwd", "//etc/passwd", "/", "/tmp/../etc/passwd"):
        with _pytest.raises(ValueError, match="invalid_file"):
            _normalise_diff_file_path(bad)


def test_normalise_diff_path_is_the_single_shared_canonicaliser():
    """Both sides must go through one function, and it must never raise."""
    from depthfusion.core.event_store import normalise_diff_path

    assert normalise_diff_path("./a//b/./c.py") == "a/b/c.py"
    assert normalise_diff_path(".\\a\\b.py") == "a/b.py"
    assert normalise_diff_path("--upload-pack=evil") == "--upload-pack=evil"
    # Rejections, all returning None rather than raising.
    for bad in ("", "   ", "..", "a/../../b", "/abs/no/root", "C:\\Windows\\win.ini", "/"):
        assert normalise_diff_path(bad) is None, bad
    assert normalise_diff_path("/root/pkg/mod.py", "/root") == "pkg/mod.py"
    assert normalise_diff_path("/root", "/root") is None
    assert normalise_diff_path("/other/pkg/mod.py", "/root") is None


def test_unaddressable_write_paths_never_spawn_a_subprocess(monkeypatch, tmp_repo):
    """A path that can't become a key is dropped before `git diff` is spawned."""
    import depthfusion.core.event_store as es

    calls: list = []
    real_run = es.subprocess.run

    def _spy(*a, **k):
        calls.append(a[0])
        return real_run(*a, **k)

    monkeypatch.setattr(es.subprocess, "run", _spy)

    diffs = es._collect_git_diffs(str(tmp_repo), ["../escape.py", "/elsewhere/x.py", TRACKED])
    assert all("../escape.py" not in argv for argv in calls), calls
    assert all("/elsewhere/x.py" not in argv for argv in calls), calls
    assert "../escape.py" not in diffs and "/elsewhere/x.py" not in diffs


# ---------------------------------------------------------------------------
# S-254 review gate — /query/checkpoints must not carry raw diff bodies
# ---------------------------------------------------------------------------

def test_query_checkpoints_does_not_return_raw_diff_payloads(client, two_checkpoints):
    """The read-side secret denylist is worthless if the sibling route leaks diffs.

    `/query/checkpoints` used to return `to_dict()` verbatim, including
    `metadata["diffs"]` — the full base64(gzip(diff)) body for every captured
    file. A caller could therefore mine a pre-denylist credential diff from this
    route while `type=file_diffs` politely refused it, and every response carried
    megabytes the timeline tile never renders.
    """
    from depthfusion.api.query import query_checkpoints

    resp = client.get("/query/checkpoints", params={"project": PROJECT, "limit": 50})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"], "fixture published no checkpoints"
    for item in body["items"]:
        # The metadata KEY stays — its presence is part of the wire contract.
        assert "metadata" in item
        assert "diffs" not in item["metadata"], item["metadata"]
    # No encoded diff body anywhere in the serialised response.
    encoded = two_checkpoints[0].metadata["diffs"][TRACKED]
    assert encoded not in resp.text

    for item in query_checkpoints(project_slug=PROJECT, limit=50)["items"]:
        assert "diffs" not in item["metadata"]


def test_query_checkpoints_preserves_other_metadata_keys(checkpoint_dir):
    """Only `diffs` is stripped — unrelated metadata passes through untouched."""
    from depthfusion.api.query import query_checkpoints

    slug_dir = checkpoint_dir / PROJECT
    slug_dir.mkdir(parents=True, exist_ok=True)
    (slug_dir / "cp-meta.json").write_text(
        json.dumps(
            {
                "checkpoint_id": "cp-meta",
                "session_id": "sess-meta",
                "project_slug": PROJECT,
                "created_at": "2026-07-03T10:00:00+00:00",
                "plan_state": "meta",
                "files_modified": [TRACKED],
                "metadata": {"diffs": {TRACKED: "ignored"}, "keep_me": {"nested": 1}},
            }
        ),
        encoding="utf-8",
    )
    (item,) = query_checkpoints(project_slug=PROJECT, limit=50)["items"]
    assert item["metadata"] == {"keep_me": {"nested": 1}}


# ---------------------------------------------------------------------------
# S-254 review gate — denylist and git-env hardening
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "secret_path",
    [
        ".git-credentials",
        ".dockercfg",
        ".docker/config.json",
        "keys/app.p8",
        "keys/server.ppk",
        "vault/pw.kdbx",
        "keys/key.asc",
        "keys/key.gpg",
        "infra/prod.tfvars",
        "infra/prod.tfvars.json",
    ],
)
def test_denylist_covers_the_credential_formats_the_first_pass_missed(secret_path):
    from depthfusion.core.event_store import is_secret_bearing_path

    assert is_secret_bearing_path(secret_path) is True, secret_path


@pytest.mark.parametrize(
    "benign", ["src/app.py", "docker/Dockerfile", "config.json", "docs/keys.md", "main.go"]
)
def test_denylist_does_not_overmatch_benign_paths(benign):
    from depthfusion.core.event_store import is_secret_bearing_path

    assert is_secret_bearing_path(benign) is False, benign


@pytest.mark.parametrize(
    "var",
    [
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_ATTR_SOURCE",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ],
)
def test_git_env_also_strips_the_config_and_redirect_routes(var, monkeypatch):
    """GIT_CONFIG_GLOBAL et al reach the same diff.external exec primitive.

    GIT_DIR/GIT_WORK_TREE additionally retarget the diff at another repository,
    which would silently make `cwd=project_path` a lie.
    """
    import depthfusion.core.event_store as es

    monkeypatch.setenv(var, "/tmp/attacker-controlled")
    monkeypatch.setenv("DEPTHFUSION_KEEP_ME", "1")
    env = es._git_subprocess_env()
    assert var not in env
    assert env["DEPTHFUSION_KEEP_ME"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
