#!/bin/bash
# test-pre-order-check.sh — regression suite for the mandatory pre-order gate.
#
# Why this exists: on 2026-08-17 pre-order-check.sh was found enforcing the
# pre-amendment §3.2 option caps (15%/20% against the amended 20%/30%). It had
# been wrong for four days. check-consistency.sh now catches a *stated* value
# drifting, but only a test can catch the arithmetic being wrong — a floored
# cap that rounds up, a boundary that is off by one cent, a gate that reports
# the wrong exit code, or a rules.yml failure that silently reads as a pass.
#
# Run: bash scripts/test-pre-order-check.sh    (0 = all pass, 1 = failures)
#
# Conventions: comp-capital 1000.00 makes every cap a round number —
#   §3.1 350.00 · §3.2 single 200.00 · §3.2 open 300.00 · §3.5 200.00
# so a boundary case is legible rather than arithmetic-by-inspection.

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.." || exit 2
GATE=scripts/pre-order-check.sh

pass=0; fail=0; out=""
_run() { out="$(bash "$GATE" "$@" 2>&1)"; return $?; }

# expect_exit CODE "desc" -- args...
expect_exit() {
  local want="$1" desc="$2"; shift 3
  local got; _run "$@"; got=$?
  if [ "$got" -eq "$want" ]; then pass=$((pass+1)); printf '  ok   %s\n' "$desc"
  else fail=$((fail+1)); printf 'FAIL  %s\n       wanted exit %s, got %s\n' "$desc" "$want" "$got"
       printf '       %s\n' "$(head -2 <<<"$out")"; fi
}

# expect_match CODE REGEX "desc" -- args...
expect_match() {
  local want="$1" re="$2" desc="$3"; shift 4
  local got; _run "$@"; got=$?
  if [ "$got" -eq "$want" ] && grep -qE "$re" <<<"$out"; then
    pass=$((pass+1)); printf '  ok   %s\n' "$desc"
  else
    fail=$((fail+1)); printf 'FAIL  %s\n       wanted exit %s matching /%s/, got exit %s\n' "$desc" "$want" "$re" "$got"
    printf '       %s\n' "$(head -2 <<<"$out")"
  fi
}

# Base argument sets. C=comp capital, S=settled cash (large unless under test).
EQ=(--instrument equity --symbol TST --comp-capital 1000.00 --settled-cash 100000.00)
OPT=(--instrument option --symbol TST --comp-capital 1000.00 --settled-cash 100000.00 --underlying-price 50.00)
LEV=(--instrument leveraged_etf --symbol TQQQ --comp-capital 1000.00 --settled-cash 100000.00)

echo "== happy paths (exit 0) =="
expect_exit 0 "equity within every cap" -- "${EQ[@]}" --qty 10 --price 10.00 --intent-notional 100.00
expect_exit 0 "option within every cap" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00
expect_exit 0 "leveraged ETF within caps" -- "${LEV[@]}" --qty 10 --price 10.00 --intent-notional 100.00 --leveraged-aggregate 0
expect_exit 0 "option on leveraged underlying" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --leveraged-underlying --leveraged-aggregate 0
expect_match 0 'ALL CHECKED GATES PASS' "pass prints the affirmative line" -- "${EQ[@]}" --qty 10 --price 10.00 --intent-notional 100.00
expect_match 0 'NOT-CHECKED' "pass prints the NOT-CHECKED block" -- "${EQ[@]}" --qty 10 --price 10.00 --intent-notional 100.00
expect_match 5 'NOT-CHECKED' "a cap FAIL also prints the NOT-CHECKED block" -- "${EQ[@]}" --qty 1 --price 350.01 --intent-notional 350.01
expect_match 3 'NOT-CHECKED' "a floor FAIL also prints the NOT-CHECKED block" -- "${EQ[@]}" --qty 1 --price 4.99 --intent-notional 4.99

echo "== §1.4 price floor (exit 3) =="
expect_exit 3 "equity below \$5.00" -- "${EQ[@]}" --qty 1 --price 4.99 --intent-notional 4.99
expect_exit 0 "equity exactly \$5.00 (boundary passes)" -- "${EQ[@]}" --qty 1 --price 5.00 --intent-notional 5.00
expect_exit 3 "equity at 4.9999 (sub-cent below floor)" -- "${EQ[@]}" --qty 1 --price 4.9999 --intent-notional 4.9999
expect_exit 3 "option underlying below floor" -- --instrument option --symbol TST --comp-capital 1000.00 --settled-cash 100000.00 --underlying-price 4.99 --qty 1 --price 1.00 --intent-notional 100.00
expect_exit 3 "leveraged ETF below floor" -- "${LEV[@]}" --qty 1 --price 4.99 --intent-notional 4.99 --leveraged-aggregate 0

echo "== §4.10 notional sanity (exit 4) =="
expect_exit 0 "intent off by exactly \$0.01 (tolerance boundary)" -- "${EQ[@]}" --qty 1 --price 100.00 --intent-notional 100.01
expect_exit 4 "intent off by \$0.0101 (just past tolerance)" -- "${EQ[@]}" --qty 1 --price 100.00 --intent-notional 100.0101
expect_match 4 'unit-confusion' "notional breach names the unit-confusion abort" -- "${EQ[@]}" --qty 1 --price 100.00 --intent-notional 500.00
expect_exit 4 "option: multiplier omitted from intent (unit confusion)" -- "${OPT[@]}" --qty 1 --price 5.50 --intent-notional 5.50
expect_exit 0 "option: multiplier included in intent" -- "${OPT[@]}" --qty 1 --price 1.50 --intent-notional 150.00
expect_exit 4 "equity: shares read as contracts (100x)" -- "${EQ[@]}" --qty 1 --price 10.00 --intent-notional 1000.00

echo "== §3.1 single position cap, 35% of 1000 = 350.00 (exit 5) =="
expect_exit 0 "exactly at cap" -- "${EQ[@]}" --qty 1 --price 350.00 --intent-notional 350.00
expect_exit 5 "one cent over cap" -- "${EQ[@]}" --qty 1 --price 350.01 --intent-notional 350.01
expect_match 5 '350\.00' "breach message names the actual §3.1 cap (350.00)" -- "${EQ[@]}" --qty 1 --price 400.00 --intent-notional 400.00
expect_exit 5 "prior adds push it over" -- "${EQ[@]}" --qty 1 --price 100.00 --intent-notional 100.00 --existing-position-value 260.00
expect_exit 0 "prior adds land exactly on cap" -- "${EQ[@]}" --qty 1 --price 100.00 --intent-notional 100.00 --existing-position-value 250.00

echo "== §3.2 option caps, single 200.00 / open 300.00 (exit 5) =="
expect_exit 0 "single exactly at cap" -- "${OPT[@]}" --qty 1 --price 2.00 --intent-notional 200.00
expect_exit 5 "single one cent over" -- "${OPT[@]}" --qty 1 --price 2.0001 --intent-notional 200.01
expect_match 5 '200\.00' "breach message names the actual §3.2 single cap (200.00)" -- "${OPT[@]}" --qty 1 --price 2.50 --intent-notional 250.00
expect_exit 5 "prior premium in same contract pushes over" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --existing-option-premium 101.00
expect_exit 0 "aggregate exactly at cap" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --open-option-premium 200.00
expect_exit 5 "aggregate one cent over" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --open-option-premium 200.01
expect_match 5 'aggregate' "aggregate breach names the aggregate gate" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --open-option-premium 250.00
expect_match 5 '300\.00' "breach message names the actual §3.2 open cap (300.00)" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --open-option-premium 250.00

echo "== §3.5 leveraged aggregate, 20% of 1000 = 200.00 (exit 5) =="
expect_exit 0 "leveraged exactly at cap" -- "${LEV[@]}" --qty 1 --price 200.00 --intent-notional 200.00 --leveraged-aggregate 0
expect_exit 5 "leveraged one cent over" -- "${LEV[@]}" --qty 1 --price 200.01 --intent-notional 200.01 --leveraged-aggregate 0
expect_match 5 '200\.00' "breach message names the actual §3.5 cap (200.00)" -- "${LEV[@]}" --qty 1 --price 250.00 --intent-notional 250.00 --leveraged-aggregate 0
expect_exit 5 "existing leveraged exposure pushes over" -- "${LEV[@]}" --qty 1 --price 50.00 --intent-notional 50.00 --leveraged-aggregate 160.00
expect_exit 5 "option on leveraged underlying breaches §3.5" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --leveraged-underlying --leveraged-aggregate 150.00

echo "== §5 settled cash (exit 6) =="
expect_exit 6 "notional exceeds settled cash by a cent" -- --instrument equity --symbol TST --comp-capital 1000.00 --settled-cash 99.99 --qty 1 --price 100.00 --intent-notional 100.00
expect_match 6 'unsettled funds cannot be used' "§5 breach explains why" -- --instrument equity --symbol TST --comp-capital 1000.00 --settled-cash 50.00 --qty 1 --price 100.00 --intent-notional 100.00
expect_exit 0 "notional exactly equals settled cash" -- --instrument equity --symbol TST --comp-capital 1000.00 --settled-cash 100.00 --qty 1 --price 100.00 --intent-notional 100.00

echo "== usage errors (exit 2) =="
expect_exit 2 "no arguments at all" --
expect_exit 2 "missing --instrument" -- --symbol TST --qty 1 --price 10.00 --intent-notional 10.00 --comp-capital 1000.00 --settled-cash 100.00
expect_exit 2 "unknown instrument" -- --instrument future --symbol TST --qty 1 --price 10.00 --intent-notional 10.00 --comp-capital 1000.00 --settled-cash 100.00
expect_exit 2 "missing --settled-cash" -- --instrument equity --symbol TST --qty 1 --price 10.00 --intent-notional 10.00 --comp-capital 1000.00
expect_exit 2 "option without --underlying-price" -- --instrument option --symbol TST --qty 1 --price 1.00 --intent-notional 100.00 --comp-capital 1000.00 --settled-cash 100000.00
expect_exit 2 "leveraged_etf without --leveraged-aggregate" -- "${LEV[@]}" --qty 1 --price 10.00 --intent-notional 10.00
expect_exit 2 "--leveraged-aggregate on option without --leveraged-underlying" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --leveraged-aggregate 0
expect_exit 2 "--leveraged-underlying without --leveraged-aggregate" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --leveraged-underlying
expect_exit 2 "--existing-position-value on an option" -- "${OPT[@]}" --qty 1 --price 1.00 --intent-notional 100.00 --existing-position-value 10.00
expect_exit 2 "--underlying-price on an equity" -- "${EQ[@]}" --qty 1 --price 10.00 --intent-notional 10.00 --underlying-price 50.00
expect_exit 2 "--open-option-premium on an equity" -- "${EQ[@]}" --qty 1 --price 10.00 --intent-notional 10.00 --open-option-premium 10.00
expect_exit 2 "duplicate --qty" -- "${EQ[@]}" --qty 1 --qty 2 --price 10.00 --intent-notional 10.00
expect_exit 2 "qty zero" -- "${EQ[@]}" --qty 0 --price 10.00 --intent-notional 0.00
expect_exit 2 "qty negative" -- "${EQ[@]}" --qty -1 --price 10.00 --intent-notional 10.00
expect_exit 2 "qty non-integer" -- "${EQ[@]}" --qty 1.5 --price 10.00 --intent-notional 15.00
expect_exit 2 "qty above 1000000" -- "${EQ[@]}" --qty 1000001 --price 10.00 --intent-notional 10000010.00
expect_exit 2 "price with 5 decimals" -- "${EQ[@]}" --qty 1 --price 10.00001 --intent-notional 10.00
expect_exit 2 "price carrying a dollar sign" -- "${EQ[@]}" --qty 1 --price '$10.00' --intent-notional 10.00
expect_exit 2 "price carrying a comma" -- "${EQ[@]}" --qty 1 --price '1,000.00' --intent-notional 1000.00
expect_exit 2 "negative price" -- "${EQ[@]}" --qty 1 --price -10.00 --intent-notional -10.00
expect_exit 2 "price above the 5-digit budget" -- "${EQ[@]}" --qty 1 --price 100000.00 --intent-notional 100000.00

echo "== gate precedence (first breach wins) =="
expect_exit 3 "§1.4 outranks §4.10" -- "${EQ[@]}" --qty 1 --price 4.99 --intent-notional 999.00
expect_exit 4 "§4.10 outranks the caps" -- "${EQ[@]}" --qty 1 --price 400.00 --intent-notional 1.00
expect_exit 5 "caps outrank §5 settled cash" -- --instrument equity --symbol TST --comp-capital 1000.00 --settled-cash 1.00 --qty 1 --price 400.00 --intent-notional 400.00

echo "== caps are FLOORED, never rounded up =="
# comp 1000.0001 -> 35% = 350.000035 -> must floor to 350.0000, so 350.0001 fails.
expect_exit 0 "floored cap admits the value at the floor" -- --instrument equity --symbol TST --comp-capital 1000.0001 --settled-cash 100000.00 --qty 1 --price 350.0000 --intent-notional 350.0000
expect_exit 5 "floored cap rejects one 1e-4 above it" -- --instrument equity --symbol TST --comp-capital 1000.0001 --settled-cash 100000.00 --qty 1 --price 350.0001 --intent-notional 350.0001

echo "== rules.yml is genuinely the source of truth =="
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/scripts"; cp scripts/pre-order-check.sh scripts/lib-rules.sh "$TMP/scripts/"; cp rules.yml "$TMP/"
_runtmp() { ( cd "$TMP" && bash scripts/pre-order-check.sh "$@" >/dev/null 2>&1 ); }

_runtmp "${EQ[@]}" --qty 1 --price 350.00 --intent-notional 350.00
[ $? -eq 0 ] && { pass=$((pass+1)); echo "  ok   copy behaves like the original"; } || { fail=$((fail+1)); echo "FAIL  copy behaves like the original"; }

sed -i.bak 's/^  single_position_pct: 35$/  single_position_pct: 30/' "$TMP/rules.yml"
_runtmp "${EQ[@]}" --qty 1 --price 350.00 --intent-notional 350.00
[ $? -eq 5 ] && { pass=$((pass+1)); echo "  ok   lowering the cap in rules.yml tightens the gate"; } || { fail=$((fail+1)); echo "FAIL  lowering the cap in rules.yml tightens the gate"; }
_runtmp "${EQ[@]}" --qty 1 --price 300.00 --intent-notional 300.00
[ $? -eq 0 ] && { pass=$((pass+1)); echo "  ok   the new lower cap is the one enforced"; } || { fail=$((fail+1)); echo "FAIL  the new lower cap is the one enforced"; }
cp "$TMP/rules.yml.bak" "$TMP/rules.yml"

echo "== rules.yml failures fail CLOSED (exit 7, never a pass) =="
mv "$TMP/rules.yml" "$TMP/rules.away"
_runtmp "${EQ[@]}" --qty 1 --price 10.00 --intent-notional 10.00
[ $? -eq 7 ] && { pass=$((pass+1)); echo "  ok   absent rules.yml exits 7"; } || { fail=$((fail+1)); echo "FAIL  absent rules.yml exits 7"; }
mv "$TMP/rules.away" "$TMP/rules.yml"

grep -v '^  single_position_pct:' "$TMP/rules.yml" > "$TMP/r2" && mv "$TMP/r2" "$TMP/rules.yml"
_runtmp "${EQ[@]}" --qty 1 --price 10.00 --intent-notional 10.00
[ $? -eq 7 ] && { pass=$((pass+1)); echo "  ok   rules.yml missing a required key exits 7"; } || { fail=$((fail+1)); echo "FAIL  rules.yml missing a required key exits 7"; }

printf 'this is not: valid: yaml: at all\n' > "$TMP/rules.yml"
_runtmp "${EQ[@]}" --qty 1 --price 10.00 --intent-notional 10.00
[ $? -eq 7 ] && { pass=$((pass+1)); echo "  ok   malformed rules.yml exits 7"; } || { fail=$((fail+1)); echo "FAIL  malformed rules.yml exits 7"; }

echo
echo "-------------------------------------------"
printf '%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
