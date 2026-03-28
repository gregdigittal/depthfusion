"""MetricsAggregator — summarizes daily metrics from JSONL files."""
from __future__ import annotations

import json
from datetime import date

from depthfusion.metrics.collector import MetricsCollector


class MetricsAggregator:
    """Reads and aggregates daily metrics from the collector's JSONL files."""

    def __init__(self, collector: MetricsCollector) -> None:
        self.collector = collector

    def daily_summary(self, target_date: date | None = None) -> dict:
        """Summarize metrics for target_date (default: today).

        Returns {metric_name: {count, sum, avg, min, max}}.
        """
        if target_date is None:
            target_date = date.today()

        file_path = self.collector.metrics_dir / f"{target_date.isoformat()}.jsonl"
        if not file_path.exists():
            return {}

        # Collect all values per metric
        buckets: dict[str, list[float]] = {}
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    name = entry["metric"]
                    value = float(entry["value"])
                    buckets.setdefault(name, []).append(value)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        summary: dict[str, dict] = {}
        for metric_name, values in buckets.items():
            count = len(values)
            total = sum(values)
            summary[metric_name] = {
                "count": count,
                "sum": total,
                "avg": total / count if count > 0 else 0.0,
                "min": min(values),
                "max": max(values),
            }
        return summary

    def format_for_digest(self, summary: dict) -> str:
        """Format summary as markdown for inclusion in daily digest."""
        if not summary:
            return "## DepthFusion Metrics\n\nNo metrics recorded for this period.\n"

        lines = ["## DepthFusion Metrics\n"]
        for metric_name, stats in sorted(summary.items()):
            lines.append(f"### `{metric_name}`")
            lines.append(f"- Count: {stats['count']}")
            lines.append(f"- Sum: {stats['sum']:.4f}")
            lines.append(f"- Avg: {stats['avg']:.4f}")
            lines.append(f"- Min: {stats['min']:.4f}")
            lines.append(f"- Max: {stats['max']:.4f}")
            lines.append("")

        return "\n".join(lines)
