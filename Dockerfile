############################
# ─── Builder stage ────────────────────────────────────────────────
############################
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

# Allow optional extras (e.g., "cli,xetrack")
ARG INSTALL_EXTRAS="cli,xetrack"
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1

# Install base dependencies without project code
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-dev --no-editable --no-install-project

# Copy source and install project plus extras
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    EXTRA="[${INSTALL_EXTRAS}]" && \
    echo "Installing project with extras: .${EXTRA}" && \
    uv pip install --system ".${EXTRA}"

############################
# ─── Runtime stage ────────────────────────────────────────────────
############################
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install OS build tools for any future binary dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy everything installed in builder
COPY --from=uv /usr/local/ /usr/local/

# Upgrade pip, setuptools, wheel, then install Python deps without Rust
RUN python -m pip install --upgrade pip setuptools wheel \
 && pip install --no-cache-dir \
      "tokenizers==0.15.2" \
      "transformers==4.39.3" \
      "sentencepiece==0.2.0" \
      "sentence-transformers==2.2.2" \
 && pip install --no-cache-dir --no-deps --no-build-isolation \
      "git+https://github.com/socialbizguy/mcp-hubspot.git@main#egg=mcp-server-hubspot"

# Create non-root user and switch
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# Copy application code (if needed at runtime)
COPY --chown=appuser:appuser . /app

ENV PYTHONUNBUFFERED=1

# Entrypoint for the gateway
ENTRYPOINT ["mcp-gateway"]
