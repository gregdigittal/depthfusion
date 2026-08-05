"""Event Graph Fabric — EventStore, StreamBackend Protocol, RedisStreamBackend.

Every memory publish, subscribe, and recall is recorded as a first-class
`event` Entity node in the knowledge graph so any session can later ask
"who knew what, when."

v0.6 / E-46 / S-141
"""
from __future__ import annotations

import asyncio
import base64
import fnmatch
import gzip
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from asyncio import AbstractEventLoop
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from depthfusion.graph.store import GraphBackend
from depthfusion.graph.types import Edge, Entity

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# StreamBackend Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class StreamBackend(Protocol):
    """Async streaming transport for event fan-out.

    Mirrors the ``GraphBackend`` Protocol pattern from ``graph/store.py``.
    The canonical v1 implementation is ``RedisStreamBackend`` (Redis Streams).
    Operators can swap in a ``KafkaFlinkBackend`` (v1.5) without touching
    EventStore by implementing this Protocol.
    """

    async def publish(self, channel: str, payload: dict) -> str:
        """Append a payload to the stream channel; return the stream entry ID."""
        ...

    def subscribe(
        self,
        channels: list[str],
        since_id: str = "0",
    ) -> AsyncIterator[tuple[str, dict]]:
        """Yield ``(stream_entry_id, payload)`` tuples in real time.

        ``since_id`` enables replay from a past position in the stream
        (pass the last consumed Redis Stream ID, e.g. ``"1716456789123-0"``).
        Pass ``"$"`` to receive only new entries.
        """
        ...

    async def read_since(
        self,
        channel: str,
        since_id: str = "0",
        count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Return up to ``count`` entries from ``channel`` starting after ``since_id``."""
        ...

    async def close(self) -> None:
        """Release underlying connections."""
        ...


# ---------------------------------------------------------------------------
# InMemoryStreamBackend (used in tests — not a production backend)
# ---------------------------------------------------------------------------

class InMemoryStreamBackend:
    """In-memory stub StreamBackend for unit tests.

    Stores entries in a list per channel so tests can inspect published
    payloads without a Redis dependency.
    """

    def __init__(self) -> None:
        self._streams: dict[str, list[tuple[str, dict]]] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"{int(time.time() * 1000)}-{self._counter}"

    async def publish(self, channel: str, payload: dict) -> str:
        entry_id = self._next_id()
        self._streams.setdefault(channel, []).append((entry_id, payload))
        return entry_id

    async def subscribe(
        self,
        channels: list[str],
        since_id: str = "0",
    ) -> AsyncIterator[tuple[str, dict]]:
        # In tests this generator yields all existing entries then stops.
        for ch in channels:
            for entry_id, payload in self._streams.get(ch, []):
                yield entry_id, payload

    async def read_since(
        self,
        channel: str,
        since_id: str = "0",
        count: int = 100,
    ) -> list[tuple[str, dict]]:
        entries = self._streams.get(channel, [])
        if since_id in ("0", "$"):
            results = entries if since_id == "0" else []
        else:
            results = [e for e in entries if e[0] > since_id]
        return results[:count]

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# RedisStreamBackend
# ---------------------------------------------------------------------------

class RedisStreamBackend:
    """Production StreamBackend backed by Redis Streams (redis.asyncio).

    Channel naming: ``depthfusion:stream:{project_slug}``

    Uses XADD for publishing and XREAD (with consumer groups for fan-out
    to multiple concurrent subscribers) for reading.

    Requires ``redis>=5.0`` (``pip install depthfusion[fabric]``).
    """

    def __init__(self, redis_url: str = "redis://127.0.0.1:6379") -> None:
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "RedisStreamBackend requires redis>=5.0. "
                "Install with: pip install depthfusion[fabric]"
            ) from exc

        self._client = aioredis.from_url(redis_url, decode_responses=True)

    async def publish(self, channel: str, payload: dict) -> str:
        flat: dict[str, str] = {
            k: json.dumps(v) if not isinstance(v, str) else v for k, v in payload.items()
        }
        entry_id: str = await self._client.xadd(channel, flat)  # type: ignore[arg-type]
        return entry_id

    async def subscribe(
        self,
        channels: list[str],
        since_id: str = "$",
    ) -> AsyncIterator[tuple[str, dict]]:
        last_ids: dict = {ch: since_id for ch in channels}
        while True:
            streams = await self._client.xread(
                last_ids,  # type: ignore[arg-type]
                count=100,
                block=1000,
            )
            if not streams:
                continue
            for channel, entries in streams:
                for entry_id, raw in entries:
                    last_ids[channel] = entry_id
                    payload = {
                        k: (json.loads(v) if v and v[0] in ("{", "[", '"') else v)
                        for k, v in raw.items()
                    }
                    yield entry_id, payload

    async def read_since(
        self,
        channel: str,
        since_id: str = "0",
        count: int = 100,
    ) -> list[tuple[str, dict]]:
        raw_entries = await self._client.xrange(channel, min=since_id, count=count)
        results = []
        for entry_id, raw in raw_entries:
            payload = {
                k: (json.loads(v) if v and v[0] in ("{", "[", '"') else v)
                for k, v in raw.items()
            }
            results.append((entry_id, payload))
        return results

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# EventStore
# ---------------------------------------------------------------------------

def _event_entity_id(
    agent_id: str, event_type: str, timestamp_iso: str, memory_refs: list[str]
) -> str:
    """Deterministic, dedup-safe entity_id for event entities.

    Formula: sha256(agent_id + event_type + timestamp_iso + sorted(memory_refs))[:12]
    """
    refs_str = "".join(sorted(memory_refs))
    raw = f"{agent_id}{event_type}{timestamp_iso}{refs_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _graph_lock_path(project_slug: str) -> Path:
    """Sidecar lock file for per-project graph write serialization."""
    lock_dir = Path(os.environ.get("DEPTHFUSION_LOCK_DIR", Path.home() / ".depthfusion" / "locks"))
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f".{project_slug}.graphlock"


# ---------------------------------------------------------------------------
# CheckpointRecord — S-250 / E-73 session checkpointing
# ---------------------------------------------------------------------------

_DEFAULT_CHECKPOINT_TTL_DAYS = 7


def checkpoint_ttl_days() -> int:
    """Resolve the checkpoint retention window in days (S-250 AC-5).

    Defaults to ``_DEFAULT_CHECKPOINT_TTL_DAYS`` (7); overridable via
    ``DEPTHFUSION_CHECKPOINT_TTL_DAYS``. A missing, empty, or non-integer
    value falls back to the default rather than raising, so a malformed env
    var degrades to safe behaviour instead of crashing the hygiene job or
    the checkpoint publish path. A non-positive value also falls back to
    the default (a TTL of 0 or negative days would prune every checkpoint
    immediately, which is never the intent of an unset/misconfigured var).
    """
    raw = os.environ.get("DEPTHFUSION_CHECKPOINT_TTL_DAYS", "").strip()
    if not raw:
        return _DEFAULT_CHECKPOINT_TTL_DAYS
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "invalid DEPTHFUSION_CHECKPOINT_TTL_DAYS=%r — falling back to default %d days",
            raw, _DEFAULT_CHECKPOINT_TTL_DAYS,
        )
        return _DEFAULT_CHECKPOINT_TTL_DAYS
    return value if value > 0 else _DEFAULT_CHECKPOINT_TTL_DAYS


def project_root_path() -> str:
    """Resolve the git working tree a checkpoint's diffs should be captured from.

    Sourced from ``DEPTHFUSION_PROJECT_PATH``, defaulting to
    ``os.getcwd()`` (T-863 / S-254). This is a *separate* source from
    ``DEPTHFUSION_CHECKPOINT_DIR``, and deliberately so: the checkpoint store
    layout is ``<checkpoint dir>/<project_slug>/<id>.json``, which is a plain
    JSON directory and **not** a git working tree — a git root can never be
    derived from it.

    Resolution is degrade-safe in the same spirit as ``checkpoint_ttl_days``:
    a missing, empty, or whitespace-only value falls back to the process cwd
    rather than raising. No validation that the path exists or is a git repo
    happens here — that is the diff collector's job, and it treats "not a git
    tree" as "no diffs", never as an error.
    """
    raw = os.environ.get("DEPTHFUSION_PROJECT_PATH", "").strip()
    return raw or os.getcwd()


# Per-file `git diff` cap — bounds how many subprocesses the loop may spawn.
# files_modified is caller-supplied and could in principle be thousands of
# entries. This bounds the *count*; _MAX_DIFF_TOTAL_SECONDS below bounds the
# aggregate wall-clock (the per-file timeout alone does not: 50 files each
# taking the full _DIFF_TIMEOUT_SECONDS would be minutes, far too long to sit
# in a publish).
_MAX_DIFF_FILES = 50

# Raw `git diff` bytes retained per file, applied BEFORE gzip+base64 so the
# stored payload is a bounded prefix of the real diff rather than a bounded
# prefix of its encoding.
_MAX_DIFF_RAW_BYTES = 4096

# Bound on any single `git diff` subprocess (seconds).
_DIFF_TIMEOUT_SECONDS = 3

# Bound on the whole collection loop (seconds). Once exceeded, remaining files
# are skipped: diffs are a best-effort side channel, so returning fewer of them
# is always preferable to delaying the authoritative checkpoint write.
#
# Why this is deliberately far BELOW the suite's per-test timeout (30s, see
# pyproject addopts --timeout=30): the loop runs inside a thread executor
# (publish_checkpoint hops off the event loop), and pytest-timeout's default
# signal method fires SIGALRM in the MAIN thread only. A blocked worker thread
# is therefore not interruptible by the per-test timeout, and a collection loop
# allowed to run for the full 30s could outlive the timeout that is supposed to
# bound it — the test errors, but the process cannot tear down until the child
# git exits, which reads externally as a wedged suite. Keeping the aggregate
# budget an order of magnitude under the test timeout makes that impossible on
# any host, however slow its git. Production cost of the smaller budget is nil:
# the loop still captures every file whose diff completes inside the budget and
# simply skips the tail, which is exactly the documented degradation.
_MAX_DIFF_TOTAL_SECONDS = 8


# ---------------------------------------------------------------------------
# Secret-bearing path denylist (S-254 Gate-4 security finding)
# ---------------------------------------------------------------------------
# A captured `git diff` is verbatim file content. For a credential file that
# means the credential itself lands in checkpoint JSON on disk and is then
# served back over /query/aggregate — a plaintext secret at rest plus a
# read amplifier. The denylist below is applied on BOTH sides:
#
#   * WRITE (``_collect_git_diffs``) — the path is skipped before any
#     subprocess is spawned, so the secret is never read in the first place.
#   * READ (``api.query.query_file_diffs``) — the same predicate rejects the
#     request, so checkpoints written BEFORE this denylist existed cannot be
#     mined for secrets either.
#
# Matching is on the lower-cased final path segment (plus a small set of
# denied directory names), never on the full string, so `src/app.env.py`
# is not caught while `config/.env.production` is.
_SECRET_BASENAME_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.keyfile",
    "secrets",
    "secrets.*",
    "secret",
    "secret.*",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "credentials",
    "credentials.*",
    "*.credentials",
    "*_rsa",
    "htpasswd",
    ".htpasswd",
    ".npmrc",
    ".pypirc",
    ".netrc",
    # S-254 review gate — closing false negatives in the list above. A false
    # negative writes a credential to disk and serves it over HTTP, so this
    # list errs long. Private-key and keystore formats the original list
    # missed:
    "*.p8",
    "*.ppk",
    "*.kdbx",
    "*.asc",
    "*.gpg",
    # Credential *stores* that live in a repo or a home directory:
    ".git-credentials",
    ".dockercfg",
    # Terraform variable files are the canonical place secrets are pasted:
    "*.tfvars",
    "*.tfvars.json",
)

# Any path containing one of these as a directory segment is denied outright —
# everything under them is credential material by convention.
# ``.docker`` added by the S-254 review gate: ``.docker/config.json`` holds
# registry auth, and the basename ``config.json`` is far too common to deny.
_SECRET_DIR_SEGMENTS: frozenset[str] = frozenset(
    {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", "secrets", ".secrets"}
)


def is_secret_bearing_path(path: str) -> bool:
    """Return True when *path* looks like it holds credential material.

    Used to keep secrets out of captured diffs (see the denylist comment
    above). Deliberately conservative — a false positive costs one missing
    diff for an observability side channel; a false negative writes a
    credential to disk and serves it over HTTP.

    Never raises: an unparseable value is treated as secret-bearing (fail
    closed), because "I could not classify this" must not mean "capture it".
    """
    try:
        normalised = str(path).strip().replace("\\", "/")
        if not normalised:
            return False
        segments = [s for s in normalised.split("/") if s not in ("", ".")]
        if not segments:
            return False
        if any(seg.lower() in _SECRET_DIR_SEGMENTS for seg in segments[:-1]):
            return True
        basename = segments[-1].lower()
        return any(fnmatch.fnmatch(basename, glob) for glob in _SECRET_BASENAME_GLOBS)
    except Exception:  # noqa: BLE001 — fail closed: unclassifiable means "do not capture"
        return True


# ---------------------------------------------------------------------------
# Canonical diff-map key (S-254 review gate)
# ---------------------------------------------------------------------------
# ``metadata["diffs"]`` is keyed by path, and the WRITE side (``_collect_git_diffs``)
# and the READ side (``api.query.query_file_diffs``) must agree on the shape of
# that key or the feature silently returns nothing.
#
# They did not. The writer stored whatever ``files_modified`` carried, and
# ``files_modified`` is populated in ``mcp/session_activity.py`` from raw tool-call
# arguments (``file_path`` / ``files`` / ``source_files``) — which in practice are
# ABSOLUTE paths. The reader rejected absolute selectors outright, so every real
# checkpoint stored keys the reader could never address: a 422 for the dashboard's
# file pills and an empty history forever.
#
# One canonicaliser, used by both sides, fixes that by making the reader's
# documented assumption ("checkpoint diff keys are repo-relative by construction")
# actually true. It is pure string manipulation — no ``open``, no ``resolve()``, no
# subprocess — because the read path must never touch the filesystem with a
# caller-supplied value (pinned by
# ``test_file_param_never_reaches_a_subprocess_or_open``).
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def normalise_diff_path(path: str, project_path: str | None = None) -> str | None:
    """Canonical repo-relative diff-map key for *path*, or ``None`` if it cannot be one.

    Rules, in order:

    * separators are folded (``\\`` → ``/``) and redundant ``.``/empty segments
      dropped, so ``./a//b`` and ``.\\a\\b`` both canonicalise to ``a/b``;
    * any ``..`` segment is rejected rather than resolved, so no traversal string
      can ever become a key or be used to address one;
    * a Windows drive-absolute path (``C:\\x``) is rejected;
    * a POSIX-absolute path is made relative to *project_path* when it lies
      inside it, and rejected otherwise. ``/etc/shadow`` is therefore rejected on
      both sides, while the absolute paths the MCP session tracker really records
      resolve to the same key the collector stored.

    Returns ``None`` rather than raising: the write side skips the file, and the
    read side maps ``None`` to its own ``ValueError("invalid_file")`` sentinel so
    the route can answer 422. Never raises for any input.
    """
    raw = str(path).strip()
    if not raw or _WINDOWS_DRIVE_RE.match(raw):
        return None

    text = raw.replace("\\", "/")
    segments = [seg for seg in text.split("/") if seg not in ("", ".")]
    if not segments or any(seg == ".." for seg in segments):
        return None

    if not text.startswith("/"):
        return "/".join(segments)

    # Absolute: only admissible as the project-relative remainder of a path that
    # is genuinely inside the working tree the diffs were captured from.
    if not project_path:
        return None
    root = [
        seg
        for seg in str(project_path).strip().replace("\\", "/").split("/")
        if seg not in ("", ".")
    ]
    if not root or segments[: len(root)] != root:
        return None
    remainder = segments[len(root):]
    return "/".join(remainder) if remainder else None


def _git_subprocess_env() -> dict[str, str]:
    """Environment for the ``git diff`` child: inherited minus the code-exec hooks.

    ``GIT_EXTERNAL_DIFF`` (and ``diff.external`` via ``GIT_CONFIG_*``) make git
    execute an arbitrary program per diffed file, and ``GIT_PAGER`` /
    ``GIT_SSH_COMMAND`` are similar footguns. They are stripped rather than
    trusted so this collector cannot be turned into an execution primitive by
    ambient environment state. ``GIT_TERMINAL_PROMPT=0`` additionally keeps the
    child from ever blocking on an interactive credential prompt, which is a
    boundedness property the timeout alone would only paper over.

    S-254 review gate: the original list closed ``GIT_CONFIG_PARAMETERS`` /
    ``GIT_CONFIG_COUNT`` but left three equivalent routes to the same
    ``diff.external`` / ``textconv`` execution primitive open, plus a redirect:

    * ``GIT_CONFIG_GLOBAL`` / ``GIT_CONFIG_SYSTEM`` point git at an arbitrary
      config file, which can set ``diff.external`` — the very knob
      ``GIT_EXTERNAL_DIFF`` was stripped to close.
    * ``GIT_ATTR_SOURCE`` chooses the gitattributes source, and a ``diff=<drv>``
      attribute plus a config driver reaches the same place.
    * ``GIT_DIR`` / ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` /
      ``GIT_OBJECT_DIRECTORY`` / ``GIT_ALTERNATE_OBJECT_DIRECTORIES`` would
      silently retarget the diff at a *different* repository than the ``cwd``
      this function was called for, so the captured diff would not be the
      project's. Stripping them makes ``cwd=project_path`` authoritative, which
      is what every docstring on this path already claims.
    """
    env = dict(os.environ)
    for var in (
        "GIT_EXTERNAL_DIFF",
        "GIT_DIFF_OPTS",
        "GIT_PAGER",
        "GIT_SSH_COMMAND",
        "GIT_SSH",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_ATTR_SOURCE",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        env.pop(var, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _collect_git_diffs(project_path: str, files_modified: list[str]) -> dict[str, str]:
    """Best-effort ``git diff HEAD`` capture, gzip+base64 per file (T-863).

    Runs ``git diff HEAD -- <path>`` once per entry in *files_modified* with
    ``cwd=project_path``, truncates the RAW diff to ``_MAX_DIFF_RAW_BYTES``,
    then gzips and base64-encodes it. Returns a ``{path: encoded}`` mapping
    of the files that actually produced a diff.

    Strictly best-effort — this feeds ``CheckpointRecord.metadata``, which is
    supplementary observability, while the checkpoint file write is
    authoritative. Every failure mode degrades to "fewer (or no) diffs":

      * git missing / not executable  → ``OSError`` → empty mapping
      * *project_path* is not a git tree, or HEAD does not exist yet (fresh
        repo with no commit) → non-zero exit → that file is skipped
      * subprocess timeout            → that file is skipped
      * empty *files_modified*        → empty mapping, no subprocess spawned
      * unreadable/binary/renamed path, or any other per-file exception
                                      → that file is skipped
      * ``_MAX_DIFF_TOTAL_SECONDS`` budget exhausted
                                      → all remaining files are skipped

    Secret hygiene: any path for which :func:`is_secret_bearing_path` is true
    (``.env*``, ``*.pem``, ``*.key``, ``secrets.*``, ``id_rsa``,
    ``credentials.json``, ``*.p12``, ``*.keystore``, anything under ``.ssh``/
    ``.aws``/``secrets``, …) is dropped BEFORE any subprocess runs, so the
    credential is never read, never encoded, and never written to checkpoint
    JSON. Logging on every path here is deliberately limited to the path and a
    byte count — a diff body is verbatim file content and must never reach a
    log record at any level.

    Resource bounds, all enforced rather than assumed: at most
    ``_MAX_DIFF_FILES`` subprocesses; at most ``_DIFF_TIMEOUT_SECONDS`` each and
    ``_MAX_DIFF_TOTAL_SECONDS`` across the whole loop; and peak memory of
    ``_MAX_DIFF_RAW_BYTES`` per file regardless of how large the real diff is
    (git writes to a temp file and only the capped prefix is read back).

    Keys are canonical repo-relative paths, not the caller's raw strings — see
    :func:`normalise_diff_path`, which is shared verbatim with the read side so
    the two can never disagree about how a diff is addressed. A path that cannot
    be expressed as such a key (absolute but outside *project_path*, or
    containing ``..``) is skipped before any subprocess runs, because a key the
    reader can never look up is not worth a ``git diff``.

    Nothing here raises. Files whose diff is empty (unchanged, untracked, or
    outside the tree) are omitted rather than stored as an empty payload.
    """
    diffs: dict[str, str] = {}
    if not files_modified:
        log.debug("_collect_git_diffs: no files_modified — skipping diff collection")
        return diffs

    # Secret denylist first, so a credential path never even costs a subprocess
    # and never appears in the capture budget. Only the path is logged.
    #
    # Then canonicalise to the repo-relative key the READ side addresses records
    # by (see normalise_diff_path). Storing the caller's raw path instead — which
    # for the MCP session tracker is an absolute one — produced keys that
    # query_file_diffs could never look up. The canonical key is used BOTH as the
    # dict key and as the git pathspec; with cwd=project_path the two are the same
    # file, so nothing about what git is asked to diff changes.
    kept: list[str] = []
    seen: set[str] = set()
    for raw_candidate in files_modified:
        candidate = str(raw_candidate)
        if not candidate.strip():
            continue
        if is_secret_bearing_path(candidate):
            log.debug(
                "_collect_git_diffs: path_sha256=%s matches the secret-bearing "
                "denylist — not capturing",
                hashlib.sha256(candidate.encode()).hexdigest()[:12],
            )
            continue
        key = normalise_diff_path(candidate, project_path)
        if key is None:
            log.debug(
                "_collect_git_diffs: path_sha256=%s is not addressable as a repo-relative key "
                "under the project root — not capturing",
                hashlib.sha256(candidate.encode()).hexdigest()[:12],
            )
            continue
        # Re-check the canonical form: relativisation strips leading segments, so
        # a denied directory could in principle only be *removed*, never added —
        # but the check is free and a missed credential is the expensive
        # direction to be wrong in.
        if is_secret_bearing_path(key):
            log.debug(
                "_collect_git_diffs: path_sha256=%s canonicalises to a "
                "secret-bearing key — not capturing",
                hashlib.sha256(candidate.encode()).hexdigest()[:12],
            )
            continue
        if key in seen:
            # Two supplied spellings of one file (`x` and `./x`) collapse to one
            # key; capturing it twice would waste a subprocess and a budget slot.
            continue
        seen.add(key)
        kept.append(key)
    paths = kept
    if not paths:
        return diffs

    if len(paths) > _MAX_DIFF_FILES:
        log.debug(
            "_collect_git_diffs: %d files exceeds cap %d — capturing the first %d",
            len(paths), _MAX_DIFF_FILES, _MAX_DIFF_FILES,
        )
        paths = paths[:_MAX_DIFF_FILES]

    deadline = time.monotonic() + _MAX_DIFF_TOTAL_SECONDS
    # Built once for the whole loop — it is a snapshot of the same env for every
    # file, and copying os.environ up to _MAX_DIFF_FILES times is pure waste.
    git_env = _git_subprocess_env()

    for path in paths:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.debug(
                "_collect_git_diffs: %.1fs total budget exhausted — skipping remaining files",
                _MAX_DIFF_TOTAL_SECONDS,
            )
            break

        try:
            # Injection safety, in order of importance:
            #   1. argv LIST, never a string, and no shell interpolation (the
            #      `shell` kwarg is never enabled anywhere in this module) — the
            #      path is one argv element, so metacharacters in it are inert.
            #   2. The path is always the element AFTER the literal `--`, which
            #      terminates git's option parsing. A caller-supplied path such
            #      as "--upload-pack=evil" or "--output=/etc/x" is therefore
            #      parsed as a pathspec, never as a git option.
            #   3. `--no-ext-diff --no-textconv` disables repository-controlled
            #      diff drivers, while `env` strips ambient Git execution hooks.
            # Bytes (not text=True) because the 4096-byte cap is defined on raw
            # diff bytes.
            #
            # stdout goes to a temp FILE rather than a pipe: capture_output
            # buffers git's *entire* output in memory before the cap below can
            # apply, so a single huge diff would defeat _MAX_DIFF_RAW_BYTES and
            # balloon RSS. Spooling to disk and reading only the capped prefix
            # keeps peak memory at _MAX_DIFF_RAW_BYTES regardless of diff size,
            # while still giving subprocess.run its normal timeout and a real
            # returncode (both of which a bounded pipe read would forfeit).
            # stderr is discarded — only returncode is consulted.
            # TODO(S-254 review gate): replace the temporary-file spool with a
            # byte-bounded process reader. Raised independently by the external
            # reviewer, and deferred deliberately — the reasoning, so the next
            # reader does not have to redo it:
            #
            #   * The spool bounds MEMORY (only the capped prefix is read back) but
            #     not temporary-DISK use, which is the residual gap.
            #   * That gap is bounded in practice rather than open-ended: the child
            #     is killed at _DIFF_TIMEOUT_SECONDS (3s) and the loop stops at
            #     _MAX_DIFF_TOTAL_SECONDS (8s), so worst-case spill is ~8s of git
            #     write throughput into a TemporaryFile that is unlinked on close
            #     and on process exit. It is not an unbounded-disk primitive.
            #   * The obvious fix — Popen + a bounded stdout.read() — trades a
            #     bounded-disk property for an UNBOUNDED-time one: a bare read()
            #     blocks with no timeout if git is slow to produce bytes, which
            #     would let a single file outlive the aggregate budget that the
            #     whole design of this loop rests on. Doing it correctly means
            #     reimplementing timeout, kill and reap semantics that
            #     subprocess.run already gets right.
            #
            # Net: fixing this properly is a self-contained change with its own
            # failure modes to test, not a line edit inside a review pass.
            with tempfile.TemporaryFile() as out:
                result = subprocess.run(
                    ["git", "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", path],
                    cwd=project_path,
                    stdout=out,
                    stderr=subprocess.DEVNULL,
                    env=git_env,
                    timeout=min(_DIFF_TIMEOUT_SECONDS, remaining),
                    check=False,
                )
                if result.returncode != 0:
                    log.debug(
                        "_collect_git_diffs: git diff exited %d for path_sha256=%s — skipping",
                        result.returncode, hashlib.sha256(path.encode()).hexdigest()[:12],
                    )
                    continue
                out.seek(0)
                raw = out.read(_MAX_DIFF_RAW_BYTES)
        except Exception as exc:  # noqa: BLE001 — degrade to no diff, never crash publish
            # Exception TYPE only, not str(exc): a subprocess error message can
            # carry captured child output, and diff bytes must never be logged.
            log.debug(
                "_collect_git_diffs: git diff failed for path_sha256=%s — %s",
                hashlib.sha256(path.encode()).hexdigest()[:12], type(exc).__name__,
            )
            continue

        try:
            if not raw:
                continue
            diffs[path] = base64.b64encode(gzip.compress(raw)).decode("ascii")
            # Path + byte count ONLY. The diff body is verbatim file content, so
            # it must never be interpolated into a log record at any level.
            log.debug(
                "_collect_git_diffs: captured path_sha256=%s (%d raw bytes)",
                hashlib.sha256(path.encode()).hexdigest()[:12], len(raw),
            )
        except Exception as exc:  # noqa: BLE001 — degrade to no diff, never crash publish
            log.debug(
                "_collect_git_diffs: encoding failed for path_sha256=%s — %s",
                hashlib.sha256(path.encode()).hexdigest()[:12], type(exc).__name__,
            )
            continue

    return diffs


def _checkpoint_base_dir() -> Path:
    """Root directory for persisted checkpoint JSON files (no side effects).

    Read-only resolution — does NOT create the directory. Callers that need
    to write must create the per-project directory themselves (see
    ``checkpoint_store_dir``); callers that only need to read (prune, lookup)
    must treat a missing directory as "no checkpoints yet", not an error.
    """
    return Path(
        os.environ.get("DEPTHFUSION_CHECKPOINT_DIR", Path.home() / ".depthfusion" / "checkpoints")
    ).expanduser()


def checkpoint_store_dir(project_slug: str) -> Path:
    """Per-project directory holding persisted ``CheckpointRecord`` JSON files.

    Creates the directory if absent — this is the write-path helper (used by
    ``EventStore.publish_checkpoint``). Layout:
    ``<DEPTHFUSION_CHECKPOINT_DIR or ~/.depthfusion/checkpoints>/<project_slug>/``.
    """
    d = _checkpoint_base_dir() / project_slug
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class CheckpointRecord:
    """A session checkpoint (S-250 / E-73 Growth, Reliability & Observability).

    Captured on clean session close (``DELETE /mcp``), on server shutdown
    (SIGTERM via the FastAPI lifespan), or on-demand at plan boundaries.
    Enables ``depthfusion_session_seed(mode="resume")`` to hand a fresh agent
    session enough state to pick up where a crashed or closed session left
    off, without re-deriving it from scratch.

    AC-1 binding literal fields (S-250 acceptance criterion — do not rename
    or drop these four):
      * ``plan_state``                — free-form summary of the active plan/task
      * ``files_modified``            — paths touched since the last checkpoint
      * ``git_stash_ref``             — a ``git stash`` ref if uncommitted work
                                         was stashed at checkpoint time, else
                                         ``None``
      * ``context_pct_at_checkpoint`` — context-window utilisation (0-100) at
                                         checkpoint time, else ``None``

    ``checkpoint_id``, ``session_id``, ``project_slug``, and ``created_at``
    are identity/addressing fields required so a checkpoint can be persisted
    and later looked up by id or by "most recent for this project" — they are
    not part of the AC-1 literal set but are necessary for the record to be
    retrievable at all.

    ``metadata`` (T-863) is an open, best-effort side-channel for supplementary
    capture that must never be load-bearing. Its one current producer is
    ``EventStore.publish_checkpoint``'s git-diff collector, which populates
    ``metadata["diffs"]`` with a ``{path: base64(gzip(raw git diff))}`` mapping.
    Consumers must treat every key as optional: an older on-disk record has no
    ``metadata`` at all (``from_dict`` defaults it), and a newer one may have
    ``metadata`` but no ``"diffs"`` key when collection was skipped.
    """

    checkpoint_id: str
    session_id: str
    project_slug: str
    created_at: str  # ISO-8601 UTC
    plan_state: str
    files_modified: list[str] = field(default_factory=list)
    git_stash_ref: str | None = None
    context_pct_at_checkpoint: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "project_slug": self.project_slug,
            "created_at": self.created_at,
            "plan_state": self.plan_state,
            "files_modified": list(self.files_modified),
            "git_stash_ref": self.git_stash_ref,
            "context_pct_at_checkpoint": self.context_pct_at_checkpoint,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CheckpointRecord":
        # Every optional key uses a defensive .get() default: this is the sole
        # deserialiser on every read path, and checkpoint JSON written before a
        # field existed must still load (a KeyError here would make
        # list_checkpoints skip the record entirely).
        return cls(
            checkpoint_id=d["checkpoint_id"],
            session_id=d.get("session_id", ""),
            project_slug=d.get("project_slug", ""),
            created_at=d.get("created_at", ""),
            plan_state=d.get("plan_state", ""),
            files_modified=list(d.get("files_modified") or []),
            git_stash_ref=d.get("git_stash_ref"),
            context_pct_at_checkpoint=d.get("context_pct_at_checkpoint"),
            metadata=dict(d.get("metadata") or {}),
        )


def list_checkpoints(
    project_slug: str | None = None, limit: int = 20, cursor: str | None = None,
) -> list[CheckpointRecord]:
    """Return checkpoints newest first (T-860 / S-253).

    Module-level and filesystem-only — deliberately a sibling of
    ``prune_expired_checkpoints`` and ``checkpoint_store_dir`` rather than an
    ``EventStore`` method, because reading persisted ``CheckpointRecord`` JSON
    touches no graph and no stream.  Read paths must not have to construct (or
    lazily open) a ``GraphBackend`` just to list files.

    Note the ordering contrast with ``EventStore.get_latest_checkpoint``, which
    sorts ASCENDING and takes ``[-1]``: this function sorts ``created_at``
    DESCENDING and returns the leading ``limit`` records, so callers get the
    most recent checkpoints without re-sorting.

    An empty or ``None`` *project_slug* means **all projects**: every
    immediate subdirectory of the checkpoint root is scanned and the merged
    result is sorted globally by ``created_at`` before the ``limit`` slice.
    This is what makes an unscoped caller (the dashboard tile, which has no
    project selector yet) show real rows instead of an always-empty list.

    Returns an empty list — never raises — when no checkpoint directory
    exists yet.  Malformed record files are logged and skipped rather than
    failing the whole listing.

    ``cursor`` is accepted for forward compatibility with the opaque offset
    cursors used by ``/query/discoveries`` and ``/query/sessions``, but is
    deliberately not interpreted yet (T-860): this returns a single
    ``limit``-bounded page.
    """
    base = _checkpoint_base_dir()
    if project_slug:
        dirs = [base / project_slug]
    else:
        if not base.is_dir():
            return []
        dirs = sorted(p for p in base.iterdir() if p.is_dir())

    records: list[CheckpointRecord] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for path in d.glob("*.json"):
            try:
                rec = CheckpointRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                log.warning(
                    "list_checkpoints: skipping malformed record %s — %s: %s",
                    path, type(exc).__name__, exc,
                )
                continue
            records.append(rec)

    records.sort(key=lambda r: r.created_at, reverse=True)
    return records[:limit]


def prune_expired_checkpoints(project_slug: str, *, ttl_days: int | None = None) -> int:
    """Delete checkpoint JSON files older than the TTL window (S-250 AC-5).

    Synchronous and file-glob based (mirrors ``capture/pruner.py``'s
    age-based heuristic) so it can be called directly from the synchronous
    nightly hygiene job (``capture/hygiene.py``) without an event loop.

    Age is judged by ``CheckpointRecord.created_at`` (the record's own
    creation timestamp), not filesystem mtime, so a copied or restored
    checkpoint file with a stale mtime still expires on its original
    creation time.

    Never raises — a missing checkpoint directory is treated as "nothing to
    prune" (returns 0, no directory is created as a side effect of a
    read/prune call), and a malformed or unreadable checkpoint file is
    logged and skipped rather than aborting the whole run.

    Returns the number of checkpoint files deleted.
    """
    ttl = ttl_days if ttl_days is not None else checkpoint_ttl_days()
    cutoff_ts = datetime.now(timezone.utc).timestamp() - ttl * 86400.0
    d = _checkpoint_base_dir() / project_slug
    if not d.exists():
        return 0

    deleted = 0
    for path in sorted(d.glob("*.json")):
        try:
            record = CheckpointRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            created_ts = datetime.fromisoformat(record.created_at).timestamp()
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            log.warning(
                "prune_expired_checkpoints: skipping malformed checkpoint %s — %s: %s",
                path, type(exc).__name__, exc,
            )
            continue
        if created_ts < cutoff_ts:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                log.warning(
                    "prune_expired_checkpoints: could not delete %s — %s", path, exc,
                )
    return deleted


class EventStore:
    """Fabric-level event store.

    Writes event Entities to the knowledge graph (authoritative, durable)
    and optionally notifies via a StreamBackend (best-effort, real-time).

    Graph writes are:
    - Serialized per-project via ``file_locking.flock_ex``
    - Run in a thread-pool executor to avoid blocking the asyncio event loop
      (the underlying ``GraphBackend`` is synchronous)
    - Performed with SQLite WAL mode enforced at init time when the backend
      supports it

    Redis stream notifications are best-effort: if the StreamBackend is
    unavailable, a warning is logged and the publish() call still returns
    successfully (with the graph write completed).
    """

    def __init__(
        self,
        graph: GraphBackend,
        stream: StreamBackend | None = None,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        self._graph = graph
        self._stream = stream
        self._loop = loop
        self._enforce_wal()

    @property
    def graph(self) -> GraphBackend:
        """Public read-only access to the underlying graph backend."""
        return self._graph

    def _enforce_wal(self) -> None:
        """Enable SQLite WAL mode if the graph backend is SQLite-backed."""
        import sqlite3 as _sqlite3
        backend = self._graph
        # SQLiteGraphBackend exposes a ``_conn`` attribute (from graph/store.py).
        conn = getattr(backend, "_conn", None)
        if isinstance(conn, _sqlite3.Connection):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.commit()
            log.debug("EventStore: SQLite WAL mode enforced")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def publish(
        self,
        agent_id: str,
        project_slug: str,
        memory_refs: list[str],
        event_type: str = "publish",
        session_id: str | None = None,
    ) -> str:
        """Record a publish event in the graph and notify via stream.

        Returns the event entity_id.
        """
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        event_id = _event_entity_id(agent_id, event_type, timestamp_iso, memory_refs)

        entity = Entity(
            entity_id=event_id,
            name=f"{event_type}:{agent_id}:{timestamp_iso}",
            type="event",
            project=project_slug,
            source_files=[],
            confidence=1.0,
            first_seen=timestamp_iso,
            metadata={
                "acl_allow": [project_slug],
                "event_type": event_type,
                "agent_id": agent_id,
                "project_slug": project_slug,
                "memory_refs": memory_refs,
                "session_id": session_id,
            },
        )

        # Graph write (authoritative) — serialize per project
        await self._graph_write(project_slug, entity)

        # Edges: AGENT_PUBLISHED from event → each referenced memory entity
        for ref_id in memory_refs:
            await self._graph_write_edge(project_slug, entity.entity_id, ref_id, "AGENT_PUBLISHED")

        # Stream notification (best-effort)
        await self._stream_publish(project_slug, entity)

        return event_id

    async def publish_memory(
        self,
        content: str,
        agent_id: str,
        project_slug: str,
        event_type: str = "publish",
        session_id: str | None = None,
    ) -> dict:
        """Publish content as a MemoryEntity + EventEntity with content-hash dedup.

        The MemoryEntity is content-addressed: entity_id = sha256(content)[:12].
        Concurrent publishes of identical content converge to 1 MemoryEntity
        because ``upsert_entity`` is idempotent (INSERT OR REPLACE in SQLite).

        Returns: {"memory_id": str, "event_id": str, "deduped": bool}
        """
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]
        timestamp_iso = datetime.now(timezone.utc).isoformat()

        # Check if MemoryEntity already exists (best-effort dedup signal).
        # Under concurrency, multiple callers may see None simultaneously —
        # the upsert below is still idempotent so only 1 entity ends up in the graph.
        loop = asyncio.get_event_loop()
        existing = await loop.run_in_executor(None, self._graph.get_entity, content_hash)
        deduped = existing is not None

        memory_entity = Entity(
            entity_id=content_hash,
            name=content[:80].replace("\n", " "),
            type="memory",
            project=project_slug,
            source_files=[],
            confidence=1.0,
            first_seen=existing.first_seen if existing else timestamp_iso,
            metadata={
                "acl_allow": [project_slug],
                "content_hash": content_hash,
                "agent_id": agent_id,
            },
        )
        await self._graph_write(project_slug, memory_entity)

        if deduped:
            log.debug(
                "EventStore.publish_memory: content_hash=%s already in graph — dedup hit",
                content_hash,
            )

        # EventEntity — always unique (distinct timestamp per call)
        event_id = _event_entity_id(agent_id, event_type, timestamp_iso, [content_hash])
        event_entity = Entity(
            entity_id=event_id,
            name=f"{event_type}:{agent_id}:{timestamp_iso}",
            type="event",
            project=project_slug,
            source_files=[],
            confidence=1.0,
            first_seen=timestamp_iso,
            metadata={
                "acl_allow": [project_slug],
                "event_type": event_type,
                "agent_id": agent_id,
                "project_slug": project_slug,
                "memory_refs": [content_hash],
                "session_id": session_id,
                "content_hash": content_hash,
            },
        )
        await self._graph_write(project_slug, event_entity)
        await self._graph_write_edge(project_slug, event_id, content_hash, "AGENT_PUBLISHED")
        await self._stream_publish(project_slug, event_entity)

        return {"memory_id": content_hash, "event_id": event_id, "deduped": deduped}

    # ------------------------------------------------------------------
    # Checkpointing — S-250 / E-73 (T-847)
    # ------------------------------------------------------------------

    async def publish_checkpoint(
        self,
        session_id: str,
        project_slug: str,
        plan_state: str,
        files_modified: list[str],
        *,
        agent_id: str = "system",
        git_stash_ref: str | None = None,
        context_pct_at_checkpoint: float | None = None,
        checkpoint_id: str | None = None,
        project_path: str | None = None,
    ) -> CheckpointRecord:
        """Persist a ``CheckpointRecord`` and mirror it into the event fabric.

        The record is written to two places:

        1. A JSON file under ``checkpoint_store_dir(project_slug)`` — the
           durable, directly-queryable copy that ``get_checkpoint`` /
           ``get_latest_checkpoint`` read from, and that the nightly hygiene
           job's ``prune_expired_checkpoints`` TTL-prunes (S-250 AC-5). This
           write is authoritative; if it fails, the exception propagates —
           callers must know a checkpoint publish failed.
        2. An ``event`` Entity in the graph (``event_type="checkpoint"``) via
           the existing ``publish()`` path, for fabric-wide observability
           parity with every other event type. This mirror is best-effort —
           a graph-write failure is logged and swallowed rather than losing
           the checkpoint file that was already written.

        *project_path* (T-863) is the git working tree to capture diffs from —
        resolve it from ``project_root_path()`` at the call site rather than
        letting it default here. When it is ``None`` no subprocess is spawned
        at all and ``metadata`` carries no ``"diffs"`` key, which keeps every
        existing caller and test byte-identical to the pre-T-863 behaviour.
        When it is set, ``metadata["diffs"]`` gains a
        ``{path: base64(gzip(raw git diff HEAD -- path))}`` mapping. That
        collection is best-effort in the same sense as the graph mirror: it is
        wrapped so that a missing git binary, a non-repo path, or any per-file
        failure can never prevent the authoritative file write.

        Returns the ``CheckpointRecord`` that was persisted.
        """
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        record_id = checkpoint_id or _event_entity_id(
            agent_id, "checkpoint", timestamp_iso, [session_id]
        )
        files = list(files_modified)

        loop = asyncio.get_event_loop()

        metadata: dict[str, Any] = {}
        if project_path is not None:
            try:
                # Executor hop: _collect_git_diffs shells out to git, which is
                # blocking — same reason the file write below is offloaded.
                diffs = await loop.run_in_executor(
                    None, _collect_git_diffs, project_path, files
                )
            except Exception as exc:  # noqa: BLE001 — diffs degrade, publish must not
                # Exception TYPE only, never str(exc) — same rule as the per-file
                # handler in _collect_git_diffs: anything raised on a diff path can
                # carry captured child output, and diff bytes are verbatim file
                # content that must not reach a log record at any level. This
                # handler should be unreachable (_collect_git_diffs swallows its own
                # per-file failures), which is exactly why it must not be the one
                # place the rule is relaxed.
                log.debug(
                    "publish_checkpoint: diff collection failed for session=%s — %s",
                    session_id, type(exc).__name__,
                )
                diffs = {}
            if diffs:
                metadata["diffs"] = diffs

        record = CheckpointRecord(
            checkpoint_id=record_id,
            session_id=session_id,
            project_slug=project_slug,
            created_at=timestamp_iso,
            plan_state=plan_state,
            files_modified=files,
            git_stash_ref=git_stash_ref,
            context_pct_at_checkpoint=context_pct_at_checkpoint,
            metadata=metadata,
        )

        def _write_file() -> None:
            d = checkpoint_store_dir(project_slug)
            path = d / f"{record.checkpoint_id}.json"
            path.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")

        await loop.run_in_executor(None, _write_file)

        try:
            await self.publish(
                agent_id=agent_id,
                project_slug=project_slug,
                memory_refs=[],
                event_type="checkpoint",
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort fabric mirror
            log.warning(
                "EventStore.publish_checkpoint: graph mirror failed for checkpoint_id=%s — %s: %s",
                record.checkpoint_id, type(exc).__name__, exc,
            )

        return record

    def get_checkpoint(self, checkpoint_id: str, project_slug: str) -> CheckpointRecord | None:
        """Load a single checkpoint by id. Returns ``None`` if not found or malformed.

        Synchronous — pure local-file I/O, no graph or network access, so no
        executor hop is needed (mirrors ``EventStore.graph`` being a plain
        property rather than an async accessor).
        """
        path = _checkpoint_base_dir() / project_slug / f"{checkpoint_id}.json"
        if not path.exists():
            return None
        try:
            return CheckpointRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            log.warning(
                "get_checkpoint: malformed record %s — %s: %s", path, type(exc).__name__, exc,
            )
            return None

    def get_latest_checkpoint(
        self, project_slug: str, session_id: str | None = None,
    ) -> CheckpointRecord | None:
        """Return the most recently created checkpoint for a project.

        ``session_id``, when given, narrows the search to checkpoints from
        that session only. Returns ``None`` when no (matching) checkpoint
        exists — including when the project has no checkpoint directory yet.
        """
        d = _checkpoint_base_dir() / project_slug
        if not d.exists():
            return None

        records: list[CheckpointRecord] = []
        for path in d.glob("*.json"):
            try:
                rec = CheckpointRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                log.warning(
                    "get_latest_checkpoint: skipping malformed record %s — %s: %s",
                    path, type(exc).__name__, exc,
                )
                continue
            if session_id and rec.session_id != session_id:
                continue
            records.append(rec)

        if not records:
            return None
        records.sort(key=lambda r: r.created_at)
        return records[-1]

    # ------------------------------------------------------------------
    # Ambient trace publishing — S-250 AC-2 (T-849)
    # ------------------------------------------------------------------

    async def publish_ambient_trace(
        self,
        agent_id: str,
        project_slug: str,
        session_id: str | None = None,
    ) -> str:
        """Publish a lightweight ambient trace event.

        Ambient traces are low-signal, high-frequency activity markers
        (intended to be recorded on every MCP tool call) so a resumed
        session can reconstruct recent activity even without an explicit
        checkpoint. They are recorded with ``event_type="ambient_trace"`` so
        ``get_recent_events`` and other fabric consumers can distinguish
        them from both ordinary publishes and checkpoints.

        Excluded from standard recall by default — see
        ``retrieval/hybrid.py::filter_blocks_by_ambient_trace`` and
        ``mcp/tools/_shared.py::_tool_recall_impl``'s ``include_ambient_trace``
        argument, which is the recall-side half of this AC. This method is
        the publish-side half: a thin, single-purpose wrapper over
        ``publish()`` (same Entity write + best-effort stream fan-out) with
        a distinct ``event_type`` tag.

        Returns the event entity_id.
        """
        return await self.publish(
            agent_id=agent_id,
            project_slug=project_slug,
            memory_refs=[],
            event_type="ambient_trace",
            session_id=session_id,
        )

    async def get_recent_events(
        self,
        project_slug: str,
        since_hours: float = 24.0,
        agent_id: str | None = None,
        event_types: list[str] | None = None,
    ) -> list[Entity]:
        """Return recent event entities from the graph matching the filters."""
        loop = asyncio.get_event_loop()
        entities: list[Entity] = await loop.run_in_executor(
            None, self._graph.all_entities
        )
        cutoff = datetime.now(timezone.utc).timestamp() - since_hours * 3600

        results = []
        for e in entities:
            if e.type != "event":
                continue
            if e.metadata.get("project_slug") != project_slug:
                continue
            try:
                ts = datetime.fromisoformat(e.first_seen).timestamp()
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            if agent_id and e.metadata.get("agent_id") != agent_id:
                continue
            if event_types and e.metadata.get("event_type") not in event_types:
                continue
            results.append(e)

        results.sort(key=lambda x: x.first_seen)
        return results

    async def fabric_seed_bundle(
        self,
        projects: list[str],
        goal: str = "",
        top_k: int = 5,
        since_hours: float = 24.0,
    ) -> dict:
        """Compute a ranked context bundle for fabric_seed mode.

        Ranking: score = recall_relevance × recency_decay × log(1 + observer_count)
        - recall_relevance: BM25 keyword overlap between goal and memory name (0-1)
        - recency_decay: exp(-days_since_first_seen / 7)
        - observer_count: distinct agent_ids with AGENT_RECEIVED edges to the memory

        Falls back to graph-only traversal if Redis unavailable; returns
        ``degraded: True`` in that case.
        """
        import math

        degraded = self._stream is None
        loop = asyncio.get_event_loop()

        # Collect recent event entities for the requested projects
        all_entities: list[Entity] = await loop.run_in_executor(None, self._graph.all_entities)
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - since_hours * 3600

        # Gather unique memory_refs from recent events across requested projects
        memory_ids: set[str] = set()
        for e in all_entities:
            if e.type != "event":
                continue
            if e.metadata.get("project_slug") not in projects:
                continue
            try:
                ts = datetime.fromisoformat(e.first_seen).timestamp()
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            for ref in e.metadata.get("memory_refs", []):
                memory_ids.add(ref)

        # Fetch MemoryEntities and score them
        goal_tokens = set(goal.lower().split()) if goal else set()
        scored: list[tuple[float, Entity]] = []

        for mid in memory_ids:
            entity = await loop.run_in_executor(None, self._graph.get_entity, mid)
            if entity is None:
                continue

            # recency_decay
            try:
                ts = datetime.fromisoformat(entity.first_seen).timestamp()
                days_old = (now - ts) / 86400.0
                recency_decay = math.exp(-days_old / 7.0)
            except (ValueError, TypeError):
                recency_decay = 0.1

            # observer_count via AGENT_RECEIVED edges
            edges = await loop.run_in_executor(
                None,
                lambda: self._graph.get_edges(mid, relationship_filter=["AGENT_RECEIVED"]),
            )
            agent_ids = {e.metadata.get("agent_id", e.source_id) for e in edges}
            observer_count = len(agent_ids)

            # recall_relevance — Jaccard-style token overlap with goal
            if goal_tokens:
                name_tokens = set(entity.name.lower().split())
                overlap = len(goal_tokens & name_tokens)
                recall_relevance = overlap / max(len(goal_tokens | name_tokens), 1)
            else:
                recall_relevance = 1.0

            score = recall_relevance * recency_decay * math.log(1 + observer_count + 1)
            scored.append((score, entity))

        scored.sort(key=lambda x: x[0], reverse=True)
        bundle = [
            {
                "memory_id": e.entity_id,
                "name": e.name,
                "project": e.project,
                "first_seen": e.first_seen,
                "score": round(s, 4),
                "observer_count": len(
                    {ed.metadata.get("agent_id", ed.source_id)
                     for ed in self._graph.get_edges(
                         e.entity_id, relationship_filter=["AGENT_RECEIVED"]
                     )}
                ),
                "metadata": e.metadata,
            }
            for s, e in scored[:top_k]
        ]

        return {
            "bundle": bundle,
            "degraded": degraded,
            "project_count": len(projects),
            "memory_ids_scanned": len(memory_ids),
        }

    async def subscribe_stream(
        self,
        projects: list[str],
        since_id: str = "$",
        consumer_id: str | None = None,
    ) -> AsyncIterator[tuple[str, Entity]]:
        """Yield ``(stream_entry_id, EventEntity)`` from the live stream.

        Requires a configured StreamBackend. Raises ``RuntimeError`` if none
        is configured.
        """
        if self._stream is None:
            raise RuntimeError(
                "EventStore.subscribe_stream requires a StreamBackend. "
                "Configure RedisStreamBackend and pass it to EventStore()."
            )
        channels = [f"depthfusion:stream:{p}" for p in projects]
        async for entry_id, payload in self._stream.subscribe(channels, since_id=since_id):
            entity = self._payload_to_entity(payload)
            if entity is not None:
                yield entry_id, entity

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _graph_write(self, project_slug: str, entity: Entity) -> None:
        """Write entity to graph, serialized per-project via fcntl lock."""
        from depthfusion.core.file_locking import flock_ex, flock_un

        lock_path = _graph_lock_path(project_slug)
        loop = asyncio.get_event_loop()

        def _write() -> None:
            lock_fh = open(lock_path, "a", encoding="utf-8")
            try:
                flock_ex(lock_fh.fileno())
                try:
                    self._graph.upsert_entity(entity)
                finally:
                    flock_un(lock_fh.fileno())
            finally:
                lock_fh.close()

        await loop.run_in_executor(None, _write)

    async def _graph_write_edge(
        self,
        project_slug: str,
        source_id: str,
        target_id: str,
        relationship: str,
    ) -> None:
        """Write a directed edge to the graph, serialized per-project."""
        import hashlib as _hl

        from depthfusion.core.file_locking import flock_ex, flock_un

        edge_id = _hl.sha256(f"{source_id}{relationship}{target_id}".encode()).hexdigest()[:12]
        edge = Edge(
            edge_id=edge_id,
            source_id=source_id,
            target_id=target_id,
            relationship=relationship,
            weight=1.0,
            signals=["event_fabric"],
            adapter_name="event_store",
            source_type="event",
            metadata={"acl_allow": [project_slug]},
        )

        lock_path = _graph_lock_path(project_slug)
        loop = asyncio.get_event_loop()

        def _write() -> None:
            lock_fh = open(lock_path, "a", encoding="utf-8")
            try:
                flock_ex(lock_fh.fileno())
                try:
                    self._graph.upsert_edge(edge)
                finally:
                    flock_un(lock_fh.fileno())
            finally:
                lock_fh.close()

        await loop.run_in_executor(None, _write)

    async def _stream_publish(self, project_slug: str, entity: Entity) -> None:
        """Best-effort stream notification. Logs and continues on failure."""
        if self._stream is None:
            return
        channel = f"depthfusion:stream:{project_slug}"
        payload = {
            "entity_id": entity.entity_id,
            "name": entity.name,
            "type": entity.type,
            "project": entity.project,
            "first_seen": entity.first_seen,
            "metadata": entity.metadata,
        }
        try:
            await self._stream.publish(channel, payload)
        except Exception as exc:
            log.warning(
                "EventStore: stream publish failed (best-effort) — %s: %s",
                type(exc).__name__,
                exc,
            )

    @staticmethod
    def _payload_to_entity(payload: dict) -> Entity | None:
        """Reconstruct an EventEntity from a raw stream payload dict."""
        try:
            metadata = payload.get("metadata", {})
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            return Entity(
                entity_id=payload["entity_id"],
                name=payload.get("name", ""),
                type=payload.get("type", "event"),
                project=payload.get("project", ""),
                source_files=[],
                confidence=1.0,
                first_seen=payload.get("first_seen", ""),
                metadata=metadata,
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            log.warning("EventStore: malformed stream payload — %s", exc)
            return None
