"""DepthFusion installer — configures hooks and environment for local or VPS mode."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

HOOKS_DIR = Path.home() / ".claude" / "hooks"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

_LOCAL_ENV_LINES = [
    "DEPTHFUSION_MODE=local",
    "DEPTHFUSION_TIER_AUTOPROMOTE=false",
    "DEPTHFUSION_GRAPH_ENABLED=true",
]
_VPS_ENV_LINES = [
    "DEPTHFUSION_MODE=vps",
    "DEPTHFUSION_TIER_AUTOPROMOTE=true",
    "DEPTHFUSION_RERANKER_ENABLED=true",
    "DEPTHFUSION_GRAPH_ENABLED=true",
]


def _print_step(msg: str, dry_run: bool = False) -> None:
    prefix = "[DRY-RUN]" if dry_run else "[INSTALL]"
    print(f"{prefix} {msg}")


def install_local(dry_run: bool = False) -> None:
    _print_step("Configuring DepthFusion for LOCAL mode", dry_run)
    _print_step("  - BM25 retrieval: enabled", dry_run)
    _print_step("  - Haiku reranker: DISABLED (no API calls in local mode)", dry_run)
    _print_step("  - PostCompact hook: heuristic extraction only", dry_run)
    _print_step("  - ChromaDB: not required", dry_run)
    if not dry_run:
        _write_env_config(_LOCAL_ENV_LINES)
        _register_hooks()
    _print_step("Local install complete.", dry_run)
    _print_step("Add to your environment: DEPTHFUSION_MODE=local", dry_run)


def install_vps(dry_run: bool = False, tier_threshold: int = 500) -> None:
    _print_step(f"Configuring DepthFusion for VPS mode (tier threshold: {tier_threshold})", dry_run)
    _print_step("  - BM25 retrieval: enabled", dry_run)
    _print_step("  - Haiku reranker (Tier 1): enabled (requires ANTHROPIC_API_KEY)", dry_run)
    _print_step(f"  - ChromaDB vector store (Tier 2): enabled at {tier_threshold}+ sessions", dry_run)
    _print_step("  - PreCompact + PostCompact auto-capture hooks: enabled", dry_run)
    if not dry_run:
        env_lines = _VPS_ENV_LINES.copy()
        env_lines.append(f"DEPTHFUSION_TIER_THRESHOLD={tier_threshold}")
        _write_env_config(env_lines)
        _register_hooks()
        _check_anthropic_key()
    _print_step("VPS install complete.", dry_run)
    _print_step("Ensure ANTHROPIC_API_KEY is set for haiku reranker.", dry_run)


def _write_env_config(lines: list[str]) -> None:
    env_file = Path.home() / ".claude" / "depthfusion.env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Wrote config: {env_file}")


def _register_hooks() -> None:
    if not SETTINGS_PATH.exists():
        print(f"  Warning: {SETTINGS_PATH} not found, skipping hook registration")
        return
    with open(SETTINGS_PATH) as f:
        settings = json.load(f)
    hooks = settings.setdefault("hooks", {})
    for event, script in [
        ("PreCompact", "depthfusion-pre-compact.sh"),
        ("PostCompact", "depthfusion-post-compact.sh"),
    ]:
        script_path = HOOKS_DIR / script
        if not script_path.exists():
            print(f"  Warning: {script_path} not found — skipping {event} hook")
            continue
        existing = hooks.get(event, [])
        cmd = f"bash {script_path}"
        already_registered = any(
            h.get("command") == cmd or
            any(ih.get("command") == cmd for ih in h.get("hooks", []))
            for h in existing
        )
        if not already_registered:
            hooks.setdefault(event, []).append(
                {"hooks": [{"type": "command", "command": cmd}]}
            )
            print(f"  Registered {event} hook: {script}")
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


def _check_anthropic_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  Warning: ANTHROPIC_API_KEY not set. Haiku reranker will be disabled.")
        print("  Set it with: export ANTHROPIC_API_KEY=sk-...")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DepthFusion installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mode", choices=["local", "vps"], required=True,
                        help="Install mode: 'local' (no API calls) or 'vps' (haiku + ChromaDB)")
    parser.add_argument("--tier-threshold", type=int, default=500,
                        help="Session count threshold for Tier 2 promotion (default: 500)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be done without making changes")
    args = parser.parse_args()

    if args.mode == "local":
        install_local(dry_run=args.dry_run)
    else:
        install_vps(dry_run=args.dry_run, tier_threshold=args.tier_threshold)


if __name__ == "__main__":
    main()
