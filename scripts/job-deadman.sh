#!/bin/bash
# Did the jobs that should have run today actually run?
#
# This is the MACHINERY check. strategy.md §9 already has OUTPUT checks (the
# preopen/screen mtime deadman, the universe `Assembled:` stamp) and both stay —
# they catch a job that ran and quietly produced nothing, which this cannot see.
# Together they cover both halves; alone, either misses a real failure.
#
# HONEST LIMITATION, stated because it would otherwise be assumed away: this
# runs INSIDE the scheduler container. If the container is down, this does not
# run either, and nothing here fires. A dead container is caught by the §9
# session-open deadman, by a human, a day later. Detecting it faster needs a
# heartbeat watched from OUTSIDE the box, which this is not.
#
# Exit: 0 always (a watchdog must never fail a scheduler run).
set -uo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
heartbeat="$repo_root/status/cron/heartbeat.jsonl"
et() { TZ=America/New_York date "$@"; }
notify() { "$repo_root/scripts/discord-notify.sh" "$1" >/dev/null 2>&1 || true; }
say() { printf 'job-deadman: %s\n' "$*" >&2; }

today="$(et +%F)"
dow="$(et +%u)"

if [ ! -f "$heartbeat" ]; then
  say "no heartbeat file"
  notify "🚨 **No scheduled-job heartbeat** — \`status/cron/heartbeat.jsonl\` does not exist. Either nothing has run since the server came up, or the data mount is wrong."
  exit 0
fi

# Latest verdict for a job on a given date. Plain grep on the JSON line rather
# than a jq dependency — this must still work if jq is somehow missing, because
# a watchdog that fails silently is worse than no watchdog.
# A dry run is a rehearsal, not evidence that the job fired. It writes to the
# same heartbeat, and on 2026-08-26 two dry runs executed on the server at 01:10
# ET landed as that day's preopen and postclose with verdict "ok" — which would
# have told this watchdog the morning's jobs were fine while nothing had run.
# The entries are tagged; ignore them here so a rehearsal can never stand in for
# a real fire.
verdict_for() { # job date
  grep "\"job\":\"$1\"" "$heartbeat" 2>/dev/null \
    | grep "\"started\":\"$2" \
    | grep -v '"dry_run":true' \
    | tail -1 \
    | sed -n 's/.*"verdict":"\([a-z]*\)".*/\1/p'
}
detail_for() {   # same dry-run exclusion as verdict_for, or the two disagree
  grep "\"job\":\"$1\"" "$heartbeat" 2>/dev/null \
    | grep "\"started\":\"$2" \
    | grep -v '"dry_run":true' | tail -1 \
    | sed -n 's/.*"detail":"\([^"]*\)".*/\1/p'
}

problems=()

check() { # job date label
  local job="$1" date="$2" label="$3" v d
  v="$(verdict_for "$job" "$date")"
  d="$(detail_for "$job" "$date")"
  case "$v" in
    ok)      say "$label: ok" ;;
    "")      problems+=("**$label** — never fired. No heartbeat entry for \`$job\` on $date.") ;;
    failed)  problems+=("**$label** — FAILED ($d). See \`status/cron/${date}-${job}.log\`.") ;;
    skipped) problems+=("**$label** — skipped ($d). A skip on a normal session day means the schedule and the guard disagree.") ;;
    *)       problems+=("**$label** — unexpected verdict '$v'.") ;;
  esac
}

# Weekdays only: today's preopen should have fired by 09:35 ET.
if [ "$dow" -le 5 ]; then
  check preopen "$today" "preopen $today"

  # Yesterday's postclose — the most recent prior weekday.
  back=1; [ "$dow" -eq 1 ] && back=3        # Monday looks back to Friday
  prev="$(et -v-${back}d +%F 2>/dev/null || et -d "-${back} days" +%F)"
  check postclose "$prev" "postclose $prev"
fi

if [ "${#problems[@]}" -eq 0 ]; then
  say "all expected jobs accounted for"
  exit 0
fi

msg="🔔 **Scheduled-job deadman — $today**"
for p in "${problems[@]}"; do msg+=$'\n• '"$p"; done
say "${#problems[@]} problem(s)"
notify "$msg"
exit 0
