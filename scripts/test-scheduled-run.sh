#!/bin/bash
# Regression suite for scheduled-run.sh's guards.
#
# Companion to test-pre-order-check.sh, and for the same reason the manual gives
# there: check-consistency verifies what the rules SAY, only tests verify that
# the code enforcing them is right. The window guard is the whole reason a late
# container start cannot backdate a pre-open brief, and an unexercised guard is
# an assumption.
#
# Runs entirely under TC_DRY_RUN=1 — no claude, no broker, no network.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2

# Every dispatch in this suite writes its logs into a scratch tree, never the
# repo's own status/. On the laptop status/ is a SYMLINK into the private store
# repo, and the `rm -rf "$SCRATCH_CRON"` these tests do between dispatches was
# deleting server-written cron logs out of a live working tree — the "files keep
# vanishing from the store" behaviour that got blamed on iCloud for two days.
# scheduled-run.sh honours TC_STATUS_DIR only under TC_DRY_RUN=1.
export TC_STATUS_DIR="$(mktemp -d)"
trap 'rm -rf "$TC_STATUS_DIR"' EXIT
SCRATCH_CRON="$TC_STATUS_DIR/cron"

pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

run() { # job min dow -> prints RUN or SKIP
  TC_DRY_RUN=1 TC_NOW_ET_MIN="$2" TC_DOW="$3" \
    ./scripts/scheduled-run.sh "$1" 2>&1 | grep -oE 'RUN|SKIP' | head -1
}

expect() { # job min dow want label
  local got; got="$(run "$1" "$2" "$3")"
  [ "$got" = "$4" ] && ok "$5" || bad "$5 — got '${got:-<none>}', want '$4'"
}

hhmm() { printf '%02d:%02d' $(($1/60)) $(($1%60)); }

echo "== preopen window (08:00-09:15 ET, Mon-Fri) =="
expect preopen $((7*60+59)) 2 SKIP "one minute before the window opens"
expect preopen $((8*60))    2 RUN  "exactly at the window open"
expect preopen $((8*60+17)) 2 RUN  "at the scheduled fire time"
expect preopen $((9*60+15)) 2 RUN  "exactly at the window close"
expect preopen $((9*60+16)) 2 SKIP "one minute after the window closes"
expect preopen $((11*60))   2 SKIP "late container start must NOT backdate a brief"
expect preopen $((8*60+17)) 6 SKIP "Saturday"
expect preopen $((8*60+17)) 7 SKIP "Sunday"

echo "== postclose window (16:15-18:00 ET, Mon-Fri) =="
expect postclose $((15*60))    3 SKIP "market still open"
expect postclose $((16*60+14)) 3 SKIP "one minute before the close window"
expect postclose $((16*60+22)) 3 RUN  "at the scheduled fire time"
expect postclose $((18*60))    3 RUN  "exactly at the window close"
expect postclose $((18*60+1))  3 SKIP "one minute after"
expect postclose $((16*60+22)) 7 SKIP "Sunday"

echo "== sessionclose window (16:00-16:15 ET, Mon-Fri) =="
# The upper bound is load-bearing in a way the other windows' are not: the
# 16:22 postclose deep run reads "the latest status/*.md" for held symbols and
# competition capital. A close write that lands after it has already run leaves
# that run reading YESTERDAY, which is the staleness this job exists to end.
expect sessionclose $((15*60+59)) 2 SKIP "one minute before the bell — marks are not closing marks yet"
expect sessionclose $((16*60))    2 RUN  "exactly at the bell"
expect sessionclose $((16*60+5))  2 RUN  "at the scheduled fire time"
expect sessionclose $((16*60+15)) 2 RUN  "exactly at the window close"
expect sessionclose $((16*60+16)) 2 SKIP "one minute after — postclose would read a stale status file"
expect sessionclose $((16*60+22)) 2 SKIP "cannot still be running when postclose fires"
expect sessionclose $((16*60+5))  6 SKIP "Saturday"
expect sessionclose $((16*60+5))  7 SKIP "Sunday"

echo "== weekly-universe (06:00-12:00 ET, weekend only) =="
expect weekly-universe $((7*60+40)) 6 RUN  "Saturday at the fire time"
expect weekly-universe $((7*60+40)) 7 RUN  "Sunday still allowed"
expect weekly-universe $((7*60+40)) 2 SKIP "must never run on a session day"
expect weekly-universe $((5*60+59)) 6 SKIP "before the window"
expect weekly-universe $((12*60+1)) 6 SKIP "after the window"

echo
echo "== tick (09:30-16:00 ET, weekdays) =="
# The monitoring loop became a scheduled service on 2026-08-26. Before that it
# existed only inside a live session, so on every unattended day the resting
# stops were the ONLY thing watching the book — and nothing said so out loud.
expect tick $((9*60+2))  3 SKIP "09:02 cron firing, before the open"
expect tick $((9*60+29)) 3 SKIP "one minute before the bell"
expect tick $((9*60+30)) 3 RUN  "at the opening bell"
expect tick $((12*60+2)) 3 RUN  "mid-session"
expect tick $((15*60+47)) 3 RUN "last sweep of the day"
expect tick $((16*60))   3 RUN  "exactly at the close"
expect tick $((16*60+2)) 3 SKIP "after the close — postclose owns the rest"
expect tick $((12*60))   6 SKIP "Saturday"
expect tick $((12*60))   7 SKIP "Sunday"

echo
echo "== a tick must be bounded well inside its own cadence =="
# The lock makes an overlapping tick a no-op, so ONE hung sweep silently costs
# a full cadence of monitoring rather than piling up. At the hour every research
# job may legitimately take, that is four missed sweeps.
rm -rf "$SCRATCH_CRON"
TC_DRY_RUN=1 TC_NOW_ET_MIN=$((12*60)) TC_DOW=3 ./scripts/scheduled-run.sh tick >/dev/null 2>&1
tick_to="$(grep -oE 'timeout -k 60 [0-9]+' "$SCRATCH_CRON"/*tick.log 2>/dev/null | grep -oE '[0-9]+$' | tail -1)"
if [ -z "$tick_to" ]; then
  # Dry run does not print the runner; fall back to the source declaration.
  tick_to="$(sed -n '/^  tick)/,/;;/p' scripts/scheduled-run.sh | sed -n 's/.*job_timeout=\([0-9]*\).*/\1/p')"
fi
[ -n "$tick_to" ] && [ "$tick_to" -lt 900 ] \
  && ok "tick timeout ${tick_to}s is inside the 15-minute cadence" \
  || bad "tick timeout is '${tick_to:-unset}' — a hung sweep can outlive its own cadence"
rm -rf "$SCRATCH_CRON"

echo
echo "== a tripped watch must reach a human =="
# The sweep has no order tools, so it can only observe. If that observation
# stops at a log file on a Pi, the job manufactures the belief that the book is
# being watched while telling nobody what it saw.
grep -q "grep -E '\^(TRIP|FAIL):'" scripts/scheduled-run.sh \
  && ok "TRIP:/FAIL: output is relayed" \
  || bad "nothing relays a tripped watch — detection that reaches no one"
grep -A6 "trip=\"\$(grep -E" scripts/scheduled-run.sh | grep -q 'notify' \
  && ok "the relay goes out as a notification" \
  || bad "the trip is captured but never notified"

echo
echo "== --force: a human-ordered catch-up, and nothing wider =="
# Added 2026-08-25, when the weekend sweep had to be re-run on a Tuesday night
# because research/universe.md was a week stale and the hand-rolled invocation
# that tried it died on shell quoting. --force must bypass the calendar and
# NOTHING else, and must be impossible to confuse with a normal fire.
forced_run() { # job min dow -> RUN or SKIP, with --force
  TC_DRY_RUN=1 TC_NOW_ET_MIN="$2" TC_DOW="$3" \
    ./scripts/scheduled-run.sh "$1" --force 2>&1 | grep -oE 'RUN|SKIP' | head -1
}
[ "$(forced_run weekly-universe $((23*60+20)) 2)" = "RUN" ] \
  && ok "--force runs outside both the day and the window" \
  || bad "--force did not run on a Tuesday night"
[ "$(run weekly-universe $((23*60+20)) 2)" = "SKIP" ] \
  && ok "the same call without --force still skips" \
  || bad "the guard is gone even unforced — --force leaked into the default path"

# The audit trail must distinguish the two. A forced run that looks normal in
# the heartbeat is how a bypass becomes invisible.
rm -rf "$SCRATCH_CRON"
TC_DRY_RUN=1 TC_NOW_ET_MIN=$((23*60+20)) TC_DOW=2 ./scripts/scheduled-run.sh weekly-universe --force >/dev/null 2>&1
grep -q '"forced":true' "$SCRATCH_CRON"/heartbeat.jsonl 2>/dev/null \
  && ok "the heartbeat records the run as forced" \
  || bad "a forced run is indistinguishable from a scheduled one in the heartbeat"
rm -rf "$SCRATCH_CRON"
TC_DRY_RUN=1 TC_NOW_ET_MIN=$((7*60+40)) TC_DOW=6 ./scripts/scheduled-run.sh weekly-universe >/dev/null 2>&1
grep -q '"forced"' "$SCRATCH_CRON"/heartbeat.jsonl 2>/dev/null \
  && bad "a NORMAL run is marked forced" \
  || ok "a normal run carries no forced marker"

./scripts/scheduled-run.sh weekly-universe --bogus >/dev/null 2>&1
[ "$?" -eq 2 ] && ok "an unknown flag exits 2 rather than being ignored" \
              || bad "an unknown flag was accepted"

echo
echo "== a dry run must never look like a real fire =="
# Dry runs write to the SAME heartbeat job-deadman.sh reads. On 2026-08-26 two
# rehearsals run on the server at 01:10 ET landed as that day's preopen and
# postclose with verdict "ok" — which would have told the 09:35 deadman the
# morning was fine while nothing had actually run. Tagged here, filtered there.
rm -rf "$SCRATCH_CRON"
TC_DRY_RUN=1 TC_NOW_ET_MIN=$((8*60+30)) TC_DOW=2 ./scripts/scheduled-run.sh preopen >/dev/null 2>&1
grep -q '"dry_run":true' "$SCRATCH_CRON"/heartbeat.jsonl 2>/dev/null \
  && ok "a dry run is tagged dry_run in the heartbeat" \
  || bad "a dry run is indistinguishable from a real fire in the heartbeat"
# And the deadman must actually act on the tag.
if grep -q 'dry_run' scripts/job-deadman.sh; then
  hb="$SCRATCH_CRON/heartbeat.jsonl"
  if [ "$(grep "\"job\":\"preopen\"" "$hb" | grep -v '"dry_run":true' | wc -l | tr -d ' ')" = "0" ]; then
    ok "job-deadman's filter leaves no verdict behind for a dry-run-only day"
  else
    bad "a dry-run entry survives the deadman's filter"
  fi
else
  bad "job-deadman.sh does not filter dry_run — a rehearsal can blind it"
fi
rm -rf "$SCRATCH_CRON"

echo "== the prompt survives argument parsing =="
# --allowedTools is variadic ("comma or space-separated"). Passed as separate
# words, it swallows the prompt that follows as one more tool name and claude
# exits with "Input must be provided either through stdin or as a prompt
# argument" — which would have failed EVERY scheduled job on its first real run,
# and which no amount of guard-testing would have caught.
rm -rf "$SCRATCH_CRON"
TC_DRY_RUN=1 TC_NOW_ET_MIN=$((8*60+30)) TC_DOW=2 ./scripts/scheduled-run.sh preopen >/dev/null 2>&1
line="$(grep -o 'DRY-RUN would exec: .*' "$SCRATCH_CRON"/*preopen.log 2>/dev/null | head -1)"
if grep -qE 'allowedTools [^ ]+,[^ ]+' <<<"$line"; then
  ok "--allowedTools is a single comma-separated argument"
else
  bad "--allowedTools is space-separated — it will eat the prompt"
fi
if grep -qE '<stdin-prompt:[1-9][0-9]* bytes>' <<<"$line"; then
  ok "a non-empty prompt is passed on stdin, not as a trailing arg"
else
  bad "no prompt reached the invocation"
fi
# No bare tool name may sit between the flag and the prompt.
if grep -qE 'allowedTools [^ ]+ (Read|Glob|Grep|mcp__)' <<<"$line"; then
  bad "a tool name appears as a separate argv word after --allowedTools"
else
  ok "no stray tool words after the flag"
fi

echo
echo "== no scheduled job may be handed an order tool =="
# LOAD-BEARING as of 2026-08-25. Until then the broker itself was the backstop:
# it ran with allow_write=False, so place_previewed_order did not exist for any
# scheduled run to call even if the allowlist had named it. The broker now runs
# Discord-gated (state 2) so that positions can be CLOSED, which means this
# allowlist is the only thing standing between an unattended 08:17 research run
# and an order request landing in #llm-yolo at breakfast. It is one script edit
# away from being wrong, so it gets a test.
#
# Checked for every job, not just preopen: they build their allowlists
# separately and a new one could quietly differ.
#
# Each job needs a clock and a day INSIDE its own window, or it SKIPs, writes no
# dispatch line, and the check passes on an empty string. The first draft of
# this loop used one clock for all three, skipped two of them, and survived a
# mutation that added place_previewed_order to the allowlist. A guard that
# cannot fail is not a guard — these three pairs are load-bearing.
order_leak=0
checked=0
# tick matters most here: it is the only job that runs while the market is open,
# and the one whose §E escalation would place orders if a live session ran it.
for spec in "preopen $((8*60+30)) 2" "postclose $((16*60+30)) 2" "weekly-universe $((7*60+40)) 6" \
            "tick $((12*60)) 3" "sessionclose $((16*60+5)) 2"; do
  set -- $spec; j="$1"; m="$2"; d="$3"
  rm -rf "$SCRATCH_CRON"
  TC_DRY_RUN=1 TC_NOW_ET_MIN="$m" TC_DOW="$d" ./scripts/scheduled-run.sh "$j" >/dev/null 2>&1
  l="$(grep -o 'DRY-RUN would exec: .*' "$SCRATCH_CRON"/*"$j".log 2>/dev/null | head -1)"
  if [ -z "$l" ]; then
    bad "$j did not dispatch at $m ET dow $d — the allowlist was never checked"
    order_leak=1; continue
  fi
  checked=$((checked+1))
  if grep -qE 'place_previewed_order|cancel_order' <<<"$l"; then
    bad "$j is allowed an order tool — an unattended run could request a live order"
    order_leak=1
  fi
done
[ "$order_leak" -eq 0 ] && [ "$checked" -eq 5 ] \
  && ok "no job allowlists place_previewed_order or cancel_order (5 of 5 dispatched and inspected)"

echo
echo "== execute is the ONE job that carries order tools =="
# Inverted on 2026-08-26, when Chris ruled that placing orders is the server's
# job and not an interactive session's. The guard above is what keeps that
# capability in exactly one place; this is what keeps it from being quietly
# removed from the one job that needs it, which would leave a system that
# looks like it trades and cannot.
rm -rf "$SCRATCH_CRON"
TC_DRY_RUN=1 TC_NOW_ET_MIN=$((12*60)) TC_DOW=3 ./scripts/scheduled-run.sh execute >/dev/null 2>&1
exec_line="$(grep -o 'DRY-RUN would exec: .*' "$SCRATCH_CRON"/*execute.log 2>/dev/null | head -1)"
if [ -z "$exec_line" ]; then
  bad "execute did not dispatch at 12:00 ET on a weekday — the executor is unreachable"
else
  grep -q 'place_previewed_order' <<<"$exec_line" \
    && ok "execute can place orders" \
    || bad "execute has NO place_previewed_order — nothing in the system can trade"
  grep -q 'cancel_order' <<<"$exec_line" \
    && ok "execute can cancel orders (§4.7 orphan cleanup, §7.6 exit ordering)" \
    || bad "execute cannot cancel — an orphaned stop could not be cleaned up"
fi
# The executor must still be bounded so a hung approval cannot hold the lock
# for the rest of the session.
exec_to="$(sed -n '/^  execute)/,/;;/p' scripts/scheduled-run.sh | sed -n 's/.*job_timeout=\([0-9]*\).*/\1/p')"
[ -n "$exec_to" ] && [ "$exec_to" -le 1800 ] && [ "$exec_to" -ge 1200 ] \
  && ok "execute timeout ${exec_to}s covers two 600s Discord approvals and still ends" \
  || bad "execute timeout is '${exec_to:-unset}' — too short for entry+stop, or unbounded"
rm -rf "$SCRATCH_CRON"

echo "== unknown job =="
TC_DRY_RUN=1 ./scripts/scheduled-run.sh definitely-not-a-job >/dev/null 2>&1
[ "$?" -eq 2 ] && ok "unknown job exits 2" || bad "unknown job should exit 2"

echo "== the clock override is dry-run only =="
# The whole point of the guard is that production cannot bypass it. If an
# operator sets TC_NOW_ET_MIN without TC_DRY_RUN, it must be ignored.
# This is the ONE check that must run without TC_DRY_RUN — that is the whole
# claim. Which also means TC_STATUS_DIR is (correctly) ignored for it, so it
# would write into the real status/, i.e. into the store symlink. Run it from a
# throwaway copy of scripts/ instead: scheduled-run.sh resolves its repo root
# from its own location, so a copy writes its logs beside the copy.
sandbox="$(mktemp -d)"
mkdir -p "$sandbox/scripts"
cp scripts/*.sh "$sandbox/scripts/"
out="$(cd "$sandbox" && TC_NOW_ET_MIN=$((8*60+17)) TC_DOW=2 ./scripts/scheduled-run.sh preopen 2>&1)"
real_min=$(( 10#$(TZ=America/New_York date +%H) * 60 + 10#$(TZ=America/New_York date +%M) ))
real_dow=$(TZ=America/New_York date +%u)
if [ "$real_dow" -le 5 ] && [ "$real_min" -ge $((8*60)) ] && [ "$real_min" -le $((9*60+15)) ]; then
  ok "override ignored outside dry run (real clock is inside the window, cannot distinguish — skipped)"
else
  grep -q SKIP <<<"$out" && ok "override ignored outside dry run — real clock still governs" \
                          || bad "TC_NOW_ET_MIN took effect WITHOUT TC_DRY_RUN — the guard is bypassable"
fi

echo
echo "== every script a job's command file invokes is in that job's allowlist =="
# The defect this catches has now shipped three times, each time the same shape:
# the job runs, the agent tries a script, the permission gate refuses it
# SILENTLY (there is no approver on an unattended box), and the agent reports a
# skipped step in a log line nobody is required to read. The job's verdict stays
# "ok". Nothing is broken enough to page anyone, and the step simply stops
# happening.
#
#   2026-08-25  weekly-universe   check-consistency.sh   aborted at §A.1
#   2026-08-26  postclose         universe-filter.sh     skipped universe screen
#   2026-08-26  trader (live)     all of them            bare-path form, §4a
#
# So the allowlist is checked against the command file rather than against a
# hand-written list that drifts the same way the allowlist did.
#
# Three exclusions, each for a different reason:
#   - scheduled-run.sh / job-deadman.sh: an agent must never invoke the
#     scheduler that dispatched it nor the watchdog that audits it. They appear
#     in the contract files as prose, and allowlisting either would be the bug.
#   - lib-*.sh: sourced into another script's shell (`. scripts/lib-rules.sh`),
#     never run as a command, so a Bash() rule for one would grant nothing.
#   - any path with no file behind it: contract files quote non-existent paths
#     as examples — trader.md §4a names `scripts/x.sh` precisely to show which
#     invocation forms the gate refuses.
# No associative array: this must run under the bash 3.2 that ships on macOS,
# the same reason the rest of this suite avoids bash 4 constructs.
contract_for() {
  case "$1" in
    preopen|postclose)  echo .claude/commands/deep-research.md ;;
    weekly-universe)    echo .claude/commands/weekly-universe.md ;;
    tick)               echo .claude/commands/tick.md ;;
    sessionclose)       echo .claude/agents/session-close.md ;;
    execute)            echo .claude/agents/trader.md ;;
    *)                  echo "" ;;
  esac
}
never_allowlist='scheduled-run.sh|job-deadman.sh'
missing=0
inspected=0
for spec in "preopen $((8*60+30)) 2" "postclose $((16*60+30)) 2" \
            "weekly-universe $((7*60+40)) 6" "tick $((12*60)) 3" "execute $((12*60)) 3" \
            "sessionclose $((16*60+5)) 2"; do
  set -- $spec; j="$1"; m="$2"; d="$3"
  # execute dispatches an agent, not a command file; its contract is the
  # agent definition instead.
  cmd="$(contract_for "$j")"
  if [ -z "$cmd" ] || [ ! -f "$cmd" ]; then
    bad "$j: no contract file at '${cmd:-<unmapped>}'"; missing=1; continue
  fi

  rm -rf "$SCRATCH_CRON"
  TC_DRY_RUN=1 TC_NOW_ET_MIN="$m" TC_DOW="$d" ./scripts/scheduled-run.sh "$j" >/dev/null 2>&1
  line="$(grep -o 'DRY-RUN would exec: .*' "$SCRATCH_CRON"/*"$j".log 2>/dev/null | head -1)"
  if [ -z "$line" ]; then
    bad "$j did not dispatch at $m ET dow $d — its allowlist was never checked"
    missing=1; continue
  fi
  inspected=$((inspected+1))

  for want in $(grep -oE 'scripts/[a-z-]+\.sh' "$cmd" | sort -u); do
    grep -qE "$never_allowlist" <<<"$want" && continue
    case "$(basename "$want")" in lib-*.sh) continue ;; esac
    [ -f "$want" ] || continue
    if ! grep -qF "Bash($want:*)" <<<"$line"; then
      bad "$j: $(basename "$cmd") invokes $want but the allowlist omits it — the step will silently skip"
      missing=1
    fi
  done
done
[ "$missing" -eq 0 ] && [ "$inspected" -eq 6 ] \
  && ok "all 6 job allowlists cover every script their contract file invokes"

# == verdict classification (content-level failure) ========================
# rc=0 means "claude ran", NOT "the job did its work". Every dispatch prompt
# in this file defines a `FAIL:` first line as the contract for "I could not
# complete", and on 2026-08-27 two ticks reported exactly that — no account
# hash, no broker read, no ledger row — and were recorded as verdict "ok".
# job-deadman.sh then read those entries and reported "all expected jobs
# accounted for" while the book had gone unwatched for half an hour.
#
# The heartbeat is the ONLY record the deadman reads. If a content failure
# cannot be distinguished from a clean run there, the watchdog is decorative.
echo
echo "== verdict reflects the agent's own FAIL contract =="

verdict_of() { # fake-output -> prints the recorded verdict
  rm -rf "$SCRATCH_CRON"
  TC_DRY_RUN=1 TC_NOW_ET_MIN=600 TC_DOW=3 TC_FAKE_OUTPUT="$1" \
    ./scripts/scheduled-run.sh tick >/dev/null 2>&1
  sed -n 's/.*"verdict":"\([a-z]*\)".*/\1/p' "$SCRATCH_CRON/heartbeat.jsonl" 2>/dev/null | tail -1
}

got="$(verdict_of 'FAIL: dispatch prompt supplied no account hash')"
[ "$got" = "failed" ] \
  && ok "a FAIL: sweep is recorded as failed, so the deadman can see it" \
  || bad "a FAIL: sweep recorded as '${got:-<none>}' — job-deadman.sh will call it ok"

got="$(verdict_of '10:02 ET | RTH | comp $2868.52 (-1.09%) | HWM $2900.00 | OK')"
[ "$got" = "ok" ] \
  && ok "a clean tick is still recorded as ok" \
  || bad "a clean tick recorded as '${got:-<none>}' — want ok"

# A trip is a SUCCESSFUL sweep that found something. Marking it failed would
# make the deadman nag about the one job that did exactly its job.
got="$(verdict_of '10:02 ET | RTH | comp $2868.52 | TRIP:7
TRIP: watch 7 — stop missing on CSX')"
[ "$got" = "ok" ] \
  && ok "a tripped watch stays ok — the sweep completed, §E relays the trip" \
  || bad "a trip recorded as '${got:-<none>}' — a working sweep must not read as a failure"

rm -rf "$SCRATCH_CRON"

# == the unattended tick can resolve its own account hash ==================
# tick.md §B2 calls get_account(account_hash) and §Dispatch describes a parent
# handing that hash down. The scheduled tick HAS no parent. Until 2026-08-27
# the agent contract also said "Use ONLY get_datetime, get_market_hours,
# get_account, get_orders, get_quotes" — get_accounts was granted in the
# frontmatter and forbidden by the procedure, so the agent was obeying orders
# when it returned "FAIL: no account hash" and left the book unwatched.
#
# The hash cannot come from the repo: §7.4 redacts it because this repo is
# public. get_accounts is therefore the ONLY resolution path, and all three
# layers have to keep saying so.
echo
echo "== unattended tick resolves its own account hash =="

tick_block="$(sed -n '/^  tick)/,/;;/p' scripts/scheduled-run.sh)"

grep -q 'mcp__schwab__get_accounts' <<<"$tick_block" \
  && ok "the tick allowlist grants get_accounts" \
  || bad "the tick allowlist has no get_accounts — the hash is unresolvable"

grep -q 'get_accounts' <<<"$(sed -n '/prompt="Run \/tick/,/^    ;;/p' scripts/scheduled-run.sh)" \
  && ok "the tick dispatch prompt tells the agent to resolve the hash" \
  || bad "the tick prompt supplies no hash and never mentions get_accounts"

# The BODY, not the frontmatter. `tools:` has always granted get_accounts —
# grepping the whole file passes no matter what the procedure says, which is
# precisely the state that produced the blind ticks. Strip the frontmatter.
tw_body="$(awk '/^---$/{n++; next} n>=2' .claude/agents/tick-watch.md)"
grep -q 'get_accounts' <<<"$tw_body" \
  && ok "the tick-watch procedure permits get_accounts" \
  || bad "tick-watch.md grants get_accounts but its procedure forbids calling it"

# The B2 SECTION, not the whole file. §B's call-ceiling paragraph also names
# get_accounts, so a file-wide grep stays green even with B2's resolution rule
# deleted — and B2 is the section the agent actually executes.
b2="$(awk '/^### B2\./{f=1; next} /^### /{f=0} f' .claude/commands/tick.md)"
grep -q 'get_accounts' <<<"$b2" \
  && ok "tick.md B2 documents how account_hash is resolved" \
  || bad "tick.md B2 uses account_hash without saying where it comes from"

# == every script-granted job tells the agent the invocation form ==========
# Bash(scripts/x.sh:*) matches ONLY the bare relative form; ./scripts/x.sh,
# bash scripts/x.sh and /app/scripts/x.sh are refused, with no approver behind
# the prompt in an unattended run.
#
# This wall has now been hit twice. 2026-08-26: the executor refused a whole
# pass. The rule was then added to the execute prompt and trader.md — and
# nowhere else. 2026-08-27 12:02: the tick read the broker cleanly, evaluated
# all eight watches, and could not write its ledger row. Five of six jobs were
# exposed. This test is the reason a sixth cannot be added without the rule.
echo
echo "== every script-granted job states the bare-relative rule =="

for j in preopen postclose execute tick sessionclose weekly-universe; do
  case "$j" in
    preopen)         min=500; dow=3 ;;
    postclose)       min=982; dow=3 ;;
    execute)         min=600; dow=3 ;;
    tick)            min=600; dow=3 ;;
    sessionclose)    min=965; dow=3 ;;
    weekly-universe) min=460; dow=6 ;;
  esac
  rm -rf "$SCRATCH_CRON"
  TC_DRY_RUN=1 TC_NOW_ET_MIN="$min" TC_DOW="$dow" ./scripts/scheduled-run.sh "$j" >/dev/null 2>&1
  pf="$SCRATCH_CRON/.${j}.prompt"
  if [ ! -f "$pf" ]; then
    bad "$j — never dispatched, so its prompt could not be checked"
    continue
  fi
  # Only jobs that can actually call a script need the rule.
  if grep -q 'BARE RELATIVE PATH' "$pf"; then
    ok "$j states the bare-relative invocation rule"
  else
    bad "$j is granted Bash(scripts/...) but its prompt never states the invocation form"
  fi
done
rm -rf "$SCRATCH_CRON"

rm -rf "$SCRATCH_CRON" "$sandbox"

# The suite must leave the repo's own status/ alone. On the laptop that path is
# a symlink into the private store, and a test that quietly edits real data is
# a worse bug than the one it was checking for. This is the assertion that
# would have caught it on day one.
if [ -d status/cron ] && command -v git >/dev/null 2>&1; then
  dirty="$(git -C status status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  if [ "${dirty:-0}" -eq 0 ]; then
    ok "the suite left the real status/ tree untouched"
  else
    bad "the suite modified $dirty file(s) under the real status/ — it is writing outside its scratch dir"
    git -C status status --porcelain 2>/dev/null | sed 's/^/       /'
  fi
fi

echo
echo "-------------------------------------------"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
