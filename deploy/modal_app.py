"""Modal deployment of the TourneyDesk demo API.

Serves the same LLM-free-solver + GLM/GPT-chat FastAPI app (`demo.api.main:app`)
that Render ran, but on Modal: fast container starts (seconds, not the 30-60s of
a Render free instance) and scale-to-zero so idle cost is ~nothing. The public
site's Cloudflare Pages proxy points its origin here.

Deploy:  OTEL_EXPORTER_OTLP_ENDPOINT=... \
         OTEL_EXPORTER_OTLP_AUTHORIZATION=... \
         uv run --with modal --no-project modal deploy deploy/modal_app.py
Secrets: `tourneydesk` Modal secret holds ZAI_API_KEY, OPENAI_API_KEY,
         GLM_MODEL, GPT_MODEL, MAX_OUTPUT_TOKENS. `tourneydesk-origin-auth`
         separately holds TOURNEYDESK_ORIGIN_SECRET and
         TOURNEYDESK_ADMISSION_URL. Optional
         {GLM,GPT}_{INPUT,OUTPUT}_USD_PER_MILLION_TOKENS values enable estimated
         per-turn cost telemetry without baking volatile prices into code.
         OTLP relay configuration comes from inline deploy-process values,
         without reading or replacing the named application secret. See
         docs/OBSERVABILITY.md.

Cost knob: `min_containers=0` (default) = scale-to-zero, pay per request-second.
Set it to 1 for an always-warm container (real 24/7 compute) if the first-hit
latency ever matters more than idle cost. `scaledown_window` keeps a warmed
container around for Modal's 20-minute maximum after traffic so nearby
off-window visits don't each cold-start.
"""

from __future__ import annotations

import os
from pathlib import Path

import modal

_CERTIFI_HOTFIX = str(Path(__file__).with_name("certifi-hotfix.txt"))
_TRUST_ENV = {
    "SSL_CERT_FILE": "/opt/tourneydesk-ca.pem",
    "SSL_CERT_DIR": "/opt/tourneydesk-empty-ca",
    "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH": "/opt/tourneydesk-ca.pem",
}


def _with_fixed_ca(base: modal.Image) -> modal.Image:
    """Patch only the approved CA bundle after the cached dependency layer."""
    return (
        base.pip_install_from_requirements(
            _CERTIFI_HOTFIX, extra_options="--no-deps --require-hashes --only-binary=:all:"
        )
        .run_commands(
            "mkdir -p /opt/tourneydesk-empty-ca",
            "python -c 'import certifi, shutil; shutil.copyfile(certifi.where(), \"/opt/tourneydesk-ca.pem\")'",
        )
        .env(_TRUST_ENV)
    )


# Runtime deps pinned to the repo's locked versions (parity with the tested
# code). The three first-party packages are baked in from local source.
image = _with_fixed_ca(
    modal.Image.debian_slim(python_version="3.13").pip_install(
        "ortools==9.12.4544",
        "fastapi==0.115.14",
        "pydantic==2.13.4",
        "pydantic-ai==1.44.0",
        "openai==2.44.0",
        "pyyaml>=6.0",
    )
).add_local_python_source("tournament_scheduler", "tourneydesk", "demo")

app = modal.App("tourneydesk-demo")


def _deployment_secrets() -> list[modal.Secret]:
    """Keep observability optional and separate from application credentials."""
    secrets = [modal.Secret.from_name("tourneydesk"), modal.Secret.from_name("tourneydesk-origin-auth")]
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        secrets.append(
            modal.Secret.from_dict(
                {
                    "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
                    "OTEL_EXPORTER_OTLP_AUTHORIZATION": os.getenv("OTEL_EXPORTER_OTLP_AUTHORIZATION"),
                }
            )
        )
    elif telemetry_secret := os.getenv("TOURNEYDESK_TELEMETRY_SECRET_NAME"):
        # A dedicated key-only telemetry secret can preserve existing relay
        # configuration during an operational rollout without exporting values.
        secrets.append(modal.Secret.from_name(telemetry_secret))
    return secrets


@app.function(
    image=image,
    # The inline OTLP secret is last so relay settings override stale telemetry
    # keys without mounting another named secret that could replace app keys.
    secrets=_deployment_secrets(),
    min_containers=0,  # scale-to-zero; set to 1 for always-warm
    max_containers=1,  # disposable chat sessions stay on one bounded instance
    scaledown_window=1200,  # Modal's max: retain an idle container for 20 min
    # Restore from a memory snapshot on scale-from-zero instead of re-importing
    # Python (~8-9 s measured) — restores land in a couple of seconds. The LLM
    # stack is not loaded into the snapshot; the first chat request imports it,
    # and OpenAIProvider clients and API keys are constructed lazily per run.
    enable_memory_snapshot=True,
    cpu=1.0,  # headroom for CP-SAT solves (sub-second at demo scale)
    memory=1024,
    timeout=90,
)
# Chat turns are I/O-bound (waiting on the GLM/GPT API for seconds), so one
# container can serve chat alongside health and deterministic solver requests.
# The shared admission authority permits at most four paid turns globally.
@modal.concurrent(max_inputs=12, target_inputs=8)
@modal.asgi_app()
def fastapi_app():
    from demo.api.main import app as demo_app

    return demo_app


_ADMISSION_IMAGE = _with_fixed_ca(
    modal.Image.debian_slim(python_version="3.13").pip_install("fastapi==0.115.14", "pydantic==2.13.4")
).add_local_python_source("demo")
_ADMISSION_STORE = modal.Dict.from_name("tourneydesk-demo-admission", create_if_missing=True)


@app.function(
    image=_ADMISSION_IMAGE,
    secrets=[modal.Secret.from_name("tourneydesk-origin-auth")],
    min_containers=0,
    max_containers=1,
    scaledown_window=60,
    cpu=0.25,
    memory=256,
    timeout=15,
)
@modal.concurrent(max_inputs=1)
@modal.asgi_app()
def admission_api():
    from demo.api.admission import create_admission_app

    return create_admission_app(_ADMISSION_STORE)


# --- Scheduled warm window ---------------------------------------------------
# min_containers=1 weekdays 8:00-23:00 Central, covering US business hours and
# the personal site's observed evening traffic — scale-to-zero otherwise. A warm
# 1-CPU/1-GiB container reserves ~$0.055/h (Modal pricing, 2026-07:
# $0.0000131/core/s + $0.00000222/GiB/s), so the ~75 h/week window is
# ~$18/month nominal — comfortably inside the Starter plan's $30/month free
# credits, where 24/7 always-warm (~$40/month) would not be. Off-window
# visitors get the ~8-10 s snapshot cold start, usually hidden by the site's
# intent-based warmup pings — an acceptable worst case for late nights and weekends.
#
# keep_warm re-asserts every 15 minutes because a redeploy resets the autoscaler
# to the decorator's min_containers=0. The short cadence also covers redeploys
# during the final 22:00 hour instead of missing the last hourly tick. Tune the
# window by editing the two Cron expressions.

_CRON_IMAGE = _with_fixed_ca(modal.Image.debian_slim(python_version="3.13"))


@app.function(
    image=_CRON_IMAGE,
    schedule=modal.Cron("*/15 8-22 * * 1-5", timezone="America/Chicago"),
)
def keep_warm() -> None:
    modal.Function.from_name("tourneydesk-demo", "fastapi_app").update_autoscaler(min_containers=1)


@app.function(
    image=_CRON_IMAGE,
    schedule=modal.Cron("0 23 * * 1-5", timezone="America/Chicago"),
)
def wind_down() -> None:
    modal.Function.from_name("tourneydesk-demo", "fastapi_app").update_autoscaler(min_containers=0)
