# BI Tool Connectivity — DepthFusion Query API

> **Note: This doc describes V1 behavior.** V2 behavior is documented in `docs/v2/admin-runbooks.md` (§5 Log Analysis and Audit Queries). V2 uses authenticated API endpoints with RBAC-gated access rather than raw SSH tunnels. If you are running the `v2-enterprise` branch, refer to the admin runbooks instead.



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
