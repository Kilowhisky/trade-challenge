#!/bin/bash
# Deterministic calculator for the ARITHMETIC §4.9/§4.10 pre-order gates.
# No network, no broker calls — every input is an explicit argument the
# operator supplies from the latest tick/account read. This script checks
# ONLY what is arithmetic (§1.4 price floor, §4.10 notional sanity, §3.1/
# §3.2/§3.5 caps, §5 settled cash). It never checks earnings, corporate
# actions, correlation, halts, drawdown level, option quality floors,
# order-rate ceilings, quote freshness, or the reserve invariant — those
# stay the operator's duty, and the NOT-CHECKED block says so every run.
#
# Usage:
#   scripts/pre-order-check.sh \
#     --instrument equity|option|leveraged_etf \
#     --symbol SYM --qty N --price DOLLARS \
#     --intent-notional DOLLARS \
#     --account-value DOLLARS --settled-cash DOLLARS \
#     [--existing-position-value DOLLARS]   # same symbol stock/ETF value; default 0
#                                           # (stock/ETF instruments only)
#     [--existing-option-premium DOLLARS]   # prior premium in THIS contract; default 0
#                                           # (option only)
#     [--open-option-premium DOLLARS]       # total open premium, all contracts;
#                                           # default 0 (option only)
#     [--leveraged-aggregate DOLLARS]       # TOTAL current leveraged/inverse
#                                           # exposure, INCLUDING any existing
#                                           # position in this same symbol.
#                                           # REQUIRED (no default) for
#                                           # leveraged_etf and for option +
#                                           # --leveraged-underlying; refused
#                                           # elsewhere. Type 0 explicitly.
#     [--underlying-price DOLLARS]          # required when --instrument option (§1.4)
#     [--leveraged-underlying]              # option whose underlying is a leveraged/
#                                           # inverse ETF: §3.5 also applies (manual
#                                           # §3.5 last bullet). Requires an explicit
#                                           # --leveraged-aggregate.
#
# Flags that do not apply to the chosen instrument are REFUSED (exit 2),
# never silently ignored. Duplicate flags are refused. Leading zeros are
# accepted and normalized where they fit within each input's digit budget
# (the bounds count characters, so 099999.9999 exceeds the 5-digit price).
#
# Numbers: plain decimals (no $, no commas), at most 4 decimal places, at
# most 99999.9999 for prices and 99999999.99 for account-level amounts.
# --qty: positive integer ≤ 1000000 (shares or contracts).
#
# Arithmetic: every amount is parsed by string into an exact 1e-4-scaled
# integer (no floats anywhere). Products keep full precision; ONLY the
# §4.10 comparison applies a $0.01 tolerance. Percentage caps are FLOORED
# at 1e-4 precision — a cap is never rounded up across a limit (§3.1 is
# unamendable core; the permissive direction is the wrong direction).
# Displayed amounts print the exact value (trailing sub-cent digits shown
# only when nonzero); input prices are echoed verbatim as typed.
#
# Exit codes:
#   0  all checked gates PASS
#   2  usage error (bad/missing/out-of-range arguments) — no gates evaluated
#   3  §1.4 price-floor breach
#   4  §4.10 notional-sanity mismatch (unit-confusion abort)
#   5  §3.1/§3.2/§3.5 cap breach
#   6  §5 insufficient settled cash
#   7  rules.yml missing, unreadable, or missing a required key — NO gate was
#      evaluated. Never treat as a pass; fix rules.yml and re-run.
#
# CLAUDE.md sections implemented: §1.4, §3.1, §3.2 (incl. prior adds to the
# same contract), §3.5 (incl. options on leveraged ETFs), §4.10 notional
# sanity, §5.
# This script requires bash: it uses BASH_SOURCE to locate its rule library,
# and arrays throughout. Running it under sh/zsh would resolve the library
# relative to the caller's cwd and fail confusingly. Refuse plainly instead.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "pre-order-check: must be run with bash (e.g. 'bash $0'), not $0 under another shell" >&2
  exit 2
fi
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib-rules.sh"
load_rules || exit 7

set -euo pipefail

prog="pre-order-check"

usage_text() {
  cat <<EOF
usage: $prog --instrument equity|option|leveraged_etf --symbol SYM --qty N \\
  --price D --intent-notional D --account-value D --settled-cash D \\
  [--existing-position-value D] [--existing-option-premium D] \\
  [--open-option-premium D] [--leveraged-aggregate D] \\
  [--underlying-price D] [--leveraged-underlying]
Numbers: ≤4 decimal places; price ≤ 99999.9999; amounts ≤ 99999999.99;
qty integer 1..1000000. See the script header for gate semantics.
EOF
}

usage_err() {
  echo "$prog: $1" >&2
  usage_text >&2
  exit 2
}

# ---- exact decimal parsing: dollars string -> 1e-4-scaled integer -------
to_e4() {
  # $1: decimal string already validated by is_number; prints integer e4
  local s="$1" int frac
  int="${s%%.*}"
  if [[ "$s" == *.* ]]; then frac="${s#*.}"; else frac=""; fi
  frac="${frac}0000"; frac="${frac:0:4}"
  # strip leading zeros to avoid octal, handle all-zero
  int=$((10#$int)); frac=$((10#$frac))
  echo $(( int * 10000 + frac ))
}

is_number() {
  # ≤4 decimal places, digits only
  [[ "$1" =~ ^[0-9]{1,8}(\.[0-9]{1,4})?$ ]]
}

fmt_e4() {
  # $1: e4 integer -> exact display: "D.CC" when sub-cent digits are zero,
  # else "D.CCCC"
  local v=$1 sign=""
  [ "$v" -lt 0 ] && { sign="-"; v=$(( -v )); }
  local whole=$(( v / 10000 )) frac=$(( v % 10000 ))
  if [ $(( frac % 100 )) -eq 0 ]; then
    printf "%s%d.%02d" "$sign" "$whole" $(( frac / 100 ))
  else
    printf "%s%d.%04d" "$sign" "$whole" "$frac"
  fi
}

# ---- argument parsing ---------------------------------------------------
instrument=""; symbol=""; qty=""; price=""; intent_notional=""
comp_capital=""; settled_cash=""
existing_position_value="0"; existing_option_premium="0"
open_option_premium="0"; leveraged_aggregate="0"
underlying_price=""; leveraged_underlying=0
seen_flags=" "   # space-delimited membership list (macOS bash 3.2 has no
                 # associative arrays)

need_val() { [ "$#" -ge 2 ] || usage_err "flag $1 requires a value"; }
was_seen() { case "$seen_flags" in *" $1 "*) return 0;; *) return 1;; esac }
once() {
  # $1: flag name — duplicate flags are refused, never silently last-wins
  was_seen "$1" && usage_err "duplicate flag $1"
  seen_flags="$seen_flags$1 "
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --instrument) need_val "$@"; once "$1"; instrument="$2"; shift 2 ;;
    --symbol) need_val "$@"; once "$1"; symbol="$2"; shift 2 ;;
    --qty) need_val "$@"; once "$1"; qty="$2"; shift 2 ;;
    --price) need_val "$@"; once "$1"; price="$2"; shift 2 ;;
    --intent-notional) need_val "$@"; once "$1"; intent_notional="$2"; shift 2 ;;
    # §3 caps are percentages of ACCOUNT VALUE since the 2026-08-31 §9
    # amendment. --account-value is the correct name; --comp-capital is kept
    # as a deprecated alias so no caller breaks, but a flag whose name says
    # "competition capital" while it must receive account value is a trap —
    # passing the old basis would size every cap against a base $900 too
    # small. Both set the same variable; prefer the new name.
    --account-value|--comp-capital) need_val "$@"; once "$1"; comp_capital="$2"; shift 2 ;;
    --settled-cash) need_val "$@"; once "$1"; settled_cash="$2"; shift 2 ;;
    --existing-position-value) need_val "$@"; once "$1"; existing_position_value="$2"; shift 2 ;;
    --existing-option-premium) need_val "$@"; once "$1"; existing_option_premium="$2"; shift 2 ;;
    --open-option-premium) need_val "$@"; once "$1"; open_option_premium="$2"; shift 2 ;;
    --leveraged-aggregate) need_val "$@"; once "$1"; leveraged_aggregate="$2"; shift 2 ;;
    --underlying-price) need_val "$@"; once "$1"; underlying_price="$2"; shift 2 ;;
    --leveraged-underlying) once "$1"; leveraged_underlying=1; shift ;;
    -h|--help) usage_text; exit 0 ;;
    *) usage_err "unrecognized argument '$1'" ;;
  esac
done

case "$instrument" in
  equity|option|leveraged_etf) ;;
  "") usage_err "--instrument is required (equity|option|leveraged_etf)" ;;
  *) usage_err "--instrument must be equity|option|leveraged_etf, got '$instrument'" ;;
esac

[ -n "$symbol" ] || usage_err "--symbol is required"
case "$symbol" in --*) usage_err "--symbol value looks like a flag ('$symbol') — did you drop the ticker?";; esac

[[ "$qty" =~ ^[0-9]{1,7}$ ]] || usage_err "--qty must be an integer 1..1000000, got '${qty:-<missing>}'"
qty=$((10#$qty))   # normalize leading zeros — bash would otherwise read 08 as bad octal
[ "$qty" -ge 1 ] && [ "$qty" -le 1000000 ] || usage_err "--qty must be an integer 1..1000000, got '$qty'"

# Prices are bounded to 5 integer digits so the worst-case product
# qty(1e6) x price_e4(1e9) x 100 = 1e17 stays inside int64 (~9.2e18).
# Account-level amounts keep 8 integer digits (never multiplied by qty).
is_price() { [[ "$1" =~ ^[0-9]{1,5}(\.[0-9]{1,4})?$ ]]; }

is_price "$price" || usage_err "--price must be a non-negative decimal ≤ 99999.9999 (≤5 integer digits, ≤4 decimals), got '$price'"

# intent-notional may legitimately be a product larger than any single
# account amount (qty x price x 100), so it gets a wider bound of its own
[[ "$intent_notional" =~ ^[0-9]{1,13}(\.[0-9]{1,4})?$ ]] || usage_err "--intent-notional must be a non-negative decimal (≤13 integer digits, ≤4 decimals), got '$intent_notional'"

for pair in "comp_capital:--comp-capital" "settled_cash:--settled-cash" "existing_position_value:--existing-position-value" "existing_option_premium:--existing-option-premium" "open_option_premium:--open-option-premium" "leveraged_aggregate:--leveraged-aggregate"; do
  var="${pair%%:*}"; flag="${pair##*:}"
  val="${!var}"
  is_number "$val" || usage_err "$flag must be a non-negative decimal (≤8 integer digits, ≤4 decimals), got '$val'"
done

# ---- instrument/flag legality: wrong-instrument flags are refused, not
# silently discarded (the silent-discard trap is how a §3.5 aggregate
# quietly fails to apply). Requiredness is explicit where a default of 0
# would print an affirmative PASS the operator never asserted.
case "$instrument" in
  option)
    [ -n "$underlying_price" ] || usage_err "--underlying-price is required when --instrument option (§1.4 floor applies to the underlying)"
    is_price "$underlying_price" || usage_err "--underlying-price must be a non-negative decimal ≤ 99999.9999, got '$underlying_price'"
    was_seen --existing-position-value && usage_err "--existing-position-value is stock/ETF-only; for prior adds to this contract use --existing-option-premium"
    if [ "$leveraged_underlying" -eq 1 ]; then
      was_seen --leveraged-aggregate || usage_err "--leveraged-underlying requires an explicit --leveraged-aggregate (type 0 if the book holds none) — a defaulted 0 must never print as an asserted §3.5 PASS"
    else
      was_seen --leveraged-aggregate && usage_err "--leveraged-aggregate on an option requires --leveraged-underlying (or did you mean --instrument leveraged_etf?)"
    fi
    ;;
  equity)
    for f in --underlying-price --existing-option-premium --open-option-premium --leveraged-aggregate --leveraged-underlying; do
      was_seen "$f" && usage_err "$f is not valid with --instrument equity"
    done
    ;;
  leveraged_etf)
    for f in --underlying-price --existing-option-premium --open-option-premium --leveraged-underlying; do
      was_seen "$f" && usage_err "$f is not valid with --instrument leveraged_etf"
    done
    was_seen --leveraged-aggregate || usage_err "--leveraged-aggregate is required for --instrument leveraged_etf (type 0 if the book holds none) — the §3.5 aggregate must be asserted, not defaulted"
    ;;
esac

multiplier=1
[ "$instrument" = "option" ] && multiplier=100

# ---- exact conversions ---------------------------------------------------
price_e4="$(to_e4 "$price")"
intent_e4="$(to_e4 "$intent_notional")"
comp_e4="$(to_e4 "$comp_capital")"
settled_e4="$(to_e4 "$settled_cash")"
existing_e4="$(to_e4 "$existing_position_value")"
existing_prem_e4="$(to_e4 "$existing_option_premium")"
open_prem_e4="$(to_e4 "$open_option_premium")"
lev_agg_e4="$(to_e4 "$leveraged_aggregate")"

# exact product at full precision (bounded: 1e6 * 1e9 * 100 < 2^63)
notional_e4=$(( qty * price_e4 * multiplier ))
order_value_e4=$(( qty * price_e4 ))          # stock/ETF dollars; option: per-1-share dollars
premium_e4=$(( qty * price_e4 * 100 ))        # option premium dollars (e4)

# Floored percentage caps (never rounded up across a limit).
# Percentages come from rules.yml — never hard-coded here. One variable per
# RULE, never shared: §3.2's aggregate and §3.5's leveraged aggregate were
# both 20% before 2026-08-17 and shared a variable, so raising §3.2 to 30%
# could not be expressed and silently missed this file.
pct_pos="$(rule_get manual_single_position_pct)"          || exit 7
pct_opt_single="$(rule_get manual_option_single_position_pct)" || exit 7
pct_opt_agg="$(rule_get manual_option_open_premium_pct)"  || exit 7
pct_lev="$(rule_get manual_leveraged_aggregate_pct)"      || exit 7

cap_pos_e4=$((       comp_e4 * pct_pos        / 100 ))   # §3.1 single position
cap_opt_single_e4=$((comp_e4 * pct_opt_single / 100 ))   # §3.2 per option position
cap_opt_agg_e4=$((   comp_e4 * pct_opt_agg    / 100 ))   # §3.2 total open premium
cap_lev_e4=$((       comp_e4 * pct_lev        / 100 ))   # §3.5 leveraged aggregate

pass_lines=()

not_checked_block() {
  cat <<'EOF'
NOT-CHECKED (remain the operator's §4.9/§4.10 duty — this script does not evaluate these):
  - earnings date (§3.7)
  - corporate actions (§3.7)
  - correlation vs book (§3.8)
  - halt status (§3.7)
  - drawdown level (§3.6)
  - option quality floors: OI/spread/DTE/delta (§3.2)
  - order-rate ceilings (§4.10)
  - quote freshness (§4.10)
  - reserve invariant (header rule: total position exposure ≤ 100% of
    account value minus the $900 settlement buffer — settled cash includes
    that buffer, so the §5 check passing does NOT establish this)
EOF
}

fail() {
  echo "$2" >&2
  not_checked_block >&2
  exit "$1"
}

# ---- §1.4: price floor (exact, unrounded) --------------------------------
min_price="$(rule_get manual_min_share_price_usd)" || exit 7
floor_e4="$(to_e4 "$min_price")"
if [ "$instrument" = "option" ]; then
  under_e4="$(to_e4 "$underlying_price")"
  if [ "$under_e4" -lt "$floor_e4" ]; then
    fail 3 "§1.4 FAIL: underlying price $underlying_price < $min_price floor — $symbol underlying disqualified (penny/OTC floor; compared exactly, no rounding)"
  fi
  pass_lines+=("§1.4: underlying price $underlying_price ≥ $min_price PASS")
else
  if [ "$price_e4" -lt "$floor_e4" ]; then
    fail 3 "§1.4 FAIL: price $price < $min_price floor — $symbol disqualified (penny/OTC floor; compared exactly, no rounding)"
  fi
  pass_lines+=("§1.4: price $price ≥ $min_price PASS")
fi

# ---- §4.10: notional sanity (exact product, $0.01 tolerance) -------------
diff_e4=$(( notional_e4 - intent_e4 ))
[ "$diff_e4" -lt 0 ] && diff_e4=$(( -diff_e4 ))
if [ "$diff_e4" -gt 100 ]; then
  fail 4 "§4.10 FAIL (notional sanity / unit-confusion abort): $qty x $price x $multiplier = $(fmt_e4 "$notional_e4") != intent $(fmt_e4 "$intent_e4") (diff $(fmt_e4 "$diff_e4") > \$0.01)"
fi
pass_lines+=("§4.10 notional: $qty x $price x $multiplier = $(fmt_e4 "$notional_e4") (intent $(fmt_e4 "$intent_e4")) PASS")

# ---- §3.1 / §3.2 / §3.5: caps (exact totals vs floored caps) -------------
# Defaulted (never asserted) inputs are labeled so a PASS line cannot read
# as an operator assertion that was never made.
existing_tag=""; was_seen --existing-position-value || existing_tag=" (defaulted 0 — assert with --existing-position-value if $symbol is already held)"
open_prem_tag=""; was_seen --open-option-premium || open_prem_tag=" (defaulted 0 — assert with --open-option-premium if any option is open)"

case "$instrument" in
  equity)
    total_e4=$(( existing_e4 + order_value_e4 ))
    if [ "$total_e4" -gt "$cap_pos_e4" ]; then
      fail 5 "§3.1 FAIL: $(fmt_e4 "$existing_e4") + $(fmt_e4 "$order_value_e4") = $(fmt_e4 "$total_e4") > $(fmt_e4 "$cap_pos_e4") (${pct_pos}% of comp-capital $(fmt_e4 "$comp_e4"), floored)"
    fi
    pass_lines+=("§3.1: $(fmt_e4 "$existing_e4")$existing_tag + $(fmt_e4 "$order_value_e4") = $(fmt_e4 "$total_e4") ≤ $(fmt_e4 "$cap_pos_e4") PASS")
    ;;
  leveraged_etf)
    total31_e4=$(( existing_e4 + order_value_e4 ))
    if [ "$total31_e4" -gt "$cap_pos_e4" ]; then
      fail 5 "§3.1 FAIL: $(fmt_e4 "$existing_e4") + $(fmt_e4 "$order_value_e4") = $(fmt_e4 "$total31_e4") > $(fmt_e4 "$cap_pos_e4") (${pct_pos}% of comp-capital $(fmt_e4 "$comp_e4"), floored)"
    fi
    pass_lines+=("§3.1: $(fmt_e4 "$existing_e4") + $(fmt_e4 "$order_value_e4") = $(fmt_e4 "$total31_e4") ≤ $(fmt_e4 "$cap_pos_e4") PASS")

    total35_e4=$(( lev_agg_e4 + order_value_e4 ))
    if [ "$total35_e4" -gt "$cap_lev_e4" ]; then
      fail 5 "§3.5 FAIL: $(fmt_e4 "$lev_agg_e4") + $(fmt_e4 "$order_value_e4") = $(fmt_e4 "$total35_e4") > $(fmt_e4 "$cap_lev_e4") (${pct_lev}% of comp-capital, leveraged/inverse aggregate, floored)"
    fi
    pass_lines+=("§3.5: $(fmt_e4 "$lev_agg_e4") + $(fmt_e4 "$order_value_e4") = $(fmt_e4 "$total35_e4") ≤ $(fmt_e4 "$cap_lev_e4") PASS")
    ;;
  option)
    total_single_e4=$(( existing_prem_e4 + premium_e4 ))
    if [ "$total_single_e4" -gt "$cap_opt_single_e4" ]; then
      fail 5 "§3.2 FAIL (single position, incl. prior adds to this contract): $(fmt_e4 "$existing_prem_e4") + $(fmt_e4 "$premium_e4") = $(fmt_e4 "$total_single_e4") > $(fmt_e4 "$cap_opt_single_e4") (${pct_opt_single}% of comp-capital $(fmt_e4 "$comp_e4"), floored)"
    fi
    pass_lines+=("§3.2 single: $(fmt_e4 "$existing_prem_e4") + $(fmt_e4 "$premium_e4") = $(fmt_e4 "$total_single_e4") ≤ $(fmt_e4 "$cap_opt_single_e4") PASS")

    total_agg_e4=$(( open_prem_e4 + premium_e4 ))
    if [ "$total_agg_e4" -gt "$cap_opt_agg_e4" ]; then
      fail 5 "§3.2 FAIL (aggregate): $(fmt_e4 "$open_prem_e4") + $(fmt_e4 "$premium_e4") = $(fmt_e4 "$total_agg_e4") > $(fmt_e4 "$cap_opt_agg_e4") (${pct_opt_agg}% of comp-capital $(fmt_e4 "$comp_e4"), floored)"
    fi
    pass_lines+=("§3.2 aggregate: $(fmt_e4 "$open_prem_e4")$open_prem_tag + $(fmt_e4 "$premium_e4") = $(fmt_e4 "$total_agg_e4") ≤ $(fmt_e4 "$cap_opt_agg_e4") PASS")

    if [ "$leveraged_underlying" -eq 1 ]; then
      total_lev_e4=$(( lev_agg_e4 + premium_e4 ))
      if [ "$total_lev_e4" -gt "$cap_lev_e4" ]; then
        fail 5 "§3.5 FAIL (option on leveraged ETF — §3.5 last bullet): $(fmt_e4 "$lev_agg_e4") + $(fmt_e4 "$premium_e4") = $(fmt_e4 "$total_lev_e4") > $(fmt_e4 "$cap_lev_e4") (${pct_lev}% of comp-capital, leveraged/inverse aggregate, floored)"
      fi
      pass_lines+=("§3.5 (option on leveraged ETF): $(fmt_e4 "$lev_agg_e4") + $(fmt_e4 "$premium_e4") = $(fmt_e4 "$total_lev_e4") ≤ $(fmt_e4 "$cap_lev_e4") PASS")
    fi
    ;;
esac

# ---- §5: settled cash (exact) --------------------------------------------
if [ "$notional_e4" -gt "$settled_e4" ]; then
  fail 6 "§5 FAIL: notional $(fmt_e4 "$notional_e4") > settled cash $(fmt_e4 "$settled_e4") — unsettled funds cannot be used"
fi
pass_lines+=("§5: notional $(fmt_e4 "$notional_e4") ≤ settled cash $(fmt_e4 "$settled_e4") PASS")

# ---- all gates passed ------------------------------------------------------
for l in "${pass_lines[@]}"; do
  echo "$l"
done
not_checked_block
echo "$prog: ALL CHECKED GATES PASS for $symbol ($instrument, qty $qty @ $price)"
exit 0
