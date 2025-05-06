############################
# ─── Builder stage ────────────────────────────────────────────────
############################
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

# Optional extras for your own project
# e.g. "cli,xetrack" – leave blank if you don’t need any
ARG INSTALL_EXTRAS=""
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1

# --- Install base deps listed in pyproject.toml (no extras yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-dev --no-editable --no-install-project

# --- Copy project source and install with extras (if any)
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    EXTRA_SPECIFIER="" && \
    [ -n "$INSTALL_EXTRAS" ] && EXTRA_SPECIFIER="[${INSTALL_EXTRAS}]" ; \
    echo "Installing project with extras: .${EXTRA_SPECIFIER}" && \
    uv pip install --system ".${EXTRA_SPECIFIER}"

############################
# ─── Runtime stage ────────────────────────────────────────────────
############################
FROM python:3.12-slim-bookworm

WORKDIR /app

# --- OS tooling (git needed for pip VCS install, node optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# --- Bring in everything the builder put in /usr/local
COPY --from=uv /usr/local/ /usr/local/

# --- Install HubSpot MCP straight from GitHub
RUN pip install --no-cache-dir \
    "git+https://github.com/socialbizguy/mcp-hubspot.git@main#egg=mcp-hubspot"

# --- App code (non‑root user for safety)
COPY --chown=appuser:appuser . /app
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

ENV PYTHONUNBUFFERED=1

# --- Entrypoint
ENTRYPOINT ["mcp-gateway"]
