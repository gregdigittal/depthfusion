# DepthFusion Configuration Profiles

DepthFusion ships three named profiles that collapse the combinatorial config space into
sensible operational presets. A profile is selected by setting `DEPTHFUSION_PROFILE` in
the server environment (or `~/.config/depthfusion/.env`).

## Profiles

| Profile | `DEPTHFUSION_PROFILE` value | Use case |
|---|---|---|
| **minimal** | `minimal` | Low-resource single-user installs; BM25 only, no AI passes |
| **standard** | `standard` (default) | Recommended for most self-hosted deployments |
| **research** | `research` | High-fidelity recall; enables all cognitive and fusion features |

### `minimal`

Suitable for developer laptops or CI environments with no GPU and no OpenAI key.

```
DEPTHFUSION_PROFILE=minimal
```

Effective flags:
- `DEPTHFUSION_ROUTER_ENABLED=false`
- `DEPTHFUSION_SESSION_ENABLED=false`
- `DEPTHFUSION_FUSION_GATES_ENABLED=false`
- `DEPTHFUSION_COGNITIVE_SCORING=false`
- `DEPTHFUSION_EMBEDDING_BACKEND=bm25`

### `standard` (default)

Balanced profile for production VPS deployments. BM25 retrieval with session tracking
and routing enabled; AI-powered passes are off by default.

```
DEPTHFUSION_PROFILE=standard
# or omit — standard is the default
```

Effective flags:
- `DEPTHFUSION_ROUTER_ENABLED=true`
- `DEPTHFUSION_SESSION_ENABLED=true`
- `DEPTHFUSION_FUSION_GATES_ENABLED=false`
- `DEPTHFUSION_COGNITIVE_SCORING=false`
- `DEPTHFUSION_EMBEDDING_BACKEND=bm25`

### `research`

Full feature set. Requires an OpenAI-compatible embedding backend and sufficient VRAM or
API budget. Enables Fernet cache, fusion gates, and cognitive scoring.

```
DEPTHFUSION_PROFILE=research
DEPTHFUSION_EMBEDDING_BACKEND=openai
OPENAI_API_KEY=<your-key>
```

Effective flags:
- `DEPTHFUSION_ROUTER_ENABLED=true`
- `DEPTHFUSION_SESSION_ENABLED=true`
- `DEPTHFUSION_FUSION_GATES_ENABLED=true`
- `DEPTHFUSION_COGNITIVE_SCORING=true`
- `DEPTHFUSION_EMBEDDING_BACKEND=openai`

## Overriding individual flags

Any flag can be overridden on top of a profile. The profile sets the baseline; explicit
env vars win:

```bash
DEPTHFUSION_PROFILE=standard
DEPTHFUSION_COGNITIVE_SCORING=true   # overrides the standard-profile default
```

## Inspecting the active configuration

Use `depthfusion_status` to see every effective flag, including profile-derived values
and any override:

```bash
python -m depthfusion.mcp.server --tool depthfusion_status
```

Or via MCP from Claude Code:

```
Use the depthfusion_status tool
```

## Environment variable reference

All `DepthFusionConfig` fields map 1:1 to env vars with the `DEPTHFUSION_` prefix.
See `src/depthfusion/core/config.py` for the full field list and defaults.
The two fields previously wired outside config (`DEPTHFUSION_FUSION_GATES_ENABLED`,
`DEPTHFUSION_COGNITIVE_SCORING`) are now part of `DepthFusionConfig` as of E-67 S-220.
