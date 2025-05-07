# Use a Python image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

# Define build argument for optional dependencies
ARG INSTALL_EXTRAS=""

# Install system build tools, OpenSSL dev, cmake, pkg-config, etc.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential \
      python3-dev \
      git \
      cmake \
      pkg-config \
      libssl-dev \
      curl && \
    rm -rf /var/lib/apt/lists/*

# Install Rust toolchain
RUN curl https://sh.rustup.rs -sSf | bash -s -- -y

# Make cargo available in all RUN steps
ENV PATH="/root/.cargo/bin:${PATH}"

# Install the project into `/app`
WORKDIR /app

# Copy everything so uv has access to pyproject.toml, src/, README.md, tools/, etc.
COPY . /app

# allow invalid reference casting in tokenizers
ENV RUSTFLAGS="-A invalid_reference_casting"

# Sync only the dependencies (no editable install)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable --no-install-project

# Install the project itself (plus any extras you pass via INSTALL_EXTRAS)
RUN --mount=type=cache,target=/root/.cache/uv \
    bash -c '\
      EXTRA_SPECIFIER=""; \
      if [ -n "$INSTALL_EXTRAS" ]; then \
        EXTRA_SPECIFIER="[${INSTALL_EXTRAS}]"; \
      fi; \
      echo "Installing project with extras: .${EXTRA_SPECIFIER}"; \
      uv pip install --system ".${EXTRA_SPECIFIER}" \
      pip install hubspot-api-client'

# --- Final runtime image ---
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install Node.js & npx
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      curl \
      gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# (Optional) verify Node.js/npm
RUN node --version && npm --version && npx --version

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# Copy over everything from uv stage
COPY --from=uv /usr/local/bin/ /usr/local/bin/
COPY --from=uv /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=uv /app /app

# Unbuffered Python output
ENV PYTHONUNBUFFERED=1

# Start the gateway
ENTRYPOINT ["mcp-gateway"]
