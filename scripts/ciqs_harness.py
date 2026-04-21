#!/usr/bin/env python3
"""CIQS benchmark harness (S-63 T-199).

Drives the 5-category CIQS benchmark battery through DepthFusion and
records per-prompt artefacts for scoring.

Two subcommands:

  run     - execute the battery against the CURRENT DepthFusion
            installation, capture retrieval output (Category A) and
            prompt-context for B/C/D/E, then emit:
              * docs/benchmarks/{date}-{mode}-run{N}-raw.jsonl
              * docs/benchmarks/{date}-{mode}-run{N}-scoring.md
            Category A is fully automated (retrieval is deterministic
            enough to score directly). B/C/D/E produce a scoring
            template the operator (or a judge model) fills in.

  score   - merge a filled-in scoring markdown back into the raw
            JSONL, producing {date}-{mode}-run{N}-scored.jsonl

Aggregation across runs happens in scripts/ciqs_summarise.py.

Usage:
    python scripts/ciqs_harness.py run \\
        --battery docs/benchmarks/prompts/ciqs-battery.yaml \\
        --mode local --run 1

    # operator fills in -scoring.md

    python scripts/ciqs_harness.py score \\
        --raw docs/benchmarks/2026-04-21-local-run1-raw.jsonl \\
        --scoring docs/benchmarks/2026-04-21-local-run1-scoring.md

Design note: this harness uses the Python API
(depthfusion.mcp.server._tool_recall) rather than shelling out to the
MCP server. That keeps the measurement loop tight and avoids RPC-layer
variance in latency numbers. The RPC path is separately exercised by
tests/test_mcp_server.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml required. `pip install pyyaml`", file=sys.stderr)
    sys.exit(2)


# --------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------

@dataclass
class PromptSpec:
    """One topic within one category."""
    category_id: str
    category_name: str
    topic_id: str            # e.g. "A1"
    topic_value: str         # the substitution value
    rendered_prompt: str     # full prompt after template substitution
    retrieval_only: bool
    rubric_dims: list[str]
    max_score: int


@dataclass
class RawRecord:
    """One line of the raw JSONL - pre-scoring."""
    category_id: str
    topic_id: str
    prompt: str
    retrieval_blocks: list[dict] = field(default_factory=list)
    retrieval_error: str | None = None
    scores: dict[str, int] | None = None  # filled by `score` subcommand
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "category_id": self.category_id,
            "topic_id": self.topic_id,
            "prompt": self.prompt,
            "retrieval_blocks": self.retrieval_blocks,
            "retrieval_error": self.retrieval_error,
            "scores": self.scores,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# Battery parsing
# --------------------------------------------------------------------------

def load_battery(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"battery file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def expand_battery(battery: dict[str, Any]) -> list[PromptSpec]:
    """Flatten the YAML battery into PromptSpecs per (category, topic)."""
    specs: list[PromptSpec] = []
    for cat in battery.get("categories", []):
        cid = cat["id"]
        cname = cat["name"]
        template: str = cat["template"]
        retrieval_only: bool = cat.get("retrieval_only", False)
        rubric_dims = list(cat["rubric"].keys())
        max_score = int(cat["max_score_per_run"])

        for topic_id, topic_value in cat["topics"].items():
            rendered = _substitute_template(template, topic_value)
            specs.append(PromptSpec(
                category_id=cid,
                category_name=cname,
                topic_id=topic_id,
                topic_value=topic_value,
                rendered_prompt=rendered,
                retrieval_only=retrieval_only,
                rubric_dims=rubric_dims,
                max_score=max_score,
            ))
    return specs


_TEMPLATE_VARS = ("topic", "snippet", "task_description", "continuity_question", "task")


def _substitute_template(template: str, value: str) -> str:
    """Substitute the first {var} placeholder that appears in `template`."""
    for var in _TEMPLATE_VARS:
        placeholder = "{" + var + "}"
        if placeholder in template:
            return template.replace(placeholder, value)
    return template


# --------------------------------------------------------------------------
# Retrieval execution
# --------------------------------------------------------------------------

def run_retrieval(prompt: str, top_k: int = 5) -> tuple[list[dict], str | None]:
    """Call DepthFusion's recall tool and return (blocks, error).

    Uses the Python API directly. Returns ([], error_str) on failure -
    never raises; the caller logs the error into the raw record.
    """
    try:
        from depthfusion.mcp.server import _tool_recall
    except ImportError as err:
        return [], f"import error: {err}"

    try:
        response_json = _tool_recall({
            "query": prompt,
            "top_k": top_k,
            "cross_project": True,
        })
    except Exception as err:  # noqa: BLE001 - harness must not crash
        return [], f"recall failed: {type(err).__name__}: {err}"

    try:
        parsed = json.loads(response_json) if response_json else {}
    except json.JSONDecodeError as err:
        return [], f"response not JSON: {err}"

    return parsed.get("blocks", []) or [], None


# --------------------------------------------------------------------------
# Subcommand: run
# --------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    battery_path = Path(args.battery)
    battery = load_battery(battery_path)
    specs = expand_battery(battery)

    mode = args.mode
    run_n = args.run
    today = date.today().isoformat()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / f"{today}-{mode}-run{run_n}-raw.jsonl"
    scoring_path = out_dir / f"{today}-{mode}-run{run_n}-scoring.md"

    if raw_path.exists() and not args.force:
        print(f"ERROR: {raw_path} exists; rerun with --force to overwrite", file=sys.stderr)
        return 2

    records: list[RawRecord] = []
    for spec in specs:
        record = RawRecord(
            category_id=spec.category_id,
            topic_id=spec.topic_id,
            prompt=spec.rendered_prompt,
        )
        if spec.retrieval_only:
            blocks, err = run_retrieval(spec.rendered_prompt, top_k=args.top_k)
            record.retrieval_blocks = blocks
            record.retrieval_error = err
        records.append(record)

    with open(raw_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec.to_dict()) + "\n")

    write_scoring_template(scoring_path, specs, records, mode=mode, run_n=run_n)

    print(f"Raw output:       {raw_path}")
    print(f"Scoring template: {scoring_path}")
    print()
    print(f"Next: fill in scores in {scoring_path.name}, then run:")
    print(f"  python scripts/ciqs_harness.py score \\")
    print(f"      --raw {raw_path} --scoring {scoring_path}")
    return 0


def write_scoring_template(
    path: Path,
    specs: list[PromptSpec],
    records: list[RawRecord],
    mode: str,
    run_n: int,
) -> None:
    lines: list[str] = []
    lines.append(f"# CIQS Scoring Template - {mode} / run {run_n}")
    lines.append("")
    lines.append(f"> Generated: {date.today().isoformat()}")
    lines.append("> DepthFusion version: " + _get_df_version())
    lines.append("")
    lines.append("Fill in an integer score (0-10) in each `score: ` line.")
    lines.append("Categories with `retrieval-only: true` have blocks from DepthFusion below the prompt.")
    lines.append("Other categories require running the prompt through Claude Code in a fresh session")
    lines.append("and scoring the response against the rubric.")
    lines.append("")
    lines.append("---")
    lines.append("")

    for spec, rec in zip(specs, records):
        lines.append(f"## {spec.category_id} / {spec.topic_id} - {spec.category_name}")
        lines.append("")
        lines.append("**Prompt:**")
        lines.append("")
        lines.append("```")
        lines.append(spec.rendered_prompt.rstrip())
        lines.append("```")
        lines.append("")
        lines.append(f"retrieval-only: {spec.retrieval_only}")
        lines.append("")

        if spec.retrieval_only:
            if rec.retrieval_error:
                lines.append(f"**Retrieval error:** `{rec.retrieval_error}`")
                lines.append("")
            elif not rec.retrieval_blocks:
                lines.append("**Retrieval returned 0 blocks.**")
                lines.append("")
            else:
                lines.append(f"**Retrieved {len(rec.retrieval_blocks)} blocks:**")
                lines.append("")
                for i, b in enumerate(rec.retrieval_blocks, 1):
                    src = b.get("source", "?")
                    stem = b.get("file_stem", b.get("chunk_id", "?"))
                    score = b.get("score", "?")
                    snippet = (b.get("snippet", "") or "")[:240]
                    lines.append(f"{i}. `{src}` / `{stem}` (score={score})")
                    lines.append(f"   > {snippet}")
                lines.append("")

        lines.append("**Scores (0-10 each):**")
        lines.append("")
        for dim in spec.rubric_dims:
            lines.append(f"- {dim}: `score: `")
        lines.append("")
        lines.append("**Notes:** ")
        lines.append("")
        lines.append("---")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _get_df_version() -> str:
    try:
        import depthfusion
        return getattr(depthfusion, "__version__", "unknown")
    except ImportError:
        return "not-installed"


# --------------------------------------------------------------------------
# Subcommand: score
# --------------------------------------------------------------------------

# Section header: accept any single letter + topic ID like X9; validation
# of category membership happens at the caller level. Loosening the regex
# avoids silent-skip when the battery grows past category E.
_SCORE_LINE = re.compile(r"^-\s+(\w+):\s*`score:\s*(\d+)`", re.MULTILINE)
_SECTION_HEADER = re.compile(r"^## (?P<cat>[A-Z]) / (?P<topic>[A-Z]\d+) -", re.MULTILINE)


def parse_scoring_template(text: str) -> dict[str, dict[str, int]]:
    """Parse a filled-in scoring template.

    Returns {topic_id: {dim: score}}. Raises ValueError only on
    OUT-OF-RANGE integers (>10 or <0). Missing/unfilled scores and
    non-digit values (e.g. `score: seven`) are silently skipped — they
    simply don't match the regex. Callers that need to enforce
    completeness (like cmd_score) check for missing topics separately.
    """
    sections: dict[str, str] = {}
    last_topic: str | None = None
    last_start = 0
    for m in _SECTION_HEADER.finditer(text):
        if last_topic is not None:
            sections[last_topic] = text[last_start:m.start()]
        last_topic = m.group("topic")
        last_start = m.end()
    if last_topic is not None:
        sections[last_topic] = text[last_start:]

    result: dict[str, dict[str, int]] = {}
    for topic_id, body in sections.items():
        scores: dict[str, int] = {}
        for mm in _SCORE_LINE.finditer(body):
            dim = mm.group(1)
            raw = mm.group(2)
            try:
                val = int(raw)
            except ValueError as err:
                raise ValueError(f"{topic_id}/{dim}: not an integer: {raw!r}") from err
            if not 0 <= val <= 10:
                raise ValueError(f"{topic_id}/{dim}: out of 0-10 range: {val}")
            scores[dim] = val
        if scores:
            result[topic_id] = scores
    return result


def _derive_scored_path(raw_path: Path) -> Path:
    """Given `{date}-{mode}-run{N}-raw.jsonl`, return `...-scored.jsonl`.

    Rejects inputs that don't end in the expected suffix rather than
    silently producing an output path identical to the input (which
    would overwrite the raw file on the next `write`).
    """
    stem = raw_path.stem  # strips .jsonl
    if not stem.endswith("-raw"):
        raise ValueError(
            f"expected raw file to end with '-raw.jsonl', got {raw_path.name!r}"
        )
    new_stem = stem[:-len("-raw")] + "-scored"
    return raw_path.with_name(new_stem + raw_path.suffix)


def cmd_score(args: argparse.Namespace) -> int:
    raw_path = Path(args.raw)
    scoring_path = Path(args.scoring)
    try:
        out_path = _derive_scored_path(raw_path)
    except ValueError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2

    if not raw_path.exists():
        print(f"ERROR: raw file not found: {raw_path}", file=sys.stderr)
        return 2
    if not scoring_path.exists():
        print(f"ERROR: scoring file not found: {scoring_path}", file=sys.stderr)
        return 2

    scoring_text = scoring_path.read_text(encoding="utf-8")
    try:
        scores_by_topic = parse_scoring_template(scoring_text)
    except ValueError as err:
        print(f"ERROR parsing scoring template: {err}", file=sys.stderr)
        return 2

    raw_records: list[dict] = []
    with open(raw_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_records.append(json.loads(line))

    missing: list[str] = []
    for rec in raw_records:
        topic_id = rec["topic_id"]
        scores = scores_by_topic.get(topic_id)
        if not scores:
            missing.append(topic_id)
            continue
        rec["scores"] = scores

    if missing:
        print(f"ERROR: missing scores for topics: {', '.join(missing)}", file=sys.stderr)
        print("(Fill them in then re-run `score`.)", file=sys.stderr)
        return 2

    with open(out_path, "w", encoding="utf-8") as f:
        for rec in raw_records:
            f.write(json.dumps(rec) + "\n")

    print(f"Scored output: {out_path}")
    print(f"{len(raw_records)} records scored.")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CIQS benchmark harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Execute the battery and emit raw JSONL + scoring template")
    p_run.add_argument("--battery", default="docs/benchmarks/prompts/ciqs-battery.yaml")
    p_run.add_argument("--mode", required=True, choices=("local", "vps-cpu", "vps-gpu"),
                       help="The DepthFusion mode in use (for output filename; does not switch modes)")
    p_run.add_argument("--run", type=int, required=True, help="Run number (1..N)")
    p_run.add_argument("--out-dir", default="docs/benchmarks")
    p_run.add_argument("--top-k", type=int, default=5, help="top_k for Category A retrieval")
    p_run.add_argument("--force", action="store_true", help="Overwrite existing output")
    p_run.set_defaults(func=cmd_run)

    p_score = sub.add_parser("score", help="Merge a filled-in scoring template into raw JSONL")
    p_score.add_argument("--raw", required=True)
    p_score.add_argument("--scoring", required=True)
    p_score.set_defaults(func=cmd_score)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
