#!/bin/bash
# Locked, validated writer for the §7.2 session-close status file.
#   Usage: scripts/status-write.sh DATE [--replace] < content
#          -> status/DATE.md
#
# Why this exists at all. Until 2026-08-26 nothing on the server wrote a §7.2
# close file: the manual assumed a live session did it, and on an unattended box
# no session opens. The consequence was not a missing report — it was a latent
# self-halt. tick.md §B5 resolves the high-water mark from the most recent
# status file, and its orphan-ledger recovery rule says that if a prior day's
# tick ledger shows a comp_capital ABOVE that mark, the account writes ALERT.md
# and goes closing-only until Chris ratifies. With no close write, every
# profitable day left exactly that evidence behind. The system would have
# halted itself on a win, and only on a win. 2026-08-26 missed it by $7.36.
#
# So the validation below is not stylistic. Each required element is one the
# rest of the system greps for and would misread if absent.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=lib-lock.sh
. "$repo_root/scripts/lib-lock.sh"

date_arg="${1:-}"
replace=0
[ "${2:-}" = "--replace" ] && replace=1
if [ -z "$date_arg" ] || { [ "$#" -gt 1 ] && [ "$replace" -eq 0 ]; }; then
  echo "status-write: usage: status-write.sh YYYY-MM-DD [--replace] < content" >&2; exit 2
fi
[[ "$date_arg" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
  || { echo "status-write: bad DATE '$date_arg'" >&2; exit 2; }

file="$repo_root/status/$date_arg.md"
content="$(cat)"
line_count="$(printf '%s\n' "$content" | wc -l | tr -d ' ')"

# A truncated heredoc that still parses is the failure mode these writers exist
# to catch: it produces a file that looks real and states almost nothing.
if [ "$line_count" -le 15 ]; then
  echo "status-write: refused — only $line_count lines (truncated heredoc?)" >&2; exit 1
fi

first_line="$(printf '%s\n' "$content" | head -1)"
want_h1="# Session close — $date_arg"
if [ "$first_line" != "$want_h1" ]; then
  echo "status-write: refused — first line must be '$want_h1', got '$first_line'" >&2; exit 1
fi

# tick.md §B5 greps this EXACT heading, and warns that a status file may carry
# superseded blocks with near-identical headings. Requiring exactly one keeps
# the grep unambiguous instead of trusting it to pick the right hit.
state_hits="$(printf '%s\n' "$content" | grep -c '^### State recorded — current$' || true)"
if [ "$state_hits" -ne 1 ]; then
  echo "status-write: refused — need exactly one '### State recorded — current' heading, found $state_hits" >&2
  exit 1
fi

# The four figures the rest of the system reads back out of this file. Missing
# any one of them does not break the write; it breaks the NEXT session.
for required in \
  'Account value:' \
  'Competition capital:' \
  'High-water mark:' \
  'Settled cash:'
do
  if ! printf '%s\n' "$content" | grep -qF "$required"; then
    echo "status-write: refused — required field missing: '$required'" >&2; exit 1
  fi
done

# The ratchet is the whole reason the file is written on a schedule. Requiring
# the decision to be stated in words means a run cannot quietly carry the mark
# forward without having looked at the day's high.
if ! printf '%s\n' "$content" | grep -qiE 'ratchet|carried unchanged'; then
  echo "status-write: refused — the HWM line must say whether the mark ratcheted or was carried unchanged" >&2
  exit 1
fi

mkdir -p "$(dirname "$file")"
if [ -e "$file" ] && [ "$replace" -eq 0 ]; then
  echo "status-write: refused — status/$date_arg.md exists; pass --replace to overwrite" >&2; exit 3
fi
# max_age 900: an unattended run killed mid-write leaves a lock dir with no
# process behind it, and the next day's run would block forever (lib-lock.sh).
if ! acquire_lock "$file" 10 900; then
  echo "status-write: refused — could not acquire lock on $file" >&2; exit 5
fi
if [ -f "$file" ]; then cp "$file" "$file.prev"; fi
printf '%s\n' "$content" > "$file"
echo "status-write: wrote $line_count lines to ${file#"$repo_root"/}"
