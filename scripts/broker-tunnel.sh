#!/bin/bash
# Make sure this machine can reach the ONE live Schwab broker.
#
# Schwab issues one refresh token per app authorisation, so a second schwab-mcp
# here cannot hold a live token alongside the server's. This laptop therefore
# talks to `tc-broker` on the server over an SSH forward instead of running its
# own. On 2026-08-25 that was not yet true: the laptop's own token was revoked
# by a server re-auth, it read `invalid_grant`, concluded the account was
# unreadable, and a full session passed with no §4.5 reconciliation, no §3.6
# check and no monitoring — while the server read the account fine all day.
#
# The forward dies with a reboot or a sleep, and a session that opens without it
# reproduces exactly that failure. So this runs at session start.
#
# Exit: ALWAYS 0. A session must never fail to start because a tunnel is down —
# the broker tools will fail loudly and specifically at first use, which is a
# far better error than a hook aborting the session.
#
# Usage: [TC_BROKER_SSH_HOST=host] scripts/broker-tunnel.sh [--check]
#   --check  report only; never start anything.
#
# The host is NOT hard-coded: this repo is public (§7.4), and a personal machine
# alias is exactly the sort of detail that should not be published. Set
# TC_BROKER_SSH_HOST in .claude/settings.local.json, which is gitignored.
set -uo pipefail

port="${TC_BROKER_PORT:-8000}"
host="${TC_BROKER_SSH_HOST:-}"
check_only=0
[ "${1:-}" = "--check" ] && check_only=1

say() { printf 'broker-tunnel: %s\n' "$*"; }

# Is something already listening locally? Pure bash, no nc/lsof dependency.
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; }

if port_open; then
  say "already up on 127.0.0.1:$port"
  exit 0
fi

if [ "$check_only" -eq 1 ]; then
  say "DOWN — nothing listening on 127.0.0.1:$port"
  exit 0
fi

if [ -z "$host" ]; then
  say "DOWN on 127.0.0.1:$port, and TC_BROKER_SSH_HOST is unset — cannot restore it."
  say "Set it in .claude/settings.local.json (gitignored) or start the forward by hand."
  exit 0
fi

# -f -N: background, no remote command. ExitOnForwardFailure so a refused
# forward fails here rather than leaving a live SSH that forwards nothing.
# The keepalives let a laptop that slept notice a dead connection and exit,
# instead of holding the local port hostage against the next attempt.
if ssh -f -N \
     -o ExitOnForwardFailure=yes \
     -o ConnectTimeout=8 \
     -o BatchMode=yes \
     -o ServerAliveInterval=30 \
     -o ServerAliveCountMax=3 \
     -L "${port}:127.0.0.1:${port}" "$host" 2>/dev/null
then
  # Started is not the same as usable — confirm the port actually answers.
  if port_open; then
    say "started -> $host (127.0.0.1:$port)"
  else
    say "ssh returned success but 127.0.0.1:$port is not answering — check the server"
  fi
else
  say "could not reach $host — the broker tools will not work until this is fixed"
fi
exit 0
