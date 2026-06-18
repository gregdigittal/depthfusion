# Upgrade Guide — → v1.1.0

> **Applies to:** any install running **v1.0.0**, **post-v1.0.0 main**, or any commit between `bbfeefa` and `5371b7e`.
> **Scope:** E-44 (Windows installer, cross-platform fcntl compat, CI matrix). No schema migrations. All changes are backward-compatible.
> **Estimated time:** 3 minutes (VPS) · 2 minutes (local laptop)

---

## What changed

| Epic | Summary | Config required? |
|---|---|---|
| E-44 | Windows installer (`install.ps1`), Mac/Linux installer (`install.sh`), `mcp-server.bat`, `install_local_windows()` + `--non-interactive`, fcntl cross-platform wrappers, GitHub Actions CI matrix (3 OS × 3 Python), Windows quickstart guide | None — transparent for existing installs |

**No breaking changes.** Existing env vars, hook scripts, and MCP registrations are unchanged. The fcntl refactor is internal — all POSIX behaviour is preserved; Windows is the new target.

---

## Upgrade — VPS (vps-cpu / vps-gpu)

### Step 1 — Pull latest code

```bash
cd ~/projects/depthfusion
source ~/venvs/depthfusion/bin/activate

git pull origin main
# Or pin to the release tag:
# git checkout v1.1.0
```

Expected output: commits from `a55324e` through `9773412`. If you see `Already up to date`, check `git describe --tags` — you should be on or past `v1.1.0`.

### Step 2 — Reinstall

```bash
pip install -e '.[vps-cpu]'
# Or for GPU hosts:
# pip install -e '.[vps-gpu]'
```

E-44 adds no new required dependencies — this is a quick no-op unless your venv is out of date.

### Step 3 — Restart the MCP server

```bash
# If running Claude Code interactively, close and reopen it.
# If running as a systemd service:
systemctl --user restart depthfusion-mcp.service 2>/dev/null || true
```

### Step 4 — Verify

```bash
# Should show 1986 tests passing (or more, if you have additional tests):
python -m pytest tests/ -q --ignore=tests/test_benchmark 2>&1 | tail -3

# Should show 0 violations:
ruff check src/

# Confirm no bare fcntl imports remain (should produce no output):
grep -rn "^import fcntl" src/depthfusion/
```

---

## Upgrade — Local install (Mac/Linux laptop)

### Step 1 — Pull

```bash
cd ~/projects/depthfusion
source .venv/bin/activate
git pull origin main
# Or: git checkout v1.1.0
```

### Step 2 — Reinstall

```bash
pip install -e '.[local]'
```

### Step 3 — Restart Claude Code

Close and reopen Claude Code (or any IDE with the MCP extension) to pick up the updated server.

---

## New in v1.1.0 for Windows users

If you are installing DepthFusion on Windows for the first time, use the new one-command installer:

```powershell
git clone https://github.com/gregdigittal/depthfusion.git $HOME\projects\depthfusion
cd $HOME\projects\depthfusion
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Full guide: **[docs/install/windows-quickstart.md](windows-quickstart.md)**

---

## Rollback

E-44 only touches Python source files, installer scripts, docs, and a new CI workflow — no filesystem migrations, no SQLite schema changes, no hook rewrites.

To roll back to v1.0.0:

```bash
cd ~/projects/depthfusion
git checkout v1.0.0
pip install -e '.[vps-cpu]'  # or your variant
# restart Claude Code / MCP service
```

---

## What changed at a glance

| File | Change |
|---|---|
| `scripts/install.sh` | **NEW** — Mac/Linux one-command installer |
| `scripts/install.ps1` | **NEW** — Windows PowerShell installer |
| `scripts/mcp-server.bat` | **NEW** — Windows Claude Desktop MCP launcher |
| `src/depthfusion/install/install.py` | `install_local_windows()` + `--non-interactive` flag |
| `src/depthfusion/core/file_locking.py` | `flock_ex/sh/un` wrappers; bare `import fcntl` removed |
| `src/depthfusion/storage/event_log.py` | `fcntl.flock()` → `flock_ex/sh/un` |
| `src/depthfusion/router/bus.py` | `fcntl.flock()` → `flock_ex/sh/un` |
| `src/depthfusion/metrics/collector.py` | `fcntl.flock()` → `flock_ex/sh/un` |
| `.github/workflows/installer-ci.yml` | **NEW** — 9-cell CI matrix |
| `docs/install/windows-quickstart.md` | **NEW** — Windows install guide |

All existing v1.0.0 env vars are unchanged.
