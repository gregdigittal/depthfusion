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
import shutil
import subprocess
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

# Keys that the installer owns across all three modes. When merging with an
# existing env file, only these keys are ever updated; all others are treated
# as user-authored and preserved verbatim.  (S-68 AC-2)
_INSTALLER_MANAGED_KEYS: frozenset[str] = frozenset({
    "DEPTHFUSION_MODE",
    "DEPTHFUSION_TIER_AUTOPROMOTE",
    "DEPTHFUSION_RERANKER_ENABLED",
    "DEPTHFUSION_GRAPH_ENABLED",
    "DEPTHFUSION_EMBEDDING_BACKEND",
    "DEPTHFUSION_TIER_THRESHOLD",
})

# Substrings that identify documented placeholder API-key values. The
# factory's health check already rejects these at runtime (fall-through
# to NullBackend), but catching them at install and recommend time
# surfaces the misconfiguration instead of silently degrading.
_PLACEHOLDER_KEY_MARKERS: tuple[str, ...] = (
    "your-real-key-here",  # literal from install.py + quickstart docs
)


def _is_placeholder_key(value: str | None) -> bool:
    """Return True if `value` contains a documented placeholder marker.

    Case-insensitive: copy-paste variants like `YOUR-real-key-here`
    also count, since real Anthropic keys never contain the marker
    phrase in any case (base64 alphabet has no hyphen-prefixed words).

    Empty / None values are "unset", not "placeholder" — callers need
    to distinguish those states, so this returns False for them.
    """
    if not value:
        return False
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_KEY_MARKERS)


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
        _register_mcp_server()
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
        _register_mcp_server()
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
        _register_mcp_server()
        _check_sentence_transformers_installed()
        # S-62 / T-197: run the vps-gpu-specific smoke test immediately
        # after env-write so driver/SDK/extras gaps surface at install
        # time rather than first recall. Failure is a warning (not
        # fatal) — the install still completes with the env file in
        # place, giving the operator a chance to fix the gap without
        # redoing the install.
        _print_step("Running vps-gpu smoke test...", dry_run)
        from depthfusion.install.smoke import run_vps_gpu_smoke
        smoke_result = run_vps_gpu_smoke()
        if smoke_result.ok:
            _print_step(f"  ✓ {smoke_result.reason}", dry_run)
        else:
            _print_step(f"  ⚠ Smoke test failed: {smoke_result.reason}", dry_run)
            _print_step(
                "  Install file written, but the runtime stack isn't fully "
                "functional yet. Fix the issue above and re-run the smoke "
                "test with `python -c 'from depthfusion.install.smoke "
                "import run_vps_gpu_smoke; print(run_vps_gpu_smoke())'`.",
                dry_run,
            )
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

def _parse_env_file(path: Path) -> list[tuple[str | None, str | None, str]]:
    """Parse an env file into (key, value, raw_line) tuples.

    Comments and blank lines have key=None, value=None.
    The raw_line preserves the original text (no trailing newline).
    """
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            result.append((None, None, line))
        elif "=" in line:
            key, _, value = line.partition("=")
            result.append((key.strip(), value, line))
        else:
            result.append((None, None, line))
    return result


def _write_env_config(lines: list[str]) -> None:
    """Write (or merge-update) the DepthFusion env file.

    On a fresh install (no existing file): writes lines directly — byte-identical
    to the pre-S-68 behaviour (preserves S-42 AC-6 regression contract).

    On re-install (existing file present): merges — installer-managed keys are
    updated in place; user-authored keys are preserved verbatim; comments and
    blank lines are kept in their original positions.  (S-68)
    """
    env_file = Path.home() / ".claude" / "depthfusion.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)

    new_env: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            k, _, v = line.partition("=")
            new_env[k.strip()] = v

    if not env_file.exists():
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  Wrote config: {env_file}")
        return

    # Merge path: preserve existing structure, update managed keys.
    old_mode = env_file.stat().st_mode
    parsed = _parse_env_file(env_file)
    out_lines: list[str] = []
    placed: set[str] = set()

    for key, old_val, raw in parsed:
        if key is None:
            out_lines.append(raw)
        elif key in new_env:
            new_val = new_env[key]
            if old_val != new_val:
                print(f"  Updating {key}: {old_val!r} → {new_val!r}")
            out_lines.append(f"{key}={new_val}")
            placed.add(key)
        else:
            out_lines.append(raw)

    for k, v in new_env.items():
        if k not in placed:
            out_lines.append(f"{k}={v}")

    env_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    os.chmod(env_file, old_mode)
    print(f"  Updated config: {env_file}")


def _register_mcp_server(dry_run: bool = False) -> None:
    """Register the DepthFusion MCP server with the Claude CLI.  (S-67)

    Idempotent: skips if already registered. Prints manual command if CLI
    is absent. Non-fatal: a failed registration never aborts the install.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        print(
            "  claude CLI not found — register the MCP server manually:\n"
            f"    claude mcp add depthfusion --scope user -- "
            f"{sys.executable} -m depthfusion.mcp.server"
        )
        return

    # Idempotency probe: check settings.json mcpServers key.
    settings_path = _settings_path()
    already_registered = False
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
            already_registered = "depthfusion" in settings.get("mcpServers", {})
        except (json.JSONDecodeError, OSError):
            pass

    if already_registered:
        print("  MCP server already registered — skipping.")
        return

    if dry_run:
        print(
            f"  [DRY-RUN] Would register MCP: claude mcp add depthfusion "
            f"--scope user -- {sys.executable} -m depthfusion.mcp.server"
        )
        return

    cmd = [
        claude_bin, "mcp", "add", "depthfusion",
        "--scope", "user",
        "--", sys.executable, "-m", "depthfusion.mcp.server",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print("  Registered DepthFusion MCP server.")
        else:
            print(
                f"  Warning: claude mcp add returned {result.returncode}. "
                f"Register manually:\n"
                f"    claude mcp add depthfusion --scope user -- "
                f"{sys.executable} -m depthfusion.mcp.server"
            )
            if result.stderr:
                print(f"  stderr: {result.stderr.strip()}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(
            f"  Warning: could not invoke claude CLI ({exc}). "
            "Register manually:\n"
            f"    claude mcp add depthfusion --scope user -- "
            f"{sys.executable} -m depthfusion.mcp.server"
        )


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
    key = os.environ.get("DEPTHFUSION_API_KEY")
    if key and _is_placeholder_key(key):
        print(
            "  WARNING: DEPTHFUSION_API_KEY is set to a placeholder value "
            "(contains 'your-real-key-here')."
        )
        print(
            "  The Haiku backend will fall back to NullBackend until you "
            "replace it with a real key from https://console.anthropic.com/."
        )
        print(
            "  This silently disables reranker, summariser, extractor, and "
            "linker — all vps-cpu mode's Haiku-dependent features."
        )
    elif key:
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

def _recommend_mode_from_gpu() -> tuple[str, str]:
    """Return (recommended_mode, human_reason).

    v0.5.2 S-62 / T-195: called from the interactive path when no
    `--mode` arg is supplied. Probes the host, picks the mode that
    matches the detected hardware, and returns both the recommendation
    and a one-sentence explanation for the banner.
    """
    info = detect_gpu()
    if info.has_gpu:
        return "vps-gpu", (
            f"NVIDIA GPU detected ({info.gpu_name}, {info.vram_gb} GB VRAM). "
            "vps-gpu runs Gemma + local embeddings on-box for lowest latency."
        )
    # No GPU — the choice between local and vps-cpu depends on whether the
    # user has a real DEPTHFUSION_API_KEY configured. A placeholder value is
    # treated as no key: recommending vps-cpu when only a placeholder is set
    # would produce an install that silently runs in NullBackend mode.
    key = os.environ.get("DEPTHFUSION_API_KEY")
    if key and not _is_placeholder_key(key):
        return "vps-cpu", (
            "No GPU, but DEPTHFUSION_API_KEY is set — vps-cpu enables the "
            "Haiku reranker via Anthropic's API."
        )
    if key and _is_placeholder_key(key):
        return "local", (
            "No GPU, and DEPTHFUSION_API_KEY is a placeholder — local mode "
            "runs BM25-only until you replace the key with a real one. "
            "Re-run the installer after setting it to upgrade to vps-cpu."
        )
    return "local", (
        "No GPU, no DEPTHFUSION_API_KEY — local mode runs BM25-only with "
        "zero external dependencies. Upgrade to vps-cpu later by setting "
        "the API key and re-running the installer."
    )


def _print_mode_banner(recommendation: str, reason: str) -> None:
    """Print the mode-selection banner for interactive installs."""
    print("")
    print("╭─────────────────────────────────────────────────────────────╮")
    print("│  DepthFusion installer — mode selection                     │")
    print("╰─────────────────────────────────────────────────────────────╯")
    print("")
    print(f"  {reason}")
    print("")
    print("  Available modes:")
    print("    [1] local    — BM25 only, no API calls, no GPU dependencies")
    print("    [2] vps-cpu  — Haiku reranker + optional ChromaDB (Tier 2)")
    print("    [3] vps-gpu  — Gemma on-box + local embeddings (needs GPU)")
    print("")
    marker = {"local": "1", "vps-cpu": "2", "vps-gpu": "3"}[recommendation]
    print(f"  Recommended for this host: [{marker}] {recommendation}")
    print("")


def _read_mode_choice(recommendation: str) -> str:
    """Read a mode choice from stdin; blank input accepts the recommendation.

    Retries on invalid input up to 3 times, then falls back to the
    recommendation. Never raises.
    """
    for _ in range(3):
        try:
            raw = input("  Choose [1/2/3] or press Enter to accept: ").strip()
        except (EOFError, KeyboardInterrupt):
            return recommendation
        if not raw:
            return recommendation
        if raw in ("1", "local"):
            return "local"
        if raw in ("2", "vps-cpu", "vps"):
            return "vps-cpu"
        if raw in ("3", "vps-gpu"):
            return "vps-gpu"
        print(f"  '{raw}' isn't a valid choice — enter 1, 2, 3, or Enter.")
    print(f"  Too many invalid choices; accepting recommendation: {recommendation}")
    return recommendation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="DepthFusion installer (three-mode: local / vps-cpu / vps-gpu)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # `--mode` is NOT required — when absent, the installer runs an
    # interactive probe + recommendation (v0.5.2 S-62).
    parser.add_argument(
        "--mode",
        choices=["local", "vps-cpu", "vps-gpu"],
        default=None,
        help=(
            "Install mode: 'local' (BM25 only), 'vps-cpu' (Haiku reranker), "
            "'vps-gpu' (Gemma on-box + local embeddings). "
            "When omitted, the installer auto-detects your hardware and "
            "prompts you to confirm the recommended mode."
        ),
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help=(
            "Accept the auto-recommended mode without prompting. Useful in "
            "non-interactive shells (CI, provisioning scripts) where stdin "
            "is not a tty."
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

    # Resolve mode: explicit --mode wins; otherwise auto-detect and
    # optionally prompt.
    mode = args.mode
    if mode is None:
        recommendation, reason = _recommend_mode_from_gpu()
        _print_mode_banner(recommendation, reason)
        if args.yes or not sys.stdin.isatty():
            print(f"  [auto-accept] Using recommendation: {recommendation}")
            print("")
            mode = recommendation
        else:
            mode = _read_mode_choice(recommendation)
            print("")
            print(f"  → Proceeding with mode={mode}")
            print("")


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
