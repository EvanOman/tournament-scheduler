# Observability — TourneyDesk demo API on Modal

The public demo already had spans from the browser (Grafana Faro) and from the
Cloudflare Pages Function that proxies `/api/tourneydesk/*`. This service is the
third leg: it continues the trace the proxy forwards, so a slow demo request is
one waterfall — browser fetch → edge → Modal → CP-SAT solve or chat turn —
instead of three unlinked traces.

The site repository (`conway-personal-website`) owns the shared contract in
`docs/observability.md`; this document covers only what this repo implements.

## What emits spans

`demo/api/tracing.py` is a stdlib-only OTLP/HTTP client. It deliberately does
not use the OpenTelemetry SDK:

- the SDK plus FastAPI instrumentation adds hundreds of milliseconds of import
  time to every Modal cold start — the number this instrumentation exists to
  measure;
- the edge exporter (`functions/_observability.ts` in the site repo) is already
  a hand-rolled OTLP/HTTP JSON client, so matching it keeps one contract;
- it adds no dependency, no lockfile churn, and no package release-age exposure.

The wire format is standard OTLP/HTTP with a JSON payload, and attributes follow
OTel semantic conventions, so Grafana Cloud ingests it with no special handling.

## Configuration

| Variable | Required | Meaning |
| --- | --- | --- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | to export anything | OTLP base URL, e.g. `https://otlp-gateway-prod-us-east-2.grafana.net/otlp`. `/v1/traces` is appended. |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | no | Full traces URL; overrides the base above. |
| `OTEL_EXPORTER_OTLP_AUTHORIZATION` | for Grafana Cloud | Complete `Authorization` header value, e.g. `Basic <base64 instance-id:token>`. Omit for an unauthenticated local collector. |
| `OTEL_SDK_DISABLED` | no | `true` turns export off without a redeploy path change. |
| `DEPLOY_ENVIRONMENT` | no | Sets `deployment.environment.name` on the resource. |

Only `http://` and `https://` endpoints are accepted; anything else is treated
as unconfigured.

### Modal secret

`deploy/modal_app.py` always attaches the existing required **`tourneydesk`**
application secret. When `OTEL_EXPORTER_OTLP_ENDPOINT` is present in the local
deploy process, it also builds a separate, unnamed Modal secret containing the
OTLP endpoint and optional authorization value. The unnamed secret is attached
last, so those values override stale telemetry keys without reading, replacing,
or otherwise changing the application secret.

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT='https://otlp-gateway-prod-us-east-2.grafana.net/otlp'
read -rsp 'Grafana OTLP Authorization header: ' OTEL_EXPORTER_OTLP_AUTHORIZATION
export OTEL_EXPORTER_OTLP_AUTHORIZATION
uv run --with modal --no-project modal deploy deploy/modal_app.py
unset OTEL_EXPORTER_OTLP_ENDPOINT OTEL_EXPORTER_OTLP_AUTHORIZATION
```

The authorization header remains out of shell history because `read` collects
it without echoing. Use a write-only Grafana Cloud Access Policy token. A fresh
environment or rollback with no OTLP variables creates no additional secret
dependency and still deploys normally; tracing is simply inactive unless the
named application secret already contains telemetry configuration. To turn
deploy-time tracing off, omit the endpoint and redeploy.

The existing **`tourneydesk`** application secret may optionally include
`GLM_INPUT_USD_PER_MILLION_TOKENS`,
`GLM_OUTPUT_USD_PER_MILLION_TOKENS`,
`GPT_INPUT_USD_PER_MILLION_TOKENS`, and
`GPT_OUTPUT_USD_PER_MILLION_TOKENS`. When both rates for the selected engine
are present, the chat span includes an estimated turn cost. Prices are
configuration rather than code because provider rates and custom model names
change independently of a deploy.

## Span model

`service.name` is `tourneydesk`. `service.instance.id` is a random
per-container-process id, minted lazily after a Modal snapshot restore, so a
trace can be attributed to the actual serving container without recording
anything about the caller or cloning an import-time id across restored workers.

| Span | Kind | When |
| --- | --- | --- |
| `{METHOD} {route}` | server | Every HTTP request. Route is one of `/health`, `/solve`, `/chat`, `/chat/stream`, or `/*`. |
| `container.init` | internal | First request in a process only; covers interpreter/import time. |
| `solve.tournament` | internal | `/solve` CP-SAT run, on the executor thread. |
| `chat.turn` | internal | One `/chat` or `/chat/stream` model turn. |
| `solve.session` | internal | Speculative solve of the in-progress spec inside a chat turn. |

Notable attributes:

- request: `http.request.method`, `http.route`, `http.response.status_code`,
  `request.outcome` (`success` / `not_found` / `invalid_request` /
  `rate_limited` / `timeout` / `client_error` / `internal_error` /
  `cancelled`),
  `request.correlation_id` (only if it matches a UUID/trace-id shape),
  `request.purpose=warmup`, `faas.coldstart`, `faas.instance`,
  `container.reused`, and bounded `container.reuse_bucket`
  (`first` / `2-5` / `6-20` / `21+`).
- solve: `solve.result` (`solved` / `infeasible` / `inconclusive` / `timeout`),
  `solve.status` (CP-SAT status), `solve.games`, `solve.wall_time_ms`,
  `solve.explained`, `tournament.{divisions,teams,fields,days}`.
- chat: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.system`,
  `gen_ai.response.model`, `gen_ai.usage.{input,output}_tokens`,
  optional `gen_ai.usage.cost_usd`, `chat.engine`, `chat.ttft_ms`,
  `chat.outcome` (`final` / `unconfigured` / `provider_error` / `error` /
  `early_end`), `chat.tool_calls`, `chat.spec_mutations`,
  `chat.reached_model`, and `chat.provider_error`.

Only 5xx sets span status `ERROR`. Infeasible solves, 4xx validation failures,
and a reader closing the SSE stream mid-turn (`request.outcome=cancelled`,
`chat.outcome=early_end`) are recorded as outcomes, not incidents.

### Cold starts

`container.init` deliberately starts *before* its parent request span: it covers
import and interpreter startup that finished just as the request arrived.
Attaching it to the first request is what makes a cold start visible in the same
waterfall as the latency it caused.

Modal memory-snapshot restores resume a process whose monotonic clock predates
the snapshot, which would make the measured init window meaningless. When the
measurement exceeds 120 s it is discarded and only `faas.coldstart=true` is
reported.

## Privacy

Never recorded: director messages, assistant replies, tool arguments, team,
coach, or field names, solver request/response bodies, headers, IP addresses,
user agents, query strings, or exception messages. Exceptions are recorded by
**type** only, because a validation error can echo request content.

Attribute values are truncated to 128 characters and capped at 32 per span.
`http.route` and `http.request.method` are drawn from fixed allowlists, and
container reuse is bucketed, so a scanner or long-lived container cannot mint
unbounded indexed values.

## Fail-open

No telemetry path can fail a user request:

- export runs on a bounded queue drained by a daemon thread, so a request never
  waits on the collector;
- a full queue drops spans;
- exporter construction, thread startup, enqueue, payload encoding, and span
  completion failures are all swallowed;
- connection failures, timeouts, and non-2xx responses are swallowed and counted,
  logged at most once a minute, and never include the URL, credentials, or the
  exception;
- with no endpoint configured, the whole path costs a few dict lookups.

The exporter thread starts on first use rather than at import, keeping it out of
Modal's memory snapshot; a pid/liveness check restarts it after a snapshot
restore or fork. Queue bookkeeping includes batches already removed by the
worker, so the bounded shutdown flush does not report completion while an OTLP
POST is still in flight. Modal supports the ASGI lifespan protocol; the tracing
middleware flushes during lifespan shutdown, with `atexit` retained as a final
fallback.

For streams, ASGI 2.4 `ClientDisconnect` and the older `http.disconnect`
listener path both produce `request.outcome=cancelled`, keep the response
status that was already sent, and leave span status unset rather than creating
a false server incident.

`tests/test_demo_tracing.py` covers all of this — valid, invalid, and missing
trace context, unsampled upstream decisions, collector connection failure,
collector 500, unconfigured and disabled exporters, and the privacy assertions.

## Live smoke query

After deploying with the secret attached:

1. Send a request that carries a known trace id, so you can find it without
   guessing:

   ```bash
   TRACE_ID=$(python3 -c "import secrets; print(secrets.token_hex(16))")
   echo "$TRACE_ID"
   curl -sS -o /dev/null -w '%{http_code}\n' \
     -H "traceparent: 00-${TRACE_ID}-00f067aa0ba902b7-01" \
     https://evan058--tourneydesk-demo-fastapi-app.modal.run/health
   ```

2. Wait a few seconds (the exporter batches on a 250 ms tick; Grafana ingestion
   adds a little more), then look the trace up. From the site repo:

   ```bash
   site-traces trace "$TRACE_ID" --json
   ```

   or in Grafana Explore, TraceQL:

   ```
   { resource.service.name = "tourneydesk" }
   ```

3. Confirm: the `GET /health` span exists under `service.name=tourneydesk`, its
   `parentSpanId` is `00f067aa0ba902b7`, and `faas.coldstart` is `true` on the
   first request after a scale-from-zero and `false` on an immediate repeat.

4. Exercise the real path end to end from a browser on evanoman.com and confirm
   the browser fetch, `evanoman-tourneydesk-proxy`, and `tourneydesk` spans all
   share one trace id, with `solve.tournament` or `chat.turn` beneath.

To verify the fail-open property in production, point
`OTEL_EXPORTER_OTLP_ENDPOINT` at an unreachable host and confirm `/health` and
`/solve` still answer normally.
