"""MetricsCollector — records metrics, gate logs, recall queries, and
capture events to daily JSONL files.

v0.5.0 T-158 / S-51: extended with `record_gate_log()` for the Mamba B/C/Δ
selective fusion gates. Gate logs are a D-3 invariant — emitted per query
regardless of whether any block was rejected — and are written to a
separate daily file (`YYYY-MM-DD-gates.jsonl`) so the main metrics stream
stays readable.

v0.5.0 T-163 / S-53: extended with `record_recall_query()` and
`record_capture_event()` for structured observability across backends
and capture mechanisms. Four separate daily streams now exist:

  * `YYYY-MM-DD.jsonl`          — simple (metric, value, labels) records
  * `YYYY-MM-DD-gates.jsonl`    — Mamba B/C/Δ gate audit entries
  * `YYYY-MM-DD-recall.jsonl`   — per-query backend routing + latency
  * `YYYY-MM-DD-capture.jsonl`  — capture-mechanism writes (decision/
                                   negative/dedup/git-hook/confirm)

Every structured record carries:
  * `event_subtype` — per DR-018 I-19 ratification. Values:
      "ok" | "error" | "timeout" | "sla_expiry_deny" | "user_deny" | "acs_reject"
  * `config_version_id` — per amended I-11. Empty string is the sentinel
    for "no config snapshot tracking wired yet" (same contract as gate
    logs). Callers that track config versions override it.

Gate-log records carry a `config_version_id` field per I-8 ratification
(see docs/plans/v0.5/03-skillforge-integration.md §3.3.5 action 2).

Concurrency: every structured-stream append acquires `fcntl.flock(LOCK_EX)`
on a fresh `open(path, "a")` file descriptor. Because each caller opens
its own OFD (open file description), the lock serialises both inter-
process and intra-process concurrent writers — the kernel associates
flock state with the OFD, not the thread. The lock is released when the
`with` block closes the fd; no explicit `LOCK_UN` is needed.

Gate-log entries routinely exceed 4 KiB (they contain the full
`decisions` list), which is why append-only + `O_APPEND` position
atomicity alone is NOT sufficient — the kernel only guarantees atomic
writes up to `PIPE_BUF` (4096 bytes on Linux), and above that a
multi-writer scenario can interleave. Recall + capture entries usually
fit within 4 KiB, but the lock is applied uniformly for consistency.

Note: the simple `record()` metrics stream is NOT flock-guarded
(pre-v0.5 behaviour preserved for back-compat). If multi-process
writes to `YYYY-MM-DD.jsonl` are ever observed to interleave, migrate
that stream to `_append_jsonl` too.
"""
from __future__ import annotations

import fcntl
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _json_default(o: Any) -> Any:
    """Coerce non-JSON-native values to Python natives (not strings).

    Handles numpy scalars (common when embedding backends produce
    `numpy.float32` scores). Falls back to `str(o)` only as a last
    resort, matching the pre-v0.5 behaviour for types we don't know about.
    """
    # numpy may not be installed; defer the import and handle both shapes.
    try:
        import numpy as np
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


# v0.5 T-163 / S-53: allowed event_subtype values per DR-018 I-19 ratification.
# "ok" is the default success case; others signal specific failure modes
# that downstream aggregators can count separately.
_VALID_EVENT_SUBTYPES = frozenset({
    "ok", "error", "timeout",
    "sla_expiry_deny",   # DR-018 I-19 — approval state machine SLA expiry
    "user_deny",
    "acs_reject",
})

# v0.5 T-163: allowed capture_mechanism values. Adding a new mechanism
# requires a constant here so downstream aggregators (and the aggregator's
# summary tables) can enumerate the complete set without surprise
# mechanisms leaking in via typos.
_VALID_CAPTURE_MECHANISMS = frozenset({
    "decision_extractor",  # CM-1 (S-45)
    "negative_extractor",  # CM-6 (S-48)
    "dedup",               # CM-2 (S-49)
    "git_post_commit",     # CM-3 (S-46)
    "confirm_discovery",   # CM-5 (S-47)
})


class MetricsCollector:
    """Records metrics and structured events to daily JSONL files.

    Streams:
      * `YYYY-MM-DD.jsonl`          — simple metrics (name, value, labels)
      * `YYYY-MM-DD-gates.jsonl`    — Mamba B/C/Δ gate audit entries
      * `YYYY-MM-DD-recall.jsonl`   — per-query backend routing + latency
      * `YYYY-MM-DD-capture.jsonl`  — capture-mechanism write events
    """

    def __init__(self, metrics_dir: Path | None = None) -> None:
        if metrics_dir is None:
            metrics_dir = Path.home() / ".claude" / "depthfusion-metrics"
        self.metrics_dir = metrics_dir
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal: locked JSONL append
    # ------------------------------------------------------------------

    def _append_jsonl(self, path: Path, entry: dict) -> None:
        """Append a JSON line to `path` under `fcntl.flock(LOCK_EX)`.

        The advisory lock is released automatically when `with` closes the
        fd — no explicit `LOCK_UN` needed. Errors (filesystem full,
        permission denied, filesystem without flock support) are swallowed:
        observability must never degrade serving.

        Used by every structured stream (`gates`, `recall`, `capture`).
        """
        try:
            with open(path, "a", encoding="utf-8") as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                except OSError:
                    # flock unsupported (some filesystems / OSes) — best-effort only
                    pass
                f.write(json.dumps(entry, default=_json_default) + "\n")
                # No explicit LOCK_UN: advisory lock releases when fd closes
                # at the end of the `with` block. See MED-3 review fix.
        except OSError:
            # Filesystem full / permissions — swallow silently
            return

    def _validate_event_subtype(self, subtype: str) -> str:
        """Normalise an event_subtype string; falls back to 'ok' on unknown.

        Unknown subtypes are coerced to 'ok' so aggregator enumerations
        stay tight — a typo like "timout" shouldn't create a new bucket.
        We emit a DEBUG log before coercion so operators running with
        verbose logging can still catch caller bugs that would otherwise
        be invisible (HIGH-2 review fix).
        """
        if subtype in _VALID_EVENT_SUBTYPES:
            return subtype
        logger.debug(
            "unknown event_subtype %r coerced to 'ok'; "
            "valid subtypes are %s",
            subtype, sorted(_VALID_EVENT_SUBTYPES),
        )
        return "ok"

    def record(self, metric_name: str, value: float, labels: dict | None = None) -> None:
        """Append metric to daily JSONL file: metrics_dir/YYYY-MM-DD.jsonl."""
        entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "metric": metric_name,
            "value": value,
            "labels": labels or {},
        }
        path = self.today_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def record_gate_log(
        self,
        gate_log: Any,
        *,
        query_hash: str = "",
        mode: str = "unknown",
        config_version_id: str = "",
        fallback_triggered: bool = False,
    ) -> None:
        """Append a selective-fusion-gates log entry (D-3 invariant / I-8).

        Accepts either a `fusion.gates.GateLog` dataclass instance or a
        plain dict. The timestamp, query hash, mode (local / vps-cpu /
        vps-gpu), and config_version_id are attached at write time.

        `fallback_triggered=True` marks records where the retrieval layer
        overrode the gate verdict (e.g. gates rejected everything and
        fail-open returned the original pool). Without this flag, an
        operator reading the gate log would see `passed_delta=0` with no
        indication the result wasn't actually empty.

        Writes to a separate stream (`YYYY-MM-DD-gates.jsonl`) so the
        volume doesn't drown the metrics file. Errors are swallowed —
        observability must never degrade serving.
        """
        try:
            if is_dataclass(gate_log) and not isinstance(gate_log, type):
                payload = asdict(gate_log)
            elif isinstance(gate_log, dict):
                payload = dict(gate_log)
            else:
                # Unknown shape — coerce via vars() as a best effort
                payload = dict(vars(gate_log))
        except Exception:
            return

        entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "event": "fusion_gate",
            "query_hash": query_hash,
            "mode": mode,
            "config_version_id": config_version_id,
            "fallback_triggered": fallback_triggered,
            "log": payload,
        }
        self._append_jsonl(self.today_gates_path(), entry)

    # ------------------------------------------------------------------
    # v0.5 T-163 / S-53 — structured recall + capture streams
    # ------------------------------------------------------------------

    def record_recall_query(
        self,
        *,
        query_hash: str = "",
        mode: str = "unknown",
        backend_used: dict | None = None,
        backend_fallback_chain: dict | None = None,
        latency_ms_per_capability: dict | None = None,
        total_latency_ms: float | None = None,
        result_count: int | None = None,
        event_subtype: str = "ok",
        config_version_id: str = "",
    ) -> None:
        """Append a structured recall-query record to `YYYY-MM-DD-recall.jsonl`.

        Captures the full backend routing + per-capability latency for a
        single `depthfusion_recall_relevant` invocation. Called from the
        MCP server after the query completes (success or failure).

        Fields:
          - `query_hash`: sha256[:12] of the query — never log raw queries
          - `mode`: `local` / `vps-cpu` / `vps-gpu` / `unknown`
          - `backend_used`: {capability: backend_name} — e.g. {"reranker": "haiku"}
          - `backend_fallback_chain`: {capability: [name1, name2]} — the
            cascade attempted, including final fallback (usually "null")
          - `latency_ms_per_capability`: {capability: latency_float}
          - `total_latency_ms`: end-to-end query latency
          - `result_count`: number of blocks returned to the caller
          - `event_subtype`: "ok" | "error" | "timeout" | "sla_expiry_deny"
            | "user_deny" | "acs_reject". Unknown values coerce to "ok".
          - `config_version_id`: snapshot pointer (I-11 amended); "" sentinel
            when config tracking isn't wired

        Errors are swallowed.
        """
        entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "event": "recall_query",
            "event_subtype": self._validate_event_subtype(event_subtype),
            "query_hash": query_hash,
            "mode": mode,
            "backend_used": backend_used or {},
            "backend_fallback_chain": backend_fallback_chain or {},
            "latency_ms_per_capability": latency_ms_per_capability or {},
            "total_latency_ms": total_latency_ms,
            "result_count": result_count,
            "config_version_id": config_version_id,
        }
        self._append_jsonl(self.today_recall_path(), entry)

    def record_capture_event(
        self,
        *,
        capture_mechanism: str,
        project: str = "unknown",
        session_id: str = "",
        write_success: bool = True,
        entries_written: int = 0,
        file_path: str = "",
        event_subtype: str = "ok",
        config_version_id: str = "",
    ) -> None:
        """Append a capture-mechanism event to `YYYY-MM-DD-capture.jsonl`.

        Records a single write attempt from one of the v0.5 capture
        mechanisms (decision extractor, negative extractor, dedup, git
        post-commit hook, confirm_discovery MCP tool).

        `capture_write_rate` in aggregator summaries is computed from
        these records as `write_success` count / total per mechanism.

        `capture_mechanism` is validated against the enumeration of known
        mechanisms — unknown values are preserved on disk (for
        forensics) but the aggregator won't bucket them as known streams.

        Errors are swallowed.
        """
        entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "event": "capture",
            "event_subtype": self._validate_event_subtype(event_subtype),
            "capture_mechanism": capture_mechanism,
            "capture_mechanism_known": capture_mechanism in _VALID_CAPTURE_MECHANISMS,
            "project": project,
            "session_id": session_id,
            "write_success": write_success,
            "entries_written": entries_written,
            "file_path": file_path,
            "config_version_id": config_version_id,
        }
        self._append_jsonl(self.today_capture_path(), entry)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def today_path(self) -> Path:
        """Return path to today's metrics file."""
        today = date.today().isoformat()
        return self.metrics_dir / f"{today}.jsonl"

    def today_gates_path(self) -> Path:
        """Return path to today's gate-log file (separate stream from metrics)."""
        today = date.today().isoformat()
        return self.metrics_dir / f"{today}-gates.jsonl"

    def today_recall_path(self) -> Path:
        """Return path to today's recall-query file (structured per-query stream)."""
        today = date.today().isoformat()
        return self.metrics_dir / f"{today}-recall.jsonl"

    def today_capture_path(self) -> Path:
        """Return path to today's capture-event file (capture-mechanism stream)."""
        today = date.today().isoformat()
        return self.metrics_dir / f"{today}-capture.jsonl"
