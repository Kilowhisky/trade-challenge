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

OUT=$(mktemp); ERR=$(mktemp)
# research/ is a SYMLINK into the private store repo. universe-filter.sh now
# writes research/universe-qualified.tsv under --qualified-only, so every run
# in this suite is redirected to a scratch dir: a test must never overwrite the
# live weekly sweep's output with 13 rows of fixture data.
TCRD=$(mktemp -d); export TC_RESEARCH_DIR="$TCRD"
trap 'rm -f "$OUT" "$ERR"; rm -rf "$TCRD"' EXIT

echo "== parsing =="
bash "$F" --payload "$FIX" --out "$OUT" >/dev/null 2>"$ERR"
n=$(tail -n +2 "$OUT" | wc -l | tr -d ' ')
[ "$n" -ge 1 ] && ok "produces rows from a verbose payload" || no "produces rows from a verbose payload" "got $n rows"

# Header must match ALL NINE columns, in order, exactly — Task 2 indexes this
# TSV positionally, so a silently reordered or renamed column must fail here.
EXPECTED_HEADER='symbol	price	adv10	dollar_vol	pct_from_52wk_high	optionable	leverage	last_earnings	is_etf	session_range_pct'
actual_header=$(head -n1 "$OUT")
[ "$actual_header" = "$EXPECTED_HEADER" ] && ok "emits the exact TSV header (all 10 columns, in order)" || no "emits the exact TSV header (all 10 columns, in order)" "got: $actual_header"

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
bash "$F" --payload "$FIX" --out "$OUT" --qualified-only >/dev/null 2>/dev/null
qualified_n=$(tail -n +2 "$OUT" | wc -l | tr -d ' ')
bash "$F" --payload "$FIX" --out "$OUT" --qualified-only --rank-top 1 >/dev/null 2>"$ERR"
n=$(tail -n +2 "$OUT" | wc -l | tr -d ' ')
top=$(tail -n +2 "$OUT" | cut -f1)
[ "$n" -eq 1 ] && [ "$top" = "QUALB" ] \
  && ok "--rank-top keeps the nearest-to-52wk-high row and caps the count" \
  || no "--rank-top keeps the nearest-to-52wk-high row and caps the count" "got $n row(s): $top"
# Capping to 1 must report every other qualified row as dropped. Derived from
# the unranked run above rather than hard-coded, so adding a fixture symbol
# does not silently turn this into an assertion about the wrong number.
grep -q "dropped $((qualified_n - 1))" "$ERR" && ok "truncation count is reported accurately" \
  || no "truncation count is reported accurately" "expected dropped $((qualified_n - 1)); stderr was: $(cat "$ERR")"

echo "== qualified-set emission (--emit-qualified-set writes the FULL set) =="
# The --out file is the DAILY tier's list: ranked, then truncated to
# working_universe_size. The scout tier needs the set that ranking DISCARDS —
# the low-coverage names the information edge lives in — so
# --emit-qualified-set writes every qualifying row, untruncated, to
# ${TC_RESEARCH_DIR:-research}/universe-qualified.tsv.
QDIR=$(mktemp -d)
# --rank-top 1 truncates as hard as the fixture allows, so "the full set" and
# "the ranked set" cannot be the same file by coincidence.
TC_RESEARCH_DIR="$QDIR" bash "$F" --payload "$FIX" --out "$OUT" --qualified-only --emit-qualified-set --rank-top 1 >/dev/null 2>"$ERR"
Q="$QDIR/universe-qualified.tsv"
ranked_n=$(tail -n +2 "$OUT" 2>/dev/null | wc -l | tr -d ' ')
[ -f "$Q" ] && ok "--emit-qualified-set emits research/universe-qualified.tsv" \
  || no "--emit-qualified-set emits research/universe-qualified.tsv" "no file at $Q"
# Exact header, all ten columns in order — universe-qualified.tsv is indexed
# POSITIONALLY by the cohort builder, and last_earnings (col 8) is the column
# the earnings cohort is derived from. A renamed, reordered or absent header
# must fail here.
qhdr=$(head -n1 "$Q" 2>/dev/null)
[ "$qhdr" = "$EXPECTED_HEADER" ] \
  && ok "qualified file carries the exact 10-column header incl. last_earnings" \
  || no "qualified file carries the exact 10-column header incl. last_earnings" "got: $qhdr"
# THE TRUNCATION ASSERTION. qualified_n is the untruncated qualified count
# measured above; the emitted file must carry all of it even though --out was
# capped to one row. Guarded on qualified_n > ranked_n, because if the fixture
# ever qualified only one name this comparison would pass while proving
# nothing — emitting after truncation would give the same number.
q_n=$(tail -n +2 "$Q" 2>/dev/null | wc -l | tr -d ' ')
if [ "$qualified_n" -le "$ranked_n" ]; then
  no "qualified file is written BEFORE truncation" "fixture qualifies $qualified_n name(s) and --out kept $ranked_n — the comparison cannot discriminate"
elif [ "$q_n" -eq "$qualified_n" ]; then
  ok "qualified file is written BEFORE truncation ($q_n rows vs $ranked_n ranked)"
else
  no "qualified file is written BEFORE truncation" "qualified file has $q_n rows, expected $qualified_n (--out kept $ranked_n)"
fi
# last_earnings must survive with its VALUE, not just its header: the cohort
# builder estimates the next print from it. CSX is deliberately the assertion
# subject — --rank-top 1 drops it from --out, so a truncated emission cannot
# satisfy this either.
q_csx=$(awk -F'\t' '$1=="CSX"{print $8}' "$Q" 2>/dev/null)
[ "$q_csx" = "2026-07-22" ] \
  && ok "last_earnings survives into the qualified file (CSX 2026-07-22)" \
  || no "last_earnings survives into the qualified file (CSX 2026-07-22)" "got '$q_csx'"
# The stderr line must name the path AND the untruncated count. A bare
# "universe-qualified.tsv" match would still pass if the count reported were
# the ranked one — which is precisely the confusion this whole task is about.
grep -q "wrote $qualified_n qualified symbol(s) to $Q" "$ERR" \
  && ok "stderr names the qualified file and its untruncated count ($qualified_n)" \
  || no "stderr names the qualified file and its untruncated count ($qualified_n)" "stderr was: $(cat "$ERR")"
rm -rf "$QDIR"

echo "== the qualified file is written ONLY on the explicit flag =="
# An unfiltered pass has not applied the gates, so its rows are not a QUALIFIED
# set. Writing the file anyway would silently replace the scout universe with
# an ungated one.
QDIR2=$(mktemp -d)
TC_RESEARCH_DIR="$QDIR2" bash "$F" --payload "$FIX" --out "$OUT" >/dev/null 2>&1
[ ! -e "$QDIR2/universe-qualified.tsv" ] \
  && ok "an unfiltered run writes no qualified file" \
  || no "an unfiltered run writes no qualified file" "$QDIR2/universe-qualified.tsv was written with $(tail -n +2 "$QDIR2/universe-qualified.tsv" | wc -l | tr -d ' ') rows"
# THE DAILY-RUN GUARD. /deep-research re-quotes the ~500 names already in
# research/universe.md and passes --qualified-only over them, every morning.
# If the emission triggered on that flag alone, the daily run would overwrite
# a ~3,196-name scout universe with <=500 of its own names — silently, and
# looking freshly written the whole time. Emission requires the explicit
# --emit-qualified-set, which only the weekly whole-market sweep passes.
TC_RESEARCH_DIR="$QDIR2" bash "$F" --payload "$FIX" --out "$OUT" --qualified-only >/dev/null 2>&1
[ ! -e "$QDIR2/universe-qualified.tsv" ] \
  && ok "--qualified-only alone (the daily sweep) writes no qualified file" \
  || no "--qualified-only alone (the daily sweep) writes no qualified file" "the daily run would clobber the weekly scout universe"
# And the converse must be refused rather than mislabelled: an ungated set
# written under the qualified name is worse than no file at all.
QDIR3=$(mktemp -d)
TC_RESEARCH_DIR="$QDIR3" bash "$F" --payload "$FIX" --out "$OUT" --emit-qualified-set >/dev/null 2>"$ERR"
rc=$?
[ "$rc" -eq 2 ] && [ ! -e "$QDIR3/universe-qualified.tsv" ] \
  && ok "--emit-qualified-set without --qualified-only is a usage error (exit 2)" \
  || no "--emit-qualified-set without --qualified-only is a usage error (exit 2)" "got rc=$rc; file present: $([ -e "$QDIR3/universe-qualified.tsv" ] && echo yes || echo no); stderr: $(cat "$ERR")"
rm -rf "$QDIR2" "$QDIR3"

echo "== takeover-stub gate (§4, strategy_min_session_range_pct) =="
# An announced all-cash deal target trades pinned a hair under its deal
# price, which is by construction its 52-week high — so the proximity tilt
# this universe ranks on acts as a merger detector. On 2026-08-19 nine of
# fifteen shortlisted names were deal stubs; UTZ and DBRG reached WATCH on
# that artifact and had to be retracted.
bash "$F" --payload "$FIX" --out "$OUT" --qualified-only >/dev/null 2>"$ERR"
cut -f1 "$OUT" | tail -n +2 > /tmp/uf_stub.$$
# STUBCO: high 30.10 / low 30.00 on a 30.05 price = 0.33% range. Passes every
# other gate, so its absence can only come from the stub gate.
grep -qx 'STUBCO' /tmp/uf_stub.$$ && no "pinned stub is dropped" "STUBCO survived" || ok "pinned stub is dropped"
# WIDECO: high 41.00 / low 40.00 on 40.50 = 2.47%. A live name must not be
# caught by a gate aimed at pinned ones.
grep -qx 'WIDECO' /tmp/uf_stub.$$ && ok "normal-range name survives the stub gate" || no "normal-range name survives the stub gate" "WIDECO was dropped"
# ZERONGE: high = low = 0 with 5,000,000 shares of session volume. This is
# NO DATA, not zero range, and must be kept. The rule as first specified
# ("range == 0 AND volume == 0") would keep it only by accident of the
# volume test; see the live-fixture SPY assertion below for the case that
# conjunction actually gets wrong.
grep -qx 'ZERONGE' /tmp/uf_stub.$$ && ok "no-data high/low is kept, not read as maximally pinned" || no "no-data high/low is kept, not read as maximally pinned" "ZERONGE was dropped"
rm -f /tmp/uf_stub.$$
grep -q 'stub-filtered 1 symbol' "$ERR" && ok "stub rejections are counted on stderr" || no "stub rejections are counted on stderr" "stderr was: $(cat "$ERR")"
grep -q 'STUBCO' "$ERR" && ok "stub rejection names the symbol on stderr" || no "stub rejection names the symbol on stderr" "stderr was: $(cat "$ERR")"
grep -q 'no usable high/low' "$ERR" && ok "no-data symbols are reported on stderr" || no "no-data symbols are reported on stderr" "stderr was: $(cat "$ERR")"

echo "== session_range_pct column =="
wide_rng=$(awk -F'\t' '$1=="WIDECO"{print $10}' "$OUT")
[ "$wide_rng" = "2.47" ] && ok "session_range_pct carries the computed range (2.47)" || no "session_range_pct carries the computed range (2.47)" "got $wide_rng"
zero_rng=$(awk -F'\t' '$1=="ZERONGE"{print $10}' "$OUT")
[ "$zero_rng" = "-" ] && ok "session_range_pct is '-' for no-data, never 0.00" || no "session_range_pct is '-' for no-data, never 0.00" "got $zero_rng"

echo "== stub gate is a gate, not a parse failure =="
# Both must appear in the unfiltered pass, which proves STUBCO's absence
# above comes from the gate rather than from a block the parser choked on.
bash "$F" --payload "$FIX" --out "$OUT" >/dev/null 2>&1
grep -q '^STUBCO	' "$OUT" && ok "STUBCO is present in the unfiltered output" || no "STUBCO is present in the unfiltered output"
grep -q '^WIDECO	' "$OUT" && ok "WIDECO is present in the unfiltered output" || no "WIDECO is present in the unfiltered output"

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
# THE REGRESSION FOR THE MIS-SPECIFIED STUB RULE. SPY in the live fixture
# carries highPrice 0, lowPrice 0 and openPrice 0 alongside totalVolume
# 34,356,577 — a real, maximally liquid ETF whose payload simply has no
# consolidated OHLC. Reading that as a 0.00% range rejects it; the rule as
# first written ("range == 0 AND volume == 0 -> no-data") does exactly that,
# because SPY's volume is not zero. The shipped test is on high/low alone.
grep -qx 'SPY' /tmp/uf_live.$$ \
  && ok "SPY (high = low = 0 with 34.4M volume) survives the stub gate" \
  || no "SPY (high = low = 0 with 34.4M volume) survives the stub gate" "SPY was dropped — the stub gate is reading no-data as maximally pinned"
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
