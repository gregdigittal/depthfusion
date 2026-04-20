"""DepthFusion installer — configures hooks + environment for one of three modes.

v0.5.0 T-124/S-42: the legacy two-mode split (`local`/`vps`) is replaced
with three modes:

  * `local`    — BM25-only, no API calls, no GPU dependencies
  * `vps-cpu`  — Haiku reranker + optional ChromaDB (former `vps` mode)
  * `vps-gpu`  — Gemma on-box + local embeddings; requires an NVIDIA GPU

`--mode=vps` is retained as a deprecated alias for `--mode=vps-cpu` to
avoid breaking existing operator scripts. A deprecation warning is
printed every time the alias is used.

Env file byte-identity contract (S-42 AC-6): `install_local(dry_run=False)`
produces `~/.claude/depthfusion.env` with exactly the v0.4.x content.
Changes to `_LOCAL_ENV_LINES` break the regression test on purpose —
they require a coordinated release note.

Backlog: T-124 (argparse), T-127 (smoke test integration point),
T-128 (byte-identity regression)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from depthfusion.install.gpu_probe import detect_gpu


def _hooks_dir() -> Path:
    """Return the ~/.claude/hooks directory, resolved at call time.

    Runtime resolution (rather than module-level) lets tests redirect
    `Path.home()` via monkeypatch after the module is imported, which is
    impossible for precomputed `Path` objects frozen at import time.
    """
    return Path.home() / ".claude" / "hooks"


def _settings_path() -> Path:
    """Return ~/.claude/settings.json, resolved at call time (see _hooks_dir)."""
    return Path.home() / ".claude" / "settings.json"

# v0.4.x env contract — must stay byte-identical for the local mode (S-42 AC-6).
_LOCAL_ENV_LINES = [
    "DEPTHFUSION_MODE=local",
    "DEPTHFUSION_TIER_AUTOPROMOTE=false",
    "DEPTHFUSION_GRAPH_ENABLED=true",
]

# vps-cpu: Haiku-backed reranker/extractor/linker/summariser + optional ChromaDB.
# Mirrors the v0.4.x `vps` mode byte-for-byte apart from the MODE value itself.
_VPS_CPU_ENV_LINES = [
    "DEPTHFUSION_MODE=vps-cpu",
    "DEPTHFUSION_TIER_AUTOPROMOTE=true",
    "DEPTHFUSION_RERANKER_ENABLED=true",
    "DEPTHFUSION_GRAPH_ENABLED=true",
]

# vps-gpu: Gemma on-box + sentence-transformers embeddings. The per-capability
# backend env vars let the factory route the heavy LLM work to Gemma while
# keeping embeddings local for latency.
_VPS_GPU_ENV_LINES = [
    "DEPTHFUSION_MODE=vps-gpu",
    "DEPTHFUSION_TIER_AUTOPROMOTE=true",
    "DEPTHFUSION_RERANKER_ENABLED=true",
    "DEPTHFUSION_GRAPH_ENABLED=true",
    "DEPTHFUSION_EMBEDDING_BACKEND=local",
]


def _print_step(msg: str, dry_run: bool = False) -> None:
    prefix = "[DRY-RUN]" if dry_run else "[INSTALL]"
    print(f"{prefix} {msg}")


# ---------------------------------------------------------------------------
# Per-mode install paths
# ---------------------------------------------------------------------------

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


def install_vps_cpu(dry_run: bool = False, tier_threshold: int = 500) -> None:
    _print_step(
        f"Configuring DepthFusion for VPS-CPU mode (tier threshold: {tier_threshold})",
        dry_run,
    )
    _print_step("  - BM25 retrieval: enabled", dry_run)
    _print_step(
        "  - Haiku reranker (Tier 1): opt-in "
        "(set DEPTHFUSION_HAIKU_ENABLED=true + DEPTHFUSION_API_KEY)",
        dry_run,
    )
    _print_step(
        f"  - ChromaDB vector store (Tier 2): enabled at {tier_threshold}+ sessions",
        dry_run,
    )
    _print_step("  - PreCompact + PostCompact auto-capture hooks: enabled", dry_run)
    if not dry_run:
        env_lines = _VPS_CPU_ENV_LINES.copy()
        env_lines.append(f"DEPTHFUSION_TIER_THRESHOLD={tier_threshold}")
        _write_env_config(env_lines)
        _register_hooks()
        _check_depthfusion_api_key()
    _print_step("VPS-CPU install complete.", dry_run)
    _print_step(
        "To enable Haiku summarization: set DEPTHFUSION_HAIKU_ENABLED=true "
        "and DEPTHFUSION_API_KEY=sk-ant-...",
        dry_run,
    )
    _print_step(
        "WARNING: Do NOT set ANTHROPIC_API_KEY in your environment — "
        "Claude Code uses it for billing auth.",
        dry_run,
    )


def install_vps_gpu(
    dry_run: bool = False,
    tier_threshold: int = 500,
    skip_gpu_check: bool = False,
) -> int:
    """Install the vps-gpu mode. Refuses cleanly on a no-GPU host.

    Returns:
        0 on success (or dry-run)
        2 on refusal (no GPU detected) — mirrors common CLI convention
          where 2 signals "precondition not met".
    """
    _print_step("Probing for NVIDIA GPU...", dry_run)

    if not skip_gpu_check:
        info = detect_gpu()
        if not info.has_gpu:
            print("[ERROR] vps-gpu mode requires an NVIDIA GPU but none was detected.")
            print(f"        Reason: {info.reason}")
            print("")
            print("Remediation:")
            print("  1. Verify the NVIDIA driver is installed: `nvidia-smi` should succeed.")
            print(
                "  2. On a cloud VM, provision a GPU-backed instance type "
                "(e.g. AWS g5, GCP n1-highmem + T4)."
            )
            print(
                "  3. If you don't need on-box inference, use "
                "`--mode=vps-cpu` instead — it routes the same capabilities"
            )
            print("     through Haiku with no GPU dependency.")
            print("")
            print(
                "See docs/plans/v0.5/02-build-plan.md §2.3.2 for the "
                "vps-gpu rollout runbook."
            )
            return 2
        _print_step(f"  {info.reason}", dry_run)
    else:
        _print_step(
            "  (--skip-gpu-check) — GPU probe bypassed; this is intended for CI only",
            dry_run,
        )

    _print_step(
        f"Configuring DepthFusion for VPS-GPU mode (tier threshold: {tier_threshold})",
        dry_run,
    )
    _print_step("  - BM25 retrieval: enabled", dry_run)
    _print_step("  - Gemma backend: routes reranker/extractor/linker/summariser on-box", dry_run)
    _print_step("  - LocalEmbeddingBackend: sentence-transformers (all-MiniLM-L6-v2)", dry_run)
    _print_step(
        f"  - ChromaDB vector store (Tier 2): enabled at {tier_threshold}+ sessions",
        dry_run,
    )
    _print_step("  - PreCompact + PostCompact auto-capture hooks: enabled", dry_run)

    if not dry_run:
        env_lines = _VPS_GPU_ENV_LINES.copy()
        env_lines.append(f"DEPTHFUSION_TIER_THRESHOLD={tier_threshold}")
        _write_env_config(env_lines)
        _register_hooks()
        _check_sentence_transformers_installed()
    _print_step("VPS-GPU install complete.", dry_run)
    _print_step(
        "To serve Gemma locally: `bash scripts/vllm-serve-gemma.sh` "
        "(see scripts/vllm-gemma.service for systemd).",
        dry_run,
    )
    return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_env_config(lines: list[str]) -> None:
    env_file = Path.home() / ".claude" / "depthfusion.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Wrote config: {env_file}")


def _register_hooks() -> None:
    settings_path = _settings_path()
    hooks_dir = _hooks_dir()
    if not settings_path.exists():
        print(f"  Warning: {settings_path} not found, skipping hook registration")
        return
    with open(settings_path) as f:
        settings = json.load(f)
    hooks = settings.setdefault("hooks", {})
    for event, script in [
        ("PreCompact", "depthfusion-pre-compact.sh"),
        ("PostCompact", "depthfusion-post-compact.sh"),
    ]:
        script_path = hooks_dir / script
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
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)


def _check_depthfusion_api_key() -> None:
    if os.environ.get("DEPTHFUSION_API_KEY"):
        print(
            "  DEPTHFUSION_API_KEY found. Haiku features available when "
            "DEPTHFUSION_HAIKU_ENABLED=true."
        )
    elif os.environ.get("ANTHROPIC_API_KEY"):
        print("  Warning: ANTHROPIC_API_KEY is set but will NOT be used by default.")
        print("  To avoid unintended Claude Code billing, use DEPTHFUSION_API_KEY instead.")
        print("  See README for details.")
    else:
        print("  Haiku summarization disabled (no API key). Heuristic extraction will be used.")
        print(
            "  To enable: set DEPTHFUSION_HAIKU_ENABLED=true and "
            "DEPTHFUSION_API_KEY=sk-ant-..."
        )


def _check_sentence_transformers_installed() -> None:
    """vps-gpu expects sentence-transformers for local embeddings. Warn if absent."""
    import importlib.util
    if importlib.util.find_spec("sentence_transformers") is None:
        print(
            "  Warning: sentence-transformers is not installed. "
            "Install the vps-gpu extras: `pip install -e .[vps-gpu]`"
        )
        print("  Without it, the embedding backend will fall back to NullBackend.")
    else:
        print("  sentence-transformers installed — LocalEmbeddingBackend ready.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="DepthFusion installer (three-mode: local / vps-cpu / vps-gpu)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Note: `vps` is accepted as a deprecated alias for `vps-cpu` below.
    parser.add_argument(
        "--mode",
        choices=["local", "vps-cpu", "vps-gpu", "vps"],
        required=True,
        help=(
            "Install mode: 'local' (BM25 only), 'vps-cpu' (Haiku reranker), "
            "'vps-gpu' (Gemma on-box + local embeddings). "
            "'vps' is a deprecated alias for 'vps-cpu'."
        ),
    )
    parser.add_argument(
        "--tier-threshold", type=int, default=500,
        help="Session count threshold for Tier 2 promotion (default: 500)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without making changes",
    )
    parser.add_argument(
        "--skip-gpu-check", action="store_true",
        help=(
            "Skip the nvidia-smi GPU probe on vps-gpu mode. "
            "CI only — real installs should not use this."
        ),
    )
    args = parser.parse_args(argv)

    mode = args.mode
    if mode == "vps":
        print(
            "[DEPRECATION] --mode=vps is a deprecated alias for --mode=vps-cpu; "
            "please update your install script.",
            file=sys.stderr,
        )
        mode = "vps-cpu"

    # Warn if --skip-gpu-check is passed to a mode that doesn't use it.
    # Silent acceptance is a footgun: a CI script that shifts from vps-gpu to
    # vps-cpu without removing the flag should see a signal, not silence.
    if args.skip_gpu_check and mode != "vps-gpu":
        print(
            f"[WARNING] --skip-gpu-check has no effect for --mode={mode}; "
            "the flag applies only to --mode=vps-gpu. Remove it to silence this warning.",
            file=sys.stderr,
        )

    if mode == "local":
        install_local(dry_run=args.dry_run)
        return 0
    if mode == "vps-cpu":
        install_vps_cpu(dry_run=args.dry_run, tier_threshold=args.tier_threshold)
        return 0
    if mode == "vps-gpu":
        return install_vps_gpu(
            dry_run=args.dry_run,
            tier_threshold=args.tier_threshold,
            skip_gpu_check=args.skip_gpu_check,
        )
    # argparse choices guard makes this unreachable, but keep the branch explicit.
    return 1


if __name__ == "__main__":
    sys.exit(main())
