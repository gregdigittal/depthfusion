# Digittal Method Memory — depthfusion

Last updated by run: Resolve all open items on main: (1) mark BACKLOG tasks T-738 through T-756 as complete [x] in BACKLOG.md — all were implemented in PR #26; (2) bump node-version from '20' to '22' in .github/workflows/ci.yml, release-desktop.yml, security.yml, sbom.yml, and tauri-build.yml; (3) bump Python deps in pyproject.toml to fix Dependabot alerts: pydantic-settings>=2.14.2, yt-dlp>=2026.6.9, starlette>=1.3.0, PyJWT>=2.13.0, then re-lock with uv; (4) update pnpm-lock.yaml in /app by running pnpm install (esbuild override already in package.json). Success: CI passes on push, no Dependabot moderate/medium alerts remain, all T-738–T-756 tasks show [x].

## Curated Rules

- [rung:4|conf:0.95] Before editing for verification-style tasks, inspect the current repository state first; if the requested state is already present, record it as no-op rather than attempting redundant changes.
- [rung:4|conf:0.9] For CI workflow node version bumps, grep the workflow files for the existing version string before patching so already-completed migrations are detected early and no-op tasks are reported cleanly.
- [rung:4|conf:0.85] For Dependabot alerts on transitive Python dependencies, add an explicit lower-bound constraint in the relevant pyproject.toml extras section and regenerate the lockfile with uv lock rather than editing uv.lock directly.
- [rung:4|conf:0.9] For lockfile-related dependency alerts, read the lockfile resolution before acting; the vulnerable dependency may already be resolved to a fixed version and the alert may be stale.
