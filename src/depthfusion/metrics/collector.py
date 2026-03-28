"""MetricsCollector — records metrics to daily JSONL files."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path


class MetricsCollector:
    """Records metrics to daily JSONL files in metrics_dir.

    Each record is a JSON object appended to YYYY-MM-DD.jsonl.
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

    def today_path(self) -> Path:
        """Return path to today's metrics file."""
        today = date.today().isoformat()
        return self.metrics_dir / f"{today}.jsonl"
