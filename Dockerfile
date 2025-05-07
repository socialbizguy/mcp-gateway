# Use a Python image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

# Define build argument for optional dependencies
ARG INSTALL_EXTRAS=""

# Install the project into `/app`
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# ✅ Install Rust + build essentials BEFORE dependency resolution
RUN apt-get update && apt-get install -y curl build-essential python3-dev git \
 && curl https://sh.rustup.rs -sSf | bash -s -- -y \
 && export PATH="/root/.cargo/bin:$PATH"

# Copy everything at once so `src/` and README.md are present
COPY . /app

# Install dependencies using uv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable --no-install-project

# Install the project itself, including specified optional dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    bash -c 'EXTRA_SPECIFIER=""; \
    if [ -n "$INSTALL_EXTRAS" ]; then EXTRA_SPECIFIER="[${INSTALL_EXTRAS}]"; fi; \
    echo "Installing project with extras: .${EXTRA_SPECIFIER}"; \
    uv pip install --system ".${EXTRA_SPECIFIER}"'


# --- Final runtime image ---
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install Node.js and npm (which includes npx)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Optional: verify installations
RUN node --version && npm --version && npx --version

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# Copy dependencies and installed packages from build stage
COPY --from=uv /usr/local/bin/ /usr/local/bin/
COPY --from=uv /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=uv /app /app

# Make Python output unbuffered
ENV PYTHONUNBUFFERED=1

# Start the gateway
ENTRYPOINT ["mcp-gateway"]
