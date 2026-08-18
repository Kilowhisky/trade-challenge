#!/bin/bash
# Regression suite for scripts/universe-filter.sh
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.." || exit 2
F=scripts/universe-filter.sh
FIX=tests/fixtures/verbose-sample.txt
pass=0; fail=0
ok()  { pass=$((pass+1)); printf '  ok   %s\n' "$1"; }
no()  { fail=$((fail+1)); printf 'FAIL  %s\n       %s\n' "$1" "${2:-}"; }

OUT=$(mktemp); ERR=$(mktemp); trap 'rm -f "$OUT" "$ERR"' EXIT

echo "== parsing =="
bash "$F" --payload "$FIX" --out "$OUT" >/dev/null 2>"$ERR"
n=$(tail -n +2 "$OUT" | wc -l | tr -d ' ')
[ "$n" -ge 1 ] && ok "produces rows from a verbose payload" || no "produces rows from a verbose payload" "got $n rows"

# Header must match ALL NINE columns, in order, exactly — Task 2 indexes this
# TSV positionally, so a silently reordered or renamed column must fail here.
EXPECTED_HEADER='symbol	price	adv10	dollar_vol	pct_from_52wk_high	optionable	leverage	last_earnings	is_etf'
actual_header=$(head -n1 "$OUT")
[ "$actual_header" = "$EXPECTED_HEADER" ] && ok "emits the exact TSV header (all 9 columns, in order)" || no "emits the exact TSV header (all 9 columns, in order)" "got: $actual_header"

echo "== unquotable symbols =="
# The fixture's NOQUOTE block has no lastPrice field — a genuinely
# unquotable symbol. It must never appear in the TSV, and it must never
# vanish silently: it has to be named on stderr.
! grep -q '^NOQUOTE	' "$OUT" && ok "excludes an unquotable symbol from the TSV" || no "excludes an unquotable symbol from the TSV"
grep -q 'NOQUOTE' "$ERR" && ok "names the skipped unquotable symbol on stderr" || no "names the skipped unquotable symbol on stderr" "stderr was: $(cat "$ERR")"

echo
echo "-------------------------------------------"
printf '%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
