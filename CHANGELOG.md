# Changelog

All notable changes to DepthFusion are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/) with project-specific adjustments (inline T-/S-/E- backlog references).

Conventions:
- Dates in ISO (YYYY-MM-DD)
- Version anchors: `## [Unreleased]`, `## [v0.5.0] — YYYY-MM-DD`
- Sections per release: Added / Changed / Deprecated / Removed / Fixed / Security
- Backlog cross-references in parentheses: `(T-115)`, `(S-41, S-42)`, `(E-18)`

---

## [Unreleased]

Accumulating post-v0.6.0a1. No release cut yet; items target v0.6.0 or
v0.7.0 depending on scenario (see `docs/plans/v0.7/roadmap.md`).

### Added

**Two-mode CIQS comparison (`scripts/ciqs_compare.py`):**
- Unpaired-bootstrap delta CI between baseline and candidate CIQS runs
  (e.g. vps-cpu pre-migration vs vps-gpu post-migration). Classifies each
  category as `improved` / `regressed` / `parity` based on whether zero
  falls in the delta CI — prevents claiming wins on sampling noise.
- `--exit-nonzero-on-regression` flag for CI/automation gating.
- 23 unit tests covering math, verdict classification, report formatting,
  end-to-end CLI, and the review-gate `_reset_summ_for_testing()` hook.

**Session-history prompt miner (`scripts/mine_session_prompts.py`):**
- Extracts user-authored prompts from `~/.claude/projects/*/session-*.jsonl`
  for eval-corpus expansion. Higher signal than LLM-synthesised prompts
  because the corpus is already in-distribution for the user.
- Filters: `type: user` + string content; drops wrappers
  (`<command-message>`, `<system-reminder>`, etc.); length threshold;
  exact-dup removal via normalised-hash.
- Redacts common secret patterns (OpenAI/Anthropic, AWS, GitHub,
  Slack) before the dedup hash so secrets-only-differing prompts collapse.
- Smoke-tested on 1445 real session files producing 78 unique prompts.
- 32 unit tests plus the review-gate regression for
  `dropped_project_filter` stat counting.

**Autonomous weekly regression monitor (`scripts/ciqs_weekly.py`):**
- Reads the already-emitted `backend_summary()` / `capture_summary()`
  JSONL streams; compares last 7 days to prior 7 days; flags latency
  (> 20%), error-rate (> 5pp), capture-volume (> 30% drop), and
  availability (< 95% with prior full coverage) regressions.
- Does NOT auto-score quality — that requires labelled expected outputs.
  Report ships with an explicit disclaimer so operators don't conflate
  "no mechanical regression" with "no quality issue".
- `scripts/ciqs-weekly.service` + `scripts/ciqs-weekly.timer` systemd
  units for scheduled (Monday 06:00 local) execution with 10m jitter.
- Exit 0 on no regressions, 1 on regressions (systemd marks unit failed),
  2 on analysis error. Surfaces in `systemctl status` and journal.
- 19 unit tests covering window aggregation, threshold semantics, the
  "new backend not flagged" invariant, report formatting, CLI exit codes,
  and the review-gate regression for `window_days` threading.

### Fixed

**Review-gate findings on the three new scripts (2 High, 2 Medium, 2 Low):**
- `ciqs-weekly.service`: `%i` specifier expanded to empty string in
  non-template unit (filename became `-YYYY-MM-DD.md` with leading dash).
  Replaced with static `weekly-` prefix.
- `ciqs_weekly.py`: `detect_regressions` and `format_report` hardcoded
  `/ 7` divisor for availability math, breaking correctness when
  `--window-days != 7`. Both now consume `window_days` from the
  aggregated dict.
- `ciqs_compare.py`: added `_reset_summ_for_testing()` so the lazy
  module cache can be cleared in future tests that inject a fake
  summariser (no current test does, but the trap is now defused).
- `mine_session_prompts.py`: `dropped_project_filter` stat was
  initialised but never incremented — now tracks filtered-out files.
  Summary line added to stderr output.
- `mine_session_prompts.py`: removed shadowed `sk-ant-…` redaction
  pattern — the preceding `sk-…` branch matched first, making it
  dead code.

### Changed

Nothing changed in library runtime behaviour. All additions are scripts
(not importable from the package); no existing tests touched.

### Added (install tooling)

**Bundled installer for research tools (`scripts/install-research-tools.sh`):**
- Mode-agnostic shell installer for the research-tools bundle (session-
  history miner + weekly regression monitor). Detects prerequisites
  (`python3`, `depthfusion` importable, `systemctl --user` available);
  installs systemd user units idempotently via `cmp -s` compare-then-
  copy; runs initial mining pass; prints next-scheduled-run time.
- Graceful fallback to cron guidance when `systemctl --user` isn't
  usable (headless VPSes without user lingering enabled).
- Supports `--dry-run` for preview and `--skip-miner` for timer-only
  reinstalls. 7 smoke tests covering syntax, flags, dry-run side-effect
  absence, idempotency check presence, and systemd fallback logic.

**Quickstart guides for two install paths:**
- `docs/install/README.md` — decision overview: which path to pick,
  when to run both (parallel-comparison plan)
- `docs/install/vps-cpu-quickstart.md` — complete CPU-only install
  path (~10 min)
- `docs/install/vps-gpu-quickstart.md` — complete GPU install path
  (~4 hrs including vLLM + Gemma download). Cross-references the
  GPU migration runbook for data-migration scenarios.

The two quickstart guides share the same research-tools installer
invocation — what differs is the `pip install` extras (`[vps-cpu]`
vs `[vps-gpu]`), the `--mode` flag for `depthfusion.install.install`,
and the GPU path's vLLM systemd service setup (root-level, distinct
from the user-level weekly timer).

---

## [v0.6.0a1] — 2026-04-21

**Theme:** benchmark infrastructure, pre-migration preparation, and
backlog hygiene. No runtime-behaviour changes — the library behaves
identically to v0.5.2 unless operators explicitly opt in to the new
opt-in features (FallbackChain direct construction, dogfood telemetry
collection, CIQS harness runs).

Why alpha: the FallbackChain class (S-44 AC-3/AC-4) is implemented,
tested, and ready, but not yet wired into the factory's default
dispatch. v0.6.0 stable will flip the chain on by default for
vps-gpu mode, gated initially on `DEPTHFUSION_FALLBACK_CHAIN_ENABLED`
and then default-on. Alpha signals "new public classes are stable to
depend on; integration into defaults is forthcoming."

### Added

**`FallbackChain` backend wrapper (S-44 AC-3, AC-4 / E-19):**
- `src/depthfusion/backends/chain.py` — ordered `LLMBackend` wrapper
  that catches the three typed fallback errors (`RateLimitError`,
  `BackendOverloadError`, `BackendTimeoutError`) from the primary
  and transparently falls through to the next healthy link.
- Emits `backend.runtime_fallback` events per transition (distinct
  metric name from the factory's construction-time `backend.fallback`).
  Both gated on the same `DEPTHFUSION_BACKEND_FALLBACK_LOG` env var.
- Canonical cascade once wired: `FallbackChain([Gemma, Haiku, Null])`.
- Cached MetricsCollector instance on the emission hot path — no
  repeated stat syscalls under overload waves.
- 27 tests (`test_chain.py`) covering construction, health semantics,
  each typed-error variant, non-fallback exception propagation,
  unhealthy-skip, exhaustion with full chain names, 3-link cascade,
  event emission on/off, and H-1 health-race regression.

**CIQS benchmark harness (S-63 / E-26):**
- `scripts/ciqs_harness.py` — two-subcommand runner (`run`, `score`)
  driving the 5-category battery. Category A (retrieval-only) is
  fully auto-executed via `depthfusion.mcp.server._tool_recall`;
  B/C/D/E emit a scoring template for operator/judge-model filling.
- `scripts/ciqs_summarise.py` — bootstrap CI aggregation over N
  scored runs. Linear-interpolated percentile + 5000-resample
  bootstrap at seed=1729 for determinism.
- `docs/benchmarks/prompts/ciqs-battery.yaml` — machine-readable
  5-category battery (17 topics total, per-dimension rubrics with
  0/5/10 anchors, composite weights declared).
- `docs/benchmarks/README.md` — three-stage flow methodology.
- 33 unit tests covering percentile, bootstrap CI, normalisation,
  category grouping, report formatting, and template parsing.

**Capture-mechanism eval sets (S-64 / E-26):**
- `docs/eval-sets/README.md` + three per-set READMEs — schemas,
  labelling protocol, inter-rater-agreement guidance, edge cases.
- 6 seed JSON fixtures across three sets (decision-extraction,
  dedup, negative) pinning each schema.
- `scripts/eval_decision.py`, `scripts/eval_dedup.py`,
  `scripts/eval_negative.py` — heuristic-extractor + BOW-cosine
  precision/recall reporters. Self-contained (no embedding backend
  dependency). Target thresholds report PASS/FAIL against S-45
  AC-1, S-48 AC-2, S-49 AC-2.

**Dogfood telemetry runbook (S-65 T-204 / E-26):**
- `docs/runbooks/dogfood-telemetry.md` — protocol for validating
  the v0.5.1/v0.5.2 observability layer by running on real work
  for ≥ 1 week. Covers prereqs, daily usage, end-of-week
  aggregation incantations (jq + `MetricsAggregator`), field-level
  analysis checklists, triage rubric, report template.

**GPU VPS migration runbook (E-19 ops support):**
- `docs/runbooks/gpu-vps-migration.md` — end-to-end handover:
  snapshot + rollback tarball, SCP to new host, `[vps-gpu]`
  install, vLLM systemd service, installer auto-probe + smoke
  test, data restore, 5-step validation, per-probe
  troubleshooting, rollback procedure, first-week tasks.

**Darkroom Amber design prototype (docs/design):**
- `docs/design/prototype/design_handoff_depthfusion_landing/` —
  self-contained HTML + README from a Claude design handoff
  implementing the install UX brief at
  `docs/design/install-ux-prompt.md`. Sodium-safelight palette,
  Fraunces typography, accessibility-first (`prefers-reduced-motion`
  honoured at both CSS and JS layers).

### Changed

**Backlog reconciliation (docs-only, one-pass):**
- E-14 (CIQS Data-Gap Closure) `[active]` → `[done]` — code-complete
  since v0.5 absorbed v0.3.1 fixes; S-30 benchmark ACs migrated to E-26.
- E-15 (Performance Measurement Framework) `[active]` → `[done]`
  — authoring complete; T-93 (harness automation) delivered as S-63.
- E-19 (v0.5 GPU-Enabled LLM Routing) `[backlog]` → `[done]` — both
  S-43 and S-44 code-complete with 41 + 61 tests; live-GPU benchmarks
  migrated to E-26 as S-66.
- E-20 (v0.5 Capture Mechanisms) `[active]` → `[done]` — all five
  mechanisms code-complete; benchmark-blocked precision/recall ACs
  migrated to E-26.
- E-21 (v0.5 Retrieval Quality Enhancements) `[backlog]` → `[done]`
  — S-50/S-51/S-52 all landed across v0.5 release arc.
- New E-26 (Benchmark Harness & Evaluation Data) — consolidates all
  benchmark-blocked ACs into one measurement workstream with stories
  S-63 (harness), S-64 (gold sets), S-65 (dogfood), S-66 (GPU baseline).
- S-25 T-73 ("sentence-boundary trimming not yet implemented") flipped
  `[x]` — `_trim_to_sentence()` has been in `mcp/server.py:224` since
  v0.3.1; status was stale.

### Fixed

**Review-gate fixes on v0.6.0a1 scripts (three Highs + three Mediums):**
- `scripts/ciqs_harness.py` `cmd_score` output-path derivation: naïve
  `str.replace("-raw.jsonl", ...)` was a no-op on non-matching paths,
  silently overwriting the input file. Extracted `_derive_scored_path()`
  that validates the `-raw` suffix and raises `ValueError` otherwise.
- `_SECTION_HEADER` regex loosened from `[A-E]` to `[A-Z]` — future
  F+ categories will not be silently skipped.
- Docstring on `parse_scoring_template` corrected to match code:
  non-integer scores are silently skipped (regex filter), only
  out-of-range raises.
- `ciqs_summarise.py` report table header now uses the actual
  confidence level instead of hardcoded "95% CI".
- Eval scripts: `from collections import Counter` moved to module
  top; `sys.exit(2)` from library functions replaced with
  `raise ImportError` handled by `main()`.
- `FallbackChain` `_next_healthy_name` race eliminated (H-1); event
  "to" field now uses next-by-index instead of re-probed health.
- `BackendExhaustedError.chain` now always carries the full backend
  list (was `[]` when all unhealthy); message distinguishes tried
  vs skipped.
- Fallback-event emission tests no longer silently skip when metrics
  dir doesn't resolve under `$HOME` — mock the collector directly.

### Not yet shipped (targets for v0.6.0 stable)

- **Factory wiring of FallbackChain** — currently opt-in via direct
  construction. v0.6.0 stable will switch `get_backend` on vps-gpu
  mode to return `FallbackChain([Gemma, Haiku, Null])` for LLM
  capabilities, gated initially on `DEPTHFUSION_FALLBACK_CHAIN_ENABLED`.
- **Live-GPU benchmarks** (S-66) — gated on executing the migration.
- **Post-migration dogfood report** (S-65 T-205 first pass).
- **3-run CIQS baseline** for local + vps-cpu (S-63 T-201) — calendar-
  blocked on operator time.
- **Full gold-set curation** (S-64 T-202 remainder): 50 decision sessions,
  30 dedup pairs, 40 negatives — labelling labour.

### Added

**`FallbackChain` backend wrapper (S-44 AC-3, AC-4 / E-19):**
- `src/depthfusion/backends/chain.py` — ordered `LLMBackend` wrapper
  that catches `RateLimitError` / `BackendOverloadError` /
  `BackendTimeoutError` from the primary and transparently falls
  through to the next healthy link. Emits `backend.runtime_fallback`
  events per transition (distinct metric from the factory's
  construction-time `backend.fallback`).
- Protocol-conformant (`runtime_checkable` verified in tests) so it's
  a drop-in replacement anywhere an `LLMBackend` is expected.
- Canonical cascade for vps-gpu once wired: `FallbackChain([Gemma, Haiku, Null])`
  — on Gemma 503 → Haiku; on Haiku 429 → Null returns safe defaults.
- Respects `DEPTHFUSION_BACKEND_FALLBACK_LOG` env var (default on) to
  enable/disable event emission, mirroring the factory-level gate.
- 24 tests (`tests/test_backends/test_chain.py`) covering construction,
  health semantics, each typed-error variant, non-fallback exception
  propagation, unhealthy-skip, exhaustion ordering, 3-link cascade,
  and event emission on/off.

**GPU VPS migration runbook (E-19 ops support):**
- `docs/runbooks/gpu-vps-migration.md` — end-to-end handover from a
  current vps-cpu installation to Hetzner GEX44 (NVIDIA RTX 4000 SFF
  Ada) running `vps-gpu` mode. Covers: pre-migration snapshot with
  rollback tarball, SCP to new host, package install with `[vps-gpu]`
  extras, vLLM systemd service, installer with auto-probe + smoke
  test, per-probe troubleshooting, validation checklist (health,
  recall, capture, latency, CIQS), rollback procedure, post-migration
  first-week tasks.

### Changed

**Backlog reconciliation (docs-only):**
- E-19 status `[backlog]` → `[done]` — both stories code-complete for
  ~3 releases; status drift carried over from earlier epics. Live-GPU
  benchmark ACs (S-43 AC-2/AC-3, S-44 AC-2) now referenced from E-26
  as their measurement home.
- S-44 AC-3 and AC-4 ticked upon FallbackChain delivery.
- New story S-66 opened under E-26 for the post-migration 3-run CIQS
  baseline that unblocks the benchmark ACs.

### Not yet shipped (targets for v0.6.0 stable)

- **Factory wiring of FallbackChain** — currently opt-in via direct
  construction. v0.6.0 stable will switch `get_backend` on vps-gpu
  mode to return `FallbackChain([Gemma, Haiku, Null])` for LLM
  capabilities, gated initially on `DEPTHFUSION_FALLBACK_CHAIN_ENABLED`
  and then flipped to default-on.
- **Live-GPU benchmarks** (S-66) — gated on executing the migration.
- **Post-migration dogfood report** (S-65 T-205 first pass).

---

## [v0.5.2] — 2026-04-21

**Theme:** observability depth, two dead-path wirings fixed, interactive
install UX, and a web-UX design brief.

Patch release landing three focused improvements on top of v0.5.1:
per-capability latency measurement in the recall stream, a pair of
pre-existing "method defined but never called" gaps closed (fusion
gates + vector search), an interactive installer that auto-detects GPU
and recommends the right mode, a `vps-gpu`-specific smoke test, and a
comprehensive Claude-design brief for an animated web install UX.

Test count: 991 → 1003 (+12 net new — crossed the 1000-test milestone).
Quality: mypy 0 errors, ruff 0 errors — unchanged from v0.5.1's clean
baseline.

### Added

**Per-capability latency in recall metrics (S-61 / E-24):**
- `_tool_recall` threads a mutable `perf_ms: dict[str, float]` through
  `_tool_recall_impl`. Phases time themselves with `time.monotonic()`
  brackets; `perf_ms` gets an entry only when the phase actually ran.
- `record_recall_query()`'s `latency_ms_per_capability` field now
  populated from this dict. In v0.5.1 it shipped as an always-empty dict.

**Interactive install mode auto-select (S-62 / E-25):**
- `python -m depthfusion.install.install` with no `--mode` probes GPU
  via `detect_gpu()`, prints a recommendation banner, and either prompts
  (interactive shells) or auto-accepts (`--yes` flag or non-tty shells).
- Recommendation logic: NVIDIA GPU detected → `vps-gpu`; no GPU but
  `DEPTHFUSION_API_KEY` set → `vps-cpu`; otherwise → `local`.
- Explicit `--mode=X` preserves v0.5.1 behaviour (no banner, no probe).

**`vps-gpu`-specific smoke test (S-62 / T-197):**
- `install/smoke.py::run_vps_gpu_smoke()` — three-probe check
  (`nvidia-smi`, `sentence-transformers` import, `LocalEmbeddingBackend.embed()`
  roundtrip). Runs after `install_vps_gpu` writes the env file. Failure
  is a warning (not fatal) — install completes, operator can re-run
  the smoke test after fixing the gap without redoing the install.

**Claude design prompt for web install UX:**
- `docs/design/install-ux-prompt.md` — 326-line design brief for an
  animated landing page / onboarding wizard. Covers hero animation
  ("LLM memory before vs after DepthFusion" with compaction event as
  the dramatic moment), three mode selector cards with hardware-probe
  recommendation, 4-step deployment walkthrough, tier-specific value
  callouts with progressive disclosure, and a live metrics stream
  widget. Self-contained — a designer using Claude's design mode can
  paste it and produce a production UX.

### Changed

- `_tool_recall_impl` signature gains `perf_ms: dict | None = None`
  keyword argument for callers that want per-capability timing.
- `install.main()` — `--mode` no longer required; new `-y/--yes` flag
  for non-interactive auto-accept.
- `pyproject.toml` version `0.5.1` → `0.5.2`.

### Fixed

- **Fusion gates dead-path.** S-51 added `apply_fusion_gates` to
  `RecallPipeline` but `_tool_recall_impl` never called it —
  `DEPTHFUSION_FUSION_GATES_ENABLED=true` was a silent no-op in
  production. v0.5.2 wires the call between BM25 scoring and
  reranking.
- **Vector search dead-path.** T-130 added `apply_vector_search` to
  `RecallPipeline` but `_tool_recall_impl` never called it — the
  GPU's embedding backend couldn't actually participate in recall.
  v0.5.2 wires the call (gated on `DEPTHFUSION_VECTOR_SEARCH_ENABLED`)
  with `rrf_fuse` against BM25 results. Graceful degradation when
  backend returns None (NullBackend / missing sentence-transformers).

### Deprecated

No new deprecations. `--mode=vps` alias and `vps-tier1`/`vps-tier2`
extras from v0.5.0 remain deprecated and are scheduled for v0.6.0
removal per E-23.

### Test metrics

- **1003 passed, 1 skipped** (crossed 1000-test milestone).
- **Ruff:** 0 errors on `src/` and `tests/`.
- **Mypy:** 0 errors on `src/depthfusion` (74 source files).
- Test count delta since v0.5.1: 991 → 1003 (+12 net new).

### New environment variables (v0.5.2 additions)

| Variable | Default | Purpose |
|---|---|---|
| `DEPTHFUSION_VECTOR_SEARCH_ENABLED` | `false` | Opt-in to `apply_vector_search` in the recall path. When enabled, the query + block embeddings are fused with BM25 via RRF. Requires a real embedding backend (LocalEmbeddingBackend on vps-gpu, ChromaDB on vps-cpu Tier 2). |

---

## [v0.5.1] — 2026-04-21

**Theme:** observability surface + integration, RLM task-budget wrapper,
discovery pruner, I-8 compliance wiring, and zero-error quality baseline.

v0.5.1 is a significant feature addition over v0.5.0. Primary work: E-22
(Observability & Hygiene) landed with all four stories — the structured
metrics streams from S-53, the discovery pruner from S-55, the Opus 4.7
task-budget wrapper from S-54, and the integration layer from S-60 that
wires the streams into every capture mechanism and the recall tool. In
addition, S-58 completed the I-8 `config_version_id` wiring left as a
TODO in v0.5.0, and S-59 retired every pre-existing mypy and ruff error
so the default lint + type check commands run clean for the first time
in the v0.5 release window.

Test count delta: 887 → 986 (+99 net new). Quality: mypy 0 errors,
ruff 0 errors — unchanged from v0.5.0's post-S-59 baseline but
preserved through all subsequent feature work.

### Epic summary

| Epic                        | Change in v0.5.1                      |
|-----------------------------|----------------------------------------|
| E-22 Observability & Hygiene| **CLOSED** — all 4 stories (S-53, S-54, S-55, S-60) landed |
| E-23 v0.6 Cleanup           | S-58 (I-8 wiring) + S-59 (mypy/ruff) landed; S-56/S-57 stay for v0.6 proper |
| E-19 GPU Routing            | Unchanged (still benchmark-blocked)    |
| E-20 Capture Mechanisms     | Unchanged (still benchmark-blocked)    |
| E-21 Retrieval Quality      | Unchanged (still benchmark-blocked)    |

### Added

**Structured metrics streams (S-53, T-163..T-165):**
- `MetricsCollector.record_recall_query()` — writes per-query records to
  `YYYY-MM-DD-recall.jsonl` with `backend_used`, `backend_fallback_chain`,
  `latency_ms_per_capability` (empty in v0.5.1; filled in v0.6 per-cap
  latency refactor), `total_latency_ms`, `result_count`, `event_subtype`,
  `config_version_id`.
- `MetricsCollector.record_capture_event()` — writes per-write records to
  `YYYY-MM-DD-capture.jsonl` with `capture_mechanism`, `project`,
  `session_id`, `write_success`, `entries_written`, `file_path`,
  `event_subtype`, `config_version_id`. Unknown mechanisms flagged with
  `capture_mechanism_known: false` but still preserved on disk (forensics).
- `MetricsAggregator.backend_summary()` — per-`capability::backend_name`
  rollup: `count`, `measured_count` (distinct from `count` when latency
  samples are sparse), avg/p50/p95 latency, error_count, error_rate;
  plus per-capability fallback-chain union and overall error rate.
- `MetricsAggregator.capture_summary()` — per-mechanism write rate and
  entries-written totals; unknown mechanisms surfaced separately.
- Module-level `_VALID_EVENT_SUBTYPES` enum including `sla_expiry_deny`
  per DR-018 I-19 ratification.
- `_append_jsonl()` helper in the collector — all structured streams
  acquire `fcntl.flock(LOCK_EX)` on a fresh OFD to serialise concurrent
  writers (gate entries exceed 4 KiB PIPE_BUF so `O_APPEND` atomicity
  alone is insufficient). Numpy-safe `_json_default` for serialisation.

**Production emission wiring (S-60, T-186..T-191):**
- `capture/_metrics.py` — NEW shared `emit_capture_event()` helper used
  by every capture mechanism (extractors + dedup + git hook +
  confirm_discovery).
- `_tool_recall` refactored into a thin wrapper around
  `_tool_recall_impl`; measures total latency via `time.monotonic`,
  probes backend routing via new `_detect_current_backends()` helper
  (skipped on error path for efficiency), emits `recall_query` per call.
- `_tool_confirm_discovery` passes `capture_mechanism="confirm_discovery"`
  to `write_decisions` so emits re-bucket under the higher-level tool
  label — one event per logical operation, not double-counted.
- `dedup.dedup_against_corpus` emits a dedicated event when it runs and
  finds zero duplicates — the metrics stream distinguishes "dedup ran,
  no dupes" from "dedup never ran".
- Git post-commit hook uses a LOCAL `_emit_capture_event` wrapper with
  its own try/except on top of the shared helper — defense in depth so
  a metrics failure can NEVER block a developer's git commit.

**Discovery pruner (S-55, T-169..T-171):**
- `capture/pruner.py` — NEW. `PruneCandidate` frozen dataclass +
  `identify_candidates()` + `prune_discoveries()`. Two heuristics: age
  threshold (default 90d via `DEPTHFUSION_PRUNE_AGE_DAYS`) and
  `.superseded` suffix from S-49 dedup.
- `depthfusion_prune_discoveries` MCP tool (13th tool, always enabled).
  Two-phase: `confirm=False` (default) returns candidates; `confirm=True`
  moves files to `~/.claude/shared/discoveries/.archive/`. Never deletes.
  Archive collisions get timestamp-suffix names.

**RLM task-budget wrapper (S-54, T-166..T-168):**
- `CostEstimator.budget_tokens_for_ceiling(ceiling_usd, model)` —
  translates USD cost ceiling to token budget using model input pricing.
  Docstring explicitly names the output-heavy overshoot hazard (up to
  5× for opus) so operators see the caveat in their IDE.
- `RLMClient.run()` probes the SDK surface for the Anthropic task-budgets
  beta. Dual gate: `DEPTHFUSION_RLM_TASK_BUDGET_ENABLED=true` AND SDK
  exposes `anthropic.task_budget` or `anthropic.types.TaskBudget`.
  `inspect.signature(rlm.RLM.__init__)` confirms rlm accepts the
  `task_budget_tokens` kwarg before passing it. Falls back to v0.4.x
  post-hoc estimation when either gate fails.
- Shipped as a "best-effort wrapper without CIQS claim" per build plan
  §TG-13 kill-criterion — dormant until Anthropic SDK ships the beta.

**I-8 compliance wiring (S-58, T-178..T-180):**
- `GateConfig.version_id()` — deterministic 12-char hex hash of the
  config tuple, attached to every gate-log record. Removes the
  `TODO(I-8)` marker left in v0.5.0 `apply_fusion_gates`.
- Auditors can now reproduce any historical gate decision by looking
  up the `config_version_id` in the gate log against an archived
  config snapshot.

### Changed

- `decision_extractor.write_decisions()` accepts a `capture_mechanism`
  parameter (default `"decision_extractor"`) so wrappers can re-bucket
  metrics under their own mechanism name.
- `_DISCOVERIES_DIR` module-level constants in `decision_extractor`,
  `negative_extractor`, and `git_post_commit` replaced with
  `_default_discoveries_dir()` runtime helpers — fixes a freeze-at-
  import bug where tests couldn't redirect via `monkeypatch.Path.home`.
- `MCP tool count: 12 → 13` (new `depthfusion_prune_discoveries`).
- `pyproject.toml` version `0.5.0` → `0.5.1`.

### Fixed

- **S-59 mypy/ruff cleanup.** Retired 6 pre-existing mypy errors and 5
  pre-existing ruff errors. Both `mypy src/depthfusion` and
  `ruff check src/ tests/` now exit clean for the first time in the v0.5
  release window. Added `types-PyYAML>=6.0.0` to `[dev]` extras.
- **Gate-log config_version_id defaults to empty string.** v0.5.0
  shipped with a `TODO(I-8)` marker and empty `config_version_id` on
  every record; v0.5.1 populates it from `GateConfig.version_id()`
  deterministically.
- **Dedup `no supersessions` observability gap.** v0.5.0 dedup emitted
  nothing when it ran and found zero duplicates. v0.5.1 emits a
  dedicated `write_success=True, entries_written=0` event so the
  metrics stream distinguishes the common case from "dedup never ran."
- **Review gate findings across 4 stories** — 22 issues total (0
  Critical, 6 High/Important, 13 Medium, 3 Low) all caught and fixed
  pre-commit. See individual commit messages for the breakdown.

### Deprecated

No new deprecations. `--mode=vps` alias and `vps-tier1`/`vps-tier2`
extras from v0.5.0 remain deprecated and are scheduled for v0.6.0
removal per E-23.

### Security

- Gate log + recall_query log + capture log streams all record
  `query_hash` (sha256[:12]) only — raw query text is never written to
  disk in any observability stream.
- Prune tool never deletes files; always moves to `.archive/` with
  timestamp-suffix collision handling.
- Anthropic SDK probe for task-budget beta requires explicit env var
  opt-in — a silent SDK upgrade cannot accidentally activate the wrapper.

### Test metrics

- **986 passed, 1 skipped** (sentence-transformers integration test).
- **Ruff:** 0 errors on `src/` and `tests/`.
- **Mypy:** 0 errors on `src/depthfusion` (74 source files).
- Test count delta since v0.5.0: 887 → 986 (+99 net new).

### New environment variables (v0.5.1 additions)

| Variable | Default | Purpose |
|---|---|---|
| `DEPTHFUSION_PRUNE_AGE_DAYS` | `90` | Age threshold for the discovery pruner (S-55). |
| `DEPTHFUSION_RLM_TASK_BUDGET_ENABLED` | `false` | Opt-in to the Anthropic task-budgets beta wrapper (S-54). |

---

## [v0.5.0] — 2026-04-20

**Theme:** pluggable LLM backends, three-mode installer, Category D capture
mechanisms, selective retrieval quality.

v0.5 is the largest single-release feature delta since v0.3.0. It refactors
every LLM call-site through a provider-agnostic backend protocol, adds a
three-mode installer (`local` / `vps-cpu` / `vps-gpu`), ships five new
capture mechanisms for architectural decisions and commit metadata, and
adds three retrieval-quality filters (project-scoped recall, temporal graph
edges, Mamba B/C/Δ fusion gates).

From 439 tests at v0.3.1 to **887 tests at v0.5.0**. All new features are
opt-in or byte-identical-by-default with v0.4.x — the `DEPTHFUSION_*` flag
matrix below controls what activates.

### Epic summary

| Epic                        | Stories                         | Status |
|-----------------------------|---------------------------------|--------|
| E-18 Backend Foundation     | S-41, S-42                      | Closed |
| E-19 GPU-Enabled LLM Routing| S-43, S-44                      | Code complete; CIQS/latency ACs benchmark-pending |
| E-20 Capture Mechanisms     | S-45, S-46, S-47, S-48, S-49    | Code complete; precision/false-rate ACs require labelled eval |
| E-21 Retrieval Quality      | S-50, S-51, S-52                | Closed |

### Added

**Backend protocol + factory (S-41, T-115..T-123, E-18):**
- `backends/base.py` — `LLMBackend` `Protocol` with `complete` / `embed` / `rerank` / `extract_structured` / `healthy`; typed errors `RateLimitError`, `BackendOverloadError`, `BackendTimeoutError`, `BackendExhaustedError`.
- `backends/null.py` — terminal fallback; always healthy; returns safe degenerate values.
- `backends/haiku.py` — Anthropic Haiku with explicit `api_key=DEPTHFUSION_API_KEY` (closes C2 billing-isolation hazard from v0.4.x).
- `backends/gemma.py` — vLLM HTTP client via stdlib `urllib.request`, typed 429/503/529/timeout translation (T-132).
- `backends/local_embedding.py` — sentence-transformers wrapper, default `all-MiniLM-L6-v2`; lazy model load behind `threading.Lock`; healthy check uses `importlib.util.find_spec` (no model load) (T-118/T-129).
- `backends/factory.py` — `get_backend(capability, mode)` with per-capability env-var overrides and healthy-check fallback to `NullBackend`; emits `backend.fallback` JSONL event on downgrade (T-123).
- Six capabilities routed through the factory: `reranker`, `extractor`, `linker`, `summariser`, `embedding`, `decision_extractor`.

**Three-mode installer (S-42, T-124..T-128):**
- `install/install.py` — `--mode={local,vps-cpu,vps-gpu}` with deprecated `vps`→`vps-cpu` alias; `--skip-gpu-check` CI flag with stray-flag warning.
- `install/gpu_probe.py` — `detect_gpu()` via `nvidia-smi` subprocess (2s timeout); never raises; `GPUInfo` frozen dataclass.
- `install/smoke.py` — `run_smoke_test()` writes 5-file synthetic corpus, runs BM25 query, asserts known target ranks first.
- `pyproject.toml` — `[local]`, `[vps-cpu]`, `[vps-gpu]` optional-dependencies extras; legacy `vps-tier1`/`vps-tier2` aliases retained.

**Capture mechanisms (E-20):**
- `capture/decision_extractor.py` — LLM-based decision extractor (S-45/CM-1); heuristic fallback; idempotent write to `{date}-{project}-decisions.md`.
- `capture/negative_extractor.py` — extracts "X did not work because Y" entries tagged `type: negative` (S-48/CM-6).
- `capture/dedup.py` — embedding-based discovery deduplication; cos-sim ≥ 0.92 → older file renamed `.superseded`; project-scoped; strict "never dedup projectless files" for conservative correctness (S-49/CM-2).
- `hooks/git_post_commit.py` — opt-in git post-commit hook writing `{date}-{project}-commit-{sha7}.md`; idempotent; never blocks commits (S-46/CM-3).
- `scripts/install-git-hook.sh` — per-repo opt-in installer; detects existing hooks and appends rather than overwrites.
- `mcp/server.py` — new tool `depthfusion_confirm_discovery` for session-time active confirmation (S-47/CM-5).
- `hooks/depthfusion-stop.sh` — Stop hook runs `SessionCompressor` on most-recent `.tmp` file.

**Retrieval quality (E-21):**
- `retrieval/hybrid.py` — `extract_frontmatter_project()` + `filter_blocks_by_project()`; `_tool_recall` accepts `cross_project: bool` and `project: str` args with path-traversal-safe slug sanitisation (S-52/T-160/T-161).
- `retrieval/hybrid.py` — `apply_vector_search()` uses `LocalEmbeddingBackend.embed()` and fuses with BM25 via existing `rrf_fuse` (T-130).
- `retrieval/hybrid.py` — `apply_fusion_gates()` runs the three-stage Mamba B/C/Δ filter; gated on `DEPTHFUSION_FUSION_GATES_ENABLED`; fail-open on error and on empty survivors (S-51/T-157).
- `graph/linker.py` — `SessionRecord` dataclass + `TemporalSessionLinker` producing `PRECEDED_BY` edges between sessions close in time AND sharing vocabulary; direction normalised to "later PRECEDED_BY earlier" with session-id tie-break on identical timestamps (S-50/T-153).
- `graph/traverser.py` — `time_window_hours` parameter on `traverse()` filters edges by `metadata["delta_hours"]`; non-temporal edges bypass the filter for back-compat (T-154).
- `graph/types.py` — 8th edge kind `PRECEDED_BY` documented in the `Edge` docstring.
- `fusion/gates.py` — NEW module: `GateConfig` (α default 0.30 per TG-11), `GateDecision`, `GateLog` frozen dataclasses; `SelectiveFusionGates` class; base_scores normalised to percentile [0,1] before the α blend so the B signal is not drowned out by raw BM25 magnitudes.

**Observability:**
- `metrics/collector.py` — `record_gate_log()` writes to `YYYY-MM-DD-gates.jsonl` with `fcntl.flock` against concurrent-writer interleaving; `_json_default` coerces numpy scalars to Python floats (no silent stringification). `fallback_triggered` field flags entries where the retrieval layer overrode the gate verdict (T-158/LOW-7).

**Planning artefacts:**
- `docs/plans/v0.5/01-assessment.md` through `06-*.md` — feature assessment, 15-task-group build plan with per-TG acceptance criteria, SkillForge integration spec, rollout runbook, commit strategy, backlog proposal. AC-01-8 post DR-018 ratification for quality-ranked fallback order. Invariant rows I-8/I-9/I-10/I-11 ratified per DR-018 §4.

### Changed

- `analyzer/installer.py` — extended to document the git-hook opt-in step; recommends per-repo hook install during analysis (T-142).
- `mcp/server.py` — 11 → 12 tools (added `depthfusion_confirm_discovery`).
- `retrieval/hybrid.py` — `_tool_recall` filter path applied BEFORE BM25 scoring so IDF weights reflect the filtered corpus (S-52).
- `capture/auto_learn.py` — Phase 2b dedup integration after extractor writes (T-150); gated on `DEPTHFUSION_DEDUP_ENABLED` (default true, safe no-op when embedding backend unavailable).
- `pyproject.toml` — project version 0.3.0 → 0.5.0.

### New environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPTHFUSION_RERANKER_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_EXTRACTOR_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_LINKER_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_SUMMARISER_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_EMBEDDING_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_DECISION_EXTRACTOR_BACKEND` | — | Per-capability backend override. |
| `DEPTHFUSION_DECISION_EXTRACTOR_ENABLED` | `false` | Gates the LLM decision extractor + negative extractor. |
| `DEPTHFUSION_GEMMA_URL` / `DEPTHFUSION_GEMMA_MODEL` | see gemma.py | vLLM endpoint + model. |
| `DEPTHFUSION_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model name. |
| `DEPTHFUSION_DEDUP_ENABLED` | `true` | Opt-out of embedding-based discovery dedup. |
| `DEPTHFUSION_DEDUP_THRESHOLD` | `0.92` | Cosine similarity for dedup. |
| `DEPTHFUSION_FUSION_GATES_ENABLED` | `false` | Opt-in to Mamba B/C/Δ gates. |
| `DEPTHFUSION_FUSION_GATES_ALPHA` | `0.30` | AttnRes α blend weight. |
| `DEPTHFUSION_FUSION_GATES_B_THRESHOLD` | `0.10` | B gate floor. |
| `DEPTHFUSION_FUSION_GATES_C_THRESHOLD` | `0.05` | C gate floor. |
| `DEPTHFUSION_FUSION_GATES_DELTA_THRESHOLD` | `0.0` | Δ gate floor (normalised scale). |
| `DEPTHFUSION_BACKEND_FALLBACK_LOG` | `true` | Emit `backend.fallback` events. |
| `DEPTHFUSION_PROJECT` | — | Override auto-detected project slug. |

### Fixed

- **C2 billing-isolation hazard (S-41 AC-2):** v0.4.x `HaikuBackend` constructed `anthropic.Anthropic()` with no explicit `api_key=`, falling back to the SDK's `ANTHROPIC_API_KEY` lookup — which silently switched Claude Code's billing from Pro/Max subscription to pay-per-token. v0.5 always passes `api_key=DEPTHFUSION_API_KEY` explicitly.
- **Path-traversal risk in `depthfusion_confirm_discovery` and `depthfusion_recall_relevant`:** externally-supplied `project` slugs now pass through `_sanitise_project_slug()` (lowercase + `[a-z0-9-]` allowlist, capped at 40 chars) before reaching `write_decisions()` or the filter comparison.
- **`detect_project()` "unknown" sentinel handling:** in a bare-MCP context with no git remote and no env var, `detect_project()` returns the literal string `"unknown"`. v0.4.x code that filtered against this sentinel would silently zero out recall. v0.5 treats `"unknown"` as "no project context" and skips the filter.

### Deprecated

- `--mode=vps` — alias for `--mode=vps-cpu`. Emits `[DEPRECATION]` warning on stderr. Removal target: v0.6.
- `pyproject.toml` extras `vps-tier1` / `vps-tier2` — replaced by `local` / `vps-cpu` / `vps-gpu`. Removal target: v0.6.

### Security

- Gate log emits `query_hash` (sha256[:12]) only — raw query text is never written to disk (T-158).
- All post-commit hook subprocess calls have explicit timeouts (`timeout=4` for git commands, `timeout=15` for installer).
- `nvidia-smi` probe uses 2s timeout; `smi_path` resolved via `shutil.which`; subprocess called with list-form args (no shell injection vector).

### Not yet measured (benchmark-blocked ACs)

These ACs are code-complete but require running the shipped code against live infrastructure or labelled eval corpora to close. Tracked for the v0.5.1 measurement pass:
- S-43 AC-2/AC-3: CIQS Category A delta ≥ +3 on vps-gpu; p95 recall latency ≤ 1500ms.
- S-44 AC-2/AC-3/AC-4: p95 latency per capability on vps-gpu; live chain-level fallback integration.
- S-45 AC-1: decision-extractor precision ≥ 0.80 on 50-session labelled set.
- S-48 AC-2: negative-extractor false-negative rate ≤ 10% on labelled set.
- S-49 AC-2: dedup false-positive rate ≤ 5% on 30 near-duplicate pairs.
- S-50 AC-3: CIQS Category D delta ≥ +2 on "recent work" questions.
- S-51 AC-1: CIQS Category A delta ≥ +2 on vps-cpu; ≥ +3 on vps-gpu.
- S-42 AC-5: `pip install --dry-run` conflict-check on all three extras (structural test is green; live run deferred).

### Upstream dependencies

- `docs/research/DR-018_LEGACY_INVARIANT_REINSTATEMENT.md` — ratified 2026-04-18. Five per-legacy-invariant verdicts locked; cascaded amendments applied to v0.5 planning docs and to the gate-log record shape (`config_version_id` field present on every `record_gate_log()` entry per I-8 ratification).

### Test metrics

- **887 passed, 1 skipped** (sentence-transformers integration test; gated by `pytest.importorskip`).
- **Ruff clean** on all code introduced in v0.5.
- **Mypy:** zero errors introduced by v0.5 work; 6 pre-existing errors untouched.
- Test count delta: v0.3.1 baseline 439 → v0.5.0 887 (+448 net new across the v0.5 release window).

---

## [v0.4.0] — TBD

> **Note to maintainer:** backfill this entry from git log on the `feat/v0.4.0-knowledge-graph` branch. Key landmarks expected:
> - 8-entity knowledge-graph types (`class`, `function`, `file`, `concept`, `project`, `decision`, `error_pattern`, `config_key`)
> - 7-edge relationship model (CO_OCCURS / CO_WORKED_ON / CAUSES / FIXES / DEPENDS_ON / REPLACES / CONFLICTS_WITH)
> - RegexExtractor + HaikuExtractor pipeline with confidence merging
> - `JSONGraphStore` + `SQLiteGraphStore` tier-aware factory
> - CoOccurrenceLinker / TemporalLinker / HaikuLinker chain
> - `depthfusion_graph_traverse`, `depthfusion_graph_status`, `depthfusion_set_scope` MCP tools
> - `DEPTHFUSION_GRAPH_ENABLED`, `DEPTHFUSION_GRAPH_MIN_CONFIDENCE` flags
> - Query expansion integration in `RecallPipeline`; entity extraction in `auto_learn`

Reference the backlog: E-11 (Knowledge Graph), S-28 to S-36, T-87 onwards.

---

## [v0.3.1] — TBD

> **Note to maintainer:** backfill from git log (tag applied retroactively per `docs/release-process.md` recommendation). Key landmarks:
> - BM25 scoring wired into `_tool_recall` (`mcp/server.py`) — fixes Issue 1 from honest-assessment
> - Snippet length extended from 500 → 1500 chars — fixes Issue 2
> - Source classification tracked at read time, weights {memory: 1.0, discovery: 0.85, session: 0.70} — fixes Issue 3
> - RRF fusion wired into recall pipeline — fixes Issue 4
> - Block chunking on `\n## ` H2 headers — fixes Issue 5
> - Sentence-boundary snippet trimming (60% min threshold) — T-73
> - Confidence threshold at graph store write — T-114
> - `DEPTHFUSION_API_KEY` auth isolation from `ANTHROPIC_API_KEY` — commit `3052c2b`
> - C4 compatibility YELLOW → GREEN — T-110
> - 439 tests passing

Reference the backlog: E-14 (CIQS Data-Gap Closure), S-32 to S-36.

---

## [v0.3.0] — baseline release

> **Note to maintainer:** this was the initial baseline. If the original release notes live in a project knowledge base, link them here; otherwise backfill from the earliest git history.

Key modules shipped:
- Core: `core/types.py`, `core/scoring.py`, `core/config.py`, `core/feedback.py`
- Retrieval: `retrieval/bm25.py`, `retrieval/hybrid.py`, `retrieval/reranker.py`
- Fusion: `fusion/rrf.py`, `fusion/weighted.py`, `fusion/block_retrieval.py`, `fusion/reranker.py`
- Session: `session/tagger.py`, `session/scorer.py`, `session/loader.py`, `session/compactor.py`
- Router: `router/bus.py`, `router/dispatcher.py`, `router/publisher.py`, `router/subscriber.py`, `router/cost_estimator.py`
- Recursive: `recursive/client.py`, `recursive/sandbox.py`, `recursive/strategies.py`, `recursive/trajectory.py`
- Storage: `storage/tier_manager.py`, `storage/vector_store.py`
- MCP: `mcp/server.py` (11 tools)
- Analyzer: `analyzer/scanner.py`, `analyzer/compatibility.py` (C1-C11), `analyzer/recommender.py`, `analyzer/installer.py`
- Install: `install/install.py`, `install/migrate.py`
- Metrics: `metrics/collector.py`, `metrics/aggregator.py`

Reference the backlog: E-01 through E-10, E-12, E-13.
