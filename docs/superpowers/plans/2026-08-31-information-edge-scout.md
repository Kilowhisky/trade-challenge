# Information-Edge Scout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inoperable v1 candidate pipeline with a sector-scoped information-edge scout that surfaces corroborated, non-mainstream claims for Chris to judge, and retire the competition rule set.

**Architecture:** Three phases. Phase A amends the rules (CLAUDE.md + rules.yml + check-consistency + strategy.md in one §9 commit). Phase B builds a deterministic data layer of shell scripts with test suites — sector tags, qualified sector universe, earnings cohort, evidence ledger. Phase C adds the scout and catalyst agents plus their scheduled jobs. Phases are strictly sequential; B depends on A's rules.yml keys and C depends on B's data files.

**Tech Stack:** bash (POSIX-ish, must run on both macOS/BSD and Debian/arm64 — no GNU-only flags), `jq`, `awk`, existing `scripts/lib-rules.sh` parser, Claude Code subagents with tool allowlists, supercronic in the `tc-scheduler` container.

**Spec:** `docs/superpowers/specs/2026-08-30-information-edge-scout-design.md`

## Global Constraints

- **Never hard-code a rule number.** Every parameter lives in `rules.yml` exactly once; scripts read it via `scripts/lib-rules.sh`. This is the invariant `check-consistency.sh` exists to enforce.
- **Portability:** scripts run on the laptop (BSD tools) and the Pi (GNU). No `sed -i` without a backup arg, no `grep -P`, no GNU-only `date` flags. Prefer `awk`.
- **Dates are Eastern and come from `get_datetime`,** never the machine clock (the laptop is Pacific).
- **Repo is public (CLAUDE.md §7.4).** No account number, `accountHash`, token, or personal identifier in any tracked file. `research/`, `status/`, `trade-log.csv` are gitignored.
- **Script invocation form:** agents call scripts as bare relative paths, `scripts/name.sh`. `./scripts/`, `bash scripts/` and `/app/scripts/` are refused by the permission gate.
- **Test convention:** every new script gets a test suite in `scripts/test-<name>.sh` following the existing `ok`/`bad` pattern, and **every new assertion must be mutation-verified** — break the implementation, confirm the test fails, restore.
- **Do not push to the `deploy` branch.** The server auto-adopts `origin/deploy` at every job fire. All work stays on a feature branch until Chris approves.
- **No orders.** Liquidating AMH/CSX/USB is an operational action for a live session with Discord approval, explicitly out of scope for this plan.

---

# Phase A — Rule amendment

## Task A1: Retire the competition rule set (§9 amendment)

Single commit touching `rules.yml`, `CLAUDE.md`, `strategy.md`, `CHANGELOG.md`, and `scripts/check-consistency.sh`. It must land atomically: `check-consistency.sh` fails the build if any document disagrees with `rules.yml`.

**Files:**
- Modify: `rules.yml`
- Modify: `CLAUDE.md`
- Modify: `strategy.md`
- Modify: `CHANGELOG.md`
- Modify: `scripts/check-consistency.sh`
- Test: `scripts/test-pre-order-check.sh` (existing, must still pass)

**Interfaces:**
- Produces: `rules.yml` keys consumed by all later tasks —
  `manual.option_min_delta` (0.45), **new** `manual.option_max_delta` (0.75),
  `manual.option_single_position_pct` (10), `manual.option_open_premium_pct` (30),
  **new** `strategy.scout_sectors` is NOT added here (see Task B1 — it is data, not a rule).
- Removed keys later tasks must not reference: `manual.window_start`,
  `manual.window_end`, `manual.final_session`, `manual.lockout_start`,
  `manual.lockout_final_sessions`, `strategy.all_options_flat_by`,
  `strategy.last_leveraged_entry`.

- [ ] **Step 1: Inventory every consumer of the keys being removed**

Run and record the output — this is the blast radius, and the amendment is not complete until each hit is resolved:

```bash
cd /Users/chris/Documents/Projects/trade-challenge
for k in window_start window_end final_session lockout_start lockout_final_sessions \
         all_options_flat_by last_leveraged_entry option_min_delta \
         option_single_position_pct competition_capital; do
  printf '\n=== %s ===\n' "$k"
  grep -rn "$k" --include='*.sh' --include='*.yml' --include='*.md' . \
    | grep -v '^./docs/superpowers/' | grep -v '^./CHANGELOG.md'
done
```

Expected: hits in `rules.yml`, `CLAUDE.md`, `strategy.md`, `scripts/check-consistency.sh`, `scripts/pre-order-check.sh`, `scripts/lib-rules.sh`, and several `.claude/` files.

- [ ] **Step 2: Write the failing consistency test first**

Add to `scripts/test-pre-order-check.sh`, before the final tally:

```bash
echo "== v2 option rules =="
# The delta FLOOR became a BAND on 2026-08-31. A floor alone permits 0.35-delta
# contracts that vol crush guts; the band also rejects >0.75, where you are
# paying option spreads to own something that behaves like stock.
mind="$(awk -F': *' '/^  option_min_delta:/{print $2; exit}' rules.yml | tr -d ' ')"
maxd="$(awk -F': *' '/^  option_max_delta:/{print $2; exit}' rules.yml | tr -d ' ')"
[ "$mind" = "0.45" ] && ok "manual option_min_delta is 0.45" \
  || bad "manual option_min_delta is '${mind:-<unset>}', want 0.45"
[ "$maxd" = "0.75" ] && ok "manual option_max_delta is 0.75" \
  || bad "manual option_max_delta is '${maxd:-<unset>}', want 0.75"

sp="$(awk -F': *' '/^  option_single_position_pct:/{print $2; exit}' rules.yml | tr -d ' ')"
[ "$sp" = "10" ] && ok "single-thesis premium cap reduced to 10%" \
  || bad "option_single_position_pct is '${sp:-<unset>}', want 10"

# The competition is over. A scoring rule with no contest still constrains
# trades, so these keys must be GONE, not merely unused.
for dead in window_end lockout_start final_session all_options_flat_by; do
  grep -q "^ *${dead}:" rules.yml \
    && bad "rules.yml still carries competition key '$dead'" \
    || ok "competition key '$dead' removed from rules.yml"
done
```

- [ ] **Step 3: Run it to confirm it fails**

Run: `./scripts/test-pre-order-check.sh 2>&1 | tail -20`
Expected: FAIL on `option_min_delta is '0.35'`, on the missing `option_max_delta`, on `option_single_position_pct is '20'`, and four FAILs for the still-present competition keys.

- [ ] **Step 4: Amend `rules.yml`**

In the `manual:` block, replace the §3.2 sizing and delta lines:

```yaml
  # §3.2 — option sizing, % of ACCOUNT VALUE (was "competition capital",
  # which died with the competition on 2026-08-29).
  # Single-thesis cap cut 20 -> 10 on fractional-Kelly grounds: the edge is
  # unvalidated, the modal outcome of a long option is total loss of premium,
  # and at a ~50% hit rate with 2:1 payoff quarter-Kelly is ~6%. Raise only
  # when a measured hit rate justifies it (design spec §8.1).
  option_single_position_pct: 10
  option_open_premium_pct: 30
```

and replace the delta floor with a band:

```yaml
  # §3.2 — delta is a BAND, not a floor. Vol crush destroys EXTRINSIC value,
  # so an ITM contract survives it; below 0.45 is a lottery ticket bought into
  # a known volatility collapse, above 0.75 is paying option spreads to own
  # something that already behaves like stock.
  option_min_delta: 0.45
  option_max_delta: 0.75
```

Add to `strategy:` the expiry window from spec §4, which was otherwise stated
in the design and enforced nowhere:

```yaml
  # Design spec §4 — expiry must clear the print, so a correct thesis is never
  # FORCED to sell into the vol crush and can hold into the drift instead.
  # Never the front weekly: that is where event vol is most concentrated.
  option_expiry_min_days_past_earnings: 14
  option_expiry_max_days_past_earnings: 28
```

Delete the entire `# §8 — window and endgame` block (`window_start`, `window_end`, `final_session`, `lockout_start`, `lockout_final_sessions`) and, from `strategy:`, delete `all_options_flat_by` and `last_leveraged_entry`.

In `strategy:`, raise the tightening to stay on the tight side of the new manual band:

```yaml
  # Playbook §6 — options, tighter than manual §3.2
  option_min_delta: 0.50               # manual floor is 0.45
  leveraged_exit_session: 4            # manual limit is 5
```

- [ ] **Step 5: Amend `CLAUDE.md`**

1. Delete section `## 8. Scoring and endgame` entirely, and the `§8 lockout` and `Final session` rows from the header table, and the `Window` row.
2. Replace §3.7 with the halt-only rule plus the corporate-action re-pricing mechanic:

```markdown
**3.7 — Event risk.**

- **Earnings are no longer a prohibition.** *(Amended 2026-08-31 per §9 — the
  prior rule forbade holding any position through a scheduled report, which
  categorically outlawed the only strategy this account has a demonstrated
  edge in. Chris: "Ditch 3.7 entirely", scoped to "Kill earnings bar, keep
  halt rule only".)* Know the date — it is the event a thesis is timed
  against — but it gates nothing.
- **Corporate actions re-price the stop, they do not force an exit.** A stop
  priced before a split is arithmetically meaningless afterward, so recompute
  and re-place it in the same session the action takes effect. The prior rule
  required exiting the position, which would have forced a close on exactly
  the merger theses §3.7 now exists to permit.
- **Halted stock:** place no orders in a halted security. Wait for the reopen,
  reassess from scratch, and log the halt.
```

3. Global replace of the risk-anchoring language: every "competition capital" becomes "account value", and the header's "Competition capital" row becomes "Account value — the live broker reading, recomputed every session". The `<!--rule:key-->` markers stay bound to the same keys.
4. Update the §3.2 bullets to state 10% single / 30% aggregate and the delta band `0.45–0.75`.
5. Update the §3.6 sentence so the high-water mark is the highest **account value** recorded to date.

- [ ] **Step 6: Amend `strategy.md` and `CHANGELOG.md`**

In `strategy.md`, remove the endgame-calendar section and every reference to the deleted keys; re-anchor sleeve percentages to account value. In `CHANGELOG.md`, add a dated entry recording each parameter's old and new value, with Chris's words quoted verbatim:

> "The competition is over, i'm dropping out."
> "Ditch 3.7 entirely" — scoped in the same conversation to "Kill earnings bar, keep halt rule only".

- [ ] **Step 7: Update `scripts/check-consistency.sh`**

Remove the checks that assert the presence of the deleted keys and the endgame-calendar cross-checks. Add a check that the delta band is coherent, and keep the existing derived-DTE identity check intact:

```bash
# The band must not be inverted, and the strategy tightening must sit inside it.
mn="$(rules_get manual option_min_delta)"; mx="$(rules_get manual option_max_delta)"
sd="$(rules_get strategy option_min_delta)"
awk -v a="$mn" -v b="$mx" 'BEGIN{exit !(a<b)}' \
  && ok "option delta band is ordered (min < max)" \
  || bad "option delta band inverted: min=$mn max=$mx"
awk -v s="$sd" -v a="$mn" -v b="$mx" 'BEGIN{exit !(s>=a && s<=b)}' \
  && ok "strategy option_min_delta sits inside the manual band" \
  || bad "strategy option_min_delta $sd is outside manual band $mn-$mx"
```

Note: `rules_get` is the existing accessor in `scripts/lib-rules.sh`. If the
name differs, use whatever that file already exports — do not add a second
parser.

- [ ] **Step 8: Run the full suite**

```bash
./scripts/check-consistency.sh && \
for t in test-pre-order-check test-scheduled-run test-status-write \
         test-latest-status test-broker-gating test-universe-filter test-deploy; do
  printf '%-24s ' "$t"; ./scripts/$t.sh >/dev/null 2>&1; echo "rc=$?"
done
```

Expected: `CONSISTENT`, and every suite `rc=0`. `pre-order-check` must still enforce the option caps — now at 10%.

- [ ] **Step 9: Commit**

```bash
git add rules.yml CLAUDE.md strategy.md CHANGELOG.md scripts/check-consistency.sh scripts/test-pre-order-check.sh
git commit -F- <<'MSG'
Retire the competition rule set, and stop forbidding the one edge we have

Chris withdrew from the competition on 2026-08-29:

  "The competition is over, i'm dropping out."
  "Ditch 3.7 entirely"  (scoped in the same conversation to
   "Kill earnings bar, keep halt rule only")

§8 is deleted outright: a scoring rule with no contest still constrains
trades. §3.7 keeps only the halt provision. The earnings prohibition
categorically outlawed the strategy this account has a demonstrated edge in,
and the corporate-action EXIT requirement would have forced a close on exactly
the merger theses we now intend to hold, so it becomes a stop RE-PRICING
requirement — same arithmetic protection, no forced exit.

§3.2's delta floor becomes a band, 0.45-0.75: vol crush destroys extrinsic
value, so a floor alone waves through contracts it will gut. The single-thesis
premium cap drops 20% -> 10% on fractional-Kelly grounds, because the edge is
unvalidated and total loss of premium is the modal outcome.

"Competition capital" becomes account value throughout; the $900 reserve stops
being a scoring artifact and becomes a settlement buffer.

§1 and §3.3 are deliberately untouched. We are about to hold options through
binary events, which makes OCC auto-exercise on a cash account the largest
remaining tail risk in the account.
MSG
```

---

# Phase B — Data layer

## Task B1: Sector tag store

**Files:**
- Create: `scripts/sector-write.sh`
- Create: `scripts/test-sector-write.sh`
- Data (gitignored, created at runtime): `research/sectors.tsv`

**Interfaces:**
- Produces: `scripts/sector-write.sh SYMBOL SECTOR` appends/updates one row in
  `research/sectors.tsv`, tab-separated `symbol\tsector\ttagged_date`.
  Valid sectors, and the only accepted values: `consumer-software`,
  `airlines-transport`, `semis-hardware`, `other`.
- Consumed by: Task B3 (cohort builder) and Task C1 (scout agent).

Why a whitelisted writer rather than letting the agent write the file: every
other write path in this system goes through a validating script so a
malformed agent output cannot corrupt state. Follow the existing pattern in
`scripts/research-append.sh`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-sector-write.sh`:

```bash
#!/bin/bash
# Regression suite for sector-write.sh.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2
export TC_RESEARCH_DIR="$(mktemp -d)"
trap 'rm -rf "$TC_RESEARCH_DIR"' EXIT
pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

F="$TC_RESEARCH_DIR/sectors.tsv"

./scripts/sector-write.sh ROKU consumer-software >/dev/null 2>&1
grep -q "^ROKU	consumer-software	" "$F" 2>/dev/null \
  && ok "writes a tagged symbol" || bad "did not write ROKU"

# An unknown sector must be REFUSED, not silently stored: a typo that becomes a
# new sector value silently shrinks the universe the scout looks at.
./scripts/sector-write.sh AAPL consumer-sofware >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "rejects an invalid sector" || bad "accepted a misspelled sector"
grep -q "consumer-sofware" "$F" 2>/dev/null \
  && bad "wrote the invalid sector anyway" || ok "invalid sector not persisted"

# Re-tagging must UPDATE, not duplicate: the weekly refresh re-runs over names
# already tagged, and duplicate rows would double-count the universe.
./scripts/sector-write.sh ROKU other >/dev/null 2>&1
n="$(grep -c "^ROKU	" "$F" 2>/dev/null || echo 0)"
[ "$n" = "1" ] && ok "re-tagging updates in place" || bad "ROKU has $n rows, want 1"
grep -q "^ROKU	other	" "$F" 2>/dev/null \
  && ok "re-tag took the new value" || bad "re-tag did not update the value"

./scripts/sector-write.sh "RO KU" consumer-software >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "rejects a symbol containing whitespace" \
  || bad "accepted a whitespace symbol, which would shift TSV columns"

echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `chmod +x scripts/test-sector-write.sh && ./scripts/test-sector-write.sh`
Expected: every assertion FAILs — `sector-write.sh` does not exist.

- [ ] **Step 3: Implement `scripts/sector-write.sh`**

```bash
#!/bin/bash
# Validated writer for the sector tag store.
#
# Usage: scripts/sector-write.sh SYMBOL SECTOR
#
# Sectors are a CLOSED set. A typo must be refused rather than stored: an
# unrecognised value would silently shrink the universe the scout sweeps, and
# a universe that quietly gets smaller is the v1 failure mode all over again.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "sector-write: expected SYMBOL SECTOR, got $#" >&2; exit 2
fi
symbol="$1"; sector="$2"

case "$symbol" in
  *[!A-Za-z0-9.-]*|"") echo "sector-write: bad symbol '$symbol'" >&2; exit 2 ;;
esac
case "$sector" in
  consumer-software|airlines-transport|semis-hardware|other) ;;
  *) echo "sector-write: unknown sector '$sector'" >&2; exit 2 ;;
esac

dir="${TC_RESEARCH_DIR:-$(cd "$(dirname "$0")/.." && pwd)/research}"
mkdir -p "$dir"
file="$dir/sectors.tsv"
today="$(TZ=America/New_York date +%Y-%m-%d)"

tmp="$file.tmp.$$"
if [ -f "$file" ]; then
  awk -F'\t' -v s="$symbol" '$1 != s' "$file" > "$tmp"
else
  : > "$tmp"
fi
printf '%s\t%s\t%s\n' "$symbol" "$sector" "$today" >> "$tmp"
mv "$tmp" "$file"
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `./scripts/test-sector-write.sh`
Expected: `5 passed, 0 failed`.

- [ ] **Step 5: Mutation-verify every assertion**

For each, break it, confirm the suite fails, restore:
1. Delete the `case "$sector"` validation → the two invalid-sector assertions must fail.
2. Change the `awk` de-dupe to `cat "$file" > "$tmp"` → the re-tagging assertions must fail.
3. Delete the symbol `case` validation → the whitespace assertion must fail.

Any assertion that still passes while its implementation is broken is a vacuous
test — rewrite it before continuing. Two of the four assertions written during
this project's last session passed vacuously on first attempt; assume yours do
until proven otherwise.

- [ ] **Step 6: Commit**

```bash
git add scripts/sector-write.sh scripts/test-sector-write.sh
git commit -m "Add the sector tag store, with a closed set of sector values"
```

## Task B2: Emit the qualified sector universe from the weekly sweep

**Files:**
- Modify: `scripts/universe-filter.sh`
- Modify: `.claude/commands/weekly-universe.md`
- Modify: `scripts/test-universe-filter.sh`

**Interfaces:**
- Consumes: nothing from B1 (sector join happens in B3).
- Produces: `research/universe-qualified.tsv` — every name passing the §1.4/§2
  liquidity floors, with the same columns as `universe.md`'s table
  (`symbol, price, adv10, dollar_vol, pct_from_52wk_high, optionable,
  leverage, last_earnings, is_etf, session_range_pct`), **not truncated to
  `working_universe_size`.**

Why: the sweep qualifies ~3,196 names and then ranks to 500. Hong/Lim/Stein put
the slow-diffusion edge in low-coverage names, which is exactly what a
liquidity ranking discards — and where both of Chris's worked examples lived.
`universe.md` keeps its existing 500-row behaviour for the daily tier; this is
an additional output, not a replacement.

- [ ] **Step 1: Write the failing test**

Append to `scripts/test-universe-filter.sh`:

```bash
echo "== qualified-set emission (v2 scout universe) =="
# The ranked file is for the daily tier. The scout needs the FULL qualified set,
# because ranking by liquidity throws away precisely the low-coverage names the
# information edge lives in.
out="$(mktemp -d)"; trap "rm -rf $out" EXIT
TC_RESEARCH_DIR="$out" ./scripts/universe-filter.sh --qualified-only \
  < tests/fixtures/universe-sample.tsv >/dev/null 2>&1
q="$out/universe-qualified.tsv"
[ -f "$q" ] && ok "emits research/universe-qualified.tsv" \
  || bad "no qualified-set file written"
hdr="$(head -1 "$q" 2>/dev/null)"
case "$hdr" in
  symbol*last_earnings*) ok "qualified file carries last_earnings" ;;
  *) bad "qualified file header lacks last_earnings: '$hdr'" ;;
esac
```

If `tests/fixtures/universe-sample.tsv` does not exist, create it first with at
least 12 rows: 8 that pass the liquidity floors and 4 that fail, so truncation
behaviour is observable.

- [ ] **Step 2: Run, confirm failure.** Run `./scripts/test-universe-filter.sh`; expect the two new assertions to FAIL.

- [ ] **Step 3: Implement.** Add a `--qualified-only` output path to `universe-filter.sh` that writes every qualifying row to `${TC_RESEARCH_DIR:-research}/universe-qualified.tsv` with a header line, before the ranking/truncation step. Do not change the existing default behaviour.

- [ ] **Step 4: Run, confirm pass.** `./scripts/test-universe-filter.sh` → all pass.

- [ ] **Step 5: Mutation-verify.** Move the emission to *after* truncation → the row-count assertion must fail. Drop the header → the `last_earnings` assertion must fail.

- [ ] **Step 6: Commit.**

```bash
git add scripts/universe-filter.sh scripts/test-universe-filter.sh .claude/commands/weekly-universe.md tests/fixtures/universe-sample.tsv
git commit -m "Emit the full qualified universe, not just the liquidity-ranked 500"
```

## Task B3: Earnings cohort builder

**Files:**
- Create: `scripts/cohort.sh`
- Create: `scripts/test-cohort.sh`

**Interfaces:**
- Consumes: `research/universe-qualified.tsv` (B2), `research/sectors.tsv` (B1).
- Produces: `scripts/cohort.sh TODAY_ET` prints, to stdout, tab-separated
  `symbol\tsector\test_next_earnings\tdays_out` for every name whose estimated
  next print falls in the entry window, sorted by `days_out` ascending.
  Exit 0 with empty output when the cohort is empty — an empty cohort between
  earnings seasons is correct behaviour, not an error.

The entry window is **21 to 42 days out**, matching the design spec's 3–6 weeks.
Estimate: `last_earnings + 91 days`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-cohort.sh` with a fixture covering the boundaries — a name
at 20 days (excluded), 21 (included), 42 (included), 43 (excluded), one with an
empty `last_earnings` (excluded, must not crash), one tagged `other` (excluded),
and one not present in `sectors.tsv` at all (excluded).

```bash
#!/bin/bash
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2
export TC_RESEARCH_DIR="$(mktemp -d)"
trap 'rm -rf "$TC_RESEARCH_DIR"' EXIT
pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

# TODAY is fixed so the suite does not drift with the wall clock.
TODAY=2026-09-01
# last_earnings chosen so that +91d lands at the intended offset from TODAY.
{ printf 'symbol\tprice\tadv10\tdollar_vol\tpct\toptionable\tlev\tlast_earnings\tis_etf\trange\n'
  printf 'EDGE20\t10\t1\t1\t0\ttrue\t0\t2026-06-11\tfalse\t1\n'
  printf 'EDGE21\t10\t1\t1\t0\ttrue\t0\t2026-06-12\tfalse\t1\n'
  printf 'EDGE42\t10\t1\t1\t0\ttrue\t0\t2026-07-03\tfalse\t1\n'
  printf 'EDGE43\t10\t1\t1\t0\ttrue\t0\t2026-07-04\tfalse\t1\n'
  printf 'NODATE\t10\t1\t1\t0\ttrue\t0\t\tfalse\t1\n'
  printf 'OTHERS\t10\t1\t1\t0\ttrue\t0\t2026-06-20\tfalse\t1\n'
  printf 'UNTAGD\t10\t1\t1\t0\ttrue\t0\t2026-06-20\tfalse\t1\n'
} > "$TC_RESEARCH_DIR/universe-qualified.tsv"
{ printf 'EDGE20\tconsumer-software\t2026-08-31\n'
  printf 'EDGE21\tconsumer-software\t2026-08-31\n'
  printf 'EDGE42\tsemis-hardware\t2026-08-31\n'
  printf 'EDGE43\tairlines-transport\t2026-08-31\n'
  printf 'NODATE\tconsumer-software\t2026-08-31\n'
  printf 'OTHERS\tother\t2026-08-31\n'
} > "$TC_RESEARCH_DIR/sectors.tsv"

out="$(./scripts/cohort.sh "$TODAY" 2>/dev/null)"
syms="$(printf '%s\n' "$out" | awk -F'\t' 'NF{print $1}' | sort | tr '\n' ' ')"

[ "$syms" = "EDGE21 EDGE42 " ] \
  && ok "cohort is exactly the names inside the 21-42 day window" \
  || bad "cohort was '$syms', want 'EDGE21 EDGE42 '"

printf '%s\n' "$out" | grep -q NODATE \
  && bad "a name with no last_earnings entered the cohort" \
  || ok "a missing last_earnings is skipped, not crashed on"
printf '%s\n' "$out" | grep -q OTHERS \
  && bad "a name tagged 'other' entered the cohort" \
  || ok "sector 'other' is excluded"
printf '%s\n' "$out" | grep -q UNTAGD \
  && bad "an untagged name entered the cohort" \
  || ok "untagged names are excluded"

# Between seasons the correct answer is silence, not an error. If an empty
# cohort exits non-zero, the scheduled job reports a failure every day from
# August to October and the deadman cries wolf until nobody reads it.
: > "$TC_RESEARCH_DIR/universe-qualified.tsv"
./scripts/cohort.sh "$TODAY" >/dev/null 2>&1
[ "$?" -eq 0 ] && ok "an empty cohort exits 0" || bad "an empty cohort exits non-zero"

echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
```

- [ ] **Step 2: Run, confirm failure.** `chmod +x scripts/test-cohort.sh && ./scripts/test-cohort.sh` → all FAIL, script missing.

- [ ] **Step 3: Implement `scripts/cohort.sh`**

Date arithmetic must work on both BSD and GNU `date`. Convert to days-since-epoch with `awk` rather than shelling to `date -d`, which is GNU-only:

```bash
#!/bin/bash
# Print the active earnings cohort: qualified, sector-tagged names whose
# ESTIMATED next print falls in the entry window.
#
# Usage: scripts/cohort.sh YYYY-MM-DD        (Eastern, from get_datetime)
#
# The window is 21-42 days out because that is the option ENTRY window: IV
# ramps in the final ~2 weeks into a print, so the research window and the
# entry window are deliberately the same interval.
#
# An empty cohort is a correct answer between earnings seasons, so this exits 0
# with no output rather than signalling failure.
set -euo pipefail
[ "$#" -eq 1 ] || { echo "cohort: expected YYYY-MM-DD" >&2; exit 2; }
today="$1"
case "$today" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo "cohort: bad date '$today'" >&2; exit 2 ;;
esac

dir="${TC_RESEARCH_DIR:-$(cd "$(dirname "$0")/.." && pwd)/research}"
uq="$dir/universe-qualified.tsv"; st="$dir/sectors.tsv"
[ -f "$uq" ] && [ -f "$st" ] || exit 0

WIN_MIN=21; WIN_MAX=42; QUARTER=91

awk -F'\t' -v today="$today" -v wmin="$WIN_MIN" -v wmax="$WIN_MAX" -v q="$QUARTER" '
  # days since epoch, civil-from-days inverse (Howard Hinnant algorithm)
  function g(y,m,d,  era,yoe,doy,doe) {
    if (m <= 2) y--
    era = int((y >= 0 ? y : y-399)/400)
    yoe = y - era*400
    doy = int((153*(m + (m>2 ? -3 : 9)) + 2)/5) + d-1
    doe = yoe*365 + int(yoe/4) - int(yoe/100) + doy
    return era*146097 + doe - 719468
  }
  function tod(s,  p) { split(s,p,"-"); return g(p[1]+0,p[2]+0,p[3]+0) }
  NR==FNR { if ($1 != "") sec[$1]=$2; next }
  FNR==1 && $1=="symbol" { next }
  {
    sym=$1; le=$8
    if (le == "" || sym == "") next
    s = sec[sym]
    if (s == "" || s == "other") next
    d = tod(le) + q - tod(today)
    if (d >= wmin && d <= wmax) printf "%s\t%s\t%s\t%d\n", sym, s, le, d
  }
' "$st" "$uq" | sort -t"$(printf '\t')" -k4,4n
```

- [ ] **Step 4: Run, confirm pass.** `./scripts/test-cohort.sh` → `6 passed, 0 failed`.

- [ ] **Step 5: Mutation-verify.** Change `d >= wmin` to `d > wmin` → the boundary assertion must fail. Delete the `s == "other"` exclusion → the `other` assertion must fail. Change the final `exit 0` path to `exit 1` → the empty-cohort assertion must fail.

- [ ] **Step 6: Commit.**

```bash
git add scripts/cohort.sh scripts/test-cohort.sh
git commit -m "Add the earnings cohort builder, where research window equals entry window"
```

## Task B4: Evidence ledger writer

**Files:**
- Create: `scripts/evidence-append.sh`
- Create: `scripts/test-evidence-append.sh`

**Interfaces:**
- Produces: `scripts/evidence-append.sh SYMBOL DATE 'JSON'` appends one line to
  `research/evidence/<SYMBOL>.jsonl`.
- Required JSON fields, all enforced: `claim` (string, the specific falsifiable
  assertion), `url` (string), `source_type` (one of `end-user`, `employee`,
  `counterparty`, `enthusiast`, `primary-doc`, `mainstream`),
  `observed` (YYYY-MM-DD, must equal the DATE argument),
  `independence` (string — the agent's stated reason this source is not a
  restatement of another).
- Consumed by: Task C1 and C2 agents.

`mainstream` is a legal `source_type` **on purpose**: recording that a story has
reached mainstream coverage is how the kill switch gets evidence. It counts
against escalation rather than toward it, which is a reader concern, not a
writer concern.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-evidence-append.sh`:

```bash
#!/bin/bash
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2
export TC_RESEARCH_DIR="$(mktemp -d)"
trap 'rm -rf "$TC_RESEARCH_DIR"' EXIT
pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }
D=2026-09-01
V='{"claim":"40% of reviews since July cite app crashes","url":"https://example.com/a","source_type":"end-user","observed":"2026-09-01","independence":"user reviews, not derived from any press release"}'

./scripts/evidence-append.sh ROKU "$D" "$V" >/dev/null 2>&1
F="$TC_RESEARCH_DIR/evidence/ROKU.jsonl"
[ -f "$F" ] && ok "appends a valid record" || bad "no ledger file written"

./scripts/evidence-append.sh ROKU "$D" "$V" >/dev/null 2>&1
n="$(wc -l < "$F" 2>/dev/null | tr -d ' ')"
[ "$n" = "2" ] && ok "appends rather than overwrites" || bad "ledger has $n lines, want 2"

no_claim='{"url":"https://e.com","source_type":"end-user","observed":"2026-09-01","independence":"x"}'
./scripts/evidence-append.sh ROKU "$D" "$no_claim" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a record with no claim" || bad "accepted a claimless record"

bad_type='{"claim":"c","url":"https://e.com","source_type":"blogpost","observed":"2026-09-01","independence":"x"}'
./scripts/evidence-append.sh ROKU "$D" "$bad_type" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses an unknown source_type" || bad "accepted source_type 'blogpost'"

# The baseline is only trustworthy if observation dates are. A record whose
# observed date disagrees with the pass date silently corrupts every later
# delta computed against it.
skewed='{"claim":"c","url":"https://e.com","source_type":"end-user","observed":"2026-08-01","independence":"x"}'
./scripts/evidence-append.sh ROKU "$D" "$skewed" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses observed != DATE argument" || bad "accepted a skewed observation date"

./scripts/evidence-append.sh "../../etc/x" "$D" "$V" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a symbol containing a path" || bad "accepted a traversal symbol"

# mainstream is a LEGAL type on purpose: recording that a story reached
# mainstream coverage is how the kill switch gets its evidence.
ms='{"claim":"c","url":"https://e.com","source_type":"mainstream","observed":"2026-09-01","independence":"x"}'
./scripts/evidence-append.sh ROKU "$D" "$ms" >/dev/null 2>&1
[ "$?" -eq 0 ] && ok "accepts source_type mainstream (kill-switch evidence)" \
  || bad "refused mainstream, so the kill switch can never be recorded"

echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
```

- [ ] **Step 2: Run, confirm failure**

Run: `chmod +x scripts/test-evidence-append.sh && ./scripts/test-evidence-append.sh`
Expected: all seven FAIL — the script does not exist.

- [ ] **Step 3: Implement `scripts/evidence-append.sh`**

```bash
#!/bin/bash
# Validated append for the per-name evidence ledger.
# Usage: scripts/evidence-append.sh SYMBOL DATE 'JSON_OBJECT'
#
# The ledger is the mechanism, not a log: the signal is the DELTA against a
# name's own history, so a corrupt observation date poisons every future
# comparison. Refuse rather than coerce.
set -euo pipefail
[ "$#" -eq 3 ] || { echo "evidence-append: expected SYMBOL DATE JSON, got $#" >&2; exit 2; }
symbol="$1"; date="$2"; json="$3"

case "$symbol" in
  *[!A-Za-z0-9.-]*|"") echo "evidence-append: bad symbol '$symbol'" >&2; exit 2 ;;
esac
case "$date" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo "evidence-append: DATE must be YYYY-MM-DD" >&2; exit 2 ;;
esac

req='has("claim") and has("url") and has("source_type") and has("observed") and has("independence")'
types='["end-user","employee","counterparty","enthusiast","primary-doc","mainstream"]'
if ! printf '%s' "$json" | jq -e "type==\"object\" and ($req)" >/dev/null 2>&1; then
  echo "evidence-append: missing a required field" >&2; exit 2
fi
if ! printf '%s' "$json" | jq -e "$types | index(\$t)" --arg t "$(printf '%s' "$json" | jq -r .source_type)" >/dev/null 2>&1; then
  echo "evidence-append: unknown source_type" >&2; exit 2
fi
if [ "$(printf '%s' "$json" | jq -r .observed)" != "$date" ]; then
  echo "evidence-append: observed disagrees with DATE" >&2; exit 2
fi

dir="${TC_RESEARCH_DIR:-$(cd "$(dirname "$0")/.." && pwd)/research}/evidence"
mkdir -p "$dir"
printf '%s\n' "$(printf '%s' "$json" | jq -c .)" >> "$dir/$symbol.jsonl"
```

- [ ] **Step 4: Run, confirm pass.** Expected `7 passed, 0 failed`.

- [ ] **Step 5: Mutation-verify.** Remove the `observed` comparison → the skew assertion must fail. Remove `mainstream` from `types` → the kill-switch assertion must fail. Change `>>` to `>` → the append assertion must fail. Delete the symbol `case` → the traversal assertion must fail.

- [ ] **Step 6: Commit.**

```bash
git add scripts/evidence-append.sh scripts/test-evidence-append.sh
git commit -m "Add the evidence ledger writer, which refuses a skewed observation date"
```

## Task B5: Escalation outcome ledger

**Files:**
- Create: `scripts/escalation-log.sh`
- Create: `scripts/test-escalation-log.sh`

**Interfaces:**
- Produces:
  - `scripts/escalation-log.sh raise SYMBOL DATE 'JSON'` — appends to
    `research/escalations.jsonl`. Required fields: `claim`, `direction`
    (`up`|`down`), `event_date` (YYYY-MM-DD, the print or catalyst the thesis
    is timed to), `source_types` (JSON array, length ≥ 2).
  - `scripts/escalation-log.sh score ID OUTCOME` — appends a scoring record,
    `OUTCOME` one of `right`|`wrong`|`void`.
- Consumed by: nothing in this plan. It is read by Chris and by any later
  hit-rate analysis.

**Why this exists, and why it is not optional.** Design spec §8.1 records that
Chris's hit rate is unmeasured, and §4 sizes every thesis at 10% *because* of
that. Two remembered winners cannot distinguish an edge from survivorship. If
escalations are never scored, that question is still open in a year and the
size can never justifiably move. Spec §9 lists an accumulating hit rate as a
success criterion and short-run P&L explicitly as a non-criterion — this file
is the only thing that makes the former measurable.

Recording `direction` and `event_date` **at raise time** is the whole point: a
prediction logged before the outcome is evidence, and one reconstructed
afterward is a story.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-escalation-log.sh`:

```bash
#!/bin/bash
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2
export TC_RESEARCH_DIR="$(mktemp -d)"
trap 'rm -rf "$TC_RESEARCH_DIR"' EXIT
pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }
F="$TC_RESEARCH_DIR/escalations.jsonl"
V='{"claim":"fuel hedged 18mo forward","direction":"up","event_date":"2026-10-15","source_types":["counterparty","enthusiast"]}'

./scripts/escalation-log.sh raise DAL 2026-09-01 "$V" >/dev/null 2>&1
[ -f "$F" ] && ok "raise appends an escalation" || bad "no escalations file"

# A raise with fewer than two source types must be refused: the escalation bar
# IS two independent types, and a writer that accepts one lets the bar rot.
one='{"claim":"c","direction":"up","event_date":"2026-10-15","source_types":["end-user"]}'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$one" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a raise with only one source type" \
  || bad "accepted a single-source escalation, defeating the bar"

nodir='{"claim":"c","event_date":"2026-10-15","source_types":["end-user","employee"]}'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$nodir" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a raise with no direction" \
  || bad "accepted an unfalsifiable escalation with no predicted direction"

baddir='{"claim":"c","direction":"sideways","event_date":"2026-10-15","source_types":["end-user","employee"]}'
./scripts/escalation-log.sh raise DAL 2026-09-01 "$baddir" >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses a direction outside up|down" || bad "accepted direction 'sideways'"

id="$(head -1 "$F" | jq -r .id)"
./scripts/escalation-log.sh score "$id" right >/dev/null 2>&1
grep -q '"outcome":"right"' "$F" && ok "score appends an outcome" || bad "score did not record"

./scripts/escalation-log.sh score "$id" maybe >/dev/null 2>&1
[ "$?" -ne 0 ] && ok "refuses an outcome outside right|wrong|void" || bad "accepted outcome 'maybe'"

echo; echo "$pass passed, $fail failed"; [ "$fail" -eq 0 ]
```

- [ ] **Step 2: Run, confirm failure.** All six FAIL; the script does not exist.

- [ ] **Step 3: Implement `scripts/escalation-log.sh`**

```bash
#!/bin/bash
# The escalation prediction ledger.
#
#   scripts/escalation-log.sh raise SYMBOL DATE 'JSON'
#   scripts/escalation-log.sh score ID right|wrong|void
#
# A prediction recorded BEFORE the outcome is evidence; one reconstructed after
# is a story. direction and event_date are therefore mandatory at raise time.
set -euo pipefail
dir="${TC_RESEARCH_DIR:-$(cd "$(dirname "$0")/.." && pwd)/research}"
mkdir -p "$dir"
file="$dir/escalations.jsonl"
mode="${1:-}"

case "$mode" in
  raise)
    [ "$#" -eq 4 ] || { echo "escalation-log: raise SYMBOL DATE JSON" >&2; exit 2; }
    symbol="$2"; date="$3"; json="$4"
    case "$symbol" in *[!A-Za-z0-9.-]*|"") echo "bad symbol" >&2; exit 2 ;; esac
    req='has("claim") and has("direction") and has("event_date") and has("source_types")'
    printf '%s' "$json" | jq -e "type==\"object\" and ($req)" >/dev/null 2>&1 \
      || { echo "escalation-log: missing required field" >&2; exit 2; }
    d="$(printf '%s' "$json" | jq -r .direction)"
    case "$d" in up|down) ;; *) echo "escalation-log: direction must be up|down" >&2; exit 2 ;; esac
    n="$(printf '%s' "$json" | jq -r '.source_types | length')"
    [ "$n" -ge 2 ] || { echo "escalation-log: need 2+ source types, got $n" >&2; exit 2; }
    id="$symbol-$date-$(printf '%s' "$json" | cksum | awk '{print $1}')"
    printf '%s' "$json" | jq -c --arg i "$id" --arg s "$symbol" --arg d "$date" \
      '. + {id:$i, symbol:$s, raised:$d, kind:"raise"}' >> "$file"
    ;;
  score)
    [ "$#" -eq 3 ] || { echo "escalation-log: score ID OUTCOME" >&2; exit 2; }
    id="$2"; outcome="$3"
    case "$outcome" in right|wrong|void) ;; *) echo "escalation-log: bad outcome" >&2; exit 2 ;; esac
    grep -q "\"id\":\"$id\"" "$file" 2>/dev/null \
      || { echo "escalation-log: no such id" >&2; exit 2; }
    jq -nc --arg i "$id" --arg o "$outcome" \
      --arg t "$(TZ=America/New_York date +%Y-%m-%d)" \
      '{id:$i, outcome:$o, scored:$t, kind:"score"}' >> "$file"
    ;;
  *) echo "escalation-log: mode must be raise|score" >&2; exit 2 ;;
esac
```

- [ ] **Step 4: Run, confirm pass.** Expected `6 passed, 0 failed`.

- [ ] **Step 5: Mutation-verify.** Drop the `-ge 2` check → the single-source assertion must fail. Drop the `direction` case → both direction assertions must fail. Drop the outcome case → the outcome assertion must fail.

- [ ] **Step 6: Commit.**

```bash
git add scripts/escalation-log.sh scripts/test-escalation-log.sh
git commit -m "Log escalation predictions before outcomes, so a hit rate can exist"
```

---

# Phase C — Agents and scheduling

## Task C1: Scout agent and command file

**Files:**
- Create: `.claude/agents/scout.md`
- Create: `.claude/commands/scout.md`

**Interfaces:**
- Consumes: `scripts/cohort.sh` (B3), `scripts/evidence-append.sh` (B4), `scripts/escalation-log.sh` (B5).
- Produces: a return contract identical in shape to the existing agents —
  line 1 a canonical summary, then optional `ESCALATE:` lines. `FAIL:` on a
  sweep that could not complete.

Requirements the command file must state explicitly, because each traces to a
finding rather than a preference:

1. **The signal is the delta against the name's own ledger history, never the
   absolute level.** Roku's Trustpilot score sat flat at 1.4–1.5 from 2024 to
   2026: a bad level with no delta, correctly worth nothing, because it is long
   since priced.
2. **Escalation requires 2+ distinct `source_type` values,** not 2 URLs. Five
   outlets recycling one press release is one source.
3. **A `mainstream` hit is a kill switch.** Search mainstream outlets explicitly
   and record their silence as a positive finding; if the story is there, it is
   priced and the idea is dead.
4. **Claims must be specific and falsifiable** — "40% of reviews since July cite
   crashes on Roku devices", never "sentiment is poor".
5. **Resolve the account hash with `get_accounts`** if any broker read is
   needed; the prompt supplies none and CLAUDE.md redacts it under §7.4.
6. **Invoke scripts as bare relative paths.**
7. The agent gets **no order tools and no Write/Edit** — all writes go through
   the §W whitelist scripts, matching `research-scout.md`'s construction.

- [ ] **Step 1: Write the agent file** with a `tools:` frontmatter granting `Read, Glob, Grep, Bash, ToolSearch, WebSearch, WebFetch, mcp__schwab__get_datetime, mcp__schwab__get_accounts, mcp__schwab__get_quotes, mcp__schwab__get_option_chain`.

- [ ] **Step 2: Write the command file** covering §A preconditions, §B the sweep (cohort → per-name source pass → ledger append), §C the escalation test, §D the return contract.

- [ ] **Step 3: Dry-run against one real name.** Pick a cohort member and run the sweep manually, confirming the ledger rows are specific and falsifiable rather than vague sentiment. **This step is a quality gate, not a smoke test** — if the claims read like "sentiment is mixed", the prompt is wrong and must be fixed before scheduling.

- [ ] **Step 4: Commit.**

## Task C2: Catalyst agent and command file

Same shape as C1, but source-driven rather than cohort-driven: sweeps the three
sectors for merger chatter, product launches, supply agreements and outages,
independent of the earnings calendar. This is the channel that carries the six
weeks between earnings seasons, and the channel Chris's airline trade actually
came from.

**Files:**
- Create: `.claude/agents/catalyst.md`
- Create: `.claude/commands/catalyst.md`

**Interfaces:**
- Consumes: `research/sectors.tsv` (B1), `scripts/evidence-append.sh` (B4).
  It does **not** consume `cohort.sh` — that is the difference between the two
  agents.
- Produces: the same return contract as C1.

- [ ] **Step 1: Write `.claude/agents/catalyst.md`** with the same `tools:`
  frontmatter as C1 (no order tools, no Write/Edit).

- [ ] **Step 2: Write `.claude/commands/catalyst.md`.** It restates C1's seven
  numbered requirements verbatim — the delta rule, 2+ source types, the
  mainstream kill switch, falsifiable claims, hash resolution via
  `get_accounts`, bare relative script paths, no order tools — because an agent
  reads only its own contract file and must not have to infer a rule from a
  sibling. It differs from C1 in exactly one place: the sweep is driven by
  sector-wide source monitoring for merger chatter, product launches, supply
  agreements and outages, with no earnings-calendar gate.

- [ ] **Step 3: Dry-run against one live sector.** Confirm the output is
  specific and falsifiable. Same quality gate as C1 Step 3: vague sentiment
  means the prompt is wrong, not that the sector is quiet.

- [ ] **Step 4: Commit.**

```bash
git add .claude/agents/catalyst.md .claude/commands/catalyst.md
git commit -m "Add the catalyst agent, the channel that carries the off-season"
```

## Task C3: Wire the jobs into the scheduler

**Files:**
- Modify: `scripts/scheduled-run.sh`
- Modify: `docker/crontab`
- Modify: `scripts/test-scheduled-run.sh`
- Modify: `scripts/job-deadman.sh`

- [ ] **Step 1: Write the failing tests.** Extend the existing per-job loops so `scout` and `catalyst` are covered by: the window guard, the allowlist-vs-contract-file check, and the bare-relative-path assertion added on 2026-08-27. The existing test that walks "all 6 job allowlists" must become 8 and must fail until the jobs exist.

- [ ] **Step 2: Run, confirm failure.**

- [ ] **Step 3: Add the `scout)` and `catalyst)` job arms** to `scheduled-run.sh`, each with `agent=`, `window_open`/`window_close`, `days`, `job_timeout`, `allowed=` (read-only tools plus `Bash(scripts/evidence-append.sh:*) Bash(scripts/cohort.sh:*) Bash(scripts/sector-write.sh:*) Bash(scripts/escalation-log.sh:*)`), and a `prompt=`. Add crontab entries on off-the-herd minutes, outside the existing job times.

- [ ] **Step 4: Run the suite, confirm pass.**

- [ ] **Step 5: Extend `job-deadman.sh`** to expect the new jobs, and confirm an empty cohort still reports `ok` rather than a miss.

- [ ] **Step 6: Commit.**

---

## Verification before handing back

- [ ] `./scripts/check-consistency.sh` → `CONSISTENT`
- [ ] All ten suites green (seven existing plus `test-sector-write`, `test-cohort`, `test-evidence-append`, `test-escalation-log`)
- [ ] `git log --oneline` shows one commit per task
- [ ] `git diff origin/main --stat` reviewed for anything touching `deploy`
- [ ] No account identifier anywhere in the diff (CLAUDE.md §7.4)
- [ ] **Nothing pushed to `origin/deploy`.** The server adopts that branch automatically at every job fire; deployment is Chris's call, separately.
