# Upgrade Guide — v1.0.0 → v1.1.0

> **Note:** This guide covers the E-38–E-43 changes that shipped between v1.0.0 and v1.1.0.
> For E-44 (Windows installer, fcntl compat, CI matrix) see **[upgrade-to-v1.1.0.md](upgrade-to-v1.1.0.md)**.
> If upgrading directly from v1.0.0, follow both guides in order (this one first, then v1.1.0).

> **Applies to:** existing installs running exactly v1.0.0 (`git describe --tags` shows `v1.0.0`).
> **Scope:** E-38 through E-43. No schema migrations. All changes are backward-compatible.
> **Estimated time:** 5 minutes (VPS) · 3 minutes (local laptop)

---

## What changed

| Epic | Summary | Config required? |
|---|---|---|
| E-38 | MemPalace temporal filter, KG provenance, linear blend, Wing/Room scoping, KG edge invalidation | Optional new env vars |
| E-39 | SkillForge SF-2: `run_recursive` routes via SkillForge HTTP when 3 env vars set | Only if using SkillForge |
| E-40 | CIQS Cat D benchmark harness | Dev only |
| E-41 | Metrics flock guard + `skipped_lines` in both summary methods | None — transparent |
| E-42 | Pruner `superseded_min_age_hours` grace period | Optional new env var |
| E-43 | SkillForge JWT auto-refresh, Mamba B/C/Δ Python port (S-129/S-130) | None — gate already exists |

**No breaking changes.** All new env vars default to the prior behavior. `capture_summary()` gains a `skipped_lines` key — existing callers that don't reference it are unaffected.

---

## Upgrade — VPS (vps-cpu / vps-gpu)

These steps run from a terminal connected to your VPS. All commands assume the venv is active.

### Step 1 — Pull latest code

```bash
cd ~/projects/depthfusion
source ~/venvs/depthfusion/bin/activate

git pull origin main
```

Expected output: a list of commit hashes from `a880fa8` through `bbfeefa`. If you see `Already up to date`, you're already on the latest.

### Step 2 — Reinstall (picks up any new Python deps)

```bash
pip install -e '.[vps-cpu]'
# Or for GPU hosts:
# pip install -e '.[vps-gpu]'
```

Post-v1.0.0 adds no new required dependencies — this step is a quick no-op unless your venv is out of date.

### Step 3 — Restart the MCP server

Claude Code's MCP process holds the old code in memory. Reload it:

```bash
# If you're running Claude Code interactively, close and reopen it.
# If running as a systemd service:
systemctl --user restart depthfusion-mcp.service 2>/dev/null || true
```

### Step 4 — (Optional) Add new env vars

Open `~/.claude/depthfusion.env` in your editor and append any of the following you want:

```bash
# E-38 — Wing/Room sub-project scoping
# Only needed if you share a ~/.claude/ across multiple projects and want
# recall/capture partitioned to a specific project context.
# DEPTHFUSION_WING_ID=my-project
# DEPTHFUSION_ROOM_ID=backend          # optional finer partition within the wing

# E-38 — Linear blend fusion (replaces RRF when set; benchmarked at parity)
# DEPTHFUSION_LINEAR_BLEND=true

# E-39 — SkillForge recursive routing
# Only needed if you have a running SkillForge instance with a recursive skill registered.
# DEPTHFUSION_SKILLFORGE_API_URL=http://127.0.0.1:3001
# DEPTHFUSION_SKILLFORGE_API_TOKEN=your-bearer-token
# DEPTHFUSION_SKILLFORGE_RECURSIVE_SKILL_ID=<uuid-of-registered-skill>
#
# SkillForge auto-restart on VPS reboot (run once after installing SkillForge):
#   sudo env PATH=$PATH:/home/<user>/.npm-global/bin \
#        /home/<user>/.npm-global/bin/pm2 startup systemd -u <user> --hp /home/<user>
#   pm2 save
# Note: pm2 is installed via npm global — the sudo PATH must include ~/.npm-global/bin,
# not /usr/bin. The startup command printed by plain `pm2 startup` uses /usr/bin and fails.

# E-42 — Pruner grace period for superseded files
# Default 0 = flag all .superseded files immediately (original behavior).
# Set to e.g. 24 to give dedup runs a 24-hour window before archival.
# DEPTHFUSION_PRUNE_SUPERSEDED_MIN_AGE_HOURS=24
```

After editing, reload the env file:

```bash
source ~/.claude/depthfusion.env
# Or restart your shell: exec bash
```

### Step 5 — Verify

```bash
# Should show 1993 tests passing:
python -m pytest tests/ -q 2>&1 | tail -3

# Should show 0 violations:
ruff check src/

# MCP tool confirms running build:
# In Claude Code: run depthfusion_status
# Look for "fusion_gates_enabled" in the output; S-130 boundary state is active
# when DEPTHFUSION_FUSION_GATES_ENABLED=true in your env.
```

---

## Upgrade — Local install (laptop, `local` mode)

### Step 1 — Pull

```bash
cd ~/projects/depthfusion
source .venv/bin/activate
git pull origin main
```

### Step 2 — Reinstall

```bash
pip install -e '.[local]'
```

### Step 3 — Restart Claude Code

Close and reopen Claude Code (or any IDE with the MCP extension) to pick up the updated server binary.

### Step 4 — (Optional) env vars

If you use `local` mode, Wing/Room scoping (E-38) is the most likely addition to be useful — it lets you confine recall to a named project even when your `~/.claude/shared/discoveries/` has entries from many projects:

```bash
# Append to ~/.claude/depthfusion.env
echo 'DEPTHFUSION_WING_ID=my-project' >> ~/.claude/depthfusion.env
```

SkillForge (E-39), linear blend (E-38), and fusion gates (E-43) work in `local` mode but are most impactful on vps-cpu/vps-gpu installs.

---

## Rollback

The upgrade only touches Python source files — no filesystem migrations, no SQLite schema changes, no hook rewrites. To roll back to v1.0.0:

```bash
cd ~/projects/depthfusion
git checkout v1.0.0
pip install -e '.[vps-cpu]'  # or your variant
# restart Claude Code / MCP service
```

---

## New env vars at a glance

| Var | Default | Description |
|---|---|---|
| `DEPTHFUSION_WING_ID` | — | Sub-project wing scope for recall + capture |
| `DEPTHFUSION_ROOM_ID` | — | Room partition within a wing |
| `DEPTHFUSION_LINEAR_BLEND` | `false` | Replace RRF with linear fusion |
| `DEPTHFUSION_SKILLFORGE_API_URL` | — | SkillForge base URL |
| `DEPTHFUSION_SKILLFORGE_API_TOKEN` | — | SkillForge bearer token |
| `DEPTHFUSION_SKILLFORGE_RECURSIVE_SKILL_ID` | — | UUID of pre-registered recursive skill |
| `DEPTHFUSION_PRUNE_SUPERSEDED_MIN_AGE_HOURS` | `0` | Grace period before archiving superseded files |

All existing v1.0.0 env vars are unchanged.
