# --------------------------------------
# Builder stage: install dependencies + Rust toolchain
# --------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

# Allow passing extras (cli, xetrack, hubspot)
ARG INSTALL_EXTRAS=""

# Work in /app
WORKDIR /app

# Speed up by compiling bytecode
ENV UV_COMPILE_BYTECODE=1

# Install build tools, Rust (rustc + cargo), cmake, pkg-config, OpenSSL dev headers
RUN apt-get update && apt-get install -y \
      curl \
      build-essential \
      python3-dev \
      git \
      cmake \
      pkg-config \
      rustc \
      cargo \
      libssl-dev \
    && curl https://sh.rustup.rs -sSf | bash -s -- -y \
    && ln -sf /root/.cargo/bin/* /usr/local/bin/ \
    && rm -rf /var/lib/apt/lists/*

# Copy everything (so src/, pyproject.toml, tools/, README.md are present)
COPY . /app

# Install base dependencies via uv (no extras yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable --no-install-project

# Install the project itself, including any extras (cli, xetrack, hubspot)
RUN --mount=type=cache,target=/root/.cache/uv \
    bash -c 'EXTRA_SPECIFIER=""; \
      if [ -n "$INSTALL_EXTRAS" ]; then EXTRA_SPECIFIER="[${INSTALL_EXTRAS}]"; fi; \
      echo "Installing project with extras: .${EXTRA_SPECIFIER}"; \
      uv pip install --system ".${EXTRA_SPECIFIER}"'


# --------------------------------------
# Final runtime image: lean + Node.js for JS tooling
# --------------------------------------
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install Node.js & npx (for any JS-based tooling)
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Verify versions (optional)
RUN node --version && npm --version && npx --version

# Create a non-root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# Bring in Python binaries and site-packages from build stage
COPY --from=uv /usr/local/bin/ /usr/local/bin/
COPY --from=uv /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/

# Copy your app
COPY --from=uv /app /app

# Make Python stdout/stderr unbuffered for logs
ENV PYTHONUNBUFFERED=1

# Entrypoint always runs mcp-gateway
ENTRYPOINT ["mcp-gateway"]
