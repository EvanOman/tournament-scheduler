# Public demo chat operations

The website calls the public demo through its Pages Function. Both backend chat
routes require `X-Demo-Secret`, checked against `TOURNEYDESK_ORIGIN_SECRET`, and
`X-Demo-Client-Id`, a 64-character lowercase hex identity supplied by Pages.
The proxy accepts same-origin JSON chat requests, drops incoming credentials and
identity hints, and derives a daily HMAC of Cloudflare's ingress client IP. Users
sharing an IP share its allowance. `/health` and the deterministic `/solve` stay
public and need no LLM credentials.

## Shared admission

Set `TOURNEYDESK_ADMISSION_URL` on every retained origin to
`https://evan058--tourneydesk-demo-admission-api.modal.run`. This service uses the
same scoped origin key and the persistent `tourneydesk-demo-admission` Modal Dict.
Do not create a separate counter for Render or a second deployment.

The authenticated service exposes POST `/reserve` with `client_id` and a new
32-character hex `request_id`, and POST `/release` with `lease_id`. Reserve
returns `{"lease_id":"<request_id>"}` only after a durable claim. It returns 429
at 15 requests per client per UTC day, 400 globally per UTC day, four active
leases globally, or one active lease per client. Every accepted attempt counts,
including provider failures. Release removes only the concurrency lease.

Leases expire after 120 seconds to recover from killed workers. Both origins
cancel model work at 70 seconds and wait at most 15 seconds for the final
speculative solve. The shared solver already caps search at 10 seconds. The
admission authority has one container and one input at a time, plus an atomic
Dict lock across deployment versions. API autoscaling caps at one container.

The guard fails closed on missing configuration, authentication failure,
unavailable storage or malformed admission responses. Health remains available
when chat is unavailable. This is a request and token exposure ceiling; provider
account dollar caps and provider-side retry/billing behavior are separate.

## Data and work bounds

- HTTP chat bodies: 8 KiB, messages: 2,000 characters, session IDs: 64 characters.
- Agent runs: four model requests, 16 tool calls, 800 output tokens per request.
  Reported usage stops runs after 16,000 input or 3,200 output tokens; these
  usage checks occur after a provider response and can overshoot by one response.
- Sessions: 12 model-started attempts, 64 KiB retained whole-message history,
  64 retained sessions, one-hour idle expiry. A limit breach closes the session.
  Session IDs are bound to the trusted daily client pseudonym.
- Drafts: 64 teams total, 24 per division, four divisions, eight fields, two
  calendar days, four windows per fact and 48 windows total. Numeric scheduling,
  relationships, provenance and individual tool input sizes are also bounded by
  `PUBLIC_DEMO_LIMITS`. Rejected changes do not mutate draft state.

Before any public solver model is constructed, a cheap arithmetic estimate
rejects excessive slots, decision variables and nested scan/pair work. This
applies to `/solve`, speculative chat solves and schedule-summary tools; CP-SAT
search timers alone do not bound model construction. All published site presets
fit the work budget; some otherwise valid larger shapes are conservatively
rejected with a request to reduce teams, fields or availability.

These controls are selected explicitly at the demo entry point. The ordinary
CLI and full product keep their existing defaults and share the same service
and tool implementation.

## Deploy and secrets

The existing `tourneydesk` Modal secret retains the provider configuration. A
separate `tourneydesk-origin-auth` secret contains only the new scoped origin key
and admission URL. Pages needs that key as `TOURNEYDESK_ORIGIN_SECRET`; do not
rotate the unrelated `DEMO_SHARED_SECRET` used by other demos.

Preserve telemetry when deploying. Either supply the existing documented OTLP
process variables, or set `TOURNEYDESK_TELEMETRY_SECRET_NAME` to a dedicated
secret containing only OTLP relay configuration. The latter allows transfer
inside the running container without exporting relay credentials. Never use a
general provider secret as the optional telemetry overlay.

Deploy the tested app with the normal authenticated Modal client. Before
opening chat, provision matching Pages/backend keys. Rollback to a version
without the guard reopens the exposure; a containment rollback should disable
chat or keep the authenticated middleware in place. Do not change or stop the
deterministic solver as a substitute for fixing chat.

The Modal images also apply the separately approved certifi incident update
through `deploy/certifi-hotfix.txt`, pinned to version 2026.10.19 and the release
wheel's SHA-256. This layer uses `--no-deps --require-hashes` and preserves the
existing dependency layer. Python/OpenSSL and gRPC use the patched public CA
bundle; an empty `SSL_CERT_DIR` prevents fallback to stale hashed system roots.
This does not certify or update every base-image OS/NSS package. See the
[maintainer advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)
and [release metadata](https://pypi.org/pypi/certifi/2026.10.19/json).

## Safe verification and lock recovery

Offline tests use fake models and an in-memory implementation of the persistent
store. They cover concurrent admission, restart, day rollover, write failure,
input envelopes, streaming, cancellation and provider loops without a paid call.
For live checks, missing/wrong-key chat must reject before JSON validation;
authorized malformed chat must return 422 before quota or provider construction.
Health must remain 200. These checks do not need a real chat completion.

If the authority remains unavailable after a crash, inspect the Dict's
`writer-v1` lock. Never delete the lock based only on age: a paused writer could
resume and overwrite another claim. First stop admission traffic and confirm
all old admission containers are terminated; only then clear `writer-v1` with
the authenticated administrative client and restart admission. Keep `state-v1`
intact so daily usage survives recovery. If safe recovery cannot be verified,
leave chat unavailable and keep health/the solver serving.

Modal documents [persistent Dict storage and atomic locking](https://modal.com/docs/guide/dicts)
and [rolling deployment behavior](https://modal.com/docs/guide/managing-deployments).
