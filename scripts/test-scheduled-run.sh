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

echo "== weekly-universe (06:00-12:00 ET, weekend only) =="
expect weekly-universe $((7*60+40)) 6 RUN  "Saturday at the fire time"
expect weekly-universe $((7*60+40)) 7 RUN  "Sunday still allowed"
expect weekly-universe $((7*60+40)) 2 SKIP "must never run on a session day"
expect weekly-universe $((5*60+59)) 6 SKIP "before the window"
expect weekly-universe $((12*60+1)) 6 SKIP "after the window"

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
rm -rf status/cron
TC_DRY_RUN=1 TC_NOW_ET_MIN=$((23*60+20)) TC_DOW=2 ./scripts/scheduled-run.sh weekly-universe --force >/dev/null 2>&1
grep -q '"forced":true' status/cron/heartbeat.jsonl 2>/dev/null \
  && ok "the heartbeat records the run as forced" \
  || bad "a forced run is indistinguishable from a scheduled one in the heartbeat"
rm -rf status/cron
TC_DRY_RUN=1 TC_NOW_ET_MIN=$((7*60+40)) TC_DOW=6 ./scripts/scheduled-run.sh weekly-universe >/dev/null 2>&1
grep -q '"forced"' status/cron/heartbeat.jsonl 2>/dev/null \
  && bad "a NORMAL run is marked forced" \
  || ok "a normal run carries no forced marker"

./scripts/scheduled-run.sh weekly-universe --bogus >/dev/null 2>&1
[ "$?" -eq 2 ] && ok "an unknown flag exits 2 rather than being ignored" \
              || bad "an unknown flag was accepted"

echo "== the prompt survives argument parsing =="
# --allowedTools is variadic ("comma or space-separated"). Passed as separate
# words, it swallows the prompt that follows as one more tool name and claude
# exits with "Input must be provided either through stdin or as a prompt
# argument" — which would have failed EVERY scheduled job on its first real run,
# and which no amount of guard-testing would have caught.
rm -rf status/cron
TC_DRY_RUN=1 TC_NOW_ET_MIN=$((8*60+30)) TC_DOW=2 ./scripts/scheduled-run.sh preopen >/dev/null 2>&1
line="$(grep -o 'DRY-RUN would exec: .*' status/cron/*preopen.log 2>/dev/null | head -1)"
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
for spec in "preopen $((8*60+30)) 2" "postclose $((16*60+30)) 2" "weekly-universe $((7*60+40)) 6"; do
  set -- $spec; j="$1"; m="$2"; d="$3"
  rm -rf status/cron
  TC_DRY_RUN=1 TC_NOW_ET_MIN="$m" TC_DOW="$d" ./scripts/scheduled-run.sh "$j" >/dev/null 2>&1
  l="$(grep -o 'DRY-RUN would exec: .*' status/cron/*"$j".log 2>/dev/null | head -1)"
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
[ "$order_leak" -eq 0 ] && [ "$checked" -eq 3 ] \
  && ok "no job allowlists place_previewed_order or cancel_order (3 of 3 dispatched and inspected)"

echo "== unknown job =="
TC_DRY_RUN=1 ./scripts/scheduled-run.sh definitely-not-a-job >/dev/null 2>&1
[ "$?" -eq 2 ] && ok "unknown job exits 2" || bad "unknown job should exit 2"

echo "== the clock override is dry-run only =="
# The whole point of the guard is that production cannot bypass it. If an
# operator sets TC_NOW_ET_MIN without TC_DRY_RUN, it must be ignored.
out="$(TC_NOW_ET_MIN=$((8*60+17)) TC_DOW=2 ./scripts/scheduled-run.sh preopen 2>&1)"
real_min=$(( 10#$(TZ=America/New_York date +%H) * 60 + 10#$(TZ=America/New_York date +%M) ))
real_dow=$(TZ=America/New_York date +%u)
if [ "$real_dow" -le 5 ] && [ "$real_min" -ge $((8*60)) ] && [ "$real_min" -le $((9*60+15)) ]; then
  ok "override ignored outside dry run (real clock is inside the window, cannot distinguish — skipped)"
else
  grep -q SKIP <<<"$out" && ok "override ignored outside dry run — real clock still governs" \
                          || bad "TC_NOW_ET_MIN took effect WITHOUT TC_DRY_RUN — the guard is bypassable"
fi

rm -rf status/cron
echo
echo "-------------------------------------------"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
