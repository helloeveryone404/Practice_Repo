FROM python:3.14-slim

# uv, pinned to the version that wrote uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer is cached until the lockfile changes
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY APIpractice.py ./

ENV PATH="/app/.venv/bin:$PATH"

# The script writes summary.csv to the working directory; mount /data to keep it
WORKDIR /data

CMD ["python", "/app/APIpractice.py"]
