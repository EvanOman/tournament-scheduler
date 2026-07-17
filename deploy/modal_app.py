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
