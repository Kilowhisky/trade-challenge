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

echo "== §1.4 gates (--qualified-only) =="
bash "$F" --payload "$FIX" --out "$OUT" --qualified-only >/dev/null 2>&1
cut -f1 "$OUT" | tail -n +2 > /tmp/uf_syms.$$
grep -qx 'CSX' /tmp/uf_syms.$$ && ok "CSX passes every gate" || no "CSX passes every gate"
grep -qx 'PENNYCO' /tmp/uf_syms.$$ && no "sub-\$5 name is dropped" "PENNYCO survived" || ok "sub-\$5 name is dropped"
grep -qx 'TQQQ' /tmp/uf_syms.$$ && no "leveraged ETF is dropped (§3.5)" "TQQQ survived" || ok "leveraged ETF is dropped (§3.5)"
# TDOLLAR (price 6.00, adv10 200000 -> dollar_vol 1.2M < 5M floor, shares
# 200000 >= 100000 share floor) isolates the dollar-volume gate: it fails
# ONLY that gate. TSHARE (price 200.00, adv10 50000 -> dollar_vol 10M >= 5M
# floor, shares 50000 < 100000 share floor) isolates the share-sanity-floor
# gate the same way. Mutation-tested: deleting either gate line in
# universe-filter.sh's python block makes the corresponding assertion below
# fail (see task-2-report.md for the mutation run).
grep -qx 'TDOLLAR' /tmp/uf_syms.$$ && no "thin-dollar-volume name is dropped (§1.4)" "TDOLLAR survived" || ok "thin-dollar-volume name is dropped (§1.4)"
grep -qx 'TSHARE' /tmp/uf_syms.$$ && no "thin-share-floor name is dropped (§1.4)" "TSHARE survived" || ok "thin-share-floor name is dropped (§1.4)"
rm -f /tmp/uf_syms.$$

echo "== optionable is recorded, never required =="
bash "$F" --payload "$FIX" --out "$OUT" >/dev/null 2>&1
awk -F'\t' '$1=="PENNYCO" && $6=="false"' "$OUT" | grep -q . \
  && ok "non-optionable name is kept and flagged in the unfiltered pass" \
  || no "non-optionable name is kept and flagged in the unfiltered pass"

echo "== gate-failing rows are parsed fine, not silently dropped =="
# Proves TDOLLAR/TSHARE's absence above comes from the gate, not a parse
# failure: both must show up in the unfiltered pass.
grep -q '^TDOLLAR	' "$OUT" && ok "TDOLLAR is present in the unfiltered output" || no "TDOLLAR is present in the unfiltered output"
grep -q '^TSHARE	' "$OUT" && ok "TSHARE is present in the unfiltered output" || no "TSHARE is present in the unfiltered output"

echo "== ranking (--rank-top) =="
# The fixture's qualified-only survivors are CSX (gap 5.06% from its 52wk
# high) and QUALB (gap 2.00%, added to give ranking something to sort).
# --rank-top 1 must keep the nearest-to-high name (QUALB) and drop CSX —
# this fails if the sort key direction is reversed, and it fails if
# truncation doesn't actually slice the row list.
bash "$F" --payload "$FIX" --out "$OUT" --qualified-only --rank-top 1 >/dev/null 2>"$ERR"
n=$(tail -n +2 "$OUT" | wc -l | tr -d ' ')
top=$(tail -n +2 "$OUT" | cut -f1)
[ "$n" -eq 1 ] && [ "$top" = "QUALB" ] \
  && ok "--rank-top keeps the nearest-to-52wk-high row and caps the count" \
  || no "--rank-top keeps the nearest-to-52wk-high row and caps the count" "got $n row(s): $top"
# Two symbols qualify (CSX, QUALB); capping to 1 must report exactly one drop.
grep -q 'dropped 1' "$ERR" && ok "truncation count is reported accurately" \
  || no "truncation count is reported accurately" "stderr was: $(cat "$ERR")"

echo
echo "-------------------------------------------"
printf '%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
