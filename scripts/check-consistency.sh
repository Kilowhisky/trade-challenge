#!/bin/bash
# check-consistency.sh — verify that no document or script contradicts
# rules.yml. Run at session open and before any commit touching a rule.
#
# The failure this exists to prevent, concretely: on 2026-08-17 a §9
# amendment raised the §3.2 option caps. The manual was updated. The playbook
# (twice), a design spec, and scripts/pre-order-check.sh were not. The
# mandatory pre-order gate spent four days enforcing superseded caps and
# nothing noticed.
#
# Three checks:
#   1. ANNOTATION — every `**N**<!--rule:key-->` in a doc matches rules.yml.
#      This is the authoritative check; add an annotation whenever a doc
#      states a rule number.
#   2. TIGHTNESS  — every strategy value that shadows a manual value is on
#      the stricter side of it. A strategy rule may only tighten (playbook
#      contract), never loosen.
#   3. HARD-CODE  — no script hard-codes a rule percentage that rules.yml
#      already owns.
#
# Exit: 0 all consistent · 1 a mismatch · 2 could not run.

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.." || exit 2
. scripts/lib-rules.sh
load_rules || exit 2

fails=0
note() { printf '  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n' "$1"; fails=$((fails+1)); }

# --- 1. annotations -------------------------------------------------------
echo "== annotations =="
checked=0
# Match each `<number>**<!--rule:key-->` pair individually. grep -o emits one
# line per match, so a doc line carrying two annotations is handled correctly
# — an earlier version keyed off the line and silently mispaired them.
while IFS= read -r hit; do
  file="${hit%%:*}"; rest="${hit#*:}"; line="${rest%%:*}"; match="${rest#*:}"
  key="$(sed -n 's/.*<!--rule:\([a-zA-Z0-9_]*\)-->.*/\1/p' <<<"$match")"
  stated="$(grep -oE '^[0-9][0-9.]*' <<<"$match")"
  [ -n "$key" ] && [ -n "$stated" ] || { bad "$file:$line unparseable annotation: $match"; continue; }
  varname="RULE_$key"; expected="${!varname-}"
  checked=$((checked+1))
  if [ -z "$expected" ]; then
    bad "$file:$line references unknown rule '$key'"
  elif ! awk -v a="$stated" -v b="$expected" 'BEGIN{exit !(a+0==b+0)}'; then
    bad "$file:$line states '$stated' but rules.yml has $key = $expected"
  fi
done < <(grep -rnoE '[0-9][0-9.]*%?\*\*<!--rule:[a-zA-Z0-9_]*-->' --include='*.md' . 2>/dev/null | grep -v '/docs/archive/')
note "$checked annotation(s) checked"

# --- 2. strategy may only tighten ----------------------------------------
echo "== strategy tightness =="
# key_pair: strategy_key manual_key direction  (ge = strategy must be >= manual)
while read -r skey mkey dir label; do
  [ -n "${skey:-}" ] || continue
  sv="RULE_$skey"; mv="RULE_$mkey"; s="${!sv-}"; m="${!mv-}"
  if [ -z "$s" ] || [ -z "$m" ]; then bad "tightness: missing $skey or $mkey"; continue; fi
  ok=1
  case "$dir" in
    ge) awk -v a="$s" -v b="$m" 'BEGIN{exit !(a+0>=b+0)}' || ok=0 ;;
    le) awk -v a="$s" -v b="$m" 'BEGIN{exit !(a+0<=b+0)}' || ok=0 ;;
    *)  bad "tightness: unknown direction '$dir' for $label"; continue ;;
  esac
  if [ "$ok" -eq 1 ]; then note "$label: strategy $s vs manual $m OK"
  else bad "$label: strategy $s is LOOSER than manual $m"; fi
done <<'PAIRS'
strategy_option_min_delta manual_option_min_delta ge delta-floor
strategy_leveraged_exit_session manual_leveraged_max_hold_sessions le leveraged-hold
strategy_sleeve_options_open_pct manual_option_open_premium_pct le options-open
strategy_sleeve_leveraged_pct manual_leveraged_aggregate_pct le leveraged-aggregate
PAIRS

# --- 2b. derived rules must still equal their derivation ------------------
echo "== derived values =="
# §3.2's DTE floor is not a chosen number: it is the §3.3 exit plus the longest
# plausible blackout plus room to trade. If someone shortens the §3.3 exit or
# the token cycle lengthens, the floor must move with it -- this catches the
# case where one input changes and the floor is left stale.
dte_sum=$(( RULE_manual_option_close_at_dte + RULE_manual_option_max_blind_days + RULE_manual_option_dte_execution_margin_days ))
if [ "$RULE_manual_option_min_dte" -ne "$dte_sum" ]; then
  bad "option_min_dte is $RULE_manual_option_min_dte but its inputs sum to $dte_sum (close_at_dte + max_blind_days + execution_margin)"
else
  note "option_min_dte $RULE_manual_option_min_dte = $RULE_manual_option_close_at_dte + $RULE_manual_option_max_blind_days + $RULE_manual_option_dte_execution_margin_days OK"
fi

# --- 3. no hard-coded rule percentages in scripts -------------------------
echo "== scripts do not hard-code rule values =="
hard=0
for f in scripts/*.sh; do
  case "$f" in */lib-rules.sh|*/check-consistency.sh) continue ;; esac
  # a percentage arithmetic literal like "* 35 / 100"
  if grep -nE '\*[[:space:]]*(35|20|30|50|15)[[:space:]]*/[[:space:]]*100' "$f" >/dev/null 2>&1; then
    bad "$f hard-codes a rule percentage — read it from rules.yml instead"
    grep -nE '\*[[:space:]]*(35|20|30|50|15)[[:space:]]*/[[:space:]]*100' "$f" | sed 's/^/      /'
    hard=$((hard+1))
  fi
done
[ "$hard" -eq 0 ] && note "no hard-coded rule percentages found"

# --- 4. the broker may never be run ungated -------------------------------
# schwab-mcp has three states (cli.py:300-341). Handing it the Discord config
# registers the write tools behind the ✅/❌ gate; --jesus-take-the-wheel
# registers them behind NoOpApprovalManager, which approves everything. On an
# unattended server that flag is the difference between "Chris confirms every
# order" and "no human is involved in any order". It is one copy-pasted line
# away at all times, so it gets a permanent grep.
echo "== broker is never run ungated =="
if grep -rn -- '--jesus-take-the-wheel' docker/ scripts/ .claude/ 2>/dev/null \
     | grep -v 'check-consistency.sh' | grep -vE '^\S+:[0-9]+:\s*#' >/dev/null 2>&1; then
  bad "--jesus-take-the-wheel appears outside a comment — it bypasses the Discord approval gate entirely"
  grep -rn -- '--jesus-take-the-wheel' docker/ scripts/ .claude/ 2>/dev/null \
    | grep -v 'check-consistency.sh' | grep -vE '^\S+:[0-9]+:\s*#' | sed 's/^/      /'
else
  note "no ungated-broker flag anywhere in docker/, scripts/, .claude/"
fi

# --- 5. the schedule matches what the command files claim -----------------
# The times live in two places by necessity: docker/crontab makes them happen,
# and the command files document them. This is the same drift class as check 1 —
# a schedule edit that never reaches the docs leaves every reader believing a
# time that has not been true for weeks.
echo "== schedule matches the command files =="
if [ -f docker/crontab ]; then
  sched=0
  check_slot() { # job  expected-HH:MM  file  human-label
    local job="$1" want="$2" file="$3" label="$4" line hh mm got
    line="$(grep -E "scheduled-run\.sh[[:space:]]+$job\b" docker/crontab | grep -vE '^[[:space:]]*#' | head -1)"
    if [ -z "$line" ]; then bad "docker/crontab has no entry for '$job'"; return; fi
    mm="$(awk '{print $1}' <<<"$line")"; hh="$(awk '{print $2}' <<<"$line")"
    got="$(printf '%02d:%02d' "$hh" "$mm")"
    sched=$((sched+1))
    # The crontab minute is deliberately nudged off the documented mark to dodge
    # the :00/:15/:30 herd, so agreement is to the hour, not the minute.
    if [ "${got%%:*}" != "${want%%:*}" ]; then
      bad "docker/crontab runs '$job' at $got ET but $file documents $want ($label)"
    fi
  }
  check_slot preopen   "08:15" ".claude/commands/deep-research.md" "§Dispatch / §P"
  check_slot postclose "16:20" ".claude/commands/deep-research.md" "§Dispatch / §D"
  [ "$sched" -gt 0 ] && note "$sched scheduled job(s) checked against their command files"
else
  note "docker/crontab absent — skipping (not deployed on this machine)"
fi

# --- 6. the sidecar paths stay out of the public repo ---------------------
# research/, status/ and trade-log.csv are symlinks into the PRIVATE store repo.
# They were ignored as `research/` and `status/` — trailing slash, which matches
# a DIRECTORY ONLY. The moment they became symlinks they fell out of the ignore
# rules and showed up as untracked, one `git add -A` from committing
# machine-specific absolute paths into a public repository. Caught 2026-08-24.
echo "== sidecar paths are ignored (as symlinks, not just as directories) =="
sidecar_ok=1
for path in research status trade-log.csv; do
  if ! git check-ignore -q "$path" 2>/dev/null; then
    bad "$path is NOT gitignored — it would be committed to the PUBLIC repo"
    note "if it is a symlink, drop the trailing slash from its .gitignore entry"
    sidecar_ok=0
  fi
done
if git ls-files --error-unmatch research status trade-log.csv >/dev/null 2>&1; then
  bad "a sidecar path is TRACKED in the public repo — it must never be"
  sidecar_ok=0
fi
[ "$sidecar_ok" -eq 1 ] && note "research, status, trade-log.csv all ignored and untracked"

# --- 7. documented commands must be commands that exist --------------------
# `docker compose run` has no --network flag; that is `docker run`. The re-auth
# runbook carried it in five places and failed the first time Chris ran it, at
# the exact moment the account needed a token. Host networking is declared on
# the schwab-auth service instead.
echo "== documented docker commands are real =="
if grep -rn "compose run[^|]*--network host" README.md scripts/ docker/ 2>/dev/null | grep -v check-consistency >/dev/null 2>&1; then
  bad "a doc passes --network to 'docker compose run', which does not support it"
  grep -rn "compose run[^|]*--network host" README.md scripts/ docker/ 2>/dev/null | grep -v check-consistency | sed 's/^/      /'
else
  note "no unsupported flags in documented compose commands"
fi

# --- 8. the container crontab never schedules the deployer ----------------
# deploy.sh needs the docker CLI and recreates the very containers the
# scheduler runs in — from inside, it can only fail silently (no docker) or
# kill itself mid-deploy before its health check and rollback. It runs from
# the HOST crontab, installed by bootstrap-server.sh step 5c. This check keeps
# a well-meaning "add it back to the schedule" edit from resurrecting either
# failure mode.
echo "== deploy.sh stays out of the container crontab =="
if [ -f docker/crontab ] && grep -E 'deploy\.sh' docker/crontab | grep -vE '^\s*#' >/dev/null 2>&1; then
  bad "docker/crontab schedules deploy.sh — it must run from the HOST crontab (no docker CLI in the container, and it would kill itself mid-deploy)"
else
  note "deploy.sh is not scheduled inside the container"
fi

echo
if [ "$fails" -eq 0 ]; then echo "CONSISTENT — rules.yml, docs, and scripts agree."; exit 0; fi
echo "$fails inconsistency(ies). rules.yml is the source of truth; fix the other side."; exit 1
