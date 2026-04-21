# DepthFusion Install Guides

Pick the path that matches your target host. Both install the same
research tools (session-history miner, weekly autonomous regression
monitor, two-mode CIQS comparison) — they differ only in the
DepthFusion **mode** and the hardware-specific setup steps.

| Guide | Target | Time | Hardware |
|---|---|---|---|
| [`vps-cpu-quickstart.md`](vps-cpu-quickstart.md) | Current Hetzner VPS or any CPU-only Linux host | ~10 min | None beyond CPU |
| [`vps-gpu-quickstart.md`](vps-gpu-quickstart.md) | Hetzner GEX44 or any CUDA-capable host | ~4 hrs | NVIDIA GPU + CUDA 12 |

Both guides invoke `scripts/install-research-tools.sh` for the
monitoring + mining tooling, which is **mode-agnostic** — the same
tools work whether you're running `vps-cpu` or `vps-gpu`. The only
differences between the two guides are:

- Which extras to `pip install` (`[vps-cpu]` vs `[vps-gpu]`)
- Which mode to pass the installer (`--mode=vps-cpu` vs `--mode=vps-gpu`)
- GPU path requires vLLM systemd service setup; see the GPU guide

## Which one for the "run both in parallel" plan?

If you're comparing vps-cpu against vps-gpu to measure the GPU
improvement (S-66 / S-43 AC-2 / S-44 AC-2), you run **both**:

1. On your current VPS → `vps-cpu-quickstart.md` → collect baseline
2. On the new GPU VPS → `vps-gpu-quickstart.md` → collect candidate
3. Then `scripts/ciqs_compare.py` produces the delta report

Neither path requires the other. The comparison tool waits for two
sets of scored runs; it doesn't need both hosts online simultaneously.

## Re-running the installers

Both guides' scripts are **idempotent** — safe to re-run. Use this
when:

- Upgrading DepthFusion versions
- Re-running after changing `DEPTHFUSION_*` env variables
- Monthly fresh-mining of the prompt corpus (recommended)

## Troubleshooting

If the install script fails, it prints what prerequisite is missing
and what to do. If the systemd timer doesn't fire at its next
scheduled time, check:

```bash
systemctl --user list-timers ciqs-weekly.timer --no-pager
journalctl --user -u ciqs-weekly.service -n 50
```

On headless VPSes where systemd `--user` isn't usable, the installer
prints a cron fallback command.
