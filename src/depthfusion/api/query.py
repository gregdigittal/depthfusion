"""Query data readers for DepthFusion REST query endpoints.

Provides filtered, cursor-paginated access to discovery files and session
(recall) events without modifying or indexing the underlying storage.

T-575: All query functions accept a ``principal`` argument.  Discovery records
are trimmed to those the principal can see (acl_allow check via
:mod:`depthfusion.authz.frontmatter`).  Session / aggregate data carries no
per-record ACL today, so the principal is accepted but not further filtered —
callers are expected to supply it so future column-level trimming can be added
without a signature change.
"""
from __future__ import annotations

import base64
import glob
import gzip
import io
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from depthfusion.identity.models import Principal

log = logging.getLogger(__name__)

_DISCOVERIES_DIR = Path.home() / ".claude" / "shared" / "discoveries"
_METRICS_DIR = Path.home() / ".claude" / "depthfusion-metrics"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


# ---------------------------------------------------------------------------
# Cursor helpers (opaque base64-encoded integer offset)
# ---------------------------------------------------------------------------

def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: Optional[str]) -> Optional[int]:
    """Decode cursor; returns None if cursor is non-empty but invalid."""
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ACL helper
# ---------------------------------------------------------------------------

def _principal_can_see(content: str, principal: "Optional[Principal]") -> bool:
    """Return True if *principal* is allowed to read a discovery file.

    When *principal* is None (unauthenticated / no trimming requested) we
    allow access — callers are responsible for ensuring a principal is always
    supplied on authenticated routes.

    The check reads ``acl_allow`` from the document's YAML frontmatter via
    :func:`depthfusion.authz.frontmatter.parse_acl`.  When frontmatter is
    absent the default from ``parse_acl`` applies (``acl_allow=["greg"]``,
    ``classification=internal``).

    Allowed when at least one of ``principal.principal_id`` or any item in
    ``principal.groups`` appears in the parsed ``acl_allow`` list.
    """
    if principal is None:
        return True

    try:
        from depthfusion.authz.frontmatter import parse_acl
        acl = parse_acl(content)
    except Exception:
        # Malformed ACL frontmatter → fail-closed.
        return False

    allowed_ids: set[str] = {principal.principal_id}
    for g in (principal.groups or []):
        allowed_ids.add(g)

    return bool(set(acl.acl_allow) & allowed_ids)


# ---------------------------------------------------------------------------
# Discovery readers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Extract YAML-like frontmatter from a markdown file."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        import yaml  # optional dep; safe on this host
        return yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}


def _discovery_record(filepath: Path) -> dict[str, Any]:
    """Parse a discovery file into a summary record."""
    stat = filepath.stat()
    text = filepath.read_text(errors="replace")
    fm = _parse_frontmatter(text)

    # Strip frontmatter from content preview
    body = _FRONTMATTER_RE.sub("", text).strip()
    preview = body[:300].replace("\n", " ") if body else ""

    # Normalise date: prefer frontmatter `date`, else derive from filename.
    # PyYAML parses bare YYYY-MM-DD as datetime.date; convert to str.
    raw_date = fm.get("date")
    if raw_date is not None:
        date_str = str(raw_date)  # handles datetime.date and string
    else:
        date_str = ""
    if not date_str:
        name = filepath.stem  # e.g. 2026-05-13-depthfusion-foo
        m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
        date_str = m.group(1) if m else ""

    tags_raw = fm.get("tags", "")
    if isinstance(tags_raw, list):
        tags = [str(t) for t in tags_raw]
    elif isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    else:
        tags = []

    return {
        "filename": filepath.name,
        "date": date_str,
        "project": str(fm.get("project", "")),
        "agent": str(fm.get("agent", "")),
        "tags": tags,
        "title": filepath.stem,
        "content_preview": preview,
        "size_bytes": stat.st_size,
    }


def query_discoveries(
    project: Optional[str] = None,
    agent: Optional[str] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    tags: Optional[list[str]] = None,
    cursor: Optional[str] = None,
    limit: int = 100,
    discoveries_dir: Optional[Path] = None,
    principal: "Optional[Principal]" = None,
) -> dict[str, Any]:
    """Return paginated discovery records matching filters.

    T-575: only records visible to *principal* (via acl_allow frontmatter) are
    included.  When *principal* is None, no ACL trimming is applied — this is
    intentional for the legacy/internal path; callers on authenticated routes
    MUST supply the principal.
    """
    base = discoveries_dir or _DISCOVERIES_DIR
    pattern = str(base / "*.md")
    all_files = sorted(glob.glob(pattern))

    records: list[dict] = []
    for fp in all_files:
        try:
            path = Path(fp)
            # ACL check — read full text once; pass to both _principal_can_see
            # and _discovery_record (which re-reads; acceptable for filesystem).
            content = path.read_text(errors="replace")
            if not _principal_can_see(content, principal):
                continue

            rec = _discovery_record(path)
        except Exception:
            continue

        # Date filters
        date_str = rec["date"]
        if from_dt and date_str:
            try:
                rd = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                if rd < from_dt:
                    continue
            except ValueError:
                pass
        if to_dt and date_str:
            try:
                rd = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                if rd > to_dt:
                    continue
            except ValueError:
                pass

        if project and rec["project"] != project:
            continue
        if agent and rec["agent"] != agent:
            continue
        if tags:
            rec_tags = set(rec["tags"])
            if not all(t in rec_tags for t in tags):
                continue

        records.append(rec)

    offset = _decode_cursor(cursor)
    if offset is None:
        raise ValueError("invalid_cursor")
    page = records[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = _encode_cursor(next_offset) if next_offset < len(records) else None

    return {
        "items": page,
        "total": len(records),
        "count": len(page),
        "next_cursor": next_cursor,
    }


# ---------------------------------------------------------------------------
# Session (recall) readers
# ---------------------------------------------------------------------------

def _recall_files_in_range(
    metrics_dir: Path,
    from_dt: Optional[datetime],
    to_dt: Optional[datetime],
) -> list[Path]:
    """Return sorted recall JSONL files whose date prefix falls in range."""
    pattern = str(metrics_dir / "*-recall.jsonl")
    files = []
    for fp in sorted(glob.glob(pattern)):
        name = Path(fp).stem  # e.g. 2026-05-13-recall
        m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
        if not m:
            continue
        try:
            file_date = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if from_dt and file_date.date() < from_dt.date():
            continue
        if to_dt and file_date.date() > to_dt.date():
            continue
        files.append(Path(fp))
    return files


def query_sessions(
    project: Optional[str] = None,
    agent: Optional[str] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    cursor: Optional[str] = None,
    limit: int = 100,
    metrics_dir: Optional[Path] = None,
    principal: "Optional[Principal]" = None,
) -> dict[str, Any]:
    """Return paginated recall session event records matching filters.

    T-575: *principal* is accepted and threaded through for future per-row
    trimming.  Session JSONL files do not carry per-record acl_allow today so
    no rows are filtered beyond what the authenticated route gate already
    enforces.
    """
    base = metrics_dir or _METRICS_DIR
    recall_files = _recall_files_in_range(base, from_dt, to_dt)

    all_events: list[dict] = []
    for fp in recall_files:
        try:
            for line in fp.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Timestamp filter
                ts_str = ev.get("timestamp", "")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if from_dt and ts < from_dt:
                            continue
                        if to_dt and ts > to_dt:
                            continue
                    except ValueError:
                        pass
                # Project filter: recall events carry config_version_id but no
                # project field; `agent` maps to `mode` (best-effort).
                if agent:
                    ev_mode = ev.get("mode", "")
                    if ev_mode != agent:
                        continue
                all_events.append(ev)
        except Exception:
            continue

    offset = _decode_cursor(cursor)
    if offset is None:
        raise ValueError("invalid_cursor")
    page = all_events[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = _encode_cursor(next_offset) if next_offset < len(all_events) else None

    return {
        "items": page,
        "total": len(all_events),
        "count": len(page),
        "next_cursor": next_cursor,
    }


# ---------------------------------------------------------------------------
# Checkpoints — T-860 / S-253
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Principal → project scoping (S-254 Gate-4 security finding)
# ---------------------------------------------------------------------------

# Group-claim prefix that grants read access to one project slug, e.g.
# ``project:depthfusion``. Chosen over a bespoke claim because ``groups`` is
# already the codebase's only principal-derived authorization input (see
# ``authz/capability_check.py`` and ``authz/policy_engine.py``).
_PROJECT_GROUP_PREFIX = "project:"


def _principal_project_scope(
    principal: "Optional[Principal]",
) -> Optional[set[str]]:
    """Project slugs *principal* may read, or ``None`` for unrestricted.

    Two sources, unioned:

    * an explicit ``projects`` attribute on the principal (present on
      project-scoped service accounts; read with ``getattr`` so
      :class:`~depthfusion.identity.models.Principal` needs no change and any
      principal shape keeps working), and
    * ``project:<slug>`` entries in ``principal.groups``.

    An **empty** scope means unrestricted, not "deny everything". That is
    deliberate and matches the fail-open contract of
    :func:`depthfusion.retrieval.acl_verifier.verify_acl`: today's principals
    carry no project claim at all, so treating "no claim" as "no access" would
    lock every existing caller out of its own data. A principal that DOES carry
    a project claim is confined to it — which is the leak this closes.

    Shared verbatim by :func:`query_checkpoints` and :func:`query_file_diffs`
    so the two read paths over the same checkpoint store can never drift apart
    on who may see which project.
    """
    if principal is None:
        return None

    scope: set[str] = set()
    explicit = getattr(principal, "projects", None)
    if isinstance(explicit, (list, tuple, set, frozenset)):
        scope.update(str(p).strip() for p in explicit if str(p).strip())
    for group in getattr(principal, "groups", None) or []:
        text = str(group).strip()
        if text.startswith(_PROJECT_GROUP_PREFIX):
            slug = text[len(_PROJECT_GROUP_PREFIX):].strip()
            if slug:
                scope.add(slug)
    return scope or None


def _scope_permits(scope: Optional[set[str]], project_slug: Optional[str]) -> bool:
    """True when *scope* admits records belonging to *project_slug*."""
    if scope is None:
        return True
    return bool(project_slug) and project_slug in scope


def _checkpoint_wire_dict(record: Any) -> dict[str, Any]:
    """``record.to_dict()`` with the raw diff payload stripped out.

    ``CheckpointRecord.to_dict()`` is the on-disk shape, and it now carries
    ``metadata["diffs"]`` — the full ``base64(gzip(git diff))`` body for every
    captured file. Serving that verbatim from ``/query/checkpoints`` would be
    wrong twice over (S-254 review gate):

    * **Security.** It bypasses the read-side secret denylist entirely. The whole
      point of re-checking :func:`is_secret_bearing_path` in
      :func:`query_file_diffs` is that checkpoints written *before* the denylist
      existed may hold a credential diff and this store is append-only. Returning
      the same payload unfiltered from the sibling route on the same store makes
      that check decorative — a caller just reads ``metadata.diffs`` instead and
      base64-decodes it.
    * **Size.** ``limit`` checkpoints × up to 50 files × ~4 KiB of encoded diff is
      a multi-megabyte response, on a route whose only consumer is a timeline
      tile that renders none of it (the drill-down panel fetches diffs separately
      via ``type=file_diffs``, one file at a time).

    The ``metadata`` KEY is preserved — its presence is part of the wire contract
    asserted by ``test_rest_query_checkpoints`` — and every other metadata entry
    passes through untouched. Only ``diffs`` is removed, and only from the wire
    shape: the on-disk record and ``from_dict``/``to_dict`` symmetry are unchanged.
    """
    d = record.to_dict()
    metadata = d.get("metadata")
    if isinstance(metadata, dict) and "diffs" in metadata:
        d["metadata"] = {k: v for k, v in metadata.items() if k != "diffs"}
    return d


def query_checkpoints(
    project_slug: Optional[str] = None,
    limit: int = 20,
    principal: "Optional[Principal]" = None,
) -> dict[str, Any]:
    """Return session checkpoints, newest first.

    Stays a pure filesystem reader like every other function in this module:
    it delegates to the module-level, graph-free
    :func:`depthfusion.core.event_store.list_checkpoints` (a sibling of
    ``prune_expired_checkpoints``) so the on-disk layout has exactly one
    owner, and no ``EventStore`` / ``GraphBackend`` is constructed on this
    read path.

    ``CheckpointRecord`` carries no per-record acl frontmatter, so the only
    authorization dimension is the project a record belongs to.  When
    *principal* carries a project claim (see :func:`_principal_project_scope`)
    records outside that claim are dropped; when it carries none the scope is
    unrestricted and behaviour is byte-identical to before. The same helper
    governs :func:`query_file_diffs`, which reads the same store.

    An empty or ``None`` *project_slug* means all projects the principal may
    see — the checkpoint root is scanned per-project and merged, newest first.

    Items are the record's ``to_dict()`` minus ``metadata["diffs"]`` — see
    :func:`_checkpoint_wire_dict` for why the raw diff bodies must not ride along
    on this route. Diffs are fetched per file via ``query_file_diffs``.
    """
    from depthfusion.core.event_store import list_checkpoints

    scope = _principal_project_scope(principal)
    requested = (project_slug or "").strip() or None
    if requested is not None and not _scope_permits(scope, requested):
        # Explicit request for a project outside the claim — empty, not 403, so
        # the response cannot be used to probe which slugs exist.
        return {"items": [], "total": 0, "count": 0}

    records = list_checkpoints(project_slug, limit=limit)
    # Post-filter rather than narrowing the scan: list_checkpoints owns the
    # on-disk layout and merges slugs itself, and a scoped principal is the
    # uncommon case. May return fewer than `limit` rows, which is correct — a
    # scoped caller must not learn the count of records it cannot read.
    if scope is not None:
        records = [r for r in records if _scope_permits(scope, r.project_slug)]
    items = [_checkpoint_wire_dict(r) for r in records]
    return {"items": items, "total": len(items), "count": len(items)}


# ---------------------------------------------------------------------------
# Cross-session file diff history — T-864 / S-254
# ---------------------------------------------------------------------------

# Breadth of the underlying checkpoint scan, deliberately decoupled from — and
# far larger than — the caller's ``limit``.  ``list_checkpoints`` returns
# newest-first and its ``limit`` truncates *checkpoints*, not *matches*: only a
# small fraction of checkpoints carry a diff for any one file, so a history view
# has to look at far more checkpoints than it will ever return rows for.  Passing
# the caller's ``limit`` straight through would silently answer "no history"
# for a file that simply was not touched in the last ``limit`` checkpoints.
_DIFF_SCAN_CHECKPOINTS = 5_000

# Cap on the DECOMPRESSED size of a single stored diff. The writer caps input at
# 4096 raw bytes, but checkpoint JSON is persistent on-disk data that this read
# path does not control: a corrupt or hand-crafted record could hold a gzip bomb
# that expands to gigabytes, and one request decodes up to _DIFF_SCAN_CHECKPOINTS
# of them. Decoding through a bounded read makes that a truncated string rather
# than a memory-exhaustion vector. Generously above the writer's cap so a
# legitimately-written diff is never truncated here.
_MAX_DIFF_DECOMPRESSED_BYTES = 1 << 20  # 1 MiB

# Cap on the ENCODED (base64) payload accepted for decoding, checked before
# ``base64.b64decode`` runs (S-254 review gate — independent reviewer finding).
# _MAX_DIFF_DECOMPRESSED_BYTES bounds gzip *output*, but b64decode materialises
# its whole input first, so a hand-crafted record holding a 500 MB base64 string
# would allocate that much before the gzip bound could apply. A ``len()`` on the
# string is free and turns the crafted record into a skipped one.
#
# Sized well above any legitimate value: the writer caps raw diffs at 4096 bytes,
# which gzips smaller and base64s to roughly 5.6 KB, so 1 MiB of encoding leaves
# ~180x headroom and cannot truncate a real diff.
#
# TODO(S-254 review gate): the dominant allocation is still upstream and NOT
# fixable here — ``list_checkpoints`` does ``path.read_text()`` on each record, so
# an oversized checkpoint file is already fully resident before this function sees
# it. That is pre-existing (S-250/S-253) and shared by ``get_checkpoint`` and
# ``get_latest_checkpoint``, so bounding it belongs in one place in
# ``core/event_store.py`` and warrants its own story rather than a partial fix on
# one of three read paths.
_MAX_DIFF_ENCODED_BYTES = 1 << 20  # 1 MiB of base64


def _coerce_aware(dt: datetime) -> datetime:
    """Return *dt* as tz-aware, assuming UTC when it carries no offset.

    Both sides of every ``created_at >= since`` comparison go through this, so a
    naive value (``2026-08-01``, ``2026-08-01T10:00:00``) can never raise
    ``TypeError: can't compare offset-naive and offset-aware datetimes``.
    """
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _normalise_diff_file_path(file_path: str) -> str:
    """Validate and normalise the caller-supplied ``file`` selector.

    Delegates to :func:`depthfusion.core.event_store.normalise_diff_path` — the
    *same* canonicaliser the write side keys ``metadata["diffs"]`` with — so a
    selector and a stored key can never disagree about how a diff is addressed.
    Duplicating the rules here is what previously let the writer store absolute
    keys the reader rejected outright (S-254 review gate).

    The value is only ever used as a **dict key** — the lookup
    ``metadata["diffs"][file_path]`` — and never as a filesystem path, an
    ``open()`` argument, or a subprocess argument. It is nonetheless validated
    rather than trusted, because "it is only a dict key today" is exactly the
    assumption a future refactor breaks:

    * any ``..`` segment is rejected outright rather than resolved, so no
      traversal string can ever reach a path-joining consumer;
    * a Windows drive-absolute path (``C:\\x``) is rejected; and
    * a POSIX-absolute path is admitted **only** when it lies inside
      ``project_root_path()`` — the same working tree the collector captured
      from — and is then folded to its repo-relative remainder. ``/etc/shadow``
      and ``//etc/passwd`` are still rejected, while the absolute paths the MCP
      session tracker really records resolve to the key that was stored.

    Normalisation is otherwise limited to separator folding (``\\`` → ``/``) and
    dropping redundant ``.``/empty segments, so ``./tracked.py`` still matches
    the key ``tracked.py``. Raises ``ValueError("invalid_file")`` on rejection,
    which the route maps to a 422 (never echoing the offending value back).

    Still pure string manipulation: no ``open``, no ``resolve()``, no subprocess
    (pinned by ``test_file_param_never_reaches_a_subprocess_or_open``).
    """
    from depthfusion.core.event_store import normalise_diff_path, project_root_path

    normalised = normalise_diff_path(file_path or "", project_root_path())
    if normalised is None:
        raise ValueError("invalid_file")
    return normalised


def _parse_since(since: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 ``since`` bound into a tz-aware datetime.

    ``None`` / blank means "all time".  An unparseable value is rejected with
    ``ValueError("invalid_since")`` so the route can turn it into a clean 422
    instead of a 500 — this is caller input, not a per-record degrade path.
    """
    if since is None or not since.strip():
        return None
    try:
        return _coerce_aware(datetime.fromisoformat(since.strip()))
    except ValueError:
        raise ValueError("invalid_since")


def query_file_diffs(
    file_path: str,
    since: Optional[str] = None,
    project_slug: Optional[str] = None,
    limit: int = 50,
    principal: "Optional[Principal]" = None,
) -> dict[str, Any]:
    """Return the cross-session diff history for one file, oldest first.

    Reads ``metadata["diffs"][file_path]`` off persisted ``CheckpointRecord``
    JSON — the best-effort side channel written by
    ``EventStore.publish_checkpoint``'s git-diff collector (T-863) as
    ``base64(gzip(raw git diff))`` — and decodes it back to text.

    Like :func:`query_checkpoints`, this stays a pure filesystem reader: it
    delegates to the module-level, graph-free
    :func:`depthfusion.core.event_store.list_checkpoints`, so **no**
    ``EventStore`` / ``GraphBackend`` is constructed anywhere on this read path
    (the invariant documented at ``event_store.py`` ``list_checkpoints``).

    * ``project_slug`` of ``None``/empty means all projects — ``list_checkpoints``
      enumerates every per-slug directory under the checkpoint root and merges.
    * ``since`` is an ISO-8601 lower bound on ``created_at``, compared tz-safely
      (see :func:`_coerce_aware`); omitted means all time.  Unparseable raises
      ``ValueError("invalid_since")``.
    * The scan breadth is ``_DIFF_SCAN_CHECKPOINTS``, independent of ``limit``;
      ``limit`` is applied to the *matched* items after filtering and sorting.
    * Ordering is ASCENDING by ``created_at`` — this is a chronological history
      view, the deliberate opposite of ``list_checkpoints``' newest-first — and
      the ``limit`` slice is taken from the front of that chronology.

    Security posture (S-254 Gate-4):

    * ``file_path`` goes through :func:`_normalise_diff_file_path` first —
      absolute paths and ``..`` traversal are rejected with
      ``ValueError("invalid_file")``. The normalised value is used *only* as a
      dict key; nothing on this path opens a file or spawns a process.
    * ``principal`` is enforced, not decorative: the shared
      :func:`_principal_project_scope` confines a project-claimed principal to
      its own slugs, exactly as in :func:`query_checkpoints`, so one principal
      cannot read another project's diffs.
    * Secret-bearing selectors (``.env``, ``*.pem``, ``*.key``, ``secrets.*``,
      ``id_rsa``, ``credentials.json``, ``*.p12``, ``*.keystore``, …) return an
      empty history. The writer already refuses to capture them, but
      checkpoints written before that denylist existed may still hold one, and
      this store is append-only — so the read side denies them too.

    A per-checkpoint decode or parse failure is logged and skipped rather than
    failing the whole query, mirroring ``list_checkpoints``' per-file resilience.
    Log records carry the checkpoint id, the path and the exception *type* only
    — never a decoded diff body, which is verbatim file content.
    """
    from depthfusion.core.event_store import is_secret_bearing_path, list_checkpoints

    file_path = _normalise_diff_file_path(file_path)
    since_dt = _parse_since(since)

    scope = _principal_project_scope(principal)
    requested = (project_slug or "").strip() or None
    empty: dict[str, Any] = {"items": [], "total": 0, "count": 0, "file": file_path}
    if requested is not None and not _scope_permits(scope, requested):
        return empty
    if is_secret_bearing_path(file_path):
        log.warning(
            "query_file_diffs: refusing secret-bearing path %s — returning empty history",
            file_path,
        )
        return empty

    scan_limit = max(_DIFF_SCAN_CHECKPOINTS, limit)
    records = list_checkpoints(project_slug, limit=scan_limit)

    items: list[dict[str, Any]] = []
    for rec in records:
        if scope is not None and not _scope_permits(scope, rec.project_slug):
            continue
        try:
            diffs = rec.metadata.get("diffs") if isinstance(rec.metadata, dict) else None
            if not isinstance(diffs, dict):
                continue
            encoded = diffs.get(file_path)
            if not encoded:
                continue
            # Bound the DECODE input before allocating it — see
            # _MAX_DIFF_ENCODED_BYTES. Skipped like any other unusable record
            # rather than raising, so one crafted checkpoint cannot fail the query.
            if not isinstance(encoded, str) or len(encoded) > _MAX_DIFF_ENCODED_BYTES:
                log.warning(
                    "query_file_diffs: skipping checkpoint %s for %s — "
                    "encoded diff is not a string or exceeds %d bytes",
                    getattr(rec, "checkpoint_id", "?"), file_path, _MAX_DIFF_ENCODED_BYTES,
                )
                continue

            if since_dt is not None:
                created = _coerce_aware(datetime.fromisoformat(rec.created_at))
                if created < since_dt:
                    continue

            # Bounded read rather than gzip.decompress(): decompress() would
            # expand the whole payload into memory before any cap could apply.
            with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(encoded))) as gz:
                raw = gz.read(_MAX_DIFF_DECOMPRESSED_BYTES)
            diff_text = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 — skip one bad record, never fail the query
            # Exception TYPE only — str(exc) on a decode/decompress failure can
            # embed a fragment of the payload, and a diff body is verbatim file
            # content that must never reach a log record at any level.
            log.warning(
                "query_file_diffs: skipping checkpoint %s for %s — %s",
                getattr(rec, "checkpoint_id", "?"), file_path, type(exc).__name__,
            )
            continue

        items.append(
            {
                "checkpoint_id": rec.checkpoint_id,
                "session_id": rec.session_id,
                "project_slug": rec.project_slug,
                "created_at": rec.created_at,
                "diff": diff_text,
            }
        )

    # Ascending chronology. Sorted on the raw ISO-8601 string exactly as
    # list_checkpoints does, so both orderings derive from one comparison rule.
    items.sort(key=lambda i: i["created_at"])
    # TODO(S-254 review gate): `limit` slices the FRONT of the ascending
    # chronology, so a file with more than `limit` recorded diffs returns its
    # OLDEST `limit` entries and its most recent changes are withheld — the
    # opposite of what the drill-down panel (which sends no limit, so the
    # default 50 applies) usually wants. This is currently deliberate and is
    # pinned by test_query_file_diffs_scan_breadth_is_decoupled_from_limit,
    # which asserts limit=1 -> "cp-first"; flipping to a tail slice is a
    # product decision that must change that test too, so it is deferred
    # rather than altered here.
    # TODO(S-254 review gate): the scan reads up to _DIFF_SCAN_CHECKPOINTS
    # (5000) checkpoint JSON files per request with no caching, and the panel
    # re-polls every 60s per open file. Fine at current checkpoint volumes;
    # revisit with a path->checkpoint index if the store grows large.
    # `total` is the full match count BEFORE truncation, `count` the number of
    # rows actually returned. They differ precisely when `limit` truncates, which
    # is what lets a caller tell "this file has 3 diffs" from "this file has 200
    # diffs and you are seeing 50". (This is a deliberate divergence from
    # query_checkpoints, where total == count because nothing is sliced there.)
    total = len(items)
    items = items[:limit]

    return {"items": items, "total": total, "count": len(items), "file": file_path}


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def query_aggregate(
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    metrics_dir: Optional[Path] = None,
    principal: "Optional[Principal]" = None,
) -> dict[str, Any]:
    """Return aggregated statistics over recall session events.

    Streams directly from JSONL files — does not materialise all events in
    memory. The latency p95 sample list is capped at 100k values to avoid
    unbounded memory growth on very large datasets.

    T-575: *principal* is accepted for API consistency and future trimming.
    Aggregate statistics are computed over the records the caller can see; the
    session JSONL format does not carry per-record ACL today so no rows are
    dropped by principal checks here.
    """
    base = metrics_dir or _METRICS_DIR
    recall_files = _recall_files_in_range(base, from_dt, to_dt)

    total = 0
    modes: dict[str, int] = {}
    versions: dict[str, int] = {}
    latencies: list[float] = []
    result_counts: list[int] = []
    _LAT_SAMPLE_CAP = 100_000

    for fp in recall_files:
        try:
            for line in fp.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = ev.get("timestamp", "")
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        if from_dt and ts < from_dt:
                            continue
                        if to_dt and ts > to_dt:
                            continue
                    except ValueError:
                        pass
                total += 1
                m = ev.get("mode", "unknown")
                modes[m] = modes.get(m, 0) + 1
                v = ev.get("config_version_id", "")
                if v:
                    versions[v] = versions.get(v, 0) + 1
                lat = ev.get("total_latency_ms")
                if lat is not None and len(latencies) < _LAT_SAMPLE_CAP:
                    latencies.append(lat)
                rc = ev.get("result_count")
                if rc is not None:
                    result_counts.append(rc)
        except Exception:
            continue

    if total == 0:
        return {
            "total_events": 0,
            "total_latency_ms": 0.0,
            "avg_latency_ms": None,
            "p95_latency_ms": None,
            "avg_result_count": None,
            "modes": {},
            "config_versions": {},
        }

    latencies.sort()
    p95: Optional[float] = None
    if latencies:
        p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) > 20 else latencies[-1]

    total_lat = sum(latencies)
    avg_lat: Optional[float] = total_lat / len(latencies) if latencies else None
    avg_rc: Optional[float] = sum(result_counts) / len(result_counts) if result_counts else None

    return {
        "total_events": total,
        "total_latency_ms": round(total_lat, 3),
        "avg_latency_ms": round(avg_lat, 3) if avg_lat is not None else None,
        "p95_latency_ms": round(p95, 3) if p95 is not None else None,
        "avg_result_count": round(avg_rc, 2) if avg_rc is not None else None,
        "modes": modes,
        "config_versions": versions,
    }
