#!/bin/bash
# Pull a new toolchain image, health-check it, and roll back if it is bad.
#
# TIMING IS THE SAFETY PROPERTY HERE, not an optimisation. A container restart
# inside the session does two bad things: it kills an in-flight tick, and — much
# worse — it drops any pending Discord approval. The approval manager's state is
# explicitly "in-memory and process-local" (approvals/codemap.md): a restart
# while an order sits awaiting ✅ loses the future that was waiting on it, and
# the order is neither placed nor cleanly denied. So this refuses to run inside
# 09:15-16:15 ET without --force.
#
# This is also why there is no Watchtower and no auto-pull. Uncontrolled restart
# timing IS the failure mode.
#
# Usage: scripts/deploy.sh [--force] [--check-only]
# Exit: 0 no change or deployed cleanly · 1 deployed and rolled back · 2 refused.
set -uo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root/docker" || exit 2

force=0; check_only=0
for a in "$@"; do
  case "$a" in
    --force) force=1 ;;
    --check-only) check_only=1 ;;
    *) echo "deploy: unknown arg '$a'" >&2; exit 2 ;;
  esac
done

et() { TZ=America/New_York date "$@"; }
say() { printf 'deploy: %s\n' "$*" >&2; }
notify() { "$repo_root/scripts/discord-notify.sh" "$1" >/dev/null 2>&1 || true; }
compose() { docker compose "$@"; }

image="${TC_IMAGE:-ghcr.io/kilowhisky/trade-challenge:latest}"
digest_file="$repo_root/docker/.deployed-digest"

# --- the window guard -----------------------------------------------------
now_min=$(( 10#$(et +%H) * 60 + 10#$(et +%M) ))
dow="$(et +%u)"
if [ "$force" -eq 0 ] && [ "$dow" -le 5 ] \
   && [ "$now_min" -ge $((9*60+15)) ] && [ "$now_min" -le $((16*60+15)) ]; then
  say "REFUSED — $(et '+%H:%M') ET is inside the session (09:15-16:15). A restart here can drop a pending Discord approval. Use --force only if you know no order is in flight."
  exit 2
fi

command -v docker >/dev/null 2>&1 || { say "docker not found"; exit 2; }

# Capture what is deployed NOW as a REGISTRY-ADDRESSABLE reference, before
# pulling over it. The obvious `docker inspect --format '{{.Id}}'` gives a
# LOCAL image id, which is not a reference at all: feeding it back produced
# `ghcr.io/…:sha256:0f8b…`, docker refused it as an invalid reference, the
# rollback silently did nothing, and the Discord message said "restored". A
# rollback that lies is worse than no rollback, because it is trusted at the
# exact moment something is already broken.
repo_digest() { docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$1" 2>/dev/null || true; }

prior_ref="$(repo_digest "$image")"
docker pull --quiet "$image" >/dev/null 2>&1 || { say "pull of $image failed"; exit 2; }
latest_ref="$(repo_digest "$image")"

if [ -z "$latest_ref" ]; then
  # A locally-built image has no RepoDigests. Legitimate on a box that built
  # its own first image; just say so rather than inventing a reference.
  say "$image has no registry digest (built locally?) — cannot manage it by digest"
  exit 2
fi

if [ "$prior_ref" = "$latest_ref" ]; then
  [ "$check_only" -eq 1 ] && say "up to date (${latest_ref##*@})"
  exit 0                       # quiet: the daily common case
fi

if [ "$check_only" -eq 1 ]; then
  say "update available: ${prior_ref##*@} -> ${latest_ref##*@}"
  exit 0
fi

say "deploying ${prior_ref##*@} -> ${latest_ref##*@}"
if [ -n "$prior_ref" ]; then
  printf '%s\n' "$prior_ref" > "$digest_file"
else
  # Nothing to roll back TO. Say it now, loudly, rather than discovering it
  # during a failed health check.
  say "WARNING: no previous image recorded — a failed deploy cannot be rolled back automatically"
  rm -f "$digest_file"
fi

compose up -d --quiet-pull 2>&1 | sed 's/^/  /' >&2

# --- health check ---------------------------------------------------------
# Two levels: the broker must answer, and a real end-to-end claude run must
# produce a well-formed tick line. A broker that listens but returns garbage
# would pass the first and fail the second, which is the point.
healthy=0
for _ in $(seq 1 30); do
  state="$(docker inspect --format '{{.State.Health.Status}}' tc-broker 2>/dev/null || echo none)"
  [ "$state" = "healthy" ] && { healthy=1; break; }
  sleep 2
done

if [ "$healthy" -eq 1 ]; then
  smoke="$(compose run --rm -T claude claude -p --output-format text \
            --mcp-config /app/docker/mcp-config.json --strict-mcp-config \
            --allowedTools mcp__schwab__get_datetime \
            "Call get_datetime and reply with exactly the ET date and time it returns, nothing else." 2>&1)" || smoke=""
  grep -qE '[0-9]{4}-[0-9]{2}-[0-9]{2}' <<<"$smoke" || healthy=0
fi

if [ "$healthy" -eq 1 ]; then
  notify "✅ **Deploy OK** — \`${latest_ref##*@}\` live. Broker healthy, smoke run reached Schwab."
  say "deployed ${latest_ref##*@}"
  exit 0
fi

# --- rollback -------------------------------------------------------------
prior="$(cat "$digest_file" 2>/dev/null || true)"
if [ -z "$prior" ]; then
  say "HEALTH CHECK FAILED and no prior image recorded — cannot roll back automatically"
  notify "🚨 **Deploy FAILED and could not roll back** — \`${latest_ref##*@}\` is unhealthy and no prior image was recorded. Manual intervention needed on the server."
  exit 1
fi

# Refuse to "roll back" to something docker cannot address. Reporting a restore
# that did not happen is the failure this whole block exists to prevent.
if ! printf '%s' "$prior" | grep -qE '^[a-z0-9./-]+(:[0-9]+)?/[a-z0-9._/-]+(@sha256:[0-9a-f]{64}|:[A-Za-z0-9._-]+)$'; then
  say "recorded rollback target is not a valid image reference: $prior"
  notify "🚨 **Deploy FAILED and rollback is unusable** — \`${latest_ref##*@}\` is unhealthy, and the recorded rollback target (\`$prior\`) is not a valid image reference. The box is on the bad image. Manual intervention needed."
  exit 1
fi

say "health check failed — rolling back to ${prior##*@}"
if TC_IMAGE="$prior" compose up -d --quiet-pull 2>&1 | sed 's/^/  /' >&2; then
  notify "🚨 **Deploy FAILED, rolled back** — \`${latest_ref##*@}\` failed its health check; restored \`${prior##*@}\`. Scheduled jobs continue on the prior image."
else
  notify "🚨 **Deploy FAILED and the rollback ALSO failed** — the box may be on a broken image. Manual intervention needed on the server now."
fi
exit 1
