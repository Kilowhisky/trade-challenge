#!/bin/bash
# Warn before the Schwab token dies, instead of discovering it dead.
#
# The manual's "Standing operating constraint — token expiry" says a lapsed
# token costs all read access: no §4.5 reconciliation, no §3.6 check, no ability
# to close. Until now the only detector was a session opening and failing. On an
# unattended server that is strictly worse — nothing opens a session to notice.
#
# Two clocks matter and they are different:
#   - schwab-mcp proactively forces a NEW login flow at 5 days
#     (DEFAULT_MAX_TOKEN_AGE_SECONDS, auth.py:12). That flow is interactive and
#     BLOCKS for 300s, so on a headless box it does not recover — it hangs.
#   - Schwab itself kills the refresh token at 7 days.
# So 5 days is the real deadline, not 7.
#
# This reads ONLY the creation_timestamp line. It never loads the token itself:
# a watchdog has no business holding credentials in memory, and this way its
# output is safe to log.
#
# Exit: 0 always (a watchdog must never fail a scheduler run).
set -uo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
notify() { "$repo_root/scripts/discord-notify.sh" "$1" >/dev/null 2>&1 || true; }
say() { printf 'token-watchdog: %s\n' "$*" >&2; }

# Linux (container) first, then macOS, then an explicit override.
for candidate in \
  "${SCHWAB_TOKEN_PATH:-}" \
  "$HOME/.local/share/schwab-mcp/token.yaml" \
  "$HOME/Library/Application Support/schwab-mcp/token.yaml"
do
  [ -n "$candidate" ] && [ -f "$candidate" ] && { token_file="$candidate"; break; }
done

if [ -z "${token_file:-}" ]; then
  say "no token file found"
  notify "🚨 **Schwab token missing** — no token file on the server. Every scheduled job that touches the broker will fail until you re-auth. Runbook: README.md → Schwab re-auth."
  exit 0
fi

created="$(grep -m1 '^creation_timestamp:' "$token_file" | tr -dc '0-9')"
if [ -z "$created" ]; then
  say "could not read creation_timestamp from $token_file"
  notify "⚠️ **Schwab token unreadable** — \`creation_timestamp\` missing from the token file. Cannot tell how old it is; re-auth if jobs start failing."
  exit 0
fi

now="$(date +%s)"
age_days=$(( (now - created) / 86400 ))
age_hours=$(( (now - created) / 3600 ))
left=$(( 5 - age_days ))

say "token age ${age_days}d (${age_hours}h), ${left}d until the 5-day forced re-auth"

if [ "$age_days" -ge 5 ]; then
  notify "🚨 **Schwab token expired** — ${age_days}d old, past the 5-day forced re-auth. Scheduled jobs are failing or hanging NOW. Re-auth: \`ssh -L 8182:127.0.0.1:8182 <server>\` then \`docker compose run --rm --network host schwab-auth\`."
elif [ "$age_days" -ge 4 ]; then
  notify "⏳ **Schwab token expires tomorrow** — ${age_days}d old; forced re-auth at 5d. Re-auth: \`ssh -L 8182:127.0.0.1:8182 <server>\` then \`docker compose run --rm --network host schwab-auth\`."
fi
exit 0
