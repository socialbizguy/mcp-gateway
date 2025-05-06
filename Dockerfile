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

# Upgrade base pip tools
RUN python -m pip install --upgrade pip setuptools wheel

# ✅ Install pinned dependencies to avoid breaking changes
RUN pip install --no-cache-dir \
      "tokenizers==0.15.2" \
      "transformers==4.39.3" \
      "sentencepiece==0.2.0" \
      "sentence-transformers==2.2.2" \
      "huggingface_hub==0.14.1"

# ✅ Install MCP without extra deps, create CLI shim
RUN pip install --no-cache-dir --no-deps \
      "git+https://github.com/socialbizguy/mcp-hubspot.git@main#egg=mcp-server-hubspot" && \
    printf '#!/usr/bin/env python3\nimport mcp_server_hubspot; mcp_server_hubspot.run_main()\n' > /usr/local/bin/mcp-server-hubspot && \
    chmod +x /usr/local/bin/mcp-server-hubspot

# Copy our entrypoint wrapper and make it executable
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Copy prompts (and any other code)
COPY . /app

ENV PYTHONUNBUFFERED=1

# Entrypoint wraps STDIO plugin logic
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
