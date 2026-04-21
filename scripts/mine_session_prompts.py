#!/usr/bin/env python3
"""Mine real user prompts from Claude Code session history.

Produces a prompt corpus suitable for expanding the CIQS battery or
any other DepthFusion evaluation. Draws from `~/.claude/projects/`
where Claude Code stores per-session JSONL transcripts.

Rationale:
  LLM-synthesised prompts drift from the distribution of real prompts
  a particular user actually sends. Mining actual session history gives
  a corpus that's already in-distribution for the system under test.
  Privacy cost is zero (data is the user's own); license cost is zero
  (same reason).

Usage:
    python scripts/mine_session_prompts.py \\
        --sessions-dir ~/.claude/projects/ \\
        --min-chars 20 \\
        --out docs/eval-sets/session-mined/corpus-$(date +%F).jsonl

What's kept:
  * `type: user` records with `message.content` as a STRING (tool
    results come through as arrays — those are dropped).
  * Prompts >= --min-chars after stripping.
  * De-duplicated exactly (sha256 of normalised text) by default;
    near-dup dedup available via --dedupe fuzzy (requires
    sentence-transformers, optional).

What's dropped:
  * `type: user` with array content (tool results, structured inputs).
  * Messages starting with `<command-message>`, `<system-reminder>`,
    or `<local-command-stdout>` — auto-generated wrappers, not the
    user's authored text.
  * Single-word acknowledgments under the length threshold.
  * Exact duplicates (common — operators repeat "proceed", "yes").

Redaction:
  * `--redact` takes a regex; every match is replaced with `[REDACTED]`.
  * Default pattern catches common secrets (OpenAI, Anthropic, AWS,
    GitHub). Callers can extend or override.

Output (one line per kept prompt):
    {
      "prompt": "string",
      "session_id": "uuid",
      "project_slug": "-home-gregmorris-projects-...",
      "timestamp": "2026-04-16T20:59:56.106Z",
      "content_length": 1234
    }

Spec: S-64 T-202 data-sourcing component (extends the gold-set
scaffolding with real examples). Also feeds S-65 dogfood analysis as
the "what do we actually ask" corpus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------

# Wrappers that indicate the message is auto-generated, not user-authored.
_WRAPPER_PREFIXES = (
    "<command-message>",
    "<command-name>",  # sometimes appears first
    "<system-reminder>",
    "<local-command-stdout>",
    "<user-prompt-submit-hook>",
)

# Default redaction: common secret shapes. Conservative — broad patterns
# would eat legitimate code; narrow patterns are safer. L-5 fix: removed
# `sk-ant-...` pattern because `sk-...` preceded it in the alternation and
# matched first (making the explicit Anthropic branch dead code).
_DEFAULT_REDACT = "|".join([
    r"sk-[A-Za-z0-9_-]{20,}",       # OpenAI / Anthropic / generic sk- keys
    r"AKIA[0-9A-Z]{16}",            # AWS access key ID
    r"aws_secret_access_key\s*=\s*['\"]?[A-Za-z0-9/+=]{40}",  # AWS secret
    r"ghp_[A-Za-z0-9]{36}",         # GitHub personal access token
    r"gho_[A-Za-z0-9]{36}",         # GitHub OAuth token
    r"xoxb-[A-Za-z0-9-]{50,}",      # Slack bot token
    r"xoxp-[A-Za-z0-9-]{50,}",      # Slack user token
])


def is_wrapper_message(text: str) -> bool:
    """True if the message is auto-generated command/reminder boilerplate."""
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _WRAPPER_PREFIXES)


def normalise_for_hash(text: str) -> str:
    """Lowercased, whitespace-collapsed form for exact-dup detection."""
    return re.sub(r"\s+", " ", text.strip().lower())


def apply_redaction(text: str, pattern: re.Pattern[str] | None) -> tuple[str, int]:
    """Return (redacted_text, n_matches). n_matches=0 if no pattern."""
    if pattern is None:
        return text, 0
    matches = pattern.findall(text)
    redacted = pattern.sub("[REDACTED]", text)
    return redacted, len(matches)


# --------------------------------------------------------------------------
# Session walking
# --------------------------------------------------------------------------

def iter_session_files(sessions_dir: Path) -> Iterator[Path]:
    """Yield every `*.jsonl` under `sessions_dir`, recursively.

    Claude Code nests sessions under `~/.claude/projects/<project>/*.jsonl`
    with subagent transcripts under `<project>/<uuid>/subagents/*.jsonl`.
    We walk recursively so both primary and subagent traffic are eligible.
    """
    if not sessions_dir.exists():
        logger.warning("sessions dir does not exist: %s", sessions_dir)
        return
    yield from sessions_dir.rglob("*.jsonl")


def project_slug_from_path(path: Path, sessions_dir: Path) -> str:
    """Extract the project slug (first path component under sessions_dir)."""
    try:
        rel = path.relative_to(sessions_dir)
    except ValueError:
        return "(unknown)"
    parts = rel.parts
    return parts[0] if parts else "(root)"


def extract_user_prompts_from_file(
    file_path: Path,
    project_slug: str,
    min_chars: int,
) -> Iterator[dict]:
    """Yield candidate prompt records from a single session JSONL.

    Records are filtered to user-authored strings meeting the length
    threshold. Wrapper messages and structured (array) content are
    skipped. Downstream code handles dedup, redaction, and output.
    """
    try:
        with file_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("%s:%d: invalid JSON, skipping", file_path, line_num)
                    continue

                if rec.get("type") != "user":
                    continue
                message = rec.get("message") or {}
                content = message.get("content")
                if not isinstance(content, str):
                    # Array content = tool result or structured; not user-authored text.
                    continue
                text = content.strip()
                if len(text) < min_chars:
                    continue
                if is_wrapper_message(text):
                    continue

                yield {
                    "prompt": text,
                    "session_id": rec.get("sessionId") or rec.get("session_id") or "",
                    "project_slug": project_slug,
                    "timestamp": rec.get("timestamp") or "",
                    "content_length": len(text),
                }
    except OSError as exc:
        logger.warning("could not read %s: %s", file_path, exc)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def mine_prompts(
    sessions_dir: Path,
    min_chars: int,
    redact_pattern: re.Pattern[str] | None,
    project_filter: str | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Run the full mining pipeline. Returns (records, stats_dict)."""
    stats = {
        "files_scanned": 0,
        "candidates": 0,
        "kept": 0,
        "dropped_duplicate": 0,
        "dropped_project_filter": 0,
        "redactions_applied": 0,
    }
    seen_hashes: set[str] = set()
    kept: list[dict] = []

    for path in iter_session_files(sessions_dir):
        stats["files_scanned"] += 1
        project_slug = project_slug_from_path(path, sessions_dir)
        if project_filter and project_filter not in project_slug:
            # M-4 fix: the project-filter stat was initialised but never
            # incremented, making "files_scanned vs dropped" reconciliation
            # wrong. Now tracks filtered-out files by name.
            stats["dropped_project_filter"] += 1
            continue

        for candidate in extract_user_prompts_from_file(
            path, project_slug, min_chars
        ):
            stats["candidates"] += 1

            # Redact BEFORE dedup so secrets-only-differing prompts collapse
            redacted_text, n_redactions = apply_redaction(
                candidate["prompt"], redact_pattern
            )
            candidate["prompt"] = redacted_text
            candidate["content_length"] = len(redacted_text)
            stats["redactions_applied"] += n_redactions

            h = hashlib.sha256(
                normalise_for_hash(redacted_text).encode("utf-8")
            ).hexdigest()
            if h in seen_hashes:
                stats["dropped_duplicate"] += 1
                continue
            seen_hashes.add(h)

            kept.append(candidate)
            stats["kept"] += 1

    return kept, stats


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine user prompts from Claude Code sessions")
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Root directory containing per-project session JSONLs "
             "(default: ~/.claude/projects/)",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=20,
        help="Minimum prompt length after stripping (default: 20)",
    )
    parser.add_argument(
        "--redact",
        default=_DEFAULT_REDACT,
        help="Regex pattern to redact. Pass empty string to disable. "
             "Default covers common OpenAI/Anthropic/AWS/GitHub/Slack token shapes.",
    )
    parser.add_argument(
        "--project-filter",
        default=None,
        help="If set, only include prompts whose project_slug contains this substring",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output JSONL path (default: stdout)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Log at INFO level",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    redact_pattern: re.Pattern[str] | None = None
    if args.redact:
        try:
            redact_pattern = re.compile(args.redact)
        except re.error as exc:
            print(f"ERROR: invalid --redact regex: {exc}", file=sys.stderr)
            return 2

    kept, stats = mine_prompts(
        sessions_dir=args.sessions_dir,
        min_chars=args.min_chars,
        redact_pattern=redact_pattern,
        project_filter=args.project_filter,
    )

    # Write output
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            for rec in kept:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"Wrote {len(kept)} prompts to {args.out}")
    else:
        for rec in kept:
            print(json.dumps(rec, ensure_ascii=False))

    # Stats summary to stderr so the output stream stays clean for pipes
    print(
        f"\n--- Mining summary ---\n"
        f"Files scanned:              {stats['files_scanned']}\n"
        f"Dropped (project filter):   {stats['dropped_project_filter']}\n"
        f"Candidate prompts:          {stats['candidates']}\n"
        f"Kept (unique):              {stats['kept']}\n"
        f"Dropped as duplicate:       {stats['dropped_duplicate']}\n"
        f"Redactions applied:         {stats['redactions_applied']}\n",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
