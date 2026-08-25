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
