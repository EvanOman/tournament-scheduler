FROM python:3.13-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY tournament_scheduler ./tournament_scheduler
COPY tourneydesk ./tourneydesk
COPY demo ./demo
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
# Render injects $PORT.
CMD ["sh", "-c", "uvicorn demo.api.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
