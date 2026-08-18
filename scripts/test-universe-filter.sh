#!/bin/bash
# Regression suite for scripts/universe-filter.sh
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.." || exit 2
F=scripts/universe-filter.sh
FIX=tests/fixtures/verbose-sample.txt
# Second fixture, captured verbatim from a live get_quotes(verbose=True)
# call on 2026-08-17 (AAPL, SPY, TQQQ, CSX, SMCI). The synthetic fixture
# above was hand-written, and two whole-market defects survived it: a
# fundLeverageFactor scale that does not exist (3 rather than 300) and no
# `extended:`/`regular:` sub-blocks at all. Anything about the SHAPE of a
# real payload must be asserted against this file, not that one.
LIVE=tests/fixtures/verbose-live-sample.txt
pass=0; fail=0; skip=0
ok()   { pass=$((pass+1)); printf '  ok   %s\n' "$1"; }
no()   { fail=$((fail+1)); printf 'FAIL  %s\n       %s\n' "$1" "${2:-}"; }
# skip() is NOT a pass. It marks an assertion that did not run (offline
# branch of the fetch test, where there is no network to exercise the real
# path). It must be visually and numerically distinct from ok() so a reader
# — or a script grepping the summary line — can never mistake "the network
# was down and we didn't check" for "we checked and it was fine".
skip() { skip=$((skip+1)); printf '  SKIP %s (offline — not executed)\n' "$1"; }

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
# fundLeverageFactor is a PERCENTAGE: 0 single stock, 100 a 1x fund,
# 200/300 leveraged, negative inverse. The gate rejects anything outside
# {0, 100}, so 2x and inverse funds must go the same way TQQQ does.
grep -qx 'T2X' /tmp/uf_syms.$$ && no "2x ETF is dropped (§3.5)" "T2X survived" || ok "2x ETF is dropped (§3.5)"
grep -qx 'TINVERSE' /tmp/uf_syms.$$ && no "inverse ETF is dropped (§3.5)" "TINVERSE survived" || ok "inverse ETF is dropped (§3.5)"
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

echo "== real payload shape (live fixture) =="
# ---- price is taken from the REGULAR-SESSION close, not the extended print --
# CSX in the live fixture carries three different prices:
#   extended.lastPrice            50.67   (thin after-hours tick)
#   quote.lastPrice               50.89   (consolidated, includes extended)
#   regular.regularMarketLastPrice 50.58  (the regular-session close) <-- want
# `extended:` is emitted BEFORE `quote:`, so a whole-body first-match regex
# returns 50.67 and every gate, dollar_vol, and the 52-week rank key run off
# the after-hours print. This is the assertion that catches that.
bash "$F" --payload "$LIVE" --out "$OUT" >/dev/null 2>"$ERR"
csx_price=$(awk -F'\t' '$1=="CSX"{print $2}' "$OUT")
[ "$csx_price" = "50.5800" ] \
  && ok "CSX price is the regular-session close (50.58), not the extended print (50.67)" \
  || no "CSX price is the regular-session close (50.58), not the extended print (50.67)" "got $csx_price"
# Same three-way split on SMCI, whose extended/regular gap is larger still
# (37.34 extended / 38.118 quote / 38.28 regular).
smci_price=$(awk -F'\t' '$1=="SMCI"{print $2}' "$OUT")
[ "$smci_price" = "38.2800" ] \
  && ok "SMCI price is the regular-session close (38.28)" \
  || no "SMCI price is the regular-session close (38.28)" "got $smci_price"

# ---- the leverage column stores the MULTIPLE, not the raw percentage -------
spy_lev=$(awk -F'\t' '$1=="SPY"{print $7}' "$OUT")
[ "$spy_lev" = "1.0" ] && ok "SPY leverage column is the multiple 1.0 (raw 100)" \
  || no "SPY leverage column is the multiple 1.0 (raw 100)" "got $spy_lev"
tqqq_lev=$(awk -F'\t' '$1=="TQQQ"{print $7}' "$OUT")
[ "$tqqq_lev" = "3.0" ] && ok "TQQQ leverage column is the multiple 3.0 (raw 300)" \
  || no "TQQQ leverage column is the multiple 3.0 (raw 300)" "got $tqqq_lev"
aapl_lev=$(awk -F'\t' '$1=="AAPL"{print $7}' "$OUT")
[ "$aapl_lev" = "0.0" ] && ok "AAPL leverage column is 0.0 (single stock)" \
  || no "AAPL leverage column is 0.0 (single stock)" "got $aapl_lev"

# ---- the §3.5 gate keeps 1x funds and drops leveraged ones ----------------
# Real values: AAPL 0, SPY 100.0, TQQQ 300.0. A `!= 0` gate discards EVERY
# ETF (~5,574 of the 11,227 fetched symbols), not just the leveraged ones.
bash "$F" --payload "$LIVE" --out "$OUT" --qualified-only >/dev/null 2>&1
cut -f1 "$OUT" | tail -n +2 > /tmp/uf_live.$$
grep -qx 'SPY' /tmp/uf_live.$$ && ok "SPY (1x ETF, raw leverage 100) survives --qualified-only" \
  || no "SPY (1x ETF, raw leverage 100) survives --qualified-only" "SPY was dropped; the leverage gate is discarding every ETF"
grep -qx 'TQQQ' /tmp/uf_live.$$ && no "TQQQ (3x ETF, raw leverage 300) is dropped (§3.5)" "TQQQ survived" \
  || ok "TQQQ (3x ETF, raw leverage 300) is dropped (§3.5)"
grep -qx 'AAPL' /tmp/uf_live.$$ && ok "AAPL survives every gate" || no "AAPL survives every gate"
rm -f /tmp/uf_live.$$

echo "== sub-block anchoring: extended present, regular absent =="
# The synthetic NOREG block has extended.lastPrice 4.00 and quote.lastPrice
# 20.00 and NO `regular:` block. The documented fallback is the `quote:`
# sub-block -- never `extended:`, which is emitted first and would both change
# the price and (at 4.00) flip the §1.4 price gate.
bash "$F" --payload "$FIX" --out "$OUT" >/dev/null 2>&1
noreg_price=$(awk -F'\t' '$1=="NOREG"{print $2}' "$OUT")
[ "$noreg_price" = "20.0000" ] \
  && ok "falls back to quote.lastPrice (20.00), not extended.lastPrice (4.00)" \
  || no "falls back to quote.lastPrice (20.00), not extended.lastPrice (4.00)" "got $noreg_price"

echo "== directory fetch =="
SYMS=$(mktemp)
bash scripts/universe-fetch.sh --out "$SYMS" >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  c=$(wc -l < "$SYMS" | tr -d ' ')
  [ "$c" -gt 5000 ] && ok "fetched a plausible symbol count ($c)" || no "fetched a plausible symbol count" "got $c"
  # Tighter than the >5000 check above: the real directory currently yields
  # ~11,227 symbols. A window around that (not an exact match, since the
  # live list drifts week to week) still catches a filter that's broken in
  # a way that happens to clear 5000 but is nowhere near the real shape.
  [ "$c" -ge 10000 ] && [ "$c" -le 12500 ] \
    && ok "success path writes ~11,227 symbols ($c, within [10000,12500])" \
    || no "success path writes ~11,227 symbols" "got $c, outside [10000,12500]"
  grep -qx 'AAPL' "$SYMS" && ok "AAPL present" || no "AAPL present"
  grep -qE '\$' "$SYMS" && no "test/oddball symbols excluded" "found \$ symbols" || ok "test/oddball symbols excluded"
  # The source file's last data row is a footer ("File Creation Time: ...
  # "), not a symbol. It must never leak into the output.
  grep -q 'File Creation Time' "$SYMS" && no "footer line excluded from symbol list" "footer leaked into output" || ok "footer line excluded from symbol list"
elif [ "$rc" -eq 3 ]; then
  ok "offline: exits 3 so the caller keeps last week's universe"
  skip "fetched a plausible symbol count"
  skip "success path writes ~11,227 symbols"
  skip "AAPL present / test symbols excluded"
  skip "footer line excluded from symbol list"
else
  no "universe-fetch returns 0 or 3" "got $rc"
fi
rm -f "$SYMS"

echo "== directory fetch: decoy-page rejection (fix round 1) =="
# Reproduces the bug the reviewer found live against nasdaqtrader.com: an
# unknown path 302-redirects to a small "File Not Found" HTML page, which
# curl -sf (without -L, before the fix) treats as a successful fetch. The
# python filter then parses that HTML as pipe-delimited CSV, finds zero
# symbols, and (pre-fix) wrote an empty universe and exited 0 — silently
# indistinguishable from a real sweep that legitimately found nothing.
# This does NOT depend on network reachability: tests/fixtures/
# nasdaqtrader-decoy-404.html stands in for the decoy page via the
# UNIVERSE_FETCH_URL test-only override (see the comment on URL= in
# universe-fetch.sh), so this assertion runs every time, online or off.
DECOY="tests/fixtures/nasdaqtrader-decoy-404.html"
DOUT=$(mktemp); rm -f "$DOUT"
UNIVERSE_FETCH_URL="file://$(pwd)/$DECOY" bash scripts/universe-fetch.sh --out "$DOUT" >/dev/null 2>"$ERR"
drc=$?
[ "$drc" -eq 3 ] && ok "decoy page (0 real symbols) is rejected with exit 3" || no "decoy page (0 real symbols) is rejected with exit 3" "got rc=$drc"
[ ! -e "$DOUT" ] && ok "decoy page: no output file is written" || no "decoy page: no output file is written" "$DOUT exists with $(wc -l < "$DOUT" 2>/dev/null) lines"
grep -q 'refusing to write' "$ERR" && ok "decoy page: refusal is named on stderr" || no "decoy page: refusal is named on stderr" "stderr was: $(cat "$ERR")"
rm -f "$DOUT"

echo
echo "-------------------------------------------"
if [ "$skip" -gt 0 ]; then
  printf '%s passed, %s failed, %s SKIPPED (offline — network unreachable, real fetch path NOT exercised)\n' "$pass" "$fail" "$skip"
else
  printf '%s passed, %s failed\n' "$pass" "$fail"
fi
[ "$fail" -eq 0 ] || exit 1
