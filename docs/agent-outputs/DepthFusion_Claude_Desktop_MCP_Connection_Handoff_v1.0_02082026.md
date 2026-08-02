# DepthFusion Claude Desktop MCP Connection Handoff

**Date:** 2026-08-02  
**Target repository:** `/home/gregmorris/projects/depthfusion`  
**VPS baseline inspected:** `main@8c2e315ea94c721b04d61aa5dd55bb8e9fba9e66`  
**Status:** Diagnosis complete; implementation pending

## Objective

Restore the DepthFusion MCP connection in Claude Desktop on Greg's Mac, fix the underlying packaging/install defect, and keep the new Streamable HTTP transport safe and accurately documented.

## Executive conclusion

Claude Desktop is not failing because it stopped supporting local stdio MCP servers. It launches DepthFusion over stdio, sends the MCP `initialize` request, and then the Python process exits during module import.

The confirmed exception is:

```text
ModuleNotFoundError: No module named 'cryptography'
```

The new `/mcp` Streamable HTTP implementation is relevant to Claude Code and remote deployments, but switching Claude Desktop to HTTP will not fix this crash. The HTTP server imports the same identity/auth stack and also needs the missing dependency.

## Evidence collected on the Mac

### Active Claude Desktop configuration

Configuration file:

```text
/Users/gregmorris/Library/Application Support/Claude/claude_desktop_config.json
```

DepthFusion is registered as a local stdio process:

```json
{
  "command": "/Users/gregmorris/.depthfusion-venv/bin/python",
  "args": ["-m", "depthfusion.mcp.server"],
  "env": {
    "DEPTHFUSION_ENV_FILE": "/Users/gregmorris/.claude/depthfusion.env"
  }
}
```

Claude Desktop version inspected: `1.24012.9`.

### Runtime log

Log file:

```text
/Users/gregmorris/Library/Logs/Claude/mcp-server-depthfusion.log
```

Observed sequence:

1. Claude starts `/Users/gregmorris/.depthfusion-venv/bin/python`.
2. Claude sends `initialize`.
3. Python imports `depthfusion.mcp.server`.
4. Import fails in `depthfusion.identity.token_validator` because `cryptography` is absent.
5. Claude surfaces the generic `MCP error -32000: Connection closed`.

### Active installed source

The editable virtualenv package points to:

```text
/Users/gregmorris/depthfusion
```

It was inspected at `main@dbb81c749c757068df1d2f932910b34bdd3e2b82`, behind the current remote/VPS main.

A second, non-active checkout exists at:

```text
/Users/gregmorris/Development Projects/depthfusion
```

That checkout was at `401b857fe06ff96ec46a130ba39b7c40c693ba55` and is substantially older. Do not patch this second checkout expecting Claude Desktop to use it.

### Dependency state

In `/Users/gregmorris/.depthfusion-venv`:

```text
fastapi=True
uvicorn=True
cryptography=False
```

`pip check` reports `No broken requirements found` because `cryptography` is not declared as a required runtime dependency for the installed mode.

## Confirmed import chain

```text
depthfusion.mcp.server
  -> from depthfusion.identity.models import Principal
  -> Python executes depthfusion.identity.__init__
  -> identity.__init__ eagerly imports fastapi_deps
  -> fastapi_deps imports token_validator
  -> token_validator imports cryptography
  -> ModuleNotFoundError
```

Relevant files:

- `src/depthfusion/mcp/server.py`
- `src/depthfusion/identity/__init__.py`
- `src/depthfusion/identity/fastapi_deps.py`
- `src/depthfusion/identity/token_validator.py`
- `pyproject.toml`
- `scripts/install-mac-mlx.sh`

## Immediate Mac recovery

Run on the Mac:

```bash
cd /Users/gregmorris/depthfusion
git pull --ff-only

/Users/gregmorris/.depthfusion-venv/bin/python \
  -m pip install -e '.[mac-mlx]' 'cryptography>=49.0.0'

/Users/gregmorris/.depthfusion-venv/bin/python \
  -B -c 'import depthfusion.mcp.server; print("DepthFusion MCP import OK")'
```

Then fully quit and reopen Claude Desktop. Keep the existing stdio configuration for the immediate recovery.

Expected outcome:

- The import probe prints `DepthFusion MCP import OK`.
- Claude's DepthFusion MCP log no longer contains `ModuleNotFoundError`.
- Claude Desktop reports DepthFusion connected and lists its tools.

## Durable code fix

### P0.1 — Remove eager optional-dependency imports

Make `depthfusion.identity.__init__` lightweight. Importing `depthfusion.identity.models` must not load FastAPI, OIDC, JWKS, or cryptography.

Preferred approach:

- Eagerly export only dependency-light models and errors.
- Preserve existing public imports such as `from depthfusion.identity import TokenValidator` using module-level lazy `__getattr__` imports.
- Do not break the identity package's documented public API.

This preserves the promise that local/BM25 stdio mode does not require the HTTP/OIDC stack.

### P0.2 — Correct dependency declarations

`cryptography>=49.0.0` is a direct import in `token_validator.py`; it must be declared for every install mode that can load HTTP/OIDC auth.

At minimum, add it to:

- `mac-mlx`
- `vps-cpu`
- `vps-gpu`
- `dev` (already present; retain it)

Consider a dedicated `http` or `auth` extra containing FastAPI, Uvicorn, and cryptography, then make installers explicitly request it. Avoid silently depending on transitive packages from ChromaDB.

### P0.3 — Make the installer fail fast

Immediately after the pip install in `scripts/install-mac-mlx.sh`, require:

```bash
"$VENV_PYTHON" -B -c 'import depthfusion.mcp.server'
```

The current smoke test only imports the MCP server if the REST health endpoint is already responding. When health is unavailable it prints a warning, skips the import, and still reports installation complete. Change this so an MCP import failure terminates installation with the actionable traceback.

Apply the same import probe to other platform installers.

### P0.4 — Add clean-environment packaging gates

For each supported install mode, create a fresh virtualenv, install the built package without `dev`, and run:

```bash
python -B -c 'import depthfusion.mcp.server'
python -m pip check
```

The test must not inherit dependencies from the development environment. A normal pytest run cannot expose this packaging error because the `dev` extra already installs cryptography.

## Streamable HTTP review

Relevant commits merged on 2026-08-02:

- `8c76dc47` — initial `POST /mcp` Streamable HTTP endpoint
- `8ef3f5d1` — session lifecycle, GET/DELETE, notification handling, negotiation tests
- `b5c866d2` — PR #41 merge

The transport addition does not require removing the local stdio entry from Claude Desktop. Anthropic treats local Desktop MCP and remote custom connectors as separate mechanisms.

### P1.1 — Add mandatory Origin validation

The MCP 2025-03-26 transport specification requires the server to validate the `Origin` header when present and return HTTP 403 for invalid origins. The current `src/depthfusion/mcp/http_server.py` contains no Origin handling despite the claim of full compliance.

Add a shared request dependency or middleware for `/mcp`, `/sse`, and `/messages`, backed by an explicit allowed-origin configuration. Retain loopback-only binding by default.

### P1.2 — Complete negotiation/version tests

Add tests for:

- Invalid `Origin` -> 403.
- Valid configured Origin -> accepted.
- POST `Accept` behavior required by the protocol.
- Protocol-version validation on POST, GET, and DELETE as applicable.
- Clean handling of malformed JSON and non-object/batch payloads.

### P1.3 — Unify HTTP authentication configuration

The module contains `_check_mcp_auth` logic using `DEPTHFUSION_MCP_TOKEN`, while the actual endpoints use `Depends(require_principal)`, whose legacy path uses `DEPTHFUSION_V2_LEGACY_AUTH=1` plus `DEPTHFUSION_API_TOKEN`.

Choose one authentication path, remove dead or misleading code, and make the module docstring, deployment docs, systemd environment, and curl examples use the same variables.

### P1.4 — Clarify transport documentation

Document three distinct scenarios:

1. Claude Desktop local MCP: stdio subprocess or an MCPB desktop extension.
2. Claude Code shared/local server: Streamable HTTP at `/mcp`, with legacy `/sse` only as fallback.
3. Claude remote custom connector: public HTTPS endpoint reachable from Anthropic infrastructure, normally with OAuth; `127.0.0.1`, Tailscale-only, or private-VPN URLs will not work as cloud-brokered connectors.

The current HTTP guide adds `/mcp` but still shows Claude Code registered as `type: sse` against `/sse`. Update the primary example to the new transport and mark SSE as backward compatibility.

## Acceptance criteria

- [ ] Fresh `local` install can import and start the stdio MCP server without FastAPI or cryptography installed.
- [ ] Fresh `mac-mlx`, `vps-cpu`, and `vps-gpu` installs include every direct HTTP/OIDC dependency.
- [ ] The Mac virtualenv imports `depthfusion.mcp.server` successfully.
- [ ] Claude Desktop connects over the existing stdio configuration and lists tools.
- [ ] Installer exits non-zero when the MCP entry point cannot import.
- [ ] Clean-venv packaging CI covers all supported extras.
- [ ] HTTP `/mcp` rejects invalid Origin headers.
- [ ] HTTP auth environment variables and documentation are consistent.
- [ ] Documentation distinguishes local Desktop stdio from cloud-brokered remote connectors.
- [ ] Full repository review gate passes: `pytest -v --tb=short`.

## VPS working-tree caution

At handoff creation time, the VPS had three pre-existing untracked files:

```text
.agent-hub/outputs/arch-analysis-2026-07-11.md
.agent-hub/outputs/e65-wizard-verification-2026-06-25.md
.agent-hub/outputs/rectification-plan-2026-07-11.md
```

They belong to the existing session. Preserve them and do not delete, overwrite, stage, or commit them unless explicitly requested.

## Suggested first prompt for the VPS DepthFusion session

```text
Read AGENTS.md, PLAN.md, and docs/agent-outputs/DepthFusion_Claude_Desktop_MCP_Connection_Handoff_v1.0_02082026.md. Implement the P0 packaging and installer fixes first, add clean-environment regression coverage, run the review gate, then review the P1 HTTP compliance items. Preserve all pre-existing untracked files and do not change the Mac or VPS service configuration until tests pass.
```

## Security follow-up outside DepthFusion

During diagnosis, a Supabase personal access token was found embedded directly in Claude Desktop command arguments and was exposed in diagnostic output. Rotate it and move the replacement into an environment variable, Keychain-backed desktop extension setting, or other secure secret store. Do not record the token value in this repository.
