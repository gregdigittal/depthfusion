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
# On the target host
python3 --version   # must be 3.10+
pip --version       # any recent pip
systemctl --user status  # must exit 0; needed for weekly timer
```

If `systemctl --user` reports "No session" or similar on a headless
VPS, run once as root:

```bash
sudo loginctl enable-linger $USER
```

This makes your user's systemd available without an active login session.

You'll also need a DepthFusion API key:

```bash
# Check: is DEPTHFUSION_API_KEY already set?
grep DEPTHFUSION_API_KEY ~/.claude/depthfusion.env 2>/dev/null || echo "not set"
```

If not set, [create a key from the Anthropic console] and have it
ready for step 2.

[create a key from the Anthropic console]: https://console.anthropic.com/

---

## 1. Clone and install

```bash
# Pick a stable location you won't delete
git clone https://github.com/gregdigittal/depthfusion.git ~/projects/depthfusion
cd ~/projects/depthfusion

# Install the library with CPU extras
pip install -e '.[vps-cpu]'
```

The `-e` (editable) flag means updates via `git pull` don't require
re-pip-install. Drop `-e` for a production pin.

**Verify:**

```bash
python3 -c "import depthfusion; print('ok')"
# -> ok
```

---

## 2. Run the interactive installer

```bash
python3 -m depthfusion.install.install --mode=vps-cpu
```

The installer prompts for:
- `DEPTHFUSION_API_KEY` (your Anthropic key, from step 0)
- Confirmation of the auto-detected project root
- Whether to install the Claude Code MCP integration

It writes `~/.claude/depthfusion.env` with all settings.

**Verify installation:**

```bash
python3 -c "
from depthfusion.backends.factory import get_backend
for cap in ('reranker', 'extractor', 'linker', 'summariser', 'decision_extractor'):
    b = get_backend(cap)
    print(f'{cap:22} -> {b.name:10} (healthy={b.healthy()})')
"
```

Expected: every LLM capability routes to `haiku` with `healthy=True`.
If any route to `null`, check that `DEPTHFUSION_API_KEY` is in
`~/.claude/depthfusion.env`.

---

## 3. Register the MCP server with Claude Code

**Important — this step is easy to miss.** The previous step set up
hooks and env config, but did NOT register DepthFusion's MCP tools
(recall, confirm-discovery, prune) with Claude Code. Without this,
Claude Code sessions won't have access to the tools even though the
library is installed.

```bash
# Register DepthFusion as an MCP server at user scope.
# --scope user writes to ~/.config/claude/... — available in every
# Claude Code session on this host, not tied to any single project.
claude mcp add depthfusion --scope user -- python3 -m depthfusion.mcp.server
```

**Verify:**

```bash
claude mcp list
# DepthFusion should appear. If not, check the command ran without error.
```

> **Why isn't this automatic?** The installer doesn't invoke
> `claude mcp add` today — tracked as a v0.7 polish item (see
> `BACKLOG.md` E-17 S-67). Once that lands, step 3 will be folded
> into step 2.

---

## 4. Install the research tools

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

## 5. Smoke test the full pipeline

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

## 6. Trigger the weekly monitor manually (optional sanity check)

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
