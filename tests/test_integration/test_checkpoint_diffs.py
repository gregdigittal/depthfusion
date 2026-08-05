"""Integration tests for checkpoint git-diff capture — E-73 S-254 / T-863.

Real-collaborator style throughout: a real ``git init`` working tree under
``tmp_path``, a real ``JSONGraphStore``, a real ``EventStore``, a real
filesystem checkpoint store. Nothing is mocked — in particular *not*
``subprocess`` and *not* the filesystem, because the whole point of this suite
is that the diff really is produced by the ``git`` binary and really does land
inside the persisted ``CheckpointRecord``.

Why that matters (AC-6): a test that patched ``subprocess.run`` would keep
passing if the diff-collection branch were deleted, if the truncation happened
after encoding instead of before, if the executor hop swallowed the result, or
if ``metadata`` were dropped from ``to_dict()``. The assertions here fail on
each of those.
"""
from __future__ import annotations

import base64
import gzip
import subprocess
from pathlib import Path

import pytest

from depthfusion.core.event_store import (
    CheckpointRecord,
    EventStore,
    InMemoryStreamBackend,
    project_root_path,
)
from depthfusion.graph.store import JSONGraphStore

PROJECT = "depthfusion"
MODIFIED_LINE = "value = 'T-863 modified line'"

# Every test in this module drives the real git binary, some of it from inside a
# thread executor where pytest-timeout's signal method cannot interrupt it. An
# explicit per-test bound strictly BELOW the suite-wide 30s (pyproject addopts
# --timeout=30) plus the fixture/collector bounds below means no test here can
# outlive the timeout that is meant to stop it, on any host.
pytestmark = pytest.mark.timeout(20)

# Fixture git calls are setup, not the thing under test: 5s each is generous for
# `git init`/`config`/`add`/`commit` in an empty tmp tree, and 5 calls × 5s stays
# well inside the 20s per-test bound above even in the pathological case.
_FIXTURE_GIT_TIMEOUT_SECONDS = 5


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in *repo*, failing loudly — fixture setup must not degrade."""
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=_FIXTURE_GIT_TIMEOUT_SECONDS,
        check=True,
    )


@pytest.fixture
def checkpoint_dir(tmp_path, monkeypatch) -> Path:
    """Point DEPTHFUSION_CHECKPOINT_DIR at a temp root and return it.

    Same idiom as tests/test_integration/test_rest_query_checkpoints.py — the
    checkpoint store is a plain JSON directory, deliberately NOT the git tree
    below, which is exactly why DEPTHFUSION_PROJECT_PATH has to exist as a
    separate source.
    """
    root = tmp_path / "checkpoints"
    root.mkdir()
    monkeypatch.setenv("DEPTHFUSION_CHECKPOINT_DIR", str(root))
    return root


@pytest.fixture
def tmp_repo(tmp_path) -> Path:
    """A real git working tree with one committed file that is then modified.

    ``git diff HEAD -- tracked.py`` in this tree is guaranteed non-empty and
    guaranteed to contain MODIFIED_LINE.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@depthfusion.local")
    _git(repo, "config", "user.name", "DepthFusion Test")
    _git(repo, "config", "commit.gpgsign", "false")

    tracked = repo / "tracked.py"
    tracked.write_text("value = 'original line'\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-q", "-m", "initial commit")

    # The working-tree change the checkpoint must capture.
    tracked.write_text(f"{MODIFIED_LINE}\n", encoding="utf-8")
    return repo


@pytest.fixture
def store(tmp_path, monkeypatch, checkpoint_dir):
    """A real EventStore over a real JSONGraphStore (no MagicMock)."""
    monkeypatch.setenv("DEPTHFUSION_LOCK_DIR", str(tmp_path / "locks"))
    graph = JSONGraphStore(path=tmp_path / "graph.json")
    return EventStore(graph=graph, stream=InMemoryStreamBackend())


def _decode(encoded: str) -> str:
    """base64 → gunzip → text, mirroring the write path exactly."""
    return gzip.decompress(base64.b64decode(encoded)).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# AC-2 / AC-6 — diffs are really collected, encoded, and persisted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_checkpoint_captures_real_git_diff(store, tmp_repo):
    """AC-2/AC-6: metadata["diffs"] has >=1 entry decoding to the modified line.

    Fails if the diff-collection branch is removed (no "diffs" key), if the
    encoding order is wrong (b64/gzip decode raises), or if metadata is not
    carried into to_dict().
    """
    record = await store.publish_checkpoint(
        session_id="sess-t863",
        project_slug=PROJECT,
        plan_state="implementing T-863 diff capture",
        files_modified=["tracked.py"],
        project_path=str(tmp_repo),
    )

    diffs = record.to_dict()["metadata"]["diffs"]
    assert len(diffs) >= 1
    assert "tracked.py" in diffs

    decoded = _decode(diffs["tracked.py"])
    assert MODIFIED_LINE in decoded
    assert decoded.startswith("diff --git")


@pytest.mark.asyncio
async def test_captured_diff_survives_the_file_round_trip(store, tmp_repo, checkpoint_dir):
    """AC-1/AC-2: the diff is on disk and reloads through from_dict()."""
    record = await store.publish_checkpoint(
        session_id="sess-t863-roundtrip",
        project_slug=PROJECT,
        plan_state="round trip",
        files_modified=["tracked.py"],
        project_path=str(tmp_repo),
    )

    path = checkpoint_dir / PROJECT / f"{record.checkpoint_id}.json"
    assert path.exists(), "authoritative checkpoint file must still be written"

    reloaded = store.get_checkpoint(record.checkpoint_id, PROJECT)
    assert reloaded is not None
    assert MODIFIED_LINE in _decode(reloaded.metadata["diffs"]["tracked.py"])


@pytest.mark.asyncio
async def test_raw_diff_is_truncated_to_4096_bytes_before_encoding(store, tmp_repo):
    """AC-2: truncation is applied to the RAW diff, not to its encoding.

    A ~200KB single-line change produces a diff far larger than the cap. The
    decoded payload must therefore be exactly 4096 bytes — if truncation were
    applied after gzip+base64, the decoded size would be much larger.
    """
    big = tmp_repo / "tracked.py"
    big.write_text("x = '" + ("A" * 200_000) + "'\n", encoding="utf-8")

    record = await store.publish_checkpoint(
        session_id="sess-t863-trunc",
        project_slug=PROJECT,
        plan_state="truncation",
        files_modified=["tracked.py"],
        project_path=str(tmp_repo),
    )

    raw = gzip.decompress(base64.b64decode(record.metadata["diffs"]["tracked.py"]))
    assert len(raw) == 4096


# ---------------------------------------------------------------------------
# AC-2 / AC-3 — opt-in, and best-effort in every failure mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_project_path_means_no_diffs_key(store, tmp_repo):
    """AC-2: project_path=None → no subprocess, no "diffs" key."""
    record = await store.publish_checkpoint(
        session_id="sess-t863-optout",
        project_slug=PROJECT,
        plan_state="opt out",
        files_modified=["tracked.py"],
    )

    assert record.metadata == {}
    assert "diffs" not in record.to_dict()["metadata"]


@pytest.mark.asyncio
async def test_empty_files_modified_collects_nothing(store, tmp_repo):
    """AC-3: empty files_modified is skipped; publish still succeeds."""
    record = await store.publish_checkpoint(
        session_id="sess-t863-nofiles",
        project_slug=PROJECT,
        plan_state="no files",
        files_modified=[],
        project_path=str(tmp_repo),
    )

    assert "diffs" not in record.metadata
    assert store.get_checkpoint(record.checkpoint_id, PROJECT) is not None


@pytest.mark.asyncio
async def test_non_git_project_path_degrades_without_raising(store, tmp_path, checkpoint_dir):
    """AC-3: a real directory that is not a git tree → no diffs, no exception.

    `git diff HEAD` exits non-zero here ("not a git repository"), which is the
    non-zero-exit branch of the collector.
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    (plain / "tracked.py").write_text("x = 1\n", encoding="utf-8")

    record = await store.publish_checkpoint(
        session_id="sess-t863-nongit",
        project_slug=PROJECT,
        plan_state="not a repo",
        files_modified=["tracked.py"],
        project_path=str(plain),
    )

    assert "diffs" not in record.metadata
    assert (checkpoint_dir / PROJECT / f"{record.checkpoint_id}.json").exists()


@pytest.mark.asyncio
async def test_missing_project_path_degrades_without_raising(store, tmp_path, checkpoint_dir):
    """AC-3: a nonexistent cwd makes subprocess raise; publish still returns."""
    record = await store.publish_checkpoint(
        session_id="sess-t863-missing",
        project_slug=PROJECT,
        plan_state="missing dir",
        files_modified=["tracked.py"],
        project_path=str(tmp_path / "does-not-exist"),
    )

    assert "diffs" not in record.metadata
    assert (checkpoint_dir / PROJECT / f"{record.checkpoint_id}.json").exists()


@pytest.mark.asyncio
async def test_unchanged_and_untracked_paths_are_omitted(store, tmp_repo):
    """AC-2/AC-3: only paths with a real diff are collected.

    A second committed-and-unchanged file and an untracked file both produce
    empty `git diff HEAD` output and must not appear as empty payloads.
    """
    unchanged = tmp_repo / "unchanged.py"
    unchanged.write_text("y = 2\n", encoding="utf-8")
    _git(tmp_repo, "add", "unchanged.py")
    _git(tmp_repo, "commit", "-q", "-m", "add unchanged")
    (tmp_repo / "untracked.py").write_text("z = 3\n", encoding="utf-8")

    record = await store.publish_checkpoint(
        session_id="sess-t863-mixed",
        project_slug=PROJECT,
        plan_state="mixed paths",
        files_modified=["tracked.py", "unchanged.py", "untracked.py"],
        project_path=str(tmp_repo),
    )

    assert set(record.metadata["diffs"]) == {"tracked.py"}


# ---------------------------------------------------------------------------
# AC-1 — backward compatibility of the on-disk format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pre_t863_checkpoint_json_still_deserialises(store, checkpoint_dir):
    """AC-1: a record written before `metadata` existed must still load.

    Written straight to disk in the pre-T-863 shape (no "metadata" key) and
    read back through the real module-level/EventStore read paths.
    """
    from depthfusion.core.event_store import list_checkpoints

    legacy = {
        "checkpoint_id": "cp-legacy",
        "session_id": "sess-legacy",
        "project_slug": PROJECT,
        "created_at": "2026-08-01T00:00:00+00:00",
        "plan_state": "written before T-863",
        "files_modified": ["src/depthfusion/core/event_store.py"],
        "git_stash_ref": None,
        "context_pct_at_checkpoint": 11.0,
    }
    d = checkpoint_dir / PROJECT
    d.mkdir(parents=True, exist_ok=True)
    import json as _json
    (d / "cp-legacy.json").write_text(_json.dumps(legacy), encoding="utf-8")

    loaded = store.get_checkpoint("cp-legacy", PROJECT)
    assert loaded is not None
    assert loaded.metadata == {}
    assert loaded.plan_state == "written before T-863"

    listed = [r.checkpoint_id for r in list_checkpoints(PROJECT, limit=20)]
    assert "cp-legacy" in listed, "legacy record must not be skipped as malformed"

    # ...and a freshly built record still round-trips by value.
    rec = CheckpointRecord.from_dict(legacy)
    assert CheckpointRecord.from_dict(rec.to_dict()) == rec


# ---------------------------------------------------------------------------
# AC-4 — DEPTHFUSION_PROJECT_PATH is the project-root source
# ---------------------------------------------------------------------------


def test_project_root_path_prefers_env_then_cwd(monkeypatch, tmp_repo):
    monkeypatch.setenv("DEPTHFUSION_PROJECT_PATH", str(tmp_repo))
    assert project_root_path() == str(tmp_repo)

    monkeypatch.setenv("DEPTHFUSION_PROJECT_PATH", "   ")
    import os
    assert project_root_path() == os.getcwd()

    monkeypatch.delenv("DEPTHFUSION_PROJECT_PATH", raising=False)
    assert project_root_path() == os.getcwd()


@pytest.mark.asyncio
async def test_env_sourced_project_root_feeds_collection(store, tmp_repo, monkeypatch):
    """AC-4: the env source resolves to a tree whose diffs really get captured."""
    monkeypatch.setenv("DEPTHFUSION_PROJECT_PATH", str(tmp_repo))

    record = await store.publish_checkpoint(
        session_id="sess-t863-env",
        project_slug=PROJECT,
        plan_state="env sourced",
        files_modified=["tracked.py"],
        project_path=project_root_path(),
    )

    assert MODIFIED_LINE in _decode(record.metadata["diffs"]["tracked.py"])


# ---------------------------------------------------------------------------
# AC-5 — read-path invariant guard
# ---------------------------------------------------------------------------


def test_checkpoint_read_functions_remain_module_level():
    """AC-5: the graph-free read path must not migrate onto EventStore."""
    import depthfusion.core.event_store as es

    for name in ("list_checkpoints", "prune_expired_checkpoints", "checkpoint_store_dir"):
        assert callable(getattr(es, name)), f"{name} must stay module-level"
        assert not hasattr(EventStore, name), f"{name} must NOT become an EventStore method"


# ---------------------------------------------------------------------------
# Gate-4 security findings — S-254
#
# Each test below fails if its corresponding fix is reverted. They are grouped
# by the checklist item they pin.
# ---------------------------------------------------------------------------

# Checklist 1 — subprocess boundedness / argument injection
# ---------------------------------------------------------------------------

DASH_LEADING_PATH = "--upload-pack=evil"


@pytest.mark.asyncio
async def test_dash_leading_path_is_passed_positionally_after_double_dash(
    store, tmp_repo, monkeypatch
):
    """A path beginning with `-` must be a pathspec, never a git option.

    Pins the exact argv shape rather than only the outcome, because the outcome
    alone is ambiguous: `git diff HEAD --upload-pack=evil` also produces no
    diff, so an "empty diffs" assertion would pass on the vulnerable code.

    Fails if the literal ``--`` separator is dropped, if the path is moved ahead
    of it, if ``shell=True`` is introduced, or if the per-file ``timeout`` is
    removed or raised above ``_DIFF_TIMEOUT_SECONDS``.
    """
    import depthfusion.core.event_store as es

    calls: list[tuple[tuple, dict]] = []
    real_run = subprocess.run

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(es.subprocess, "run", _spy)

    await store.publish_checkpoint(
        session_id="sess-dash",
        project_slug=PROJECT,
        plan_state="dash-leading path",
        files_modified=[DASH_LEADING_PATH, "tracked.py"],
        project_path=str(tmp_repo),
    )

    assert calls, "no git subprocess was spawned"
    dash_calls = [c for c in calls if DASH_LEADING_PATH in c[0][0]]
    assert dash_calls, f"{DASH_LEADING_PATH} was never passed to git"

    for args, kwargs in dash_calls:
        argv = args[0]
        # 1. argv LIST, not a string, and no shell.
        assert isinstance(argv, list), "argv must be a list, never a shell string"
        assert kwargs.get("shell") is not True, "shell=True must never be used"
        # 2. The path is positional, AFTER the literal `--`.
        assert argv[:6] == [
            "git", "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--",
        ], argv
        assert argv.index(DASH_LEADING_PATH) > argv.index("--")
        assert argv[-1] == DASH_LEADING_PATH
        # 3. Bounded: a real per-call timeout, never above the 5s cap.
        assert "timeout" in kwargs, "per-file timeout must be set"
        assert 0 < kwargs["timeout"] <= es._DIFF_TIMEOUT_SECONDS


def test_no_shell_true_anywhere_in_the_diff_collector():
    """Belt-and-braces AST assertion: no call in the module passes shell=True.

    Parsed rather than grepped so a comment that merely mentions the flag does
    not trip it, and so ``shell = True`` spelled with spaces cannot slip past.
    """
    import ast
    import inspect

    import depthfusion.core.event_store as es

    tree = ast.parse(inspect.getsource(es))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "shell":
                assert not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is True
                ), f"shell=True at line {node.lineno}"


def test_diff_timeout_cap_is_at_most_five_seconds():
    import depthfusion.core.event_store as es

    assert es._DIFF_TIMEOUT_SECONDS <= 5


def test_collection_budget_cannot_outlive_the_per_test_timeout():
    """The aggregate budget must stay strictly under the suite's 30s timeout.

    Collection runs in a thread executor; pytest-timeout's signal method fires in
    the main thread only, so a worker blocked for the full budget is NOT
    interruptible by the per-test timeout. If the budget were raised to (or past)
    30s, a slow host could produce a run that outlives its own timeout and reads
    as a wedged suite. Fails if anyone raises the budget back up.
    """
    import depthfusion.core.event_store as es

    assert es._MAX_DIFF_TOTAL_SECONDS <= 10
    assert es._DIFF_TIMEOUT_SECONDS <= es._MAX_DIFF_TOTAL_SECONDS
    # The non-interruptible window (executor-side collection) must fit inside the
    # module's own 20s bound with margin. Fixture git runs in the MAIN thread, so
    # SIGALRM does reach it and it needs no such headroom.
    assert es._MAX_DIFF_TOTAL_SECONDS * 2 <= 20
    assert _FIXTURE_GIT_TIMEOUT_SECONDS <= es._DIFF_TIMEOUT_SECONDS * 2


@pytest.mark.asyncio
async def test_real_git_treats_a_dash_leading_tracked_file_as_a_pathspec(
    store, tmp_path, monkeypatch
):
    """End-to-end proof with the real git binary, no spy.

    A file literally named ``--upload-pack=evil`` is committed and modified. If
    the collector dropped ``--`` git would parse the name as an option and emit
    nothing (or fail); because the separator is present the file's diff really
    is captured.
    """
    repo = tmp_path / "dashrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@depthfusion.local")
    _git(repo, "config", "user.name", "DepthFusion Test")
    _git(repo, "config", "commit.gpgsign", "false")

    target = repo / DASH_LEADING_PATH
    target.write_text("original = 1\n", encoding="utf-8")
    _git(repo, "add", "--", DASH_LEADING_PATH)
    _git(repo, "commit", "-q", "-m", "add dash-leading file")
    target.write_text("sentinel = 'dash path really diffed'\n", encoding="utf-8")

    record = await store.publish_checkpoint(
        session_id="sess-dash-real",
        project_slug=PROJECT,
        plan_state="dash-leading path, real git",
        files_modified=[DASH_LEADING_PATH],
        project_path=str(repo),
    )

    assert DASH_LEADING_PATH in record.metadata["diffs"]
    assert "dash path really diffed" in _decode(record.metadata["diffs"][DASH_LEADING_PATH])


def test_git_subprocess_env_strips_code_execution_hooks(monkeypatch):
    """GIT_EXTERNAL_DIFF & co. must not reach the child process.

    ``GIT_EXTERNAL_DIFF`` makes git execute an arbitrary program per diffed
    file, which would turn this collector into an execution primitive driven by
    ambient environment state.
    """
    import depthfusion.core.event_store as es

    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "/bin/sh -c 'touch /tmp/pwned'")
    monkeypatch.setenv("GIT_PAGER", "evil")
    monkeypatch.setenv("GIT_SSH_COMMAND", "evil")
    monkeypatch.setenv("DEPTHFUSION_KEEP_ME", "1")

    env = es._git_subprocess_env()

    assert "GIT_EXTERNAL_DIFF" not in env
    assert "GIT_PAGER" not in env
    assert "GIT_SSH_COMMAND" not in env
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    # Non-git inheritance is preserved — this is a scalpel, not a blank env.
    assert env["DEPTHFUSION_KEEP_ME"] == "1"


@pytest.mark.asyncio
async def test_collector_passes_the_hardened_env_to_git(store, tmp_repo, monkeypatch):
    """The hardened env is actually wired into the subprocess call, not just defined."""
    import depthfusion.core.event_store as es

    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "/bin/false")
    seen: list[dict] = []
    real_run = subprocess.run

    def _spy(*args, **kwargs):
        seen.append(kwargs)
        return real_run(*args, **kwargs)

    monkeypatch.setattr(es.subprocess, "run", _spy)

    await store.publish_checkpoint(
        session_id="sess-env",
        project_slug=PROJECT,
        plan_state="env hardening",
        files_modified=["tracked.py"],
        project_path=str(tmp_repo),
    )

    assert seen, "no git subprocess was spawned"
    for kwargs in seen:
        assert kwargs.get("env") is not None, "git must run with an explicit env"
        assert "GIT_EXTERNAL_DIFF" not in kwargs["env"]


# Checklist 2 — secret leakage
# ---------------------------------------------------------------------------

# Deliberately NOT shaped like a real credential (no AKIA-style prefix, no
# high-entropy blob). The collector decides purely on the PATH, so this value
# only needs to be a unique sentinel that the assertions can search for. Using a
# realistic fake would depend on gitleaks' "EXAMPLE" allowlist to keep the repo's
# secret-scanning gate green — a dependency that breaks the moment that allowlist
# changes, for zero test value.
SECRET_VALUE = "SENTINEL-CREDENTIAL-VALUE-DO-NOT-CAPTURE"

SECRET_PATHS = (
    ".env",
    ".env.production",
    "certs/server.pem",
    "certs/server.key",
    "config/secrets.yaml",
    "id_rsa",
    "config/credentials.json",
    "certs/bundle.p12",
    "certs/app.keystore",
    ".ssh/config",
    "secrets/db-password.txt",
)


@pytest.fixture
def secret_repo(tmp_path) -> Path:
    """A git tree where every SECRET_PATH is tracked and modified to hold a secret."""
    repo = tmp_path / "secretrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@depthfusion.local")
    _git(repo, "config", "user.name", "DepthFusion Test")
    _git(repo, "config", "commit.gpgsign", "false")

    for rel in (*SECRET_PATHS, "tracked.py"):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder\n", encoding="utf-8")
        _git(repo, "add", "-f", "--", rel)
    _git(repo, "commit", "-q", "-m", "initial commit")

    for rel in SECRET_PATHS:
        (repo / rel).write_text(f"token = '{SECRET_VALUE}'\n", encoding="utf-8")
    (repo / "tracked.py").write_text(f"{MODIFIED_LINE}\n", encoding="utf-8")
    return repo


@pytest.mark.asyncio
async def test_secret_bearing_paths_are_never_captured(store, secret_repo, checkpoint_dir):
    """No credential-file diff may be captured, encoded, or persisted.

    Fails if the denylist is removed: every path here has a real, non-empty
    ``git diff HEAD`` and would otherwise be stored verbatim.
    """
    record = await store.publish_checkpoint(
        session_id="sess-secrets",
        project_slug=PROJECT,
        plan_state="secret hygiene",
        files_modified=[*SECRET_PATHS, "tracked.py"],
        checkpoint_id="cp-secrets",
        project_path=str(secret_repo),
    )

    captured = record.metadata["diffs"]
    for rel in SECRET_PATHS:
        assert rel not in captured, f"{rel} must never be captured"

    # The non-secret file in the same publish IS captured — proving the denylist
    # is selective rather than a blanket "capture nothing" regression.
    assert "tracked.py" in captured
    assert MODIFIED_LINE in _decode(captured["tracked.py"])

    # And the secret never reaches the on-disk checkpoint JSON in any form.
    on_disk = (checkpoint_dir / PROJECT / "cp-secrets.json").read_text(encoding="utf-8")
    assert SECRET_VALUE not in on_disk
    for encoded in captured.values():
        assert SECRET_VALUE not in _decode(encoded)


@pytest.mark.asyncio
async def test_no_secret_or_diff_body_is_written_to_logs_at_any_level(
    store, secret_repo, caplog
):
    """Logs may carry the path and a byte count — never a diff body or secret.

    Captures from DEBUG upward — the collector's own level — so the assertion
    covers the noisiest level the code can emit at, not just WARNING and above.
    """
    import logging

    with caplog.at_level(logging.DEBUG, logger="depthfusion.core.event_store"):
        record = await store.publish_checkpoint(
            session_id="sess-secret-logs",
            project_slug=PROJECT,
            plan_state="log hygiene",
            files_modified=[*SECRET_PATHS, "tracked.py"],
            project_path=str(secret_repo),
        )

    messages = [r.getMessage() for r in caplog.records]
    blob = "\n".join(messages)

    assert SECRET_VALUE not in blob, "a secret value reached a log record"
    # No diff body either — MODIFIED_LINE is the content of the captured diff.
    assert MODIFIED_LINE not in blob, "a diff body reached a log record"
    assert record.metadata["diffs"]["tracked.py"] not in blob, "encoded diff was logged"

    # Positive half: an opaque path fingerprint + byte count is logged.
    assert any("captured path_sha256=" in m and "raw bytes" in m for m in messages), messages
    assert any("path_sha256=" in m and "denylist" in m for m in messages), messages
    assert not any(path in blob for path in SECRET_PATHS)


def test_is_secret_bearing_path_classification():
    """Unit table for the shared predicate used by both the write and read side."""
    from depthfusion.core.event_store import is_secret_bearing_path

    for denied in (
        ".env",
        ".env.local",
        ".env.production",
        "app/.env",
        "server.pem",
        "certs/server.PEM",
        "private.key",
        "secrets.yaml",
        "secrets.json",
        "id_rsa",
        "id_rsa.pub",
        "id_ed25519",
        "credentials.json",
        "credentials",
        "bundle.p12",
        "app.keystore",
        "store.jks",
        ".npmrc",
        ".netrc",
        ".ssh/config",
        "home/.aws/credentials",
        "secrets/db.txt",
        "config\\secrets.yaml",
    ):
        assert is_secret_bearing_path(denied), f"{denied} must be denied"

    for allowed in (
        "tracked.py",
        "src/depthfusion/api/query.py",
        "app/src/lib/fileDiffs.ts",
        "README.md",
        "docs/env-vars.md",
        "src/keyboard.tsx",
        "tests/test_secrets_manager.py",
        "pyproject.toml",
    ):
        assert not is_secret_bearing_path(allowed), f"{allowed} must be allowed"


def test_secret_denylist_skips_before_spawning_a_subprocess(tmp_repo, monkeypatch):
    """A denied path must not even cost a git invocation."""
    import depthfusion.core.event_store as es

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("a subprocess was spawned for a denylisted path")

    monkeypatch.setattr(es.subprocess, "run", _boom)

    assert es._collect_git_diffs(str(tmp_repo), [".env", "id_rsa", "certs/x.pem"]) == {}
