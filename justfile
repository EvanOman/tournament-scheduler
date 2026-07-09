# Justfile for tournament-scheduler development

# Default recipe - show available commands
default:
    @just --list

# Run linting, type checking, and tests
check:
    @echo "Running ruff linting..."
    uv run ruff check
    @echo "Running format check..."
    uv run ruff format --check
    @echo "Running type checking..."
    uv run ty check
    @echo "Running tests..."
    uv run pytest --cov=tournament_scheduler --cov-report=term-missing
    @just frontend-check

# Typecheck + build the frontend. Skips gracefully when Node is unavailable so
# `just check` still passes on a machine without a JS toolchain (the built
# assets under tourneydesk/web/static are committed).
frontend-check:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v npm >/dev/null 2>&1; then
        echo "frontend-check: npm not found, skipping (committed assets are used)."
        exit 0
    fi
    cd frontend
    if [ ! -d node_modules ]; then
        echo "frontend-check: installing deps..."
        npm install --no-audit --no-fund
    fi
    npm run typecheck
    npm run build

# Serve the web app (SPA + REST + WebSocket). Use --provider fake for offline.
serve *ARGS:
    uv run tourneydesk serve {{ARGS}}

# Fix linting and formatting issues
fix:
    @echo "Fixing linting issues..."
    uv run ruff check --fix
    @echo "Formatting code..."
    uv run ruff format

# Fix and then check
fc: fix check

# Run tests only
test:
    uv run pytest --cov=tournament_scheduler --cov-report=term-missing

# Run specific test file
test-file FILE:
    uv run pytest {{FILE}} -v

# Run tests with specific pattern
test-pattern PATTERN:
    uv run pytest -k "{{PATTERN}}" -v

# Install dependencies
install:
    uv sync --dev

# Clean up generated files
clean:
    rm -rf .pytest_cache
    rm -rf .coverage
    rm -rf coverage.xml
    rm -rf htmlcov
    rm -rf output
    find . -type d -name __pycache__ -exec rm -rf {} +
    find . -type f -name "*.pyc" -delete

# Solve a tournament spec
solve FILE:
    uv run tournament-scheduler solve {{FILE}}

# Solve and render HTML output
solve-html FILE:
    uv run tournament-scheduler solve {{FILE}} --format html

# Generate example fixtures
examples:
    uv run python -m tournament_scheduler.fixtures

# Type checking
typecheck:
    uv run ty check

# Full development setup
setup: install
    @echo "Development environment ready!"
    @echo "Run 'just check' to validate your setup"
