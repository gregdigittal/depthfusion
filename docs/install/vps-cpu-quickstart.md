# vps-cpu Quickstart — CPU-only DepthFusion + research tools

> **Target:** current Hetzner VPS or any CPU-only Linux host
> **Time:** ~10 minutes hands-on
> **Produces:** working DepthFusion in `vps-cpu` mode + weekly regression monitor + initial prompt corpus
> **Estimated first-run size on disk:** ~150 MB (Python deps) + corpus growth

This is the complete path for running DepthFusion on a CPU-only host
with the research tooling active. At the end you'll have:

- DepthFusion library installed with `[vps-cpu]` extras (Haiku reranker, ChromaDB Tier 2)
- Claude Code MCP integration wired
- Weekly autonomous regression monitor scheduled
- Initial session-history corpus mined (first baseline for eval)

---

## 0. Prerequisites

```bash
# Python 3.10 or newer — note "or newer", any 3.10/3.11/3.12/3.13 works.
# Ubuntu 24.04 ships 3.12 as default. You do NOT need to install 3.10
# specifically; the `>=3.10` constraint means "3.10 is the minimum".
python3 --version

# Build tools + venv module. On fresh Ubuntu 24.04 the venv module is
# not pre-installed — this is the most common first-install gotcha.
sudo apt update
sudo apt install -y python3-full python3-venv build-essential python3-dev

# systemd --user — needed for the weekly regression monitor (§4).
# On most Hetzner boxes this works out of the box. If it reports
# "No session" or similar:
systemctl --user status || sudo loginctl enable-linger $USER
```

> **Why `python3-full`?** On Debian/Ubuntu, `python3` is a minimal
> bootstrap; `python3-full` brings in `venv`, `pip`, and the standard
> library components DepthFusion's dependencies need to compile.
> `build-essential` + `python3-dev` cover the native-compile step
> for `chromadb` (which pulls in `hnswlib`).

You'll also need a DepthFusion API key:

```bash
# Check: is DEPTHFUSION_API_KEY already set anywhere?
grep DEPTHFUSION_API_KEY ~/.claude/depthfusion.env 2>/dev/null || echo "not set"
```

If not set, [create a key from the Anthropic console] and have it
ready for step 2. **Important:** use `DEPTHFUSION_API_KEY`, NOT
`ANTHROPIC_API_KEY` — see the billing-safety note in §2.

[create a key from the Anthropic console]: https://console.anthropic.com/

---

## 1. Clone, create a venv, and install

On modern Ubuntu (24.04+) pip refuses system-wide installs by design
(PEP 668). **You must install DepthFusion into a virtualenv.** This
section walks through the full pattern.

### 1a. Clone the repo

```bash
# Pick a stable location you won't delete
git clone https://github.com/gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion
```

### 1b. Create and activate a virtualenv

```bash
# Use whichever Python 3.10+ you have. On Ubuntu 24.04 this is 3.12.
python3 -m venv ~/venvs/depthfusion
source ~/venvs/depthfusion/bin/activate
```

Your prompt should now show `(depthfusion)` at the front:

```
(depthfusion) gregmorris@host:~/projects/depthfusion$
```

If it doesn't, `source` failed — check that `~/venvs/depthfusion/bin/activate`
exists and run the source line again.

### 1c. Install DepthFusion with vps-cpu extras

```bash
# Upgrade pip first — venvs ship with a several-year-old pip
pip install --upgrade pip

# Install the library. The quotes around '.[vps-cpu]' are required —
# most shells treat [brackets] as glob characters.
pip install -e '.[vps-cpu]'
```

Takes 2-4 minutes; `anthropic` and `chromadb` are the big deps.

**Verify:**

```bash
python3 -c "import depthfusion; print('ok')"
# -> ok
```

### 1d. Make the venv auto-activate in new shells

Otherwise every `ssh` or `tmux new-session` drops you back to the
system Python and DepthFusion won't import. This block is idempotent
— safe to run multiple times.

```bash
grep -q "# depthfusion venv auto-activate" ~/.bashrc || cat >> ~/.bashrc <<'EOF'

# depthfusion venv auto-activate
if [ -z "$VIRTUAL_ENV" ] && [ -f "$HOME/venvs/depthfusion/bin/activate" ]; then
    source "$HOME/venvs/depthfusion/bin/activate"
fi

# depthfusion PS1 prefix enforcement — robust against whatever the
# activate script does or doesn't do with PS1 (Ubuntu 24.04's Python
# 3.12 venv sometimes fails to apply the (depthfusion) prompt prefix).
if [ -n "$VIRTUAL_ENV" ] && [[ "$PS1" != *"(depthfusion)"* ]]; then
    PS1="(depthfusion) $PS1"
fi
EOF
```

> **⚠ DO NOT `source ~/.bashrc` while the venv is already active.**
> Ubuntu's default `.bashrc` unconditionally reassigns `PS1`, which
> leaves your shell in a half-activated state (`$VIRTUAL_ENV` set but
> `$PATH` clobbered). If you want to test the auto-activate block
> without logging out, use `deactivate; exec bash` — that replaces
> the current shell with a fresh one that starts with empty state
> and re-runs `.bashrc` cleanly.

**Test in a fresh shell:**

```bash
exec bash           # replace current shell; picks up .bashrc fresh
which python3       # should print ~/venvs/depthfusion/bin/python3
echo "$VIRTUAL_ENV" # should print /home/$USER/venvs/depthfusion
```

Prompt should show `(depthfusion) …$`. If it doesn't, the PS1
enforcement line isn't firing — check `declare -p PS1` output for
clues and see the §1d troubleshooting in
[`../runbooks/gpu-vps-migration.md`](../runbooks/gpu-vps-migration.md)
(applies identically to vps-cpu installs).

From here on, every new SSH session auto-activates the venv.

---

## 2. Run the installer and add credentials

The installer merges with any existing `~/.claude/depthfusion.env` —
credentials you add before or after are preserved across re-installs.

### 2a. Run the installer

```bash
python3 -m depthfusion.install.install --mode=vps-cpu
```

The installer:
- Writes mode-specific defaults to `~/.claude/depthfusion.env`
- Registers PreCompact + PostCompact hooks in `~/.claude/settings.json`
- Registers the DepthFusion MCP server with Claude Code via `claude mcp add` (if the `claude` CLI is present)
- Warns that Haiku is disabled until you set the API key (next step)

### 2b. Add your credentials to the env file

Both `DEPTHFUSION_API_KEY` and `DEPTHFUSION_HAIKU_ENABLED=true` are
required for Haiku features to activate — the key alone isn't enough.
You can do this before or after the installer runs; re-running the
installer will not wipe these lines.

```bash
# Replace with your real key from https://console.anthropic.com/settings/keys
cat >> ~/.claude/depthfusion.env <<'EOF'
DEPTHFUSION_API_KEY=sk-ant-api03-your-real-key-here
DEPTHFUSION_HAIKU_ENABLED=true
EOF

chmod 600 ~/.claude/depthfusion.env   # file now contains a secret

# Sanity-check both made it in
grep -E "^(DEPTHFUSION_API_KEY|DEPTHFUSION_HAIKU_ENABLED)=" ~/.claude/depthfusion.env
```

> **⚠ Billing safety — use `DEPTHFUSION_API_KEY`, NOT `ANTHROPIC_API_KEY`.**
> Claude Code reads `ANTHROPIC_API_KEY` as its own auth credential
> and will switch your Pro/Max subscription to pay-per-token API
> billing for **all** Claude Code usage — not just DepthFusion.
> The separate `DEPTHFUSION_API_KEY` name exists specifically to
> prevent this (see E-12 S-22 in BACKLOG.md). The installer explicitly
> refuses to use `ANTHROPIC_API_KEY` even if it's set, by design.

### 2c. Make the shell auto-load the env file

Writing to `depthfusion.env` doesn't put variables in your shell
environment — nothing auto-sources it. Python tools read `os.environ`,
so without this block the factory check in 2e will route everything to
`null` despite the env file being correct. Append this alongside the
venv auto-activate block from §1d:

```bash
grep -q "# depthfusion env auto-source" ~/.bashrc || cat >> ~/.bashrc <<'EOF'

# depthfusion env auto-source — export vars from the config file
# so Python processes spawned from this shell see them via os.environ
if [ -f "$HOME/.claude/depthfusion.env" ]; then
    set -a
    source "$HOME/.claude/depthfusion.env"
    set +a
fi
EOF
```

> The `set -a` / `set +a` wrapper turns every plain `KEY=value` line
> in the env file into an exported variable, without requiring the
> file itself to use `export KEY=value` syntax.

### 2d. Reload the shell so the env file loads

Do NOT `source ~/.bashrc` while the venv is active — same half-activated-
state trap as §1d. Instead `deactivate` first, then `exec bash` so the
new shell starts with empty state and runs all auto-blocks from scratch:

```bash
deactivate 2>/dev/null; exec bash
```

Your prompt should still show `(depthfusion)`, and these env vars
should now be live:

```bash
echo "haiku: $DEPTHFUSION_HAIKU_ENABLED"
echo "key:   ${DEPTHFUSION_API_KEY:0:16}..."
```

Expected:
```
haiku: true
key:   sk-ant-api03-xxx...
```

### 2e. Verify the factory now routes to Haiku

```bash
python3 -c "
from depthfusion.backends.factory import get_backend
for cap in ('reranker', 'extractor', 'linker', 'summariser', 'decision_extractor'):
    b = get_backend(cap)
    print(f'{cap:22} -> {b.name:10} (healthy={b.healthy()})')
"
```

Expected: every LLM capability routes to `haiku` with `healthy=True`.
If any route to `null`, step through the checks in 2d — specifically
that `echo $DEPTHFUSION_API_KEY` returns a non-empty value in this
shell. If the env var is empty but the file contains the key, the
auto-source block didn't run (check that 2c actually appended to
`.bashrc` via `tail ~/.bashrc`).

---

## 3. Install the research tools

```bash
bash ~/projects/depthfusion/scripts/install-research-tools.sh
```

This installs:

- `~/.config/systemd/user/ciqs-weekly.service` + `.timer` — autonomous
  regression monitor; fires every Monday 06:00 local time
- Initial session-history corpus at `~/.local/share/depthfusion/corpus/`

**Dry-run first** if you want to see what it'll do:

```bash
bash ~/projects/depthfusion/scripts/install-research-tools.sh --dry-run
```

**Verify:**

```bash
# Timer should be listed, active, next run scheduled
systemctl --user list-timers ciqs-weekly.timer --no-pager

# Corpus file should exist and contain > 0 lines
ls -lh ~/.local/share/depthfusion/corpus/
head -1 ~/.local/share/depthfusion/corpus/corpus-*.jsonl | python3 -m json.tool
```

---

## 4. Smoke test the full pipeline

```bash
# End-to-end recall query via the MCP server's tool interface
python3 -c "
from depthfusion.mcp.server import _tool_recall
import json
result = json.loads(_tool_recall({'query': 'install verification test', 'top_k': 3}))
print(f'blocks returned: {len(result.get(\"blocks\", []))}')
print(f'error: {result.get(\"error\", \"none\")}')
"
```

Expected: some number of blocks (depends on what's indexed on this
host), no error. If the project has been in use, you'll see recall
results; on a fresh host the index is empty and you'll see 0 blocks —
that's fine, indexing populates as you use Claude Code.

---

## 5. Trigger the weekly monitor manually (optional sanity check)

```bash
# Fire once without waiting for Monday
systemctl --user start ciqs-weekly.service

# Read what it logged
journalctl --user -u ciqs-weekly.service -n 50 --no-pager
ls -t ~/.local/share/depthfusion/weekly-reports/ | head -5
```

On a fresh install the report will show "no data" — this is correct
and expected. Re-run after a week of real usage to see a populated
report.

---

## Done

You now have:

- ✅ DepthFusion running in `vps-cpu` mode (Haiku-backed)
- ✅ Weekly regression monitor scheduled
- ✅ Initial prompt corpus mined
- ✅ All three research tools (`ciqs_compare.py`, `mine_session_prompts.py`, `ciqs_weekly.py`) available under `scripts/`

## What's next

**For the parallel-comparison plan (measuring vps-gpu improvement
later):** let this host run for 1-2 weeks of real usage to accumulate
a CIQS baseline. When your GPU VPS comes online and you complete
[`vps-gpu-quickstart.md`](vps-gpu-quickstart.md), run three CIQS
baseline trials on each host (via `scripts/ciqs_harness.py`), then:

```bash
python3 scripts/ciqs_compare.py \
    --baseline-label "vps-cpu (this host)" \
    --baseline docs/benchmarks/YYYY-MM-DD-vps-cpu-run{1,2,3}-scored.jsonl \
    --candidate-label "vps-gpu (new host)" \
    --candidate <scp-ed-from-gpu-host>/*-scored.jsonl \
    --out docs/benchmarks/comparison.md
```

**For weekly monitoring:** the timer runs autonomously. Check
`~/.local/share/depthfusion/weekly-reports/` every so often. Any
regression flags the systemd unit as failed — you'll see it in
`systemctl --user status ciqs-weekly.timer` immediately.

**For freshness:** re-run the install script monthly to re-mine the
prompt corpus (captures new usage patterns):

```bash
bash ~/projects/depthfusion/scripts/install-research-tools.sh
```

It's idempotent — existing systemd units won't be disturbed.
