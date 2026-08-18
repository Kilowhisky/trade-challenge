# Weekly Universe Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace an 11-day, 543-name research sweep with a weekly whole-market
pass (11,227 symbols) that regenerates a working universe the daily run sweeps
in full.

**Architecture:** A new read-only `weekly-universe` agent fetches the free
Nasdaq Trader directory, sweeps it with batched `get_quotes verbose=True`
(~45 calls, ~19 MB), and hands every payload to a local filter script — no
quote ever enters agent context. The filter applies §1.4 gates from
`rules.yml` and emits a ranked working universe. The existing daily
deep-research run then sweeps that universe in full, and its 50-symbol
chunking and resume-cursor machinery are deleted.

**Tech Stack:** bash + python3 (parsing), `scripts/lib-rules.sh` for
parameters, `scripts/research-replace.sh universe` for the validated write,
Schwab MCP `get_quotes`.

**Spec:** `docs/superpowers/specs/2026-08-17-weekly-universe-agent-design.md`

## Global Constraints

- **No rule numbers are hard-coded.** Every threshold comes from `rules.yml`
  via `scripts/lib-rules.sh`. `scripts/check-consistency.sh` fails the build
  otherwise. (CLAUDE.md header, *Percentages are canonical*.)
- **The working universe is never a source for order parameters.** Every
  candidate re-verifies live under §4.9/§4.10.
- **The weekly agent is read-only by construction** — no order tools, no
  account tools, no Write/Edit. All writes go through whitelisted scripts.
- **Fail loud, never silent.** A partial sweep records what it missed; an
  unreadable `rules.yml` aborts. A silently truncated universe is a defect.
- `research/universe.md` must keep first line `# Fallback universe` and the
  banner text `never a source for order parameters` verbatim —
  `research-replace.sh` enforces both.
- python3 is acceptable here (research path). It is *not* acceptable in
  `lib-rules.sh` or `pre-order-check.sh`, which stay awk-only because they sit
  in the order path.

---

### Task 1: Verbose payload parser

**Files:**
- Create: `scripts/universe-filter.sh`
- Create: `tests/fixtures/verbose-sample.txt`
- Create: `scripts/test-universe-filter.sh`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `scripts/universe-filter.sh --payload FILE [--payload FILE ...]
  --out TSV`, exit 0 on success, 2 on usage error, 7 on rules-load failure.
  Emits TSV with header
  `symbol	price	adv10	dollar_vol	pct_from_52wk_high	optionable	leverage	last_earnings	is_etf`

- [ ] **Step 1: Create the test fixture**

The MCP returns `{"result": "<text>"}`. This fixture is three real symbols
trimmed to the fields the filter reads. Create `tests/fixtures/verbose-sample.txt`:

```json
{"result":"CSX:\n  assetMainType: EQUITY\n  assetSubType: COE\n  symbol: CSX\n  fundamental:\n    avg10DaysVolume: 14147918.0\n    fundLeverageFactor: 0\n    lastEarningsDate: \"2026-07-22T00:00:00Z\"\n  quote:\n    \"52WeekHigh\": 53.6\n    \"52WeekLow\": 31.8\n    lastPrice: 50.89\n    netPercentChange: 1.4553429\n    totalVolume: 7751492\n  reference:\n    exchange: Q\n    optionable: true\nPENNYCO:\n  assetMainType: EQUITY\n  assetSubType: COE\n  symbol: PENNYCO\n  fundamental:\n    avg10DaysVolume: 9000000.0\n    fundLeverageFactor: 0\n    lastEarningsDate: \"2026-06-01T00:00:00Z\"\n  quote:\n    \"52WeekHigh\": 9.0\n    \"52WeekLow\": 1.0\n    lastPrice: 4.50\n    netPercentChange: 0.5\n    totalVolume: 9000000\n  reference:\n    exchange: Q\n    optionable: false\nTQQQ:\n  assetMainType: EQUITY\n  assetSubType: ETF\n  symbol: TQQQ\n  fundamental:\n    avg10DaysVolume: 50000000.0\n    fundLeverageFactor: 3\n    lastEarningsDate: \"\"\n  quote:\n    \"52WeekHigh\": 100.0\n    \"52WeekLow\": 40.0\n    lastPrice: 95.0\n    netPercentChange: 2.0\n    totalVolume: 50000000\n  reference:\n    exchange: Q\n    optionable: true\n"}
```

- [ ] **Step 2: Write the failing test**

Create `scripts/test-universe-filter.sh`:

```bash
#!/bin/bash
# Regression suite for scripts/universe-filter.sh
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.." || exit 2
F=scripts/universe-filter.sh
FIX=tests/fixtures/verbose-sample.txt
pass=0; fail=0
ok()  { pass=$((pass+1)); printf '  ok   %s\n' "$1"; }
no()  { fail=$((fail+1)); printf 'FAIL  %s\n       %s\n' "$1" "${2:-}"; }

OUT=$(mktemp); trap 'rm -f "$OUT"' EXIT

echo "== parsing =="
bash "$F" --payload "$FIX" --out "$OUT" >/dev/null 2>&1
n=$(tail -n +2 "$OUT" | wc -l | tr -d ' ')
[ "$n" -ge 1 ] && ok "produces rows from a verbose payload" || no "produces rows from a verbose payload" "got $n rows"

grep -q '^symbol	price	adv10' "$OUT" && ok "emits the TSV header" || no "emits the TSV header"

echo
echo "-------------------------------------------"
printf '%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || exit 1
```

- [ ] **Step 3: Run it to verify it fails**

Run: `bash scripts/test-universe-filter.sh`
Expected: FAIL — `scripts/universe-filter.sh` does not exist yet.

- [ ] **Step 4: Write the minimal implementation**

Create `scripts/universe-filter.sh`:

```bash
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

payloads=(); out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --payload) shift; [ $# -gt 0 ] || { echo "universe-filter: --payload needs a path" >&2; exit 2; }; payloads+=("$1") ;;
    --out)     shift; [ $# -gt 0 ] || { echo "universe-filter: --out needs a path" >&2; exit 2; }; out="$1" ;;
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

python3 - "$out" "$MIN_PRICE" "$MIN_DOLLAR" "$MIN_SHARES" "${payloads[@]}" <<'PY'
import json, re, sys

out, min_price, min_dollar, min_shares = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
paths = sys.argv[5:]

def num(block, key, default=None):
    m = re.search(r'"?%s"?:\s*"?(-?[\d.]+)"?' % re.escape(key), block)
    return float(m.group(1)) if m else default

def word(block, key, default=""):
    m = re.search(r'"?%s"?:\s*"?([^"\n]*)"?' % re.escape(key), block)
    return (m.group(1).strip() if m else default)

rows = []
for path in paths:
    raw = json.load(open(path))["result"]
    # a new symbol block starts at column 0 as "SYM:"
    for m in re.finditer(r'(?m)^([A-Z][A-Z0-9/.\-]{0,7}):\n(.*?)(?=^\S|\Z)', raw, re.S):
        sym, body = m.group(1), m.group(2)
        price = num(body, 'lastPrice')
        adv   = num(body, 'avg10DaysVolume', 0.0)
        if price is None:
            continue                      # unquotable symbol: skip, never stall
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

with open(out, 'w') as f:
    f.write("symbol\tprice\tadv10\tdollar_vol\tpct_from_52wk_high\toptionable\tleverage\tlast_earnings\tis_etf\n")
    for r in rows:
        gap = ((r['high52'] - r['price']) / r['high52'] * 100.0) if r['high52'] else 999.0
        f.write("%s\t%.4f\t%.0f\t%.0f\t%.2f\t%s\t%.1f\t%s\t%s\n" % (
            r['symbol'], r['price'], r['adv10'], r['dollar_vol'], gap,
            r['optionable'], r['leverage'], r['last_earnings'], r['is_etf']))
print("universe-filter: parsed %d symbols from %d payload(s)" % (len(rows), len(paths)), file=sys.stderr)
PY
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `chmod +x scripts/universe-filter.sh scripts/test-universe-filter.sh && bash scripts/test-universe-filter.sh`
Expected: `2 passed, 0 failed`

- [ ] **Step 6: Commit**

```bash
git add scripts/universe-filter.sh scripts/test-universe-filter.sh tests/fixtures/verbose-sample.txt
git commit -m "Add verbose quote payload parser for the weekly universe sweep"
```

---

### Task 2: Qualification gates

**Files:**
- Modify: `scripts/universe-filter.sh` (add `--qualified-only` and gate logic)
- Modify: `scripts/test-universe-filter.sh` (add gate tests)

**Interfaces:**
- Consumes: Task 1's TSV writer and rule variables `MIN_PRICE`, `MIN_DOLLAR`, `MIN_SHARES`
- Produces: `--qualified-only` flag. With it, rows failing any §1.4 gate or
  carrying `leverage != 0` are dropped. Column set is unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/test-universe-filter.sh` before the summary block:

```bash
echo "== §1.4 gates (--qualified-only) =="
bash "$F" --payload "$FIX" --out "$OUT" --qualified-only >/dev/null 2>&1
cut -f1 "$OUT" | tail -n +2 > /tmp/uf_syms.$$
grep -qx 'CSX' /tmp/uf_syms.$$ && ok "CSX passes every gate" || no "CSX passes every gate"
grep -qx 'PENNYCO' /tmp/uf_syms.$$ && no "sub-\$5 name is dropped" "PENNYCO survived" || ok "sub-\$5 name is dropped"
grep -qx 'TQQQ' /tmp/uf_syms.$$ && no "leveraged ETF is dropped (§3.5)" "TQQQ survived" || ok "leveraged ETF is dropped (§3.5)"
rm -f /tmp/uf_syms.$$

echo "== optionable is recorded, never required =="
bash "$F" --payload "$FIX" --out "$OUT" >/dev/null 2>&1
awk -F'\t' '$1=="PENNYCO" && $6=="false"' "$OUT" | grep -q . \
  && ok "non-optionable name is kept and flagged in the unfiltered pass" \
  || no "non-optionable name is kept and flagged in the unfiltered pass"
```

- [ ] **Step 2: Run to verify the gate tests fail**

Run: `bash scripts/test-universe-filter.sh`
Expected: the three `--qualified-only` assertions FAIL (the flag is rejected as
an unknown argument, so `$OUT` keeps its previous contents).

- [ ] **Step 3: Implement the gates**

In `scripts/universe-filter.sh`, add to the argument loop before the `*)` case:

```bash
    --qualified-only) qualified=1 ;;
```

Add `qualified=0` beside the `payloads=(); out=""` initialisation, and pass it
to python by changing the invocation line to:

```bash
python3 - "$out" "$MIN_PRICE" "$MIN_DOLLAR" "$MIN_SHARES" "$qualified" "${payloads[@]}" <<'PY'
```

In the python block, change the argument unpacking to:

```python
out, min_price, min_dollar, min_shares = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
qualified = sys.argv[5] == "1"
paths = sys.argv[6:]
```

and insert the gate immediately before `rows.append(...)`:

```python
        if qualified:
            if price < min_price:                    continue   # §1.4 price floor
            if (adv or 0.0) * price < min_dollar:    continue   # §1.4 dollar volume
            if (adv or 0.0) < min_shares:            continue   # §1.4 share sanity floor
            lev = num(body, 'fundLeverageFactor', 0.0) or 0.0
            if lev != 0.0:                           continue   # §3.5 gated shut by default
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash scripts/test-universe-filter.sh`
Expected: `6 passed, 0 failed`

- [ ] **Step 5: Commit**

```bash
git add scripts/universe-filter.sh scripts/test-universe-filter.sh
git commit -m "Apply §1.4 and §3.5 gates in the universe filter"
```

---

### Task 3: Ranking and the working-universe write

**Files:**
- Modify: `scripts/universe-filter.sh` (add `--rank-top N`)
- Modify: `scripts/test-universe-filter.sh`
- Modify: `rules.yml` (add `strategy: working_universe_size`)

**Interfaces:**
- Consumes: Task 2's `--qualified-only`
- Produces: `--rank-top N` sorts survivors by ascending `pct_from_52wk_high`
  (nearest the high first), tie-broken by descending `dollar_vol`, and keeps
  the top N. Prints `universe-filter: ranked N of M (dropped D)` to stderr so
  truncation is never silent.

- [ ] **Step 1: Add the parameter to rules.yml**

In `rules.yml` under `strategy:`, after `catalyst_min_whole_shares`:

```yaml
  # Size of the weekly working universe handed to the daily tier. Chosen to
  # match what the daily run can deep-vet, not everything that qualifies;
  # universe-filter reports how many it dropped so this can be tuned on
  # evidence rather than guessed twice.
  working_universe_size: 500
```

- [ ] **Step 2: Write the failing test**

Append to `scripts/test-universe-filter.sh` before the summary block:

```bash
echo "== ranking =="
bash "$F" --payload "$FIX" --out "$OUT" --qualified-only --rank-top 1 >/dev/null 2>&1
n=$(tail -n +2 "$OUT" | wc -l | tr -d ' ')
[ "$n" -eq 1 ] && ok "--rank-top caps the row count" || no "--rank-top caps the row count" "got $n"
bash "$F" --payload "$FIX" --out "$OUT" --qualified-only --rank-top 1 2>&1 >/dev/null | grep -q 'dropped' \
  && ok "truncation is reported, never silent" || no "truncation is reported, never silent"
```

- [ ] **Step 3: Run to verify it fails**

Run: `bash scripts/test-universe-filter.sh`
Expected: the two ranking assertions FAIL — `--rank-top` is an unknown argument.

- [ ] **Step 4: Implement ranking**

Add to the argument loop:

```bash
    --rank-top) shift; [ $# -gt 0 ] || { echo "universe-filter: --rank-top needs a count" >&2; exit 2; }; ranktop="$1" ;;
```

Initialise `ranktop=0` beside `qualified=0`, and pass it through:

```bash
python3 - "$out" "$MIN_PRICE" "$MIN_DOLLAR" "$MIN_SHARES" "$qualified" "$ranktop" "${payloads[@]}" <<'PY'
```

Update the unpacking:

```python
qualified = sys.argv[5] == "1"
ranktop   = int(sys.argv[6])
paths     = sys.argv[7:]
```

Replace the `with open(out...)` block with:

```python
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
print("universe-filter: ranked %d of %d (dropped %d)" % (len(rows), total, dropped), file=sys.stderr)
```

- [ ] **Step 5: Run the tests**

Run: `bash scripts/test-universe-filter.sh && bash scripts/check-consistency.sh`
Expected: `8 passed, 0 failed`, then `CONSISTENT`.

- [ ] **Step 6: Commit**

```bash
git add scripts/universe-filter.sh scripts/test-universe-filter.sh rules.yml
git commit -m "Rank the working universe and report truncation"
```

---

### Task 4: Directory fetch and pre-filter

**Files:**
- Create: `scripts/universe-fetch.sh`
- Modify: `scripts/test-universe-filter.sh` (add a fetch-offline test)

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `scripts/universe-fetch.sh --out FILE` writes one symbol per line.
  Exit 0 on success, 3 when the directory is unreachable (caller keeps last
  week's universe), 2 on usage error.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test-universe-filter.sh` before the summary block:

```bash
echo "== directory fetch =="
SYMS=$(mktemp)
bash scripts/universe-fetch.sh --out "$SYMS" >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  c=$(wc -l < "$SYMS" | tr -d ' ')
  [ "$c" -gt 5000 ] && ok "fetched a plausible symbol count ($c)" || no "fetched a plausible symbol count" "got $c"
  grep -qx 'AAPL' "$SYMS" && ok "AAPL present" || no "AAPL present"
  grep -qE '\$' "$SYMS" && no "test/oddball symbols excluded" "found \$ symbols" || ok "test/oddball symbols excluded"
elif [ "$rc" -eq 3 ]; then
  ok "offline: exits 3 so the caller keeps last week's universe"
  ok "offline: (count check skipped)"
  ok "offline: (symbol check skipped)"
else
  no "universe-fetch returns 0 or 3" "got $rc"
fi
rm -f "$SYMS"
```

- [ ] **Step 2: Run to verify it fails**

Run: `bash scripts/test-universe-filter.sh`
Expected: FAIL — `scripts/universe-fetch.sh` does not exist.

- [ ] **Step 3: Implement the fetcher**

Create `scripts/universe-fetch.sh`:

```bash
#!/bin/bash
# universe-fetch.sh — download the Nasdaq Trader symbol directory and reduce it
# to tradeable major-exchange symbols. Free, keyless, ~1 MB.
#
# Usage: scripts/universe-fetch.sh --out FILE
# Exit: 0 ok · 2 usage · 3 directory unreachable (caller keeps last week's list)
set -uo pipefail
URL="https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) shift; [ $# -gt 0 ] || { echo "universe-fetch: --out needs a path" >&2; exit 2; }; out="$1" ;;
    *) echo "universe-fetch: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$out" ] || { echo "universe-fetch: --out is required" >&2; exit 2; }

raw="$(mktemp)"; trap 'rm -f "$raw"' EXIT
if ! curl -sf --max-time 60 "$URL" -o "$raw"; then
  echo "universe-fetch: directory unreachable at $URL — caller must keep the previous universe" >&2
  exit 3
fi
[ -s "$raw" ] || { echo "universe-fetch: directory came back empty" >&2; exit 3; }

python3 - "$raw" "$out" <<'PY'
import csv, sys
src, dst = sys.argv[1], sys.argv[2]
MAJOR = {'N', 'Q', 'A', 'P', 'Z'}
JUNK = (' warrant', '% note', ' right', ' unit', 'preferred',
        ' depositary', 'when issued', ' due 20')
keep = []
for r in csv.DictReader(open(src), delimiter='|'):
    sym = (r.get('Symbol') or '').strip()
    if not sym or '$' in sym or len(sym) > 5:            continue
    if r.get('Listing Exchange') not in MAJOR:           continue
    if r.get('Test Issue') != 'N':                       continue
    name = (r.get('Security Name') or '').lower()
    if any(k in name for k in JUNK):                     continue
    keep.append(sym)
keep = sorted(set(keep))
open(dst, 'w').write('\n'.join(keep) + '\n')
print("universe-fetch: %d tradeable symbols" % len(keep), file=sys.stderr)
PY
```

- [ ] **Step 4: Run the tests**

Run: `chmod +x scripts/universe-fetch.sh && bash scripts/test-universe-filter.sh`
Expected: `12 passed, 0 failed` (or the three offline substitutes if the
network is unavailable — both are valid outcomes).

- [ ] **Step 5: Commit**

```bash
git add scripts/universe-fetch.sh scripts/test-universe-filter.sh
git commit -m "Fetch and pre-filter the Nasdaq Trader symbol directory"
```

---

### Task 5: The weekly command procedure

**Files:**
- Create: `.claude/commands/weekly-universe.md`

**Interfaces:**
- Consumes: `scripts/universe-fetch.sh`, `scripts/universe-filter.sh`,
  `scripts/research-replace.sh universe`, `scripts/data-append.sh`
- Produces: the `/weekly-universe` procedure the agent executes

- [ ] **Step 1: Write the command file**

Create `.claude/commands/weekly-universe.md`:

````markdown
---
description: Weekly whole-market sweep. Regenerates research/universe.md, the working universe the daily run sweeps in full.
---

# /weekly-universe — one whole-market pass

Design: `docs/superpowers/specs/2026-08-17-weekly-universe-agent-design.md`.

**This pass never reads a quote.** Payloads go to disk and
`scripts/universe-filter.sh` reduces them. If you find yourself reading quote
text, stop — that is the failure this design exists to remove.

## §A — Preconditions

1. `scripts/check-consistency.sh` passes. A FAIL means a rule has drifted;
   fix before sweeping.
2. Market closed. This is a weekend pass; it must never compete with a live
   session for the token.

## §B — The sweep

1. **Symbols.** `scripts/universe-fetch.sh --out /tmp/universe-syms.txt`
   - Exit 3 (unreachable): **stop, keep the existing `research/universe.md`**,
     and record the miss in §D. Discovery degrades to last week, never to
     nothing.
2. **Chunk.** Split into 150-symbol chunks. 150 is chosen so each verbose
   response (~260 KB) reliably exceeds the inline tool-result limit and is
   written to a file by the harness.
3. **Quote each chunk:** `get_quotes(symbols=<chunk>, verbose=True)`.
   - The response is saved to a file and the path returned. **Record the path;
     do not read the file.**
   - If a chunk ever returns inline instead, write it to a temp file yourself
     in the same `{"result": "..."}` shape and carry on identically.
   - A chunk that errors is logged and skipped. One bad chunk never stops the
     sweep.
4. **Filter, once, over every payload:**

   ```
   scripts/universe-filter.sh \
     --payload PATH1 --payload PATH2 ... \
     --qualified-only --rank-top <strategy_working_universe_size from rules.yml> \
     --out /tmp/universe-ranked.tsv
   ```

   Read the stderr line — it reports how many were ranked and how many
   dropped. That number goes in the ledger.

## §C — Write the working universe

Rewrite `research/universe.md` via
`scripts/research-replace.sh universe <<'EOF' ... EOF`. The script enforces the
required first line `# Fallback universe` and the banner
`never a source for order parameters`. Include, in the body:

- assembly timestamp and symbol counts (fetched / quoted / qualified / ranked / dropped)
- the ranked table: symbol, price, 10-day ADV, dollar volume, % from 52-week
  high, optionable, last earnings date, ETF flag
- a plain statement that the §4 tilts (50-day SMA, 3/6-month returns) are
  **not** applied here and belong to the daily tier

## §D — Run ledger

Append one row via
`scripts/data-append.sh events DATE '<json>'`:

```json
{"t":"HH:MM:SS","kind":"weekly_universe","fetched":N,"chunks":N,"chunks_failed":N,
 "quoted":N,"qualified":N,"ranked":N,"dropped":N,"schwab_calls":N,"skipped":"<or ->"}
```

## §E — Never, in this pass

- Never read a quote payload into context — pass paths to the filter.
- Never place, cancel, or modify an order. This agent has no order tools.
- Never write `research/universe.md` except through `research-replace.sh`.
- Never emit an empty universe. If the sweep fails, keep last week's and say so.
- Never claim the §4 tilts were applied. They were not.
````

- [ ] **Step 2: Verify the command references only things that exist**

Run:
```bash
for f in scripts/universe-fetch.sh scripts/universe-filter.sh scripts/research-replace.sh scripts/data-append.sh scripts/check-consistency.sh; do
  [ -e "$f" ] && echo "  ok $f" || echo "  MISSING $f"
done
```
Expected: all five `ok`.

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/weekly-universe.md
git commit -m "Add the /weekly-universe command procedure"
```

---

### Task 6: The agent definition

**Files:**
- Create: `.claude/agents/weekly-universe.md`

**Interfaces:**
- Consumes: `.claude/commands/weekly-universe.md`
- Produces: a `weekly-universe` subagent type, read-only by construction

- [ ] **Step 1: Write the agent definition**

Create `.claude/agents/weekly-universe.md`. The tool list is the guarantee —
no order tools, no account tools, no Write/Edit — matching how
`research-scout` and `deep-research` are built.

```markdown
---
name: weekly-universe
description: Read-only weekly whole-market sweep. Executes §A–§D of .claude/commands/weekly-universe.md — fetches the Nasdaq Trader directory, sweeps it with batched verbose get_quotes, and regenerates research/universe.md via scripts/research-replace.sh. Has no order tools, no account tools, and no Write/Edit by construction — it cannot place, cancel, or modify anything at the broker. All escalation belongs to the parent session.
tools: Read, Glob, Grep, Bash, ToolSearch, mcp__schwab__get_datetime, mcp__schwab__get_market_hours, mcp__schwab__get_quotes
model: opus
---

You are the weekly universe sweeper for the trading competition in
/Users/chris/Documents/Projects/trade-challenge. One invocation = one
whole-market pass. You research; you never trade.

Procedure — no improvisation:

1. Read `.claude/commands/weekly-universe.md` and execute **§A through §D
   exactly as written**. Do not add channels, do not skip the filter, do not
   substitute your own qualification logic for `scripts/universe-filter.sh`.

2. **You must never read a quote payload.** `get_quotes` responses go to
   files; you pass paths to the filter. Reading them defeats the entire
   design — the sweep exists because payloads in context were the bottleneck.

3. Thresholds come from `rules.yml` through the scripts. Never hard-code a
   number, never infer one from a document.

4. If the directory fetch fails, keep the existing `research/universe.md`,
   record the miss, and return. An empty or partial universe written silently
   is worse than a stale one reported honestly.

Return to the parent, in at most 10 lines: symbols fetched, chunks run and
failed, symbols qualified, ranked, dropped, Schwab calls used, and anything
that went wrong. Nothing else.
```

- [ ] **Step 2: Verify the agent registers**

Run: `grep -c 'mcp__schwab__get_quotes' .claude/agents/weekly-universe.md`
Expected: `1`

Run: `grep -cE 'place_previewed_order|cancel_order|get_account|Write|Edit' .claude/agents/weekly-universe.md`
Expected: `0` — no write or order capability anywhere in the tool list.

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/weekly-universe.md
git commit -m "Add the read-only weekly-universe agent"
```

---

### Task 7: Simplify the daily run and wire the docs

**Files:**
- Modify: `.claude/commands/deep-research.md` (§D universe sweep section)
- Modify: `strategy.md` (§9 session-open deadman)
- Modify: `README.md` (scripts row)

**Interfaces:**
- Consumes: the working universe produced by Tasks 4–6
- Produces: a daily run that sweeps the working universe in full

- [ ] **Step 1: Replace the chunked sweep in deep-research.md**

In `.claude/commands/deep-research.md`, replace the batched-quote sweep
paragraph — the one describing "~50-symbol chunks", the `_sweep_cursor` row,
`last_index`, and the wrap-to-0 resume rule — with:

```markdown
     The screen runs as a **batched `get_quotes` sweep of
     `research/universe.md` in full, every run**. The working universe is
     produced weekly by `/weekly-universe` and is already reduced to names
     clearing the §1.4 floors, so it is small enough to sweep completely:
     quote it in chunks, hand every payload path to
     `scripts/universe-filter.sh`, and never read a payload.
     There is no cursor and no resume — the sweep either completes or reports
     what it missed. *(The 50-symbol chunking and `_sweep_cursor` machinery
     were removed 2026-08-17: they existed because quote payloads entered
     agent context, and put the universe on an 11-day lap.)*
     Apply the §4 tilts — 50-day SMA and 3/6-month returns — to the ranked
     survivors here, where per-symbol price history is affordable. The weekly
     pass does not and cannot apply them.
```

- [ ] **Step 2: Add the weekly deadman to the session-open protocol**

In `strategy.md` §9, after the existing deep-research deadman bullet, add:

```markdown
- **Weekly universe deadman:** check the mtime of `research/universe.md`. If
  it is older than 8 days, the weekly sweep has not run — say so in the
  session-open summary as a status line (not `ALERT.md`) and re-create the
  cron. A stale working universe silently narrows discovery, which is exactly
  the failure the weekly tier was built to remove.
```

- [ ] **Step 3: Update the README scripts row**

In `README.md`, replace the `scripts/` row with:

```markdown
| `scripts/` | Pre-order compliance gate and its regression suite, the rule-consistency checker, the weekly universe fetch/filter pair, and the append-only writers used by the research and monitoring loops. |
```

- [ ] **Step 4: Verify nothing stale survives**

Run:
```bash
grep -rn '_sweep_cursor\|last_index\|50-symbol' .claude/commands/deep-research.md | grep -v 'were removed'
```
Expected: no output.

Run: `bash scripts/check-consistency.sh && bash scripts/test-pre-order-check.sh | tail -1 && bash scripts/test-universe-filter.sh | tail -1`
Expected: `CONSISTENT`, `72 passed, 0 failed`, `12 passed, 0 failed`.

- [ ] **Step 5: Commit**

```bash
git add .claude/commands/deep-research.md strategy.md README.md
git commit -m "Daily run sweeps the working universe in full; drop the resume cursor"
```

---

### Task 8: Record the MCP findings in the notes skill

**Files:**
- Modify: `~/.claude/skills/schwab-mcp-notes/SKILL.md`

**Interfaces:**
- Consumes: nothing
- Produces: documentation only

- [ ] **Step 1: Add the two findings**

In the "Ignored parameters — silent, no error" section of
`~/.claude/skills/schwab-mcp-notes/SKILL.md`, add:

```markdown
### `get_quotes` ignores `fields`

Passing `fields="quote,fundamental"` returned the **compact** payload
unchanged — same class as `get_advanced_option_chain`'s ignored `strike`.

**Use `verbose=True` instead.** It returns the full payload including
`reference.optionable`, `fundamental.avg10DaysVolume` (real 10-day ADV),
`quote.52WeekHigh`/`52WeekLow`, `fundamental.lastEarningsDate`,
`nextDivExDate`, and `fundLeverageFactor` (non-zero identifies leveraged and
inverse funds). Verified 2026-08-17 across a live 60-symbol batch: all fields
present on all 60.

**Size:** verbose runs ~1,723 chars/symbol against ~180 compact. A 250-symbol
verbose call is ~430 KB and will exceed the inline tool-result limit — which
is usually what you want, since the harness then writes it to a file you can
filter with a script instead of reading.
```

- [ ] **Step 2: Verify the file still parses as a skill**

Run: `head -5 ~/.claude/skills/schwab-mcp-notes/SKILL.md`
Expected: the YAML frontmatter is intact and unmodified.

- [ ] **Step 3: Commit**

The skill lives outside this repo. Report to Chris that it was edited; there is
nothing to commit here.

---

## Deferred — not in this plan

These came out of the spec's open items and are deliberately **not** built here.
Each is a separate decision, and none blocks the above.

1. **ETF sweep split.** 5,574 of the 11,227 are ETFs and most fail the dollar
   volume gate. This plan sweeps them in the same pass because the marginal
   cost is ~22 calls a week and the playbook records ETFs as unresearched.
   Revisit with ledger evidence on how many actually survive.
2. **Deriving `working_universe_size` from measured daily throughput** rather
   than the 500 chosen here. The `dropped` count in the ledger is the input.
3. **Cron registration** for the weekly cadence. Task 7 adds the deadman that
   detects a missed run; actually scheduling it is a separate change, and the
   deadman makes a missed run visible in the meantime.
4. **Intraday sweeping** — explicitly a non-goal in the spec.
