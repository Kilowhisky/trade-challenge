#!/bin/bash
# Guard the broker against attempting an interactive login inside a container
# where no human can complete one.
#
# THIS IS NOT THEORETICAL. Starting the broker with no token was measured:
# schwab-mcp's easy_client falls through to client_from_login_flow, which BLOCKS
# for callback_timeout (300s) waiting for a browser redirect that can never
# arrive, then exits. With `restart: unless-stopped` that is a crash-loop
# burning five minutes per iteration against Schwab's auth endpoint, and one
# Discord alert per restart forever.
#
# So: check the token BEFORE handing control to schwab-mcp. If it is missing or
# past the 5-day forced-re-auth age, say so once and then wait quietly, polling
# for a good token. The moment `schwab-mcp auth` writes a fresh one, the broker
# starts on its own — no `docker compose restart` needed.
set -uo pipefail

token_file="${SCHWAB_TOKEN_PATH:-$HOME/.local/share/schwab-mcp/token.yaml}"
max_age=$(( 5 * 24 * 60 * 60 ))     # DEFAULT_MAX_TOKEN_AGE_SECONDS, auth.py:12
# Test seam: shortening the poll changes only how fast recovery is noticed,
# never whether the token check passes, so it is safe to expose.
poll="${TC_BROKER_POLL_SECONDS:-300}"
notified=0

log() { printf '%s broker-entrypoint: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" >&2; }

notify_once() {
  [ "$notified" -eq 1 ] && return 0
  notified=1
  [ -x /app/scripts/discord-notify.sh ] || return 0
  /app/scripts/discord-notify.sh "$1" >/dev/null 2>&1 || log "discord notify failed"
}

token_ok() {
  [ -f "$token_file" ] || { reason="no token file at $token_file"; return 1; }
  local created age
  created="$(grep -m1 '^creation_timestamp:' "$token_file" | tr -dc '0-9')"
  [ -n "$created" ] || { reason="token file has no creation_timestamp"; return 1; }
  age=$(( $(date +%s) - created ))
  if [ "$age" -ge "$max_age" ]; then
    reason="token is $((age / 86400))d old, past the 5-day forced re-auth"
    return 1
  fi
  log "token is $((age / 3600))h old — starting broker"
  return 0
}

reason=""
until token_ok; do
  log "NOT STARTING: $reason"
  notify_once "🔑 **Schwab broker is waiting for re-auth** — $reason.

The broker is idle, not crash-looping; it will start by itself within ${poll}s of a good token appearing. Scheduled jobs that need the broker will fail until then.
\`\`\`
ssh -L 8182:127.0.0.1:8182 <server>
cd trade-challenge/docker && docker compose run --rm --network host schwab-auth
\`\`\`"
  sleep "$poll"
done

exec "$@"
