# Resolution: Dependabot yt-dlp alerts 65 / 66 / 67

> Date: 2026-06-21
> Status: Resolved (alerts dismissed — `not_used`)
> Repo: gregdigittal/depthfusion

## Alerts

| # | GHSA | Severity | Vulnerability | Manifest referenced |
|---|------|----------|---------------|---------------------|
| 67 | GHSA-69qj-pvh9-c5wg | High | yt-dlp command injection | `requirements-freeze.txt` |
| 66 | GHSA-vx4q-3cr2-7cg2 | High | yt-dlp aria2c RCE | `requirements-freeze.txt` |
| 65 | GHSA-c6mh-fpjc-4pr3 | High | yt-dlp filename sanitization | `requirements-freeze.txt` |

## Resolution path: dependency removed (stale alerts)

`yt-dlp` is **not** a current direct or transitive dependency of DepthFusion.
Verified by:

```bash
# All three manifests — no match (prints YTDLP_ABSENT_OK)
grep -in 'yt-dlp\|yt_dlp' uv.lock pyproject.toml requirements-freeze.txt || echo 'YTDLP_ABSENT_OK'

# Source + docs — no import or reference
grep -rin 'yt_dlp\|yt-dlp\|youtube' src/
```

The alerts referenced a **removed** `requirements-freeze.txt` entry. The freeze
file was regenerated from a clean `uv pip install -e .` in commit `0bf6393`
("fix(deps): regenerate requirements-freeze.txt to close Dependabot alerts"),
which is an ancestor of `main` HEAD. That regenerated snapshot no longer pins
`yt-dlp`, so the dependency is gone from every manifest tracked by Dependabot.

Because the dependency is absent, no version bump or `yt-dlp>=2026.6.9`
lower-bound constraint was required in `pyproject.toml`. Adding a floor for a
package that is not installed would be dead configuration.

## Action taken

The three alerts were dismissed with reason `not_used` and the comment:

> yt-dlp removed from requirements-freeze.txt (commit 0bf6393) and not imported
> anywhere in the codebase; manifest no longer contains this dependency.

No new high/critical alert is introduced — no version bump was performed.
