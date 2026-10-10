# first stage
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv

# git is needed to fetch tinaja-bot-base from its git source
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# compile bytecode up front, copy instead of hardlinking from the cache mount,
# and use the image's Python rather than downloading one
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

WORKDIR /code
COPY pyproject.toml uv.lock ./

# install only the locked runtime dependencies into /code/.venv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# second unnamed stage
FROM python:3.14-slim

# Create a non-root user
RUN addgroup --system --gid 1001 app && \
    adduser --system --uid 1001 --gid 1001 app

WORKDIR /code

# copy only the virtual environment from the 1st stage image, then the bot itself
COPY --from=builder --chown=app:app /code/.venv /code/.venv
COPY --chown=app:app bot.toml CONTEXT.md PERSONA.md ./
COPY --chown=app:app cogs/ ./cogs/

USER app

ENV PATH="/code/.venv/bin:$PATH"

# the token comes at runtime: docker run --env-file .env <image>
ENTRYPOINT [ "tinaja-bot", "run" ]
