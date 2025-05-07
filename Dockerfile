# --------------------------------------
# Builder stage: install dependencies + Rust
# --------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

# Allow passing extras (cli, xetrack, hubspot)
ARG INSTALL_EXTRAS=""

# Put code under /app
WORKDIR /app

# Speed up by pre-compiling __pycache__
ENV UV_COMPILE_BYTECODE=1

# Install build tools, Rust compiler & cargo, cmake, pkg-config, plus rustup fallback
RUN apt-get update && apt-get install -y \
      curl \
      build-essential \
      python3-dev \
      git \
      cmake \
      pkg-config \
      rustc \
      cargo && \
    curl https://sh.rustup.rs -sSf | bash -s -- -y && \
    # make sure rustup bins are on PATH for this session
    ln -s /root/.cargo/bin/* /usr/local/bin/ || true

# Copy your gateway code (including src/, pyproject.toml, tools/)
COPY . /app

# Resolve & install base deps with uv (no extras yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable --no-install-project

# Finally install mcp-gateway itself along with any extras
RUN --mount=type=cache,target=/root/.cache/uv \
    bash -c 'EXTRA_SPECIFIER=""; \
      if [ -n "$INSTALL_EXTRAS" ]; then EXTRA_SPECIFIER="[${INSTALL_EXTRAS}]"; fi; \
      echo "Installing project with extras: .${EXTRA_SPECIFIER}"; \
      uv pip install --system ".${EXTRA_SPECIFIER}"'


# --------------------------------------
# Final runtime image: lean + Node.js
# --------------------------------------
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install Node.js & npx for any JS tooling (e.g. n8n UI)
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Optionally verify versions
RUN node --version && npm --version && npx --version

# Create a non-root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# Bring over Python binaries & deps from builder
COPY --from=uv /usr/local/bin/ /usr/local/bin/
COPY --from=uv /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/

# Copy the app itself
COPY --from=uv /app /app

# Make Python unbuffered for logs
ENV PYTHONUNBUFFERED=1

# Launch the gateway
ENTRYPOINT ["mcp-gateway"]
