#!/bin/bash
# universe-filter.sh — turn saved verbose get_quotes payloads into a ranked
# working universe. The agent never reads a quote; it passes file paths here.
#
# Usage: scripts/universe-filter.sh --payload FILE [--payload FILE ...] --out TSV
# Exit: 0 ok · 2 usage · 7 rules.yml missing/unreadable/incomplete
#
# python3 is used for parsing (research path only). lib-rules.sh and
# pre-order-check.sh stay awk-only because they sit in the ORDER path.
#
# OUTPUT — ten tab-separated columns:
#   symbol  price  adv10  dollar_vol  pct_from_52wk_high  optionable
#   leverage  last_earnings  is_etf  session_range_pct
#
#   price     the REGULAR-SESSION close (`regular.regularMarketLastPrice`),
#             falling back to the consolidated `quote.lastPrice`. Never the
#             `extended.lastPrice` after-hours tick -- see the sub-block note
#             in the python block below.
#   leverage  the leverage MULTIPLE, not the raw payload field. Schwab's
#             `fundLeverageFactor` is a PERCENTAGE (0 single stock, 100.0 a
#             1x fund, 300.0 a 3x fund, -100.0 an inverse fund); this column
#             is that value / 100, because §3.5 reasoning reads in multiples.
#             Under --qualified-only only raw 0 and raw 100 survive, so the
#             column is 0.0 or 1.0 in a qualified run.
#   session_range_pct
#             the latest session's (high - low) as a percentage of price, or
#             `-` when the payload carries no usable high/low. It is the
#             takeover-stub gate's own working, published so a name that
#             survives just over the threshold is visible in the universe
#             file instead of only in hindsight.
set -uo pipefail
if [ -z "${BASH_VERSION:-}" ]; then
  echo "universe-filter: must be run with bash" >&2; exit 2
fi
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.." || exit 2
. scripts/lib-rules.sh
load_rules || exit 7

payloads=(); out=""; qualified=0; ranktop=0
while [ $# -gt 0 ]; do
  case "$1" in
    --payload) shift; [ $# -gt 0 ] || { echo "universe-filter: --payload needs a path" >&2; exit 2; }; payloads+=("$1") ;;
    --out)     shift; [ $# -gt 0 ] || { echo "universe-filter: --out needs a path" >&2; exit 2; }; out="$1" ;;
    --qualified-only) qualified=1 ;;
    --rank-top) shift; [ $# -gt 0 ] || { echo "universe-filter: --rank-top needs a count" >&2; exit 2; }; ranktop="$1" ;;
    *) echo "universe-filter: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ "${#payloads[@]}" -gt 0 ] || { echo "universe-filter: at least one --payload is required" >&2; exit 2; }
[ -n "$out" ] || { echo "universe-filter: --out is required" >&2; exit 2; }
for p in "${payloads[@]}"; do
  [ -r "$p" ] || { echo "universe-filter: cannot read payload: $p" >&2; exit 2; }
done

MIN_PRICE="$(rule_get manual_min_share_price_usd)"      || exit 7
MIN_DOLLAR="$(rule_get manual_min_avg_daily_dollar_volume)" || exit 7
MIN_SHARES="$(rule_get manual_min_avg_daily_volume)"    || exit 7
MIN_RANGE="$(rule_get strategy_min_session_range_pct)"  || exit 7

python3 - "$out" "$MIN_PRICE" "$MIN_DOLLAR" "$MIN_SHARES" "$qualified" "$ranktop" "$MIN_RANGE" "${payloads[@]}" <<'PY'
import json, re, sys

out, min_price, min_dollar, min_shares = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
qualified = sys.argv[5] == "1"
ranktop   = int(sys.argv[6])
min_range = float(sys.argv[7])
paths     = sys.argv[8:]

# ---------------------------------------------------------------------------
# Sub-block-anchored field access.
#
# A verbose get_quotes symbol block is 2-space-indented YAML-ish text with
# several named sub-blocks, emitted in this order:
#
#   SYM:
#     assetMainType/assetSubType/symbol/...   <- top level, scalars
#     extended:      <- PRE/POST-market print
#     fundamental:   <- avg10DaysVolume, fundLeverageFactor, lastEarningsDate
#     quote:         <- 52WeekHigh, netPercentChange, lastPrice (consolidated)
#     reference:     <- optionable, description, exchange
#     regular:       <- regularMarketLastPrice: the regular-session close
#
# Several keys appear in MORE THAN ONE sub-block -- `lastPrice` lives in both
# `extended` and `quote`, and `extended` is emitted FIRST. A whole-body regex
# therefore returns the after-hours print, which is what this parser used to
# do for every gate, dollar_vol, and the 52-week rank key. Never search the
# whole body; always name the sub-block.
BLOCK_HEADER = re.compile(r'^  ([A-Za-z][A-Za-z0-9_]*):\s*$')
TOP_LEVEL_KEY = re.compile(r'^  \S')

def split_blocks(body):
    """Split a symbol block into {sub-block name: text}; '' is the top level."""
    blocks = {'': []}
    cur = ''
    for line in body.split('\n'):
        m = BLOCK_HEADER.match(line)
        if m:
            cur = m.group(1)
            blocks.setdefault(cur, [])
            continue
        if TOP_LEVEL_KEY.match(line):
            cur = ''
        blocks[cur].append(line)
    return {k: '\n'.join(v) for k, v in blocks.items()}

def _num(text, key):
    m = re.search(r'"?%s"?:\s*"?(-?[\d.]+)"?' % re.escape(key), text)
    return float(m.group(1)) if m else None

def _word(text, key):
    m = re.search(r'"?%s"?:\s*"?([^"\n]*)"?' % re.escape(key), text)
    return m.group(1).strip() if m else None

def num(blocks, where, key, default=None):
    """Numeric field from a named sub-block. `where` may be a tuple: first hit
    wins, so a fallback chain is explicit rather than an accident of ordering."""
    for w in ((where,) if isinstance(where, str) else where):
        v = _num(blocks.get(w, ''), key)
        if v is not None:
            return v
    return default

def word(blocks, where, key, default=""):
    for w in ((where,) if isinstance(where, str) else where):
        v = _word(blocks.get(w, ''), key)
        if v is not None:
            return v
    return default

rows = []
skipped = []
stubs = []      # rejected by the takeover-stub gate
nodata = []     # no usable high/low: gate could not be evaluated, kept
for path in paths:
    raw = json.load(open(path))["result"]
    # a new symbol block starts at column 0 as "SYM:"
    for m in re.finditer(r'(?m)^([A-Z][A-Z0-9/.\-]{0,7}):\n(.*?)(?=^\S|\Z)', raw, re.S):
        sym, body = m.group(1), m.group(2)
        b = split_blocks(body)
        # PRICE: the regular-session close, falling back to the consolidated
        # `quote:` print when a symbol carries no `regular:` block. This is a
        # weekend screen -- the regular close is the correct and stable
        # screening price; `extended.lastPrice` is a thin after-hours tick.
        price = num(b, 'regular', 'regularMarketLastPrice')
        if price is None:
            price = num(b, 'quote', 'lastPrice')
        adv   = num(b, 'fundamental', 'avg10DaysVolume', 0.0)
        if price is None:
            skipped.append(sym)           # unquotable symbol: skip, never stall,
            continue                      # but never silently — logged below
        # fundLeverageFactor is a PERCENTAGE, not a multiple: 0 = single stock,
        # 100 = a 1x fund, 200/300 = leveraged, negative = inverse. Store the
        # MULTIPLE (raw / 100) because §3.5 reasoning is expressed in multiples.
        lev_raw = num(b, 'fundamental', 'fundLeverageFactor', 0.0) or 0.0
        # Latest session range as a percentage of price. `None` means the
        # payload carried no usable high/low — not "zero range" — and is the
        # no-data case the stub gate below must not treat as pinned.
        hi = num(b, 'quote', 'highPrice')
        lo = num(b, 'quote', 'lowPrice')
        if hi is None or lo is None or hi <= 0.0 or lo <= 0.0 or price <= 0.0:
            rng = None
        else:
            rng = (hi - lo) / price * 100.0
        if qualified:
            if price < min_price:                    continue   # §1.4 price floor
            if (adv or 0.0) * price < min_dollar:    continue   # §1.4 dollar volume
            if (adv or 0.0) < min_shares:            continue   # §1.4 share sanity floor
            # §3.5 gated shut by default. Reject anything that is NOT a single
            # stock (0) or a 1x fund (100) -- i.e. every leveraged and inverse
            # fund. Rejecting on `!= 0` instead would discard every ETF, which
            # is roughly half the fetched directory.
            if lev_raw not in (0.0, 100.0):          continue
            # TAKEOVER-STUB GATE. An announced all-cash deal target trades
            # pinned a hair under its deal price. That price is by
            # construction its 52-week high, so the proximity tilt this
            # universe ranks on acts as a merger detector: on 2026-08-19,
            # nine of fifteen shortlisted names were deal stubs. A pinned
            # name has no daily range, so the range is the discriminator.
            #
            # ORDER MATTERS: the no-data branch runs FIRST. A payload with
            # no usable high/low reads as range 0.00% — maximally pinned —
            # and would be rejected on the strength of a missing field. The
            # test is `high or low absent or <= 0`, NOT the narrower
            # `range == 0 AND volume == 0` this rule was first specified
            # with: SPY in the live fixture carries high = low = 0 with
            # 34.4M shares of session volume, so the volume conjunction
            # would have false-rejected the most liquid ETF in the market.
            # A genuine stub still prints a real high and a real low that
            # happen to sit close together; it never prints zeros.
            if rng is not None and rng < min_range:
                stubs.append(sym)
                continue
        if rng is None:
            nodata.append(sym)
        rows.append({
            'symbol': sym,
            'price': price,
            'adv10': adv or 0.0,
            'dollar_vol': (adv or 0.0) * price,
            'high52': num(b, 'quote', '52WeekHigh', 0.0) or 0.0,
            'pct_chg': num(b, 'quote', 'netPercentChange', 0.0) or 0.0,
            'optionable': 'true' if word(b, 'reference', 'optionable') == 'true' else 'false',
            'leverage': lev_raw / 100.0,
            'last_earnings': (word(b, 'fundamental', 'lastEarningsDate') or '')[:10],
            'is_etf': 'true' if word(b, '', 'assetSubType') == 'ETF' else 'false',
            'range_pct': rng,
        })

for r in rows:
    r['gap'] = ((r['high52'] - r['price']) / r['high52'] * 100.0) if r['high52'] else 999.0

total = len(rows)
rows.sort(key=lambda r: (r['gap'], -r['dollar_vol']))
dropped = 0
if ranktop and total > ranktop:
    dropped = total - ranktop
    rows = rows[:ranktop]

with open(out, 'w') as f:
    f.write("symbol\tprice\tadv10\tdollar_vol\tpct_from_52wk_high\toptionable\tleverage\tlast_earnings\tis_etf\tsession_range_pct\n")
    for r in rows:
        f.write("%s\t%.4f\t%.0f\t%.0f\t%.2f\t%s\t%.1f\t%s\t%s\t%s\n" % (
            r['symbol'], r['price'], r['adv10'], r['dollar_vol'], r['gap'],
            r['optionable'], r['leverage'], r['last_earnings'], r['is_etf'],
            '-' if r['range_pct'] is None else '%.2f' % r['range_pct']))
if skipped:
    shown = skipped[:20]
    more = len(skipped) - len(shown)
    suffix = " (+%d more)" % more if more > 0 else ""
    print("universe-filter: skipped %d unquotable symbol(s): %s%s" % (
        len(skipped), ", ".join(shown), suffix), file=sys.stderr)
def _name_list(names, n=20):
    shown = names[:n]
    more = len(names) - len(shown)
    return ", ".join(shown) + (" (+%d more)" % more if more > 0 else "")

if qualified and stubs:
    print("universe-filter: stub-filtered %d symbol(s) under %.2f%% session range: %s" % (
        len(stubs), min_range, _name_list(stubs)), file=sys.stderr)
if nodata:
    print("universe-filter: %d symbol(s) had no usable high/low; stub gate not applied: %s" % (
        len(nodata), _name_list(nodata)), file=sys.stderr)
print("universe-filter: parsed %d symbols from %d payload(s)" % (total, len(paths)), file=sys.stderr)
if ranktop:
    print("universe-filter: ranked %d of %d (dropped %d)" % (len(rows), total, dropped), file=sys.stderr)
PY
