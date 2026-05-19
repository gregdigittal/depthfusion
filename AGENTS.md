# AGENTS.md — DepthFusion

## Quick Reference
- **Stack:** Python 3.10+ / numpy / pyyaml / structlog / optional: chromadb / rlms
- **VPS Path:** /home/gregmorris/projects/depthfusion
- **Description:** Depth-aware memory fusion for Claude Code — weighted retrieval, session attention, context routing, recursive LLMs

## For Antigravity
- Browser sub-agent URL: N/A — library, no web UI
- Write plans to: PLAN.md in this directory
- Acceptance criteria: pytest -v --tb=short

## For Claude Code
- Read PLAN.md at start of every session — execute pending tasks first
- This library serves all Claude Code sessions on the VPS — changes affect all agents
- Commit format: feat(scope): description

## Review Gate
pytest -v --tb=short

## Sub-Agent Rules: Large File Generation

The `large-file-generation` skill applies across all stages. Each
stage has a specific responsibility.

### spec agent
- When the spec implies generation of low-entropy artifacts, call
  this out under a "Generation Risk" subsection.
- List each at-risk file with estimated line count and logical
  chunking boundary.
- Stack-specific triggers for this project:
  - pytest fixtures (large fixture files under `tests/fixtures/`)
  - TypedDict / enum / Literal generators
  - YAML config dumps (capability tables, routing maps)
  - SQL seed fixtures (if added)

### architect agent
- For every flagged file, the architecture doc must specify the
  chunking plan: skeleton + per-chunk Edit sequence.
- Never approve a plan that writes a flagged file in one Write call.

### implement agent
- Execute the chunked plan. One Write for skeleton, one Edit per
  logical unit.
- Run the chunk validator between Edits. On failure, stop and
  report. Never continue past an invalid chunk.
- Validators for this project:
  - `python -m py_compile <file>` after each Python chunk
  - `jq .` on any JSON
  - `mypy --no-error-summary` after TypedDict / enum changes
  - `python -c "import yaml; yaml.safe_load(open('<file>'))"` on YAML chunks
- On stream timeout, verify file on disk first. If valid, continue
  from next chunk. If invalid, truncate to last known-good.

### review agent
- Verify all logical units from the spec are present, the validator
  passes on the final artifact, no chunk was silently skipped.
- Flag any file >20% below the spec's line estimate — usually
  indicates a chunk lost to a silent stream kill.
