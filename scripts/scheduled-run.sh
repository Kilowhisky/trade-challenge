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

# TC_STATUS_DIR is a TEST SEAM, honoured only under TC_DRY_RUN=1 — production
# cannot be redirected, the same rule TC_NOW_ET_MIN follows.
#
# It exists because on the laptop `status/` is a SYMLINK into the private store
# repo, so the suite's `rm -rf status/cron` between dispatches was deleting
# server-written cron logs out of a real working tree. That is the "files keep
# vanishing from the store" behaviour that went unexplained for two days and got
# blamed on iCloud sync; it was this. A test that damages real data to check a
# guard is a worse defect than the guard being unchecked.
if [ "${TC_DRY_RUN:-0}" = "1" ] && [ -n "${TC_STATUS_DIR:-}" ]; then
  printf 'scheduled-run: TEST SEAM active — logs to %s, not the real status/\n' "$TC_STATUS_DIR" >&2
  log_dir="$TC_STATUS_DIR/cron"
else
  log_dir="$repo_root/status/cron"
fi
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
           --argjson dr "${TC_DRY_RUN:-0}" \
      '{job:$j,started:$t,ended:$e,verdict:$v,detail:$d}
       + (if $f == 1 then {forced:true} else {} end)
       + (if $dr == 1 then {dry_run:true} else {} end)' >> "$heartbeat"
  else
    printf '{"job":"%s","started":"%s","verdict":"%s","detail":"%s"%s%s}\n' \
      "$job" "$stamp" "$verdict" "$detail" \
      "$([ "$forced" -eq 1 ] && printf ',"forced":true')" \
      "$([ "${TC_DRY_RUN:-0}" = "1" ] && printf ',"dry_run":true')" >> "$heartbeat"
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
# universe-filter.sh computes; it writes nothing. It is separated from
# write_scripts so the name keeps meaning what it says, and it is here at all
# because deep-research.md §A screens research/universe.md through it. Omitting
# it did not fail the 2026-08-26 postclose run — it made it SKIP the universe
# screen and report the skip in a line nobody was required to read. That is the
# third time an allowlist has silently dropped a step, so the omission is now a
# test failure rather than a log entry (test-scheduled-run.sh).
compute_scripts="Bash(scripts/universe-filter.sh:*) Bash(scripts/latest-status.sh:*)"

case "$job" in
  preopen)
    agent="deep-research"
    window_open=$((8*60));  window_close=$((9*60+15))
    days="1 2 3 4 5"
    allowed="$common_tools $schwab_read $write_scripts $compute_scripts"
    prompt="Run /deep-research preopen for ${today}.

You are the scheduled 08:15 ET pre-open run, dispatched by the server
scheduler. Execute .claude/commands/deep-research.md §A then §P exactly as
written.

Resolve your own context from the canonical sources — do NOT assume any value
was passed to you:
- Date and ET time: mcp__schwab__get_datetime, never the machine clock.
- Held symbols and sectors: the latest status/*.md file. Do NOT Glob for it —
  status/ is gitignored and Glob returns nothing under an ignored path (11
  files present, Glob reported 0, measured 2026-08-26). Resolve the path with
  scripts/latest-status.sh and Read that path.
- Competition capital: the 'State recorded — current' block of that same file
  (echo the figure and its date).
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
    allowed="$common_tools $schwab_read $write_scripts $compute_scripts"
    prompt="Run /deep-research postclose for ${today}.

You are the scheduled 16:20 ET post-close run, dispatched by the server
scheduler. Execute .claude/commands/deep-research.md §A then §D exactly as
written, and you own the POST window.

Resolve your own context from the canonical sources — do NOT assume any value
was passed to you:
- Date and ET time: mcp__schwab__get_datetime, never the machine clock.
- Held symbols and sectors: the latest status/*.md file. Do NOT Glob for it —
  status/ is gitignored and Glob returns nothing under an ignored path (11
  files present, Glob reported 0, measured 2026-08-26). Resolve the path with
  scripts/latest-status.sh and Read that path.
- Competition capital: the 'State recorded — current' block of that same file
  (echo the figure and its date).
- Active calendar guards: read them from strategy.md §4 and §10 at run time.

You have no session to ping into and no §E authority — §E belongs to the
parent, and here there is none. Emit your HOT-FRESH: lines in your final
output as usual; the wrapper relays them to Discord for Chris to act on at the
next session open. Do not attempt to notify anyone yourself."
    ;;

  execute)
    agent="trader"
    window_open=$((9*60+30)); window_close=$((16*60))
    days="1 2 3 4 5"
    # 30 minutes, deliberately LONGER than the 15-minute cadence. Every write
    # blocks on Chris's ✅/❌ with a 600s timeout, and an entry needs two of
    # them — the entry, then its mandatory §3.4 stop — plus the fill poll in
    # between. A budget under that would abandon a filled position between its
    # entry and its stop, which is the precise state §4.3 forbids.
    #
    # Overlapping runs are therefore expected and correct: the next firing
    # finds the lock held and exits as a no-op. While an order is in flight,
    # a second executor is the last thing anyone wants.
    job_timeout=1800
    # The ONLY allowlist in this system containing order tools. Everything else
    # — research, ticks, the universe sweep — is read-only by construction, and
    # test-scheduled-run.sh enforces that for every job but this one.
    allowed="Read Glob Grep ToolSearch mcp__schwab__get_datetime mcp__schwab__get_market_hours mcp__schwab__get_accounts mcp__schwab__get_account mcp__schwab__get_orders mcp__schwab__get_order mcp__schwab__get_quotes mcp__schwab__get_instruments mcp__schwab__get_option_chain mcp__schwab__create_option_symbol mcp__schwab__preview_equity_order mcp__schwab__preview_option_order mcp__schwab__place_previewed_order mcp__schwab__cancel_order Bash(scripts/pre-order-check.sh:*) Bash(scripts/trade-log-append.sh:*) Bash(scripts/check-consistency.sh:*) Bash(scripts/data-append.sh:*)"
    prompt="Run one execution pass for ${today}.

You are the \`trader\` agent, dispatched unattended by the server scheduler.
Follow .claude/agents/trader.md exactly — it is your operating document and it
outranks anything you infer from this prompt.

Order of work, no improvisation:
1. §1 refusals FIRST. ALERT.md, cashCall, isClosingOnlyRestricted, §3.6 halt,
   §8 lockout, market not open, check-consistency FAIL, broker unreadable.
   Any one of them ends the run.
2. Reconcile from the broker. Read strategy.md — the manual's header requires
   the playbook before any order, however fresh this process is.
3. §2 decision. Protective actions outrank entries, always. AT MOST ONE
   order-bearing action this invocation, then stop.
4. §3 workflow for that action: §4.9 log row BEFORE the order via
   scripts/trade-log-append.sh, preview, scripts/pre-order-check.sh, all four
   §4.10 gates, place, verify.

**No entry after 15:30 ET.** You must be able to supervise an entry to a
resting, verified stop inside this same invocation (§4), and after 15:30 you
cannot. Protective actions continue to the close.

You have roughly 25 minutes of wall clock. Each write blocks up to 600s on
Chris's Discord reaction. Budget for entry + stop being two of those. If you
cannot complete an entry AND its stop, do not start the entry.

Invoke every repo script as a BARE RELATIVE PATH — 'scripts/name.sh ...'.
'./scripts/', 'bash scripts/' and '/app/scripts/' are all refused by the
permission gate, with no approver behind it. Measured in this container on
2026-08-26, after the first live run refused its entire pass because every
script call was blocked.

Resolve date and time from get_datetime, never the machine clock. Doing
nothing is a legitimate and common outcome — report 'EXEC none' and stop."
    ;;

  tick)
    agent="tick-watch"
    window_open=$((9*60+30)); window_close=$((16*60))
    days="1 2 3 4 5"
    job_timeout=600
    # tick.md §F's baseline is 15 min with every stop confirmed. The crontab
    # fires that; this job is one sweep, not a loop.
    #
    # NO ORDER TOOLS, and that is the whole design. §E escalation — the part
    # that would actually place or amend an order — cannot run unattended, so
    # this job DETECTS and REPORTS and nothing else. That is not the full loop
    # a live session runs, and it must never be described as one. It is the
    # difference between "nobody is watching the book between 09:30 and 16:00"
    # and "something is watching and will ping Chris". Before 2026-08-26 the
    # answer was the former, on every unattended day.
    allowed="Read Glob Grep ToolSearch mcp__schwab__get_datetime mcp__schwab__get_market_hours mcp__schwab__get_accounts mcp__schwab__get_account mcp__schwab__get_orders mcp__schwab__get_quotes Bash(scripts/tick-append.sh:*) Bash(scripts/latest-status.sh:*)"
    prompt="Run /tick for ${today}.

You are the SCHEDULED, UNATTENDED tick, dispatched by the server scheduler on
the §F cadence. Execute .claude/commands/tick.md §B-§D exactly as written and
append the ledger row via scripts/tick-append.sh.

Resolve the date and time from get_datetime, never the machine clock. Fetch
get_market_hours yourself — there is no cached session state here, because each
scheduled tick is a fresh process rather than an iteration of a live loop.

**Resolve the account hash yourself with get_accounts.** This prompt does not
carry one and no file in the repo does either — CLAUDE.md redacts it under §7.4
because the repo is public. get_accounts takes no hash and returns the one
account in the token's scope with its accountHash; that is the value B2/B3 need.
Not finding a hash lying around is never a reason to return FAIL — that is the
2026-08-27 defect, where two sweeps gave up here and left the book unwatched.

**No session is watching and you have no order tools.** §E escalation is
therefore NOT yours to run: you cannot place, amend or cancel anything, and you
must not try. On a trip, output the canonical one-line summary followed by a
line beginning 'TRIP:' naming the tripped watch numbers and what they mean; the
wrapper relays that to Chris in Discord. Same for 'FAIL:' if the sweep could not
complete. Output the canonical line and nothing else when the tick is clean."
    ;;

  sessionclose)
    agent="session-close"
    # 16:00-16:15 ET. Opens AT the bell so the marks are closing marks, and
    # closes before the 16:22 postclose deep run, which reads "the latest
    # status/*.md" for held symbols and competition capital — it must find
    # today's file, not yesterday's.
    window_open=$((16*60)); window_close=$((16*60+15))
    days="1 2 3 4 5"
    # 720s, deliberately shorter than the tick's budget is generous. Fired at
    # 16:04, the worst case ends 16:16 — six minutes clear of the 16:22
    # postclose. The window guard only tests the START of a run, so the timeout
    # is the only thing bounding the END, and an overrun here means postclose
    # reads this file while it is being written.
    job_timeout=720
    # NO ORDER TOOLS. This job records state; it never changes it. Its only
    # write path is status-write.sh, which validates the shape the rest of the
    # system greps for.
    #
    # This is the job whose ABSENCE was the bug. tick.md §B5 resolves the HWM
    # from the latest status file, and its orphan-ledger rule writes ALERT.md —
    # closing-only, no buys — when a prior day's ledger shows a comp_capital
    # above that mark. With nothing writing the file, every profitable day left
    # that evidence behind: the system self-halted on a win, and only on a win.
    allowed="Read Glob Grep ToolSearch mcp__schwab__get_datetime mcp__schwab__get_market_hours mcp__schwab__get_accounts mcp__schwab__get_account mcp__schwab__get_orders mcp__schwab__get_quotes Bash(scripts/status-write.sh:*) Bash(scripts/latest-status.sh:*)"
    prompt="Write the §7.2 session-close status file for ${today}.

You are the SCHEDULED, UNATTENDED session close, dispatched by the server
scheduler just after the bell. Follow .claude/agents/session-close.md exactly.

Resolve the date and ET time from get_datetime, never the machine clock, and
read the account live per §4.5 — never from a cached figure and never from a
number written into this prompt.

Perform the §3 high-water-mark ratchet against the prior mark you resolve
yourself from the most recent status/*.md 'State recorded — current' block.
Ratchet on the CLOSING competition capital, never on an intraday print, and
never lower the mark.

Write exactly one file, via scripts/status-write.sh ${today}. If it already
exists, do NOT pass --replace unless you are deliberately correcting it, and
say so if you do.

Output the §5 contract line. If the file could not be written, output
CLOSE-FAIL with the reason — an unwritten close file is what halts trading
tomorrow, so it must not fail silently."
    ;;

  weekly-universe)
    agent="weekly-universe"
    window_open=$((6*60));  window_close=$((12*60))
    days="6 7"
    # check-consistency.sh is §A.1 of weekly-universe.md — a PRECONDITION the
    # agent must run, not an optional extra. It was missing here, so the agent
    # correctly refused to sweep ("precondition unverified ≠ passed") and the
    # job could never have completed. Found 2026-08-25 by the first forced run;
    # the Saturday cron would have hit it on 8/29. It is read-only: it verifies
    # that rules.yml, the docs and the scripts still agree, and writes nothing.
    allowed="Read Glob Grep ToolSearch mcp__schwab__get_datetime mcp__schwab__get_market_hours mcp__schwab__get_quotes Bash(scripts/check-consistency.sh:*) Bash(scripts/universe-fetch.sh:*) Bash(scripts/universe-filter.sh:*) Bash(scripts/research-replace.sh:*) Bash(scripts/data-append.sh:*)"
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

# --- shared: how to invoke repo scripts -----------------------------------
# Appended to EVERY dispatch whose allowlist grants Bash(scripts/...), instead of
# being written into each prompt by hand.
#
# `Bash(scripts/x.sh:*)` matches ONLY the bare relative form. `./scripts/x.sh`,
# `bash scripts/x.sh` and `/app/scripts/x.sh` are all refused, and in an
# unattended run there is no approver behind that prompt — the refusal is silent
# and total. Measured in this container 2026-08-26, not assumed.
#
# This is the SECOND time the same wall was hit. On 2026-08-26 the executor
# refused a whole pass because every script call was blocked; the rule was added
# to that one prompt and to trader.md, and nowhere else. On 2026-08-27 the 12:02
# tick hit it again — it had read the broker cleanly, evaluated all eight watches
# and then could not write the ledger row, so the sweep is missing from the
# ledger despite having actually run. Five of the six jobs were exposed.
#
# Hence: central, conditioned on the allowlist, and covered by a test that walks
# every job. A rule that has to be remembered per job is a rule that will be
# missing from the next job someone adds.
if [[ "$allowed" == *"Bash(scripts/"* ]]; then
  prompt="$prompt

**Invoke every repo script as a BARE RELATIVE PATH from the repo root —
\`scripts/name.sh ...\`.** \`./scripts/x.sh\`, \`bash scripts/x.sh\` and
\`/app/scripts/x.sh\` are all refused by the permission gate, and NOTHING is
watching to approve them: a refusal here is silent, total and permanent.

If a script call comes back 'requires approval', that is the invocation FORM,
not a missing permission. Retry it once as a bare relative path before you
report FAIL — a sweep that read the broker cleanly and then could not write its
row is a sweep that has to be re-run for no reason."
fi

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
# job_timeout is set per-job above where a job needs a tighter bound than the
# hour a research run may legitimately take. A tick on a 15-minute cadence must
# not still be running when the next one fires: the lock would make that next
# tick a no-op, so one hung sweep would silently cost an hour of monitoring.
job_timeout="${job_timeout:-3600}"
runner=()
if command -v timeout >/dev/null 2>&1; then runner=(timeout -k 60 "$job_timeout")
elif command -v gtimeout >/dev/null 2>&1; then runner=(gtimeout -k 60 "$job_timeout")
else say "WARN no timeout(1) available; running unbounded"; fi

say "RUN agent=$agent"
out_file="$log_dir/.${job}.out.$$"
if [ "${TC_DRY_RUN:-0}" = "1" ]; then
  # TC_FAKE_OUTPUT stands in for the agent's stdout so the regression suite can
  # exercise the verdict classification below without a claude or a broker.
  # Honoured ONLY under dry run, exactly like TC_STATUS_DIR: a seam that works
  # in production is a way to forge a heartbeat.
  # The composed prompt, for the regression suite to inspect. Dry run only.
  printf '%s' "$prompt" > "$log_dir/.${job}.prompt"
  if [ -n "${TC_FAKE_OUTPUT:-}" ]; then
    printf '%s\n' "$TC_FAKE_OUTPUT" >"$out_file"
  else
    printf 'DRY-RUN would exec: claude %s <stdin-prompt:%d bytes>\n' "${claude_args[*]}" "${#prompt}" >"$out_file"
  fi
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
  say "FAIL timed out after ${job_timeout}s"
  beat failed "timeout after ${job_timeout}s"
  notify "⚠️ scheduled job \`$job\` timed out after ${job_timeout}s on $today. Log: status/cron/${today}-${job}.log"
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

# rc=0 means the agent PROCESS exited cleanly. It does not mean the job did its
# work. Every dispatch prompt above defines a line beginning `FAIL:` as the
# agent's own "I could not complete" contract, and the heartbeat is the only
# record job-deadman.sh reads. Recording a FAIL as `ok` is how the watchdog
# reports "all expected jobs accounted for" over a book nobody watched: on
# 2026-08-27 two ticks returned FAIL (no account hash, no broker read, no ledger
# row) and both landed as `ok`, so the 09:35 deadman passed them clean.
#
# Classification only — the run continues through the relays below, because a
# FAIL is precisely the thing that must still reach Chris in Discord. The
# non-zero exit is deferred to the end of the script for the same reason.
detail="$(head -c 200 <<<"$output" | tr '\n' ' ')"
job_failed=0
if grep -qE '^FAIL\b' <<<"$output"; then
  job_failed=1
  say "FAIL agent reported it could not complete"
  beat failed "$detail"
else
  say "OK"
  beat ok "$detail"
fi

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

# --- relay a tripped watch ------------------------------------------------
# THIS IS THE WHOLE POINT OF THE SCHEDULED TICK. The sweep has no order tools,
# so it cannot run tick.md §E — it can only see that something tripped. If that
# observation stops at a log file on a Pi, the job has detected a problem and
# told nobody, which is worse than not running it: it manufactures the belief
# that the book is being watched.
#
# Deliberately unconditional and loud. A clean tick relays nothing at all —
# 26 "all clear" messages a day would train Chris to ignore the channel, and
# the one that mattered would scroll past with the rest.
# --- relay every execution outcome that is not "nothing happened" ---------
# An order-bearing run is the one thing in this system Chris must always be able
# to reconstruct after the fact. The Discord ✅/❌ already showed him the order;
# this says what came of it — filled, aborted at a gate, refused outright.
# 'EXEC none' is the common case and stays silent, for the same reason a clean
# tick does: routine noise is how the message that matters gets missed.
if [ "$job" = "execute" ]; then
  ex="$(grep -E '^(EXEC|REFUSE|ABORT|ALERT)' <<<"$output" | grep -v '^EXEC none' || true)"
  if [ -n "$ex" ]; then
    notify "⚙️ **EXECUTE — $today $(et '+%H:%M') ET**
\`\`\`
$ex
\`\`\`"
  fi
fi

trip="$(grep -E '^(TRIP|FAIL):' <<<"$output" || true)"
if [ -n "$trip" ]; then
  notify "🚨 **TICK TRIP — $today $(et '+%H:%M') ET** — a watch tripped on the unattended sweep. **Nothing has acted on this**: the scheduled tick has no order tools and §E belongs to a live session. Open one.
\`\`\`
$(head -1 <<<"$output")
$trip
\`\`\`"
fi

# Line 1 is the command file's return-line contract; it is the one line worth
# reading without opening the log. Ticks are excluded on purpose: at 26 a day a
# routine relay is noise, and a tick that matters goes out as a TRIP above.
if [ "$job" != "tick" ]; then
  first="$(head -1 <<<"$output")"
  if grep -qE '^FAIL\b' <<<"$first"; then
    notify "🚨 $first"
  elif grep -qE '^(DEEP|UNIVERSE)' <<<"$first"; then
    notify "✅ $first"
  fi
fi

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

# Non-zero when the agent said it could not complete. Deferred to here so the
# Discord relay and the sidecar push above still run on a failed job — the exit
# code is for supercronic's log, the relay is for the human.
exit "$job_failed"
