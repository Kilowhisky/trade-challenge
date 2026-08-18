# Screener API spike — verdict (design rev2 §10.1)

Spike date: 2026-08-14. Spike code is throwaway; this document is the only
artifact kept. Key used throughout: `$FMP_API_KEY` (placeholder only — never
the literal value), sourced from `.env.local` (gitignored, verified before
this doc was written).

## Verdict: **FMP, partial** — screener fails, drift-screen endpoint passes

- **Screener (stock-screener / company-screener): FAILS on the free tier**,
  on both the legacy v3 path and the newer "stable" path. Neither error is
  "endpoint doesn't exist" — both are explicit paywall responses (see below).
  There is no free-tier screener available from FMP.
- **Earnings calendar (drift-screen source): PASSES**, on the "stable" path
  only (the v3 path is deprecated/blocked). Returns EPS and revenue
  actual-vs-estimate, which is what the drift screen needs.
- **Finnhub: not probed.** Chris registered only an FMP account (Step 1 was
  already resolved before this spike started); no `FINNHUB_API_KEY` exists to
  probe with. This is moot for the screener specifically — the brief itself
  notes "Finnhub has no screener, so it can at best support the drift
  screen," and FMP's stable earnings-calendar already covers the drift screen
  on the free tier, so there is nothing a Finnhub probe would add right now.

**`get_movers` retirement is effective only from this verdict forward
(design rev2 §3.3); if verdict is no-API, the Task 8 universe file is the
screener until revisited.** Applied literally to the screener half of this
verdict: the screener came back no-API (paywalled on both endpoint
generations), so **`research/universe.md` (543 names, built in Task 8)
remains the screener** until a paid FMP tier (or another provider) is
adopted and re-verified. The drift screen is not in the same boat — its
endpoint passed — so it can use the FMP stable earnings-calendar endpoint
going forward, with `research/universe.md` as its own fallback only if that
endpoint later breaks.

## Endpoints probed

All requests below are the exact shapes tried, with `$FMP_API_KEY`
substituted for the real key. Response bodies shown are structure/error
text only — no key material.

### 1. Screener — v3 (legacy) — FAILED

```
curl -s "https://financialmodelingprep.com/api/v3/stock-screener?priceMoreThan=5&priceLowerThan=1015&volumeMoreThan=1000000&exchange=NYSE,NASDAQ&limit=50&apikey=$FMP_API_KEY"
```

HTTP 403. Body:
```json
{
  "Error Message": "Legacy Endpoint : Due to Legacy endpoints being no longer supported - This endpoint is only available for legacy users who have valid subscriptions prior August 31, 2025. Please visit our documentation page https://site.financialmodelingprep.com/developer/docs for our current APIs."
}
```

### 2. Screener — stable (current generation) — FAILED

```
curl -s "https://financialmodelingprep.com/stable/company-screener?priceMoreThan=5&priceLowerThan=1015&volumeMoreThan=1000000&exchange=NYSE,NASDAQ&limit=50&apikey=$FMP_API_KEY"
```

HTTP 402. Body:
```
Restricted Endpoint: This endpoint is not available under your current subscription please visit our subscription page to upgrade your plan at https://financialmodelingprep.com/
```

Verdict: the stable screener exists and the key authenticates fine (it's a
plan-tier 402, not an auth 401/403), but it is a paid-plan-only endpoint.
Free tier cannot use it under either generation.

### 3. Earnings calendar — v3 (legacy) — FAILED

```
curl -s "https://financialmodelingprep.com/api/v3/earning_calendar?from=2026-08-10&to=2026-08-14&apikey=$FMP_API_KEY"
```

HTTP 403, same "Legacy Endpoint" body shape as endpoint 1.

### 4. Earnings calendar — stable (current generation) — PASSED

```
curl -s "https://financialmodelingprep.com/stable/earnings-calendar?from=2026-08-10&to=2026-08-14&apikey=$FMP_API_KEY"
```

HTTP 200. Body (full, 2 rows for this date range):
```json
[
  {
    "symbol": "CSCO",
    "date": "2026-08-12",
    "epsActual": 1.22,
    "epsEstimated": 1.17,
    "revenueActual": 17252000000,
    "revenueEstimated": 16836300000,
    "lastUpdated": "2026-08-14"
  },
  {
    "symbol": "RIOT",
    "date": "2026-08-10",
    "epsActual": -0.68,
    "epsEstimated": -0.30328,
    "revenueActual": 174235000,
    "revenueEstimated": 154309400,
    "lastUpdated": "2026-08-14"
  }
]
```

**Payload fields present:** `symbol`, `date`, `epsActual`, `epsEstimated`,
`revenueActual`, `revenueEstimated`, `lastUpdated`. This is exactly the
EPS/revenue actual-vs-estimate data the drift screen needs.

**Payload fields absent — important gap:** there is **no report-time field**
(no `time`, `bmo`/`amc`, or "before/after market" indicator anywhere in the
object). The drift screen will need before/after-open timing from some other
source (e.g. cross-reference against Schwab quote/instrument data, or accept
that this endpoint alone cannot distinguish a pre-open print from an
after-close print — only the calendar date).

**Generation note for future maintainers:** FMP has fully deprecated the
`api/v3/*` generation for accounts created after 2025-08-31 (this account
included) — every `v3` path returned the same "Legacy Endpoint" 403 in this
spike. The `stable/*` generation is the live one; use it exclusively going
forward. Confirmed working shape: `stable/earnings-calendar`. Confirmed
paywalled shape (not usable free): `stable/company-screener`.

## Rate limits observed

No `x-ratelimit-*` (or any request-count/quota) response headers were
present on **any** of the four probes above — checked via `curl -D -` on
each request; full header dumps showed only standard `date`, `content-type`,
`content-length`, CORS, and `etag` headers, nothing quota-related. FMP does
not expose remaining-quota headers on these endpoints at this tier.

In the absence of header data, the daily cap was checked against FMP's
published pricing/docs (not the response itself, and not verified by
exhausting the quota): **free tier = 250 requests/day**, consistent across
multiple current FMP documentation and third-party sources as of this spike
date. Treat this as a documented figure, not an observed one — spec §8.5
says the ledger records provider headers "when present"; here, none were
present, so the ledger should log the 250/day figure as doc-sourced rather
than header-sourced, and any future header sighting should supersede it.

## No-API fallback

The no-API branch (design rev2 §3.3) does not need to be hypothesized — it
already exists: `research/universe.md` (543 names, S&P 500+400 band-filtered,
built in Task 8) is the standing fallback screener list, live-reverified
under §4.9/§4.10 at use time same as any other candidate source.

## Self-review

- Confirmed `.env.local` is gitignored (`.gitignore` has `.env.*`) before
  sourcing it, and confirmed the key length only (never the value) to prove
  it loaded.
- Tried both endpoint generations for both endpoint families before
  concluding failure, per the task instructions — did not stop at the first
  v3 403.
- Distinguished the two screener failures precisely: v3 is a hard
  deprecation (403, endpoint gone), stable is a paywall (402, endpoint
  exists but requires upgrade) — different failure modes, both land at the
  same "no free-tier screener" conclusion, but a future re-verification
  should hit the stable 402 only, since v3 is permanently gone.
- Did not fabricate a rate-limit header reading — headers were checked and
  genuinely absent; the 250/day figure is explicitly labeled as doc-sourced,
  not header-sourced.
- Verified no key material appears in this document before commit (see
  commit step below).

## Concerns

- **Screener gap is real, not just free-tier friction.** Upgrading FMP would
  fix it, but that's a paid-plan decision for Chris, not something this spike
  resolves. Until then, every screener use goes through `universe.md` and its
  weekly-refresh manual process (per Task 8's report).
- **No report-time (bmo/amc) field on the earnings calendar.** Whatever
  downstream drift-screen logic assumes a company reported before/after the
  session may need a secondary source or an explicit "unknown, treat
  conservatively" fallback for this field.
- **Rate limit is a documented figure, not a measured one.** If FMP's free
  tier terms change, this doc will not reflect that until someone hits a 429
  or re-checks the docs.

## Dry-run addendum (2026-08-14 evening)

The first live postclose run (events ledger, 21:52 ET row) surfaced findings
that supersede parts of the verdict above:

- **`stable/earnings-calendar` is a thin pointer, not a calendar.** It
  returned only 2 rows for 8/10–8/14 and 7 rows for 8/17–8/28, while missing
  known reporters (HD, LOW, CRM, DLTR, GAP) that should have been in range.
  Treat this endpoint as a pointer only, not a source of truth — the drift
  screen's date source remains the web-confirmed calendar-watch process, not
  this API.
- **`stable/historical-price-eod/full` is symbol-gated on the free tier.**
  CSCO/AAPL/SPY/WMT were served; AXTA/GAP were refused as premium-only. This
  means tilt computation collapsed back to Schwab bars rather than this
  endpoint.
- **`stable/quote` is paywalled entirely** on the free tier.
- **No rate-limit headers observed on any endpoint** in this dry run either,
  consistent with the spike above.

Net: the FMP free tier is a thin calendar pointer only — it does not carry
the discovery load. The Schwab-quoted `universe.md` sweep is the discovery
backbone, exactly as the no-API branch above anticipated.

## Daily-bars spike (2026-08-16/17)

Scope: spec §12 item 4 — evaluate Stooq (keyless CSV) and Yahoo-chart-style
endpoints as the free daily-bars source for §D.3 tilt computation, adopted
only if bars match Schwab dailies within tolerance on a ~10-symbol sample.
Full report: `.superpowers/sdd/spike-bars-report.md`.

**Stooq: REJECT.** 11/11 fetch attempts (10 symbols + 1 CSX retry) returned
an identical ~800-byte Cloudflare JS proof-of-work challenge page, never
CSV data. A non-JS-executing fetch tool gets 0% success at any scale — not
a rate-limit or quality problem, a hard technical block.

**Yahoo chart endpoint (`v8/finance/chart/<SYMBOL>?range=6mo&interval=1d`):
CONDITIONAL ADOPT, shortlist-only.** Tolerance check against 6 fresh Schwab
`get_advanced_price_history` pulls (CSX, USB, WMT, GAP, AXTA, HONA; last
close, date-aligned mid close, ATR10, SMA50): 4/6 symbols passed clean
(diffs at the floating-point floor); AXTA missed the 1% ATR tolerance by a
hair because Yahoo silently dropped one trading day (2026-08-11) from its
series — a completeness gap, not an adjustment/rounding issue. Every first
fetch attempt (10/10) returned "Too Many Requests"; every retry (10/10)
succeeded — treat that as steady-state, always wrap in retry-after-backoff,
never treat as a keyed rate limit (no crumb/cookie ceremony needed on this
endpoint, unlike Yahoo's v7 quote endpoints). Use raw `quote.close`, never
`indicators.adjclose` (Schwab bars aren't dividend-adjusted). **Always
cross-check bar count / date-set against the Schwab pull for the same
window before trusting a Yahoo-sourced ATR or SMA** — that's the guard that
would have caught the AXTA gap.

**Sweep-scale verdict: does not replace the universe sweep.** §D.3's
543-name universe screen runs ~50 symbols/chunk/postclose-run — at that
volume, Yahoo's 100%-first-attempt-429 behavior (measured at n=10, zero
sweep pressure) is a rate-limit wall waiting to happen against an
undocumented, unofficial endpoint with no published quota to plan around.
Confirming the task brief's prediction: **the honest pattern is daily bars
for the ranked shortlist survivors only (~15/day out of a swept chunk,
matching §D.3's WATCH cap and the ~20–30-name options roster), never the
raw 543-name sweep**, which keeps the weekly-proxy caveat for the
qualification filter itself. Three prior agent runs on this exact task
being killed mid-fetch by machine sleep across just a 10-symbol sample is
weighed as corroborating (not conclusive) evidence that a per-symbol
bars-fetch loop against Yahoo is slow/fragile enough in practice to matter
at sweep scale, even though the sleeps themselves were a laptop issue, not
a network one.

**Net adoption:** Schwab stays the daily-bar source of record for any name
reaching promotion/order consideration (already reliable, 6/6 fresh calls
succeeded this run, zero added provider risk). Yahoo's marginal value is
upgrading the §D.3 per-chunk ranking math from weekly-proxy to daily-bar
precision for the ~15 names that make a chunk's shortlist — after the cheap
`get_quotes` filter has already cut ~50 down to ~15, not before. This keeps
Yahoo volume in the teens/day, where its retry-tolerant behavior is
survivable, and off the critical path for anything that spends money.
