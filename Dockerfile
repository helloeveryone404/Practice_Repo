# ---------- base: shared by the test and final images ----------
FROM python:3.14-slim AS base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# uv, pinned to the version that wrote uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv

# Copy only the dependency files first, so the install layer below is
# cached until the lock file changes
COPY pyproject.toml uv.lock ./

# Create the non-root user, then install runtime dependencies only (no dev group)
RUN groupadd -r test_group && useradd -r -g test_group test_user \
    && uv sync --locked --no-dev --no-install-project --no-build


# ---------- test: adds pytest and the tests; never pushed to GHCR ----------
FROM base AS test

# Add the dev group (pytest, pytest-cov) on top of the runtime dependencies
RUN uv sync --locked --no-install-project --no-build

COPY tests/ ./tests/
COPY workflow_run_stats.py ./

# Exits non-zero if any test fails, so CI can fail on it
CMD ["pytest", "--cov=workflow_run_stats", "--cov-branch", "--cov-report=term-missing"]


# ---------- final: the production image (the default build target) ----------
# Keep this as the last stage, so a plain "docker build ." produces it
FROM base AS final

# The script writes summary.csv to the working directory; mount /data to keep it
WORKDIR /data

COPY workflow_run_stats.py /app/

# Run as the non-root user
USER test_user

CMD ["python", "/app/workflow_run_stats.py"]