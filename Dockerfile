############################
# ─── Builder stage ────────────────────────────────────────────────
############################
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

ARG INSTALL_EXTRAS="cli,xetrack"
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1

# Base deps
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-dev --no-editable --no-install-project

COPY . /app

# Project + extras
RUN --mount=type=cache,target=/root/.cache/uv \
    EXTRA="[${INSTALL_EXTRAS}]" && \
    echo "Installing project with extras: .${EXTRA}" && \
    uv pip install --system ".${EXTRA}"

############################
# ─── Runtime stage ────────────────────────────────────────────────
############################
FROM python:3.12-slim-bookworm

WORKDIR /app

# ── OS tools: git + build‑essentials for PEP‑517 wheel builds
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# ── NodeJS (uncomment if you genuinely need it)
# RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
#     && apt-get install -y --no-install-recommends nodejs \
#     && rm -rf /var/lib/apt/lists/*

# ── Copy everything the builder installed
COPY --from=uv /usr/local/ /usr/local/

# ── Ensure latest pip & friends, then install HubSpot MCP from GitHub
RUN python -m pip install --upgrade pip setuptools wheel \
 && pip install --no-cache-dir \
    "git+https://github.com/socialbizguy/mcp-hubspot.git@main"

# ── Non‑root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# ── App code (if runtime needs it; omit for pure library image)
COPY --chown=appuser:appuser . /app

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["mcp-gateway"]
