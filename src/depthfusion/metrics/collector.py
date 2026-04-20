"""MetricsCollector — records metrics and gate logs to daily JSONL files.

v0.5.0 T-158 / S-51: extended with `record_gate_log()` for the Mamba B/C/Δ
selective fusion gates. Gate logs are a D-3 invariant — emitted per query
regardless of whether any block was rejected — and are written to a
separate daily file (`YYYY-MM-DD-gates.jsonl`) so the main metrics stream
stays readable.

Gate-log records carry a `config_version_id` field per I-8 ratification
(see docs/plans/v0.5/03-skillforge-integration.md §3.3.5 action 2). The
default empty string is the sentinel for "no config snapshot tracking
wired yet"; callers that track config versions override it.

Concurrency: JSONL append is guarded by `fcntl.flock` (POSIX) so parallel
pipeline threads can't interleave gate-log entries past the 4 KiB
PIPE_BUF atomicity window. Gate-log entries contain the full `decisions`
list and frequently exceed 4 KiB for realistic candidate pool sizes.
"""
from __future__ import annotations

import fcntl
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


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


class MetricsCollector:
    """Records metrics and gate logs to daily JSONL files in metrics_dir.

    Each record is a JSON object appended to YYYY-MM-DD.jsonl.
    Gate logs go to YYYY-MM-DD-gates.jsonl (separate stream).
    """

    def __init__(self, metrics_dir: Path | None = None) -> None:
        if metrics_dir is None:
            metrics_dir = Path.home() / ".claude" / "depthfusion-metrics"
        self.metrics_dir = metrics_dir
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

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
        try:
            path = self.today_gates_path()
            # Guard against concurrent-writer interleaving: gate-log entries
            # can exceed 4 KiB PIPE_BUF, so O_APPEND alone is not atomic.
            # fcntl.flock is advisory but sufficient for the single-process
            # multi-thread case, and a no-op on Windows (which we don't
            # ship to — but the try/except covers it anyway).
            with open(path, "a", encoding="utf-8") as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                except OSError:
                    # flock unsupported (some filesystems) — best-effort only
                    pass
                f.write(json.dumps(entry, default=_json_default) + "\n")
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        except OSError:
            # Filesystem full / permissions — swallow silently
            return

    def today_path(self) -> Path:
        """Return path to today's metrics file."""
        today = date.today().isoformat()
        return self.metrics_dir / f"{today}.jsonl"

    def today_gates_path(self) -> Path:
        """Return path to today's gate-log file (separate stream from metrics)."""
        today = date.today().isoformat()
        return self.metrics_dir / f"{today}-gates.jsonl"
