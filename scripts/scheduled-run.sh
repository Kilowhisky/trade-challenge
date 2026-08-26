#!/bin/bash
# The single entry point for every scheduled job on the server.
#
# Nothing in docker/crontab calls `claude` directly. Locking, the ET window
# guard, tool grants, logging, the heartbeat, the Discord relay and the sidecar
# push all live here, once, so a new job is a case branch rather than a new
# opportunity to forget the guard.
#
# Usage: scripts/scheduled-run.sh <job>
#   preopen | postclose | weekly-universe
#
# Exit: 0 ran (or correctly no-opped) · 1 the job failed · 2 usage error.
set -uo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root" || exit 2
# shellcheck source=lib-lock.sh
. "$repo_root/scripts/lib-lock.sh"

job="${1:-}"
[ -n "$job" ] || { echo "scheduled-run: usage: scheduled-run.sh JOB [--force]" >&2; exit 2; }
shift

# --force runs a job outside its day/window, for a HUMAN-ordered catch-up: a
# missed weekend sweep, a job the container was down for. Same precedent as
# deploy.sh --force, and for the same reason — the guard exists to stop the
# SCHEDULER firing at the wrong time, not to stop Chris asking for a run.
#
# It is deliberately not the TC_DOW/TC_NOW_ET_MIN seam below, which stays
# dry-run-only: those SIMULATE a different clock, so a live one would make every
# log line and every date-derived decision inside the run a lie. --force changes
# no clock. The run still resolves its own date from get_datetime, still takes
# the lock, still logs, still heartbeats — and the heartbeat records `forced`,
# so a forced run can never be mistaken for a normal fire in the audit trail.
#
# What it does NOT bypass: each command file's own preconditions. weekly-universe
# §A.2 still requires get_market_hours to show the market closed.
forced=0
for a in "$@"; do
  case "$a" in
    --force) forced=1 ;;
    *) echo "scheduled-run: unknown arg '$a'" >&2; exit 2 ;;
  esac
done

# --- ET clock -------------------------------------------------------------
# Explicit TZ rather than the machine zone: this script is written on a PT
# laptop and runs in an ET container, and a window guard that silently means a
# different three hours depending on where it runs is worse than no guard.
#
# This is the SCHEDULING clock only. Every trading decision downstream still
# resolves its date and time from get_datetime, per the manual — the wrapper
# never hands a date to the agent.
et() { TZ=America/New_York date "$@"; }
now_et_min=$(( 10#$(et +%H) * 60 + 10#$(et +%M) ))
today="$(et +%F)"
dow="$(et +%u)"          # 1=Mon .. 7=Sun
stamp="$(et '+%Y-%m-%d %H:%M:%S %Z')"

# --- local secrets --------------------------------------------------------
# deep-research.md §D says FMP_API_KEY is "sourced from .env.local". Nothing
# did the sourcing, so the 2026-08-25 postclose ran with the key unreadable
# and had to substitute a weaker web sweep. The file is a bind-mounted,
# mode-600 KEY=value list; export it into the agent's environment if present.
# It is never logged and never handed to the prompt.
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
if [ -r "$repo_root/.env.local" ]; then
  set -a; . "$repo_root/.env.local"; set +a
fi

# --- test seam ------------------------------------------------------------
# TC_DRY_RUN=1 replaces the claude dispatch with an echo, so the guards can be
# exercised for real. The clock and day-of-week overrides are honoured ONLY
# under dry run — a production invocation cannot reach them, which is the point:
# a window guard with a live bypass is not a guard.
if [ "${TC_DRY_RUN:-0}" = "1" ]; then
  [ -n "${TC_NOW_ET_MIN:-}" ] && now_et_min="$TC_NOW_ET_MIN"
  [ -n "${TC_DOW:-}" ] && dow="$TC_DOW"
fi

log_dir="$repo_root/status/cron"
mkdir -p "$log_dir"
log_file="$log_dir/${today}-${job}.log"
heartbeat="$log_dir/heartbeat.jsonl"

say() { printf '%s  %s\n' "$(et '+%H:%M:%S')" "$*" | tee -a "$log_file" >&2; }

# Records every outcome, including the no-ops. job-deadman.sh reads this, and a
# job that skipped for a good reason must be distinguishable from one that never
# fired at all — otherwise the deadman cries wolf every weekend.
beat() {
  local verdict="$1" detail="${2:-}"
  if command -v jq >/dev/null 2>&1; then
    jq -nc --arg j "$job" --arg t "$stamp" --arg e "$(et '+%Y-%m-%d %H:%M:%S %Z')" \
           --arg v "$verdict" --arg d "$detail" --argjson f "$forced" \
      '{job:$j,started:$t,ended:$e,verdict:$v,detail:$d} + (if $f == 1 then {forced:true} else {} end)' >> "$heartbeat"
  else
    printf '{"job":"%s","started":"%s","verdict":"%s","detail":"%s"%s}\n' \
      "$job" "$stamp" "$verdict" "$detail" \
      "$([ "$forced" -eq 1 ] && printf ',"forced":true')" >> "$heartbeat"
  fi
}

notify() { "$repo_root/scripts/discord-notify.sh" "$1" || say "WARN discord-notify failed"; }

# --- job table ------------------------------------------------------------
# window_open/window_close are ET minutes-since-midnight. The guard is what
# stops a late fire — a container that was down at 08:17 and came up at 11:00 —
# from writing a file that claims to be a pre-open brief. §P is explicit that a
# brief written mid-session is "a backdated file describing a session already
# half over".
common_tools="Read Glob Grep ToolSearch WebSearch WebFetch"
schwab_read="mcp__schwab__get_datetime mcp__schwab__get_market_hours mcp__schwab__get_quotes mcp__schwab__get_instruments mcp__schwab__get_option_chain mcp__schwab__get_option_expiration_chain mcp__schwab__get_advanced_price_history mcp__schwab__get_advanced_option_chain"
write_scripts="Bash(scripts/research-replace.sh:*) Bash(scripts/research-append.sh:*) Bash(scripts/research-write.sh:*) Bash(scripts/oi-append.sh:*) Bash(scripts/data-append.sh:*)"

case "$job" in
  preopen)
    agent="deep-research"
    window_open=$((8*60));  window_close=$((9*60+15))
    days="1 2 3 4 5"
    allowed="$common_tools $schwab_read $write_scripts"
    prompt="Run /deep-research preopen for ${today}.

You are the scheduled 08:15 ET pre-open run, dispatched by the server
scheduler. Execute .claude/commands/deep-research.md §A then §P exactly as
written.

Resolve your own context from the canonical sources — do NOT assume any value
was passed to you:
- Date and ET time: mcp__schwab__get_datetime, never the machine clock.
- Held symbols and sectors: the latest status/*.md file.
- Competition capital: the 'State recorded — current' block of the latest
  status/*.md (echo the figure and its date).
- Active calendar guards: read them from strategy.md §4 and §10 at run time.
  Never from a date written into a command file or into this prompt.

§P is file-only: no pings, no §E, no HOT promotions, no candidates.md writes.
Write exactly one file, via scripts/research-replace.sh preopen ${today}.
If §A.6 finds research/preopen/${today}.md already exists, no-op and say so."
    ;;

  postclose)
    agent="deep-research"
    window_open=$((16*60+15)); window_close=$((18*60))
    days="1 2 3 4 5"
    allowed="$common_tools $schwab_read $write_scripts"
    prompt="Run /deep-research postclose for ${today}.

You are the scheduled 16:20 ET post-close run, dispatched by the server
scheduler. Execute .claude/commands/deep-research.md §A then §D exactly as
written, and you own the POST window.

Resolve your own context from the canonical sources — do NOT assume any value
was passed to you:
- Date and ET time: mcp__schwab__get_datetime, never the machine clock.
- Held symbols and sectors: the latest status/*.md file.
- Competition capital: the 'State recorded — current' block of the latest
  status/*.md (echo the figure and its date).
- Active calendar guards: read them from strategy.md §4 and §10 at run time.

You have no session to ping into and no §E authority — §E belongs to the
parent, and here there is none. Emit your HOT-FRESH: lines in your final
output as usual; the wrapper relays them to Discord for Chris to act on at the
next session open. Do not attempt to notify anyone yourself."
    ;;

  weekly-universe)
    agent="weekly-universe"
    window_open=$((6*60));  window_close=$((12*60))
    days="6 7"
    allowed="Read Glob Grep ToolSearch mcp__schwab__get_datetime mcp__schwab__get_market_hours mcp__schwab__get_quotes Bash(scripts/universe-fetch.sh:*) Bash(scripts/universe-filter.sh:*) Bash(scripts/research-replace.sh:*)"
    prompt="Run /weekly-universe for ${today}.

You are the scheduled weekend whole-market sweep, dispatched by the server
scheduler. Execute .claude/commands/weekly-universe.md §A then §B-§D exactly as
written, and regenerate research/universe.md via scripts/research-replace.sh.

§A.2 requires the market to be closed — confirm with get_market_hours before
sweeping. Resolve the date from get_datetime, never the machine clock."
    ;;

  *)
    echo "scheduled-run: unknown job '$job'" >&2; exit 2 ;;
esac

# --- guards ---------------------------------------------------------------
printf '\n===== %s  job=%s =====\n' "$stamp" "$job" >> "$log_file"

if [ "$forced" -eq 1 ]; then
  say "FORCED — human-ordered catch-up; day/window guards bypassed (dow=$dow, $(et '+%H:%M') ET)"
fi

if [ "$forced" -eq 0 ] && ! grep -qw "$dow" <<<"$days"; then
  say "SKIP wrong day of week (dow=$dow, wanted: $days)"
  beat skipped "wrong day of week"
  exit 0
fi

if [ "$forced" -eq 0 ] \
   && { [ "$now_et_min" -lt "$window_open" ] || [ "$now_et_min" -gt "$window_close" ]; }; then
  say "SKIP outside ET window ($(printf '%02d:%02d' $((window_open/60)) $((window_open%60)))-$(printf '%02d:%02d' $((window_close/60)) $((window_close%60))) ET)"
  beat skipped "outside ET window"
  # A missed preopen is not silent: the playbook §9 catch-up is the guarantee,
  # but Chris should know the fast path failed rather than discover it there.
  [ "$job" = "preopen" ] && notify "⏭️ preopen skipped — fired at $(et '+%H:%M') ET, outside its 08:00-09:15 window. The §9 session-open catch-up still covers today if you open before 12:00 ET."
  exit 0
fi

# Take the repo update BEFORE the lock: repo-update.sh has its own gate and may
# roll back, and holding the job lock across a git checkout buys nothing.
if [ -x "$repo_root/scripts/repo-update.sh" ]; then
  "$repo_root/scripts/repo-update.sh" >>"$log_file" 2>&1 || say "WARN repo-update reported a problem; continuing on current checkout"
fi

# 3h stale breaker: longer than any legitimate run, shorter than the gap to the
# next job of the same name.
if ! acquire_lock "$log_dir/$job" 30 10800; then
  say "SKIP another $job run holds the lock"
  beat skipped "lock held"
  exit 0
fi

# --- dispatch -------------------------------------------------------------
mcp_config="$repo_root/docker/mcp-config.json"
claude_args=(-p --agent "$agent" --output-format text)
if [ -f "$mcp_config" ] && [ -n "${TC_BROKER_URL:-}" ]; then
  # --strict-mcp-config: the container broker is the ONLY reachable MCP server,
  # so a stray user-scope config on the host cannot smuggle in a second broker.
  claude_args+=(--mcp-config "$mcp_config" --strict-mcp-config)
fi
# --allowedTools is VARIADIC. Commander keeps consuming following non-flag
# words, so ANY positional prompt after it is swallowed as one more tool name —
# and commas do not save you, because "a,b" is just its first value and the
# prompt becomes its second. Measured on the Pi: claude exits with "Input must
# be provided either through stdin or as a prompt argument". This would have
# failed every scheduled job on its first real run.
#
# The prompt therefore goes on STDIN (see the dispatch below), which no argv
# ordering can break. Comma-joining here is still right: it keeps the tool list
# to a single argv word so nothing else can drift into it.
claude_args+=(--allowedTools "$(printf '%s' "$allowed" | tr -s ' ' ',')")

# Bound the run. A hung job must not still be holding its lock when tomorrow's
# fires. `timeout` is GNU-only; the container has it, a dev Mac may not.
runner=()
if command -v timeout >/dev/null 2>&1; then runner=(timeout -k 60 3600)
elif command -v gtimeout >/dev/null 2>&1; then runner=(gtimeout -k 60 3600)
else say "WARN no timeout(1) available; running unbounded"; fi

say "RUN agent=$agent"
out_file="$log_dir/.${job}.out.$$"
if [ "${TC_DRY_RUN:-0}" = "1" ]; then
  printf 'DRY-RUN would exec: claude %s <stdin-prompt:%d bytes>\n' "${claude_args[*]}" "${#prompt}" >"$out_file"
  rc=0
else
  # Prompt on stdin, never as a trailing argument — see the --allowedTools note.
  printf '%s' "$prompt" | "${runner[@]}" claude "${claude_args[@]}" >"$out_file" 2>>"$log_file"
  rc=$?
fi
cat "$out_file" >> "$log_file"
output="$(cat "$out_file")"; rm -f "$out_file"

# 124 is timeout(1) giving up; 137 is SIGKILL, which on a 3.7Gi Pi is far more
# often the OOM killer than our own -k escalation. Reporting both as "timed out"
# would send someone hunting a slow API when the real fix is a memory limit.
if [ "$rc" -eq 124 ]; then
  say "FAIL timed out after 3600s"
  beat failed "timeout"
  notify "⚠️ scheduled job \`$job\` timed out after 60m on $today. Log: status/cron/${today}-${job}.log"
  exit 1
fi
if [ "$rc" -eq 137 ]; then
  say "FAIL killed (SIGKILL) — most likely the OOM killer, or the timeout escalation"
  beat failed "sigkill (probable OOM)"
  notify "⚠️ scheduled job \`$job\` was KILLED on $today — probably out of memory (the Pi has 3.7Gi). Check \`docker stats\` and the mem_limit on the scheduler service. Log: status/cron/${today}-${job}.log"
  exit 1
fi
if [ "$rc" -ne 0 ]; then
  say "FAIL claude exited $rc"
  beat failed "exit $rc"
  notify "⚠️ scheduled job \`$job\` failed (exit $rc) on $today. Log: status/cron/${today}-${job}.log"
  exit 1
fi

say "OK"
beat ok "$(head -c 200 <<<"$output" | tr '\n' ' ')"

# --- relay HOT-FRESH ------------------------------------------------------
# research.md §E is explicit that a suppressed ping is not lost: the candidate is
# in the file and the session protocol reads it at open. This relay is a
# convenience on top of that, never a substitute — and deliberately NOT a ping
# gate. §E.2 needs the latest tick's figures and §E.5 owns the two daily ping
# slots; both belong to a live session, and at 16:20 ET nothing is actionable
# until one opens anyway.
hot="$(grep -E '^HOT-FRESH:' <<<"$output" || true)"
if [ -n "$hot" ]; then
  notify "📈 **$job $today** — deep-research surfaced HOT-FRESH candidates. Not a ping: no §E gate has run, and none can until a session opens.
\`\`\`
$hot
\`\`\`"
fi

# Line 1 is the command file's return-line contract; it is the one line worth
# reading without opening the log.
head -1 <<<"$output" | grep -qE '^(DEEP|UNIVERSE|FAIL)' && notify "✅ $(head -1 <<<"$output")"

# --- push what we wrote to the private sidecar ---------------------------
# Non-fatal by design: the research already succeeded and is on disk. A failed
# push is a delivery problem, not a research problem, and the next job retries.
#
# NEVER under dry run. The regression suite drives this script through every
# window-guard case, and without this check each passing case committed and
# PUSHED its heartbeat to the live private repo — 7 junk commits before it was
# noticed. A test must not write to production.
if [ "${TC_DRY_RUN:-0}" = "1" ]; then
  say "dry run — not syncing the sidecar"
else
  "$repo_root/scripts/sidecar-sync.sh" "$job $today" >>"$log_file" 2>&1 \
    || say "WARN sidecar sync failed; output is on the server but not yet pushed"
fi

exit 0
