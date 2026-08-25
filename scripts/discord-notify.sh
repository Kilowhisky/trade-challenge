#!/bin/bash
# Post one message to the Discord channel the approval workflow already uses.
#
# Why this exists: on the server there is no session to print to. A postclose
# HOT-FRESH ping, a token-expiry warning, or a failed deploy has to reach Chris
# somewhere, and #llm-yolo is where he already watches for ✅/❌ prompts.
#
# This is a NOTIFIER, not an approval path. It cannot approve anything and must
# never be used to ask for a decision — approvals belong to the Schwab MCP's own
# Discord workflow (approvals/discord.py), which owns reaction handling and
# authorised-approver checks. Sending a "reply yes to confirm" message from here
# would create a second, unauthenticated approval channel.
#
# Usage:
#   scripts/discord-notify.sh "message"
#   echo "message" | scripts/discord-notify.sh -
#
# Env, in priority order:
#   TC_DISCORD_TOKEN / TC_DISCORD_CHANNEL_ID          (preferred)
#   SCHWAB_MCP_DISCORD_TOKEN / SCHWAB_MCP_DISCORD_CHANNEL_ID  (fallback)
#
# The alias is a SAFETY mechanism, not convenience. schwab-mcp decides whether
# to register the order-execution tools with
#   discord_requested = any((discord_token, discord_channel_id, approvers))
# reading exactly the SCHWAB_MCP_DISCORD_* names from the environment. So giving
# the broker container those names — merely so it can send a notification —
# would silently flip allow_write to True and hand it place_previewed_order.
# The broker therefore gets TC_DISCORD_*, which schwab-mcp never reads.
#
# Exit: 0 posted · 1 post failed · 2 usage/config error.
# Callers should treat a failure as non-fatal — a missed notification must never
# take down the research run it was reporting on.
set -uo pipefail

if [ "$#" -ne 1 ]; then
  echo "discord-notify: usage: discord-notify.sh MESSAGE|-" >&2
  exit 2
fi

if [ "$1" = "-" ]; then message="$(cat)"; else message="$1"; fi
[ -n "${message//[[:space:]]/}" ] || { echo "discord-notify: empty message" >&2; exit 2; }

token="${TC_DISCORD_TOKEN:-${SCHWAB_MCP_DISCORD_TOKEN:-}}"
channel="${TC_DISCORD_CHANNEL_ID:-${SCHWAB_MCP_DISCORD_CHANNEL_ID:-}}"
if [ -z "$token" ] || [ -z "$channel" ]; then
  echo "discord-notify: need TC_DISCORD_TOKEN/TC_DISCORD_CHANNEL_ID (or the SCHWAB_MCP_DISCORD_* fallback)" >&2
  exit 2
fi
command -v jq >/dev/null 2>&1 || { echo "discord-notify: jq not found" >&2; exit 2; }

# Discord hard-caps content at 2000 characters and rejects the whole request if
# it is exceeded, so truncate rather than lose the message entirely.
if [ "${#message}" -gt 1900 ]; then
  message="${message:0:1900}"$'\n…(truncated)'
fi

payload="$(jq -nc --arg c "$message" '{content: $c, allowed_mentions: {parse: []}}')"

code="$(curl -sS -o /tmp/discord-notify.$$ -w '%{http_code}' \
  -X POST "https://discord.com/api/v10/channels/${channel}/messages" \
  -H "Authorization: Bot ${token}" \
  -H "Content-Type: application/json" \
  --max-time 20 \
  -d "$payload" 2>/dev/null)" || code="000"

body="$(cat /tmp/discord-notify.$$ 2>/dev/null)"; rm -f /tmp/discord-notify.$$

if [ "$code" = "200" ] || [ "$code" = "201" ]; then
  exit 0
fi

# Never echo the token back, even on failure — these logs get committed.
echo "discord-notify: POST failed (HTTP ${code}): ${body:0:300}" >&2
exit 1
