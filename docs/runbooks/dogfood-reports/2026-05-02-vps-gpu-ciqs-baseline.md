# vps-gpu CIQS Baseline Report — 2026-05-02

## Hardware

- **Host**: hetzner-gpu (SSH alias)
- **GPU**: RTX 4000 SFF Ada (GEX44 node)
- **OS**: Ubuntu 22.04, Python 3.10
- **pip**: 22.0.2 (too old for PEP 660 editable installs)

## Install Method

Non-editable pip install failed (UNKNOWN-0.0.0 dist-info only, no module files).
Workaround used:

```bash
PYTHONPATH=/home/gregmorris/projects/depthfusion/src python3 scripts/ciqs_harness.py ...
```

Project files synced via rsync before runs. No code changes required.

**Note for S-43 follow-up**: pip upgrade on hetzner-gpu will allow standard editable
install. Current pip 22.0.2 predates PEP 660; `pip install -e '.[vps-gpu]'` fails
with "missing build_editable hook".

## Run Configuration

| Field | Value |
|---|---|
| Date | 2026-05-02 |
| Mode label | `vps-gpu` |
| DEPTHFUSION_MODE env | `vps-gpu` |
| Battery | `docs/benchmarks/prompts/ciqs-battery.yaml` |
| Runs | 3 |
| Harness | `scripts/ciqs_harness.py run` |

## Category A Auto-Scores

Category A tests retrieval quality (BM25 + reranking) without LLM generation.
Human scores are deferred to post-dogfood (S-65 ≥7 day soak).

### Top retrieval score per prompt (BM25 score, higher = better match)

| Prompt | Run 1 | Run 2 | Run 3 | Local baseline |
|---|---|---|---|---|
| A1 (TypeScript error handling) | 29.8 | 29.8 | 29.8 | 28.9 |
| A2 (second prompt) | 35.5 | 35.5 | 35.5 | 34.8 |
| A3 (third prompt) | 32.2 | 32.2 | 32.2 | 30.9 |

**Delta vs local-mode baseline**: +0.7 to +1.3 per prompt (consistent, positive).

Retrieval is deterministic — identical scores across all 3 runs for both modes.
This is expected: BM25 is a pure term-frequency algorithm with no stochastic component.

### Current retrieval content

Category A prompts retrieve session tombstones (session end events from various projects).
Actual architectural decisions and discoveries not yet indexed — this is the S-65
dogfood gap. Scores will be re-evaluated after ≥7 days of real use per AC-3/AC-4.

## S-43 AC-2 Latency Assessment

The harness does not instrument per-prompt wall-clock time. Latency ≤1500ms p95 (S-43 AC-2)
cannot be confirmed from these outputs. Requires in-process timing instrumentation or
MCP tool-level telemetry — out of scope for this harness run.

## S-43 AC-3 Assessment

GPU mode (vps-gpu) shows consistent +0.7–1.3 improvement in BM25 retrieval scores
vs local mode. Direction matches the AC-3 expectation (≥+3 points per AC-2 — this AC
references embedding boost; BM25 scores are a proxy indicator only, not the definitive
embedding distance metric).

Full AC-3 validation requires: (a) populated index post-S-65 dogfood, (b) embedding
similarity scores from the dense retrieval path, not BM25 term scores.

## Errors and Anomalies

- **PEP 660 install failure**: resolved with PYTHONPATH workaround (see Install Method above)
- **`python` not found**: hetzner-gpu uses `python3`; all commands use `python3`
- **No harness errors** during the 3 runs; all 6 output files generated cleanly

## Output Files

| File | Size |
|---|---|
| `docs/benchmarks/2026-05-02-vps-gpu-run1-raw.jsonl` | 31K |
| `docs/benchmarks/2026-05-02-vps-gpu-run1-scoring.md` | 12K |
| `docs/benchmarks/2026-05-02-vps-gpu-run2-raw.jsonl` | 31K |
| `docs/benchmarks/2026-05-02-vps-gpu-run2-scoring.md` | 12K |
| `docs/benchmarks/2026-05-02-vps-gpu-run3-raw.jsonl` | 31K |
| `docs/benchmarks/2026-05-02-vps-gpu-run3-scoring.md` | 12K |

Committed: `2136d91`

## Next Steps

- S-65: Run ≥7 day dogfood period; publish context regularly to build index
- S-63/S-66 AC-3/AC-4: Fill scoring templates after S-65 completes
- S-64: Curate 50+30+40 gold set examples (manual human work)
- S-43: Upgrade pip on hetzner-gpu; add harness timing instrumentation for p95 latency
