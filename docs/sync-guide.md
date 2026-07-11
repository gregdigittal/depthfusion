# DepthFusion Sync Guide

Bidirectional rsync of discovery and memory files between local machine and VPS
(`gregmorris@77.42.45.197`).

---

## Usage

```bash
./sync.sh                    # bidirectional (pull then push) — default
./sync.sh --push             # local → VPS only
./sync.sh --pull             # VPS → local only
./sync.sh --dry-run          # preview without writing
./sync.sh --discoveries-only # discoveries only, skip memory dirs
```

Flags can be combined: `./sync.sh --push --dry-run`

**What gets synced:** `~/.claude/shared/discoveries/` and `~/.claude/projects/*/memory/`
(excludes `.depthfusion-*`, `*.tmp`, `depthfusion.env`, and `MEMORY.md`)

---

## Conflict Resolution

The script uses `--update --ignore-existing`. Combined effect:

| Scenario | Result |
|----------|--------|
| File exists only on source | Copied to destination |
| File exists on both sides | Destination is **never overwritten** |

This is a **merge-without-overwrite** strategy: safe and idempotent. **First writer wins.**

If the same filename is written independently on both machines before a sync, the local copy
survives — the VPS version already exists and is not touched.

**Why this rarely matters:** Discovery filenames embed a session stem (e.g.
`depthfusion-2026-04-15T09-32-autocapture.md`). Two machines generating the same stem at the same
second is effectively impossible.

**If a conflict does occur:**

```bash
diff ~/.claude/shared/discoveries/the-file.md \
     <(ssh gregmorris@77.42.45.197 cat ~/.claude/shared/discoveries/the-file.md)
# Manually merge, rename one copy if both contain unique content, then re-push.
```

---

## Automated Scheduling

### Cron (recommended for local machine)

```bash
crontab -e
```

```cron
# Sync every 30 minutes
*/30 * * * * /home/gregmorris/projects/depthfusion/sync.sh --both >> /var/log/depthfusion-sync.log 2>&1
```

To sync on session end, add a push to the PostCompact hook:

```bash
# ~/.claude/hooks/depthfusion-post-compact.sh
#!/usr/bin/env bash
/home/gregmorris/projects/depthfusion/sync.sh --push >> /tmp/depthfusion-sync.log 2>&1 &
```

```bash
chmod +x ~/.claude/hooks/depthfusion-post-compact.sh
```

### Systemd Timer (recommended for VPS)

`Persistent=true` fires any missed runs immediately on next boot.

```bash
mkdir -p ~/.config/systemd/user
```

`~/.config/systemd/user/depthfusion-sync.service`:

```ini
[Unit]
Description=DepthFusion discovery sync
[Service]
Type=oneshot
ExecStart=/home/gregmorris/projects/depthfusion/sync.sh --both
StandardOutput=append:/var/log/depthfusion-sync.log
StandardError=append:/var/log/depthfusion-sync.log
```

`~/.config/systemd/user/depthfusion-sync.timer`:

```ini
[Unit]
Description=DepthFusion sync — every 30 minutes
[Timer]
OnCalendar=*:0/30
Persistent=true
[Install]
WantedBy=timers.target
```

Enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now depthfusion-sync.timer
systemctl --user list-timers depthfusion-sync.timer   # verify
```

Check logs: `tail -f /var/log/depthfusion-sync.log`
