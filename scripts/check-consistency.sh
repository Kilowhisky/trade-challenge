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

echo
if [ "$fails" -eq 0 ]; then echo "CONSISTENT — rules.yml, docs, and scripts agree."; exit 0; fi
echo "$fails inconsistency(ies). rules.yml is the source of truth; fix the other side."; exit 1
