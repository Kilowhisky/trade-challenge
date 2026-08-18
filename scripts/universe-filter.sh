#!/bin/bash
# universe-filter.sh — turn saved verbose get_quotes payloads into a ranked
# working universe. The agent never reads a quote; it passes file paths here.
#
# Usage: scripts/universe-filter.sh --payload FILE [--payload FILE ...] --out TSV
# Exit: 0 ok · 2 usage · 7 rules.yml missing/unreadable/incomplete
#
# python3 is used for parsing (research path only). lib-rules.sh and
# pre-order-check.sh stay awk-only because they sit in the ORDER path.
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

python3 - "$out" "$MIN_PRICE" "$MIN_DOLLAR" "$MIN_SHARES" "$qualified" "$ranktop" "${payloads[@]}" <<'PY'
import json, re, sys

out, min_price, min_dollar, min_shares = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
qualified = sys.argv[5] == "1"
ranktop   = int(sys.argv[6])
paths     = sys.argv[7:]

def num(block, key, default=None):
    m = re.search(r'"?%s"?:\s*"?(-?[\d.]+)"?' % re.escape(key), block)
    return float(m.group(1)) if m else default

def word(block, key, default=""):
    m = re.search(r'"?%s"?:\s*"?([^"\n]*)"?' % re.escape(key), block)
    return (m.group(1).strip() if m else default)

rows = []
skipped = []
for path in paths:
    raw = json.load(open(path))["result"]
    # a new symbol block starts at column 0 as "SYM:"
    for m in re.finditer(r'(?m)^([A-Z][A-Z0-9/.\-]{0,7}):\n(.*?)(?=^\S|\Z)', raw, re.S):
        sym, body = m.group(1), m.group(2)
        price = num(body, 'lastPrice')
        adv   = num(body, 'avg10DaysVolume', 0.0)
        if price is None:
            skipped.append(sym)           # unquotable symbol: skip, never stall,
            continue                      # but never silently — logged below
        if qualified:
            if price < min_price:                    continue   # §1.4 price floor
            if (adv or 0.0) * price < min_dollar:    continue   # §1.4 dollar volume
            if (adv or 0.0) < min_shares:            continue   # §1.4 share sanity floor
            lev = num(body, 'fundLeverageFactor', 0.0) or 0.0
            if lev != 0.0:                           continue   # §3.5 gated shut by default
        rows.append({
            'symbol': sym,
            'price': price,
            'adv10': adv or 0.0,
            'dollar_vol': (adv or 0.0) * price,
            'high52': num(body, '52WeekHigh', 0.0) or 0.0,
            'pct_chg': num(body, 'netPercentChange', 0.0) or 0.0,
            'optionable': 'true' if word(body, 'optionable') == 'true' else 'false',
            'leverage': num(body, 'fundLeverageFactor', 0.0) or 0.0,
            'last_earnings': (word(body, 'lastEarningsDate') or '')[:10],
            'is_etf': 'true' if word(body, 'assetSubType') == 'ETF' else 'false',
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
    f.write("symbol\tprice\tadv10\tdollar_vol\tpct_from_52wk_high\toptionable\tleverage\tlast_earnings\tis_etf\n")
    for r in rows:
        f.write("%s\t%.4f\t%.0f\t%.0f\t%.2f\t%s\t%.1f\t%s\t%s\n" % (
            r['symbol'], r['price'], r['adv10'], r['dollar_vol'], r['gap'],
            r['optionable'], r['leverage'], r['last_earnings'], r['is_etf']))
if skipped:
    shown = skipped[:20]
    more = len(skipped) - len(shown)
    suffix = " (+%d more)" % more if more > 0 else ""
    print("universe-filter: skipped %d unquotable symbol(s): %s%s" % (
        len(skipped), ", ".join(shown), suffix), file=sys.stderr)
print("universe-filter: parsed %d symbols from %d payload(s)" % (total, len(paths)), file=sys.stderr)
if ranktop:
    print("universe-filter: ranked %d of %d (dropped %d)" % (len(rows), total, dropped), file=sys.stderr)
PY
