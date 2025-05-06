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

# ── OS tools for wheel builds (no Rust needed now)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# ── Bring in everything from the builder
COPY --from=uv /usr/local/ /usr/local/

# ── Python deps
# 1. upgrade build tooling
RUN python -m pip install --upgrade pip setuptools wheel \
# 2. pin wheelable tokenizers & its friends (no Rust compile)
 && pip install --no-cache-dir \
      "tokenizers==0.15.2" \
      "transformers==4.39.3" \
      "sentencepiece==0.2.0" \
      "sentence-transformers==2.2.2" \
# 3. install HubSpot MCP itself without pulling extra deps
 && pip install --no-cache-dir --no-deps --no-build-isolation \
      "git+https://github.com/socialbizguy/mcp-hubspot.git@main"

# ── Non‑root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# ── (Optional) copy runtime app code
COPY --chown=appuser:appuser . /app

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["mcp-gateway"]
