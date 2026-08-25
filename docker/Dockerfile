# Toolchain image for the trade-challenge server.
#
# This image holds ONLY the toolchain. The repo itself (CLAUDE.md, strategy.md,
# rules.yml, .claude/, scripts/) is bind-mounted at /app — never baked in — so a
# §9 amendment or a command-file fix takes effect without a rebuild. If the repo
# were baked in, every rule amendment would become a Docker release and the
# container would inevitably drift from the manual.
#
# Every version here is pinned. `claude` auto-updates by default, and a
# headless-behaviour change landing mid-window against a fixed 2026-09-14
# deadline is a live risk; DISABLE_AUTOUPDATER below is load-bearing, not tidy.

FROM node:22-bookworm-slim

# uv manages its own CPython, so schwab-mcp never fights Debian's system python.
ENV UV_INSTALL_DIR=/usr/local/bin \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    DISABLE_AUTOUPDATER=1 \
    TZ=America/New_York

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq tzdata procps ripgrep \
    && rm -rf /var/lib/apt/lists/*

# --- claude, pinned -------------------------------------------------------
ARG CLAUDE_VERSION=2.1.234
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
    && npm cache clean --force

# --- supercronic, pinned + checksummed ------------------------------------
# Debian's cron does not inherit container env and logs to a place nothing
# reads. supercronic runs in the foreground as PID 1, inherits env, and logs to
# stdout — which is what makes `docker logs scheduler` a useful incident record.
# TARGETARCH is set by buildkit. Both checksums are real and were computed from
# the published release — the server is amd64, a dev Mac is arm64, and a
# hard-coded amd64 URL fails outright on the latter rather than degrading.
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256_amd64=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ARG SUPERCRONIC_SHA256_arm64=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5
ARG TARGETARCH
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) sha="${SUPERCRONIC_SHA256_amd64}" ;; \
      arm64) sha="${SUPERCRONIC_SHA256_arm64}" ;; \
      *) echo "unsupported TARGETARCH '${TARGETARCH}' — add its checksum" >&2; exit 1 ;; \
    esac; \
    curl -fsSLO "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH}"; \
    echo "${sha}  supercronic-linux-${TARGETARCH}" | sha256sum -c -; \
    chmod +x "supercronic-linux-${TARGETARCH}"; \
    mv "supercronic-linux-${TARGETARCH}" /usr/local/bin/supercronic

# --- schwab-mcp, pinned to the commit currently in production -------------
# NOT on PyPI — a git install. This commit is the one the Mac has been trading
# against, so the container starts behaviourally identical rather than "latest".
ARG SCHWAB_MCP_COMMIT=5be6357fed0f3d5ea5da1b1a0e73a76e7a322c88
RUN curl -fsSL https://astral.sh/uv/install.sh | sh \
    && uv tool install --python 3.12 \
       "schwab-mcp @ git+https://github.com/jkoelker/schwab-mcp.git@${SCHWAB_MCP_COMMIT}" \
    && chmod -R a+rX /opt/uv-tools /opt/uv-python

# --- unprivileged runtime user -------------------------------------------
# UID 1000 matches the usual first Ubuntu user, so the bind-mounted repo and the
# sidecar data repo are writable without chowning the host tree.
# The node base image already ships a `node` user at 1000:1000, so a plain
# `useradd -u 1000` FAILS. An earlier revision of this file suppressed that with
# `|| true` and produced an image whose USER did not exist — every container
# died with "unable to find user trader". No `|| true` here: if this block
# cannot produce the user, the build must fail loudly rather than ship broken.
ARG UID=1000
ARG GID=1000
RUN set -eu; \
    if getent group "${GID}" >/dev/null; then \
      groupmod -n trader "$(getent group "${GID}" | cut -d: -f1)"; \
    else \
      groupadd -g "${GID}" trader; \
    fi; \
    if getent passwd "${UID}" >/dev/null; then \
      usermod -l trader -d /home/trader -m -s /bin/bash "$(getent passwd "${UID}" | cut -d: -f1)"; \
    else \
      useradd -m -u "${UID}" -g "${GID}" -s /bin/bash trader; \
    fi; \
    mkdir -p /home/trader/.local/share/schwab-mcp /home/trader/.claude; \
    chown -R "${UID}:${GID}" /home/trader; \
    # prove it: a broken user must not survive the layer
    getent passwd trader

USER trader
WORKDIR /app
ENV HOME=/home/trader
