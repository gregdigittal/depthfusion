# BI Tool Connectivity — DepthFusion Query API

> **This doc covers both V1 and V2.** Section **0** (V2) is the current,
> recommended path: JWT-authenticated analytics endpoints with RBAC- and
> classification-ceiling-gated service accounts. Sections **1–7** describe the
> legacy V1 SSH-tunnel + API-key model and remain for deployments still on the
> V1 query API. If you are on the `v2-enterprise` branch, use Section 0.

---

## 0. V2 — Authenticated Analytics Endpoints (current)

V2 replaces the V1 `X-DepthFusion-Key` header model with **OIDC/JWT bearer
auth** and adds purpose-built aggregate + facet endpoints under
`/v2/analytics`. Access is scoped per-principal and trimmed by classification
ceiling — a BI tool never sees data above the ceiling assigned to its service
account.

### 0.1 Authentication — JWT bearer (RS256)

Every `/v2/analytics/*` request must carry an `Authorization: Bearer <jwt>`
header. The server validates the token against the configured JWKS endpoint
(RS256) and derives the principal from the `sub` claim — callers can only see
their own metrics; the principal is **never** taken from a query parameter.

Server configuration (env vars):

| Variable | Purpose |
|---|---|
| `DEPTHFUSION_JWKS_URI` | JWKS endpoint URL for RS256 public keys |
| `DEPTHFUSION_OIDC_ISSUER` | Expected `iss` claim |
| `DEPTHFUSION_OIDC_AUDIENCE` | Expected `aud` claim |

If issuer/audience are unset the endpoints return **503** (auth not
configured). A development-only fallback exists — set
`DEPTHFUSION_ALLOW_UNAUTH_ANALYTICS=1` to accept the raw bearer string as a
dev principal — but it logs a prominent WARNING at startup and **must never be
enabled in production**.

The raw bearer token is never stored, logged, or returned.

### 0.2 V2 endpoints

| Endpoint | Description | Key parameters |
|---|---|---|
| `GET /v2/analytics/summary` | Aggregated usage counts (searches/ingests/syncs) for the authenticated principal | `period` (e.g. `7d`, `30d`, max `365d`) |
| `GET /v2/analytics/facets` | Faceted usage counts grouped by a facet dimension | `facet` (allowlisted; currently `event_type`), `period` |

**Summary response shape:**

```json
{
  "principal_id": "user-sub-claim",
  "period_days": 7,
  "period_start": "2026-06-10",
  "period_end": "2026-06-17",
  "total_events": 42,
  "by_event_type": {"search": 30, "ingest": 8, "sync": 4}
}
```

**Facet response shape:**

```json
{
  "principal_id": "user-sub-claim",
  "facet": "event_type",
  "period_days": 7,
  "period_start": "2026-06-10",
  "period_end": "2026-06-17",
  "total": 42,
  "buckets": {"search": 30, "ingest": 8, "sync": 4}
}
```

> **Performance (T-622).** The facet/aggregate group-bys are backed by a
> composite index `analytics_events(principal_id, recorded_at, event_type)`,
> which is *covering* for the hot `WHERE principal_id = ? AND recorded_at
> BETWEEN ? AND ? GROUP BY event_type` query. The facet path meets the
> p95 < 500 ms SLO (see `tests/test_analytics.py::TestFacetPerformance`).

> **Facet allowlist.** The `facet` parameter is validated against a server-side
> allowlist (`event_type`); `principal_id` is intentionally **not** facetable,
> so a caller can never group across principals. An unsupported facet returns
> **422**.

```bash
# Summary, last 30 days
curl -s "https://<host>/v2/analytics/summary?period=30d" \
  -H "Authorization: Bearer $DEPTHFUSION_JWT" | jq .

# Facet breakdown by event type, last 7 days
curl -s "https://<host>/v2/analytics/facets?facet=event_type&period=7d" \
  -H "Authorization: Bearer $DEPTHFUSION_JWT" | jq .
```

### 0.3 BI-tool service accounts + classification ceiling (T-624)

BI tools connect through a **service account** rather than an interactive
user. A service account is a scoped, read-only principal carrying a
**classification ceiling** — the highest sensitivity level its bearer may see.

Two hard rules:

1. **The ceiling is server-assigned at issuance, never hardcoded** by the BI
   tool or client config. The default issuance ceiling is the least-privilege
   level (`public`); it may only be raised by an explicit, audited issuance
   request.
2. **Records above the ceiling are excluded** server-side before any rows are
   returned. A record with a missing/unknown classification is treated as
   `restricted` (default-deny).

Classification ceiling is inclusive and ordered
`public < internal < confidential < restricted`. A `confidential` ceiling
admits public/internal/confidential records and excludes restricted.

Issue a service account (admin, server-side):

```python
from depthfusion.identity import issue_service_account
from depthfusion.authz.classification import ClassificationLevel

acct = issue_service_account(
    name="metabase-prod",
    ceiling=ClassificationLevel.INTERNAL,   # server-assigned, audited
    scopes=("query:read",),                  # read-only; write scopes rejected
)
print(acct.account_id, acct.token)           # persist alongside BI tool config
```

The BI tool then presents `acct.token` as the bearer; the query path calls
`filter_records_by_ceiling(records, acct)` so only at-or-below-ceiling records
are returned. Service accounts are strictly read-only — any write/create/
update/delete/admin/manage scope is refused at issuance.

### 0.4 V2 connection flows for Metabase / Grafana / Power BI

The V2 endpoints are plain JSON over HTTPS with a bearer token, so every BI
tool's generic REST/JSON connector works. The only change vs V1 is the auth
header: replace `X-DepthFusion-Key: <key>` with `Authorization: Bearer <jwt>`.

**Metabase** (HTTP / REST API connector or Infinity-style proxy):
1. Admin → Databases → Add Database → REST/HTTP connector.
2. Base URL: `https://<host>/v2/analytics`.
3. Header: `Authorization` → `Bearer <service-account-token>`.
4. Build cards against `summary` / `facets` (e.g. bar chart of `buckets`).

**Grafana** (Infinity data source — recommended):
1. Add data source → Infinity.
2. Base URL: `https://<host>/v2/analytics`.
3. Headers → add `Authorization` = `Bearer <service-account-token>`.
4. Panel query → Type JSON, GET `/facets?facet=event_type&period=7d`,
   root selector `buckets` (or `/summary`, selector `by_event_type`).
5. Save & Test → expect 200.

**Power BI** (Get Data → Web):
1. Home → Get Data → Web → Advanced.
2. URL: `https://<host>/v2/analytics/summary?period=30d`.
3. HTTP request header parameters: `Authorization` = `Bearer <token>`.
4. OK → Power Query expands the JSON. Convert `by_event_type` /`buckets`
   record to a table via *Transform → Record → To Table*.

Validation: each flow above was exercised against the live endpoints with a
service account; a 200 with the documented JSON shape confirms connectivity,
and supplying a stale/invalid bearer returns 401, confirming auth enforcement.

---

## V1 — Legacy SSH-tunnel + API-key model

> The sections below describe the V1 query API (`/query/*` on `127.0.0.1:7300`)
> using the `X-DepthFusion-Key` header. Retained for deployments still on V1.



The DepthFusion REST API exposes three query endpoints on `127.0.0.1:7300` (loopback only by
default). To connect BI tools running on other machines, use an SSH tunnel. Public binding is
supported but requires both `DEPTHFUSION_API_TOKEN` (bearer) and `DEPTHFUSION_QUERY_API_KEY`
(header) to be set — see the Security section below.

---

## 1. SSH Tunnel Setup

Open a tunnel from your local machine to the server running DepthFusion:

```bash
# Forward localhost:7300 on your laptop to localhost:7300 on the server
ssh -N -L 7300:127.0.0.1:7300 gregmorris@176.9.147.206
```

Keep this shell open while the BI tool is in use. With `-N` the session stays open without
running a command. Use `-f` to background it:

```bash
ssh -f -N -L 7300:127.0.0.1:7300 gregmorris@176.9.147.206
```

To kill a backgrounded tunnel:

```bash
pkill -f "ssh -f -N -L 7300"
```

### Persistent tunnel via systemd (optional)

Create `/etc/systemd/system/df-tunnel.service` on your laptop:

```ini
[Unit]
Description=DepthFusion API tunnel
After=network.target

[Service]
ExecStart=/usr/bin/ssh -N -o ServerAliveInterval=60 \
  -L 7300:127.0.0.1:7300 gregmorris@176.9.147.206
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now df-tunnel
```

---

## 2. API Key Configuration

By default (loopback only), no API key is required. When `DEPTHFUSION_QUERY_API_KEY` is set on
the server, every `/query/*` request must include:

```
X-DepthFusion-Key: <your-key>
```

Generate a key on the server:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Add to server environment:
# DEPTHFUSION_QUERY_API_KEY=<output>
```

All BI tool configurations below show where to add this header.

---

## 3. Endpoints Reference

| Endpoint | Description | Key parameters |
|---|---|---|
| `GET /query/discoveries` | Discovery files from `~/.claude/shared/discoveries/` | `project`, `agent`, `tags`, `from`, `to`, `cursor`, `limit` |
| `GET /query/sessions` | Recall JSONL events from `~/.claude/depthfusion-metrics/` | `agent`, `from`, `to`, `cursor`, `limit` |
| `GET /query/aggregate` | Aggregated latency/count stats | `from`, `to` |

Full OpenAPI spec: `docs/api/query-api.yaml`. Interactive docs at `http://127.0.0.1:7300/docs`
(when the server is running).

### Pagination

All list endpoints support cursor pagination. Pass `limit` and use `next_cursor` from the
response as `cursor` for the next page:

```
GET /query/sessions?limit=500
→ {"items":[...], "total":1200, "count":500, "next_cursor":"NTAw"}

GET /query/sessions?limit=500&cursor=NTAw
→ {"items":[...], "total":1200, "count":500, "next_cursor":"MTAwMA=="}
```

---

## 4. Metabase

### Connection

Metabase does not natively query REST APIs. Use the **REST API Connector** plugin or a proxy
approach via **Metabase Questions → Custom API** (available in Metabase v0.49+).

Simpler approach: use a **Metabase JSON/JSONB data source** by writing a thin proxy table in
PostgreSQL that pulls from the DepthFusion API, or use the Metabase HTTP connector:

1. Open Metabase → Admin → Databases → Add Database → **REST API** (if plugin installed)
2. Set base URL: `http://127.0.0.1:7300`
3. Add header: `X-DepthFusion-Key: <your-key>`

### Sample queries (HTTP connector / curl)

**Last 7 days of aggregate stats:**

```bash
curl -s "http://127.0.0.1:7300/query/aggregate?from=$(date -d '7 days ago' '+%Y-%m-%d')&to=$(date '+%Y-%m-%d')" \
  -H "X-DepthFusion-Key: $DEPTHFUSION_QUERY_API_KEY" | jq .
```

**All sessions for agent `vps` this week:**

```bash
curl -s "http://127.0.0.1:7300/query/sessions?agent=vps&from=$(date -d 'monday' '+%Y-%m-%d')&limit=200" \
  -H "X-DepthFusion-Key: $DEPTHFUSION_QUERY_API_KEY"
```

### Sample Metabase dashboard JSON

See `docs/api/metabase-dashboard.json` — a portable dashboard export with three pre-built cards:

- **Recall Events by Day** (bar chart, `total_events` from `/query/aggregate` by date)
- **Mode Distribution** (pie chart, `modes` breakdown)
- **p95 Latency Trend** (line chart, `p95_latency_ms` over rolling 30 days)

To import: Metabase → Browse Data → ⋮ → Import Dashboard → select the JSON file.

---

## 5. Grafana

### Data source setup

Grafana supports REST APIs via the **JSON API** or **Infinity** data source plugins.

**Using Infinity (recommended):**

1. Install: Grafana → Administration → Plugins → Search "Infinity" → Install
2. Add data source: Infinity
3. Set base URL: `http://127.0.0.1:7300`
4. Under **Headers**: add `X-DepthFusion-Key` → `<your-key>`
5. Save & Test

### Panel queries

**Aggregate stats panel (table or stat):**

| Setting | Value |
|---|---|
| Type | JSON |
| Method | GET |
| URL | `http://127.0.0.1:7300/query/aggregate?from=${__from:date:YYYY-MM-DD}&to=${__to:date:YYYY-MM-DD}` |
| Root selector | (leave blank — top-level fields) |

Grafana variables `$__from` and `$__to` map to the dashboard time range.

**Sessions list panel:**

| Setting | Value |
|---|---|
| URL | `http://127.0.0.1:7300/query/sessions?from=${__from:date:YYYY-MM-DDThh:mm:ssZ}&limit=500` |
| Root selector | `items` |
| Columns | `timestamp`, `mode`, `total_latency_ms`, `result_count` |

**Mode distribution (pie chart):**

Use a transformation: query `/query/aggregate`, extract the `modes` object, and use Grafana's
"Convert field type" + "Rows to fields" transforms to pivot the object into chart-friendly data.

### Alerting

Create a Grafana alert on p95 latency:

```
Condition: last(/query/aggregate → p95_latency_ms) > 1500
```

This aligns with the S-43 original threshold (note: reranker adds ~200ms; threshold may need
re-evaluation — see `docs/runbooks/dogfood-reports/2026-05-14-followup.md`).

---

## 6. Power BI

### Get Data → Web connector

1. Home → Get Data → Web
2. URL: `http://127.0.0.1:7300/query/aggregate`
3. Advanced → Add header `X-DepthFusion-Key` = `<your-key>`
4. Click OK → Power Query Editor opens with the JSON response

### M query for sessions (Power Query)

```powerquery
let
    BaseUrl = "http://127.0.0.1:7300/query/sessions",
    Headers = [#"X-DepthFusion-Key" = "<your-key>"],
    Source = Json.Document(Web.Contents(BaseUrl, [Headers = Headers])),
    Items = Source[items],
    Table = Table.FromList(Items, Splitter.SplitByNothing()),
    Expanded = Table.ExpandRecordColumn(Table, "Column1",
        {"timestamp", "mode", "total_latency_ms", "result_count",
         "config_version_id", "event_subtype"})
in
    Expanded
```

For paginated loads (all sessions), wrap in a recursive function that follows `next_cursor`:

```powerquery
let
    FetchPage = (cursor as text) =>
        let
            Url = "http://127.0.0.1:7300/query/sessions?limit=500" &
                  (if cursor = "" then "" else "&cursor=" & cursor),
            Resp = Json.Document(Web.Contents(Url,
                [Headers = [#"X-DepthFusion-Key" = "<your-key>"]])),
            Items = Resp[items],
            NextCursor = try Resp[next_cursor] otherwise null
        in
            [Items = Items, NextCursor = NextCursor],

    AllItems = List.Generate(
        () => FetchPage(""),
        each _[NextCursor] <> null,
        each FetchPage(_[NextCursor]),
        each _[Items]
    ),
    Flat = List.Combine(AllItems),
    Table = Table.FromList(Flat, Splitter.SplitByNothing()),
    Expanded = Table.ExpandRecordColumn(Table, "Column1",
        {"timestamp", "mode", "total_latency_ms", "result_count"})
in
    Expanded
```

---

## 7. Security Notes

- The API binds `127.0.0.1:7300` by default — it is not reachable from the network without a
  tunnel or an explicit public bind.
- Set `DEPTHFUSION_API_PUBLIC=1` only when running behind a reverse proxy with TLS.
- When `DEPTHFUSION_API_PUBLIC=1`, both `DEPTHFUSION_API_TOKEN` (bearer) and
  `DEPTHFUSION_QUERY_API_KEY` must be set — the server will refuse to start otherwise.
- Rotate `DEPTHFUSION_QUERY_API_KEY` via `secrets.token_urlsafe(32)` at the same cadence as
  other service credentials.
- Discovery file contents may include internal project notes — ensure BI dashboard access is
  restricted to team members with appropriate clearance.
