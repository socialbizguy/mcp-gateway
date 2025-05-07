# --- Build stage ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# allow passing extras like "cli,xetrack,hubspot"
ARG INSTALL_EXTRAS=""

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1

# install build tools, cmake/pkg-config, Rust toolchain
RUN apt-get update && apt-get install -y \
      curl \
      build-essential \
      python3-dev \
      git \
      cmake \
      pkg-config \
    && curl https://sh.rustup.rs -sSf | bash -s -- -y \
    && echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> /root/.bashrc \
    && export PATH="$HOME/.cargo/bin:$PATH"

# copy your source in
COPY . /app

# resolve dependencies with uv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable --no-install-project

# install your package (with any extras) then the official HubSpot client
RUN --mount=type=cache,target=/root/.cache/uv \
    bash -c '\
      EXTRA_SPECIFIER=""; \
      if [ -n "$INSTALL_EXTRAS" ]; then \
        EXTRA_SPECIFIER="[${INSTALL_EXTRAS}]"; \
      fi; \
      echo "Installing project with extras: .${EXTRA_SPECIFIER}"; \
      uv pip install --system ".${EXTRA_SPECIFIER}"; \
      pip install hubspot-api-client \
    '

# --- Final runtime image ---
FROM python:3.12-slim-bookworm

WORKDIR /app

# install Node.js & npm (for npx)
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# sanity‐check
RUN node --version && npm --version && npx --version

# drop to unprivileged user
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

# pull in everything from our build stage
COPY --from=builder /usr/local/bin/ /usr/local/bin/
COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /app /app

ENV PYTHONUNBUFFERED=1

# copy in our launcher
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# use it as the image entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
