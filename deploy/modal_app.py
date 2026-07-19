"""Modal deployment of the TourneyDesk demo API.

Serves the same LLM-free-solver + GLM/GPT-chat FastAPI app (`demo.api.main:app`)
that Render ran, but on Modal: fast container starts (seconds, not the 30-60s of
a Render free instance) and scale-to-zero so idle cost is ~nothing. The public
site's Cloudflare Pages proxy points its origin here.

Deploy:  uv run modal deploy deploy/modal_app.py
Secrets: `tourneydesk` Modal secret holds ZAI_API_KEY, OPENAI_API_KEY,
         GLM_MODEL, GPT_MODEL, MAX_OUTPUT_TOKENS (and DEMO_SHARED_SECRET if the
         proxy is configured to require it).

Cost knob: `min_containers=0` (default) = scale-to-zero, pay per request-second.
Set it to 1 for an always-warm container (real 24/7 compute) if the first-hit
latency ever matters more than idle cost. `scaledown_window` keeps a warmed
container around briefly after traffic so bursts don't each cold-start.
"""

from __future__ import annotations

import modal

# Runtime deps pinned to the repo's locked versions (parity with the tested
# code). The three first-party packages are baked in from local source.
image = (
    modal.Image.debian_slim(python_version="3.13")
    .pip_install(
        "ortools==9.12.4544",
        "fastapi==0.115.14",
        "pydantic==2.13.4",
        "pydantic-ai==1.44.0",
        "openai==2.44.0",
        "pyyaml>=6.0",
    )
    .add_local_python_source("tournament_scheduler", "tourneydesk", "demo")
)

app = modal.App("tourneydesk-demo")


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("tourneydesk")],
    min_containers=0,  # scale-to-zero; set to 1 for always-warm
    scaledown_window=300,  # keep a warm container ~5 min after last request
    # Restore from a memory snapshot on scale-from-zero instead of re-importing
    # Python (~8-9 s measured) — restores land in a couple of seconds. Snapshot-
    # safe: the module-level pydantic-ai Agent holds no model; OpenAIProvider
    # HTTP clients and API keys are constructed lazily per run.
    enable_memory_snapshot=True,
    cpu=1.0,  # headroom for CP-SAT solves (sub-second at demo scale)
    memory=1024,
    timeout=90,
)
# Chat turns are I/O-bound (waiting on the GLM/GPT API for seconds), so one
# container serves many concurrently instead of one-at-a-time; Modal spins up
# another container once ~8 are in flight.
@modal.concurrent(max_inputs=12, target_inputs=8)
@modal.asgi_app()
def fastapi_app():
    from demo.api.main import app as demo_app

    return demo_app


# --- Scheduled warm window ---------------------------------------------------
# min_containers=1 during US business hours — weekdays 8:00-20:00 Central,
# covering 9am Eastern through 6pm Pacific — scale-to-zero otherwise. A warm
# 1-CPU/1-GiB container reserves ~$0.055/h (Modal pricing, 2026-07:
# $0.0000131/core/s + $0.00000222/GiB/s), so the ~60 h/week window is
# ~$14/month nominal — comfortably inside the Starter plan's $30/month free
# credits, where 24/7 always-warm (~$40/month) would not be. Off-window
# visitors get the ~8-10 s snapshot cold start, usually hidden by the site's
# warm-on-load pings — an acceptable worst case for nights and weekends.
#
# keep_warm re-asserts HOURLY (not once at 8:00) because a redeploy resets the
# autoscaler to the decorator's min_containers=0 — the hourly tick self-heals
# that within the hour. Tune the window by editing the two Cron expressions.

_CRON_IMAGE = modal.Image.debian_slim(python_version="3.13")


@app.function(
    image=_CRON_IMAGE,
    schedule=modal.Cron("0 8-19 * * 1-5", timezone="America/Chicago"),
)
def keep_warm() -> None:
    modal.Function.from_name("tourneydesk-demo", "fastapi_app").update_autoscaler(min_containers=1)


@app.function(
    image=_CRON_IMAGE,
    schedule=modal.Cron("0 20 * * 1-5", timezone="America/Chicago"),
)
def wind_down() -> None:
    modal.Function.from_name("tourneydesk-demo", "fastapi_app").update_autoscaler(min_containers=0)
