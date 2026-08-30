# Information-Edge Scout — design

**Status:** design approved in conversation 2026-08-30; not yet implemented.
Supersedes the sleeve/candidate architecture in `strategy.md` and the
competition framing throughout `CLAUDE.md`. Dollar figures are dated snapshots,
never rules — every cap below is a percentage by deliberate convention.

**Reference convention:** `CLAUDE.md §X` means a rule in the trading manual;
a bare `§X` means a section of *this document*. The two numbering schemes
collide (both have a §3.2 and a §3.3) and the distinction is load-bearing.

---

## 1. What changed, and why this document exists

The competition ended 2026-08-29 when Chris withdrew. That removes the
constraint that made the previous strategy unwinnable *and* removes the
justification for most of its rules.

Two separate failures are being corrected, and it matters that they are
separate:

1. **v1 was inoperable.** Promotion to HOT required a quote timestamped inside
   regular trading hours, while the only research passes ran pre-open and
   post-close. No candidate could ever qualify. The system was structurally
   incapable of opening a position, which is why ~60 consecutive execute passes
   returned `EXEC none` and 63.7% of capital sat idle. This was a plumbing
   defect wearing a strategy's clothes.
2. **v1 had no edge to operate on even if it had worked.** Its selection logic
   was generic factor screening at a three-week horizon, where the achievable
   edge is roughly a tenth of the noise (Grinold: `IR = IC × √BR`, with breadth
   in single digits). No amount of engineering rescues that.

v2 replaces the *source of edge*, not just the pipeline. The edge is Chris's
domain knowledge in three sectors, applied to information that is public but
dispersed, before it is aggregated into price.

---

## 2. The strategy

**Thesis.** Locate claims about a company's products, operations or prospects
that (a) are corroborated across genuinely independent source types, (b) have
not yet reached mainstream financial coverage, and (c) plausibly move a
financial line item. Express the resulting view in long options positioned to
pay when the market re-prices — usually, though not only, at an earnings print.

This is Chris's own historical method. Two worked examples he supplied:

- **Paramount+** — early user, poor app quality corroborated by weak reviews
  and negative customer sentiment; bought puts ahead of earnings; the print
  confirmed and the position paid.
- **An airline** — blog reporting that it had secured fuel supply ~18 months
  forward while fuel costs were expected to rise; bought calls expecting
  relative earnings strength; the print confirmed.

Note the second did **not** originate from an earnings calendar. It was a
standing story that later cashed out at earnings. The architecture must support
both origins.

### 2.1 Why this has a foundation

- **Grossman & Stiglitz (1980)** — if information is costly to acquire, prices
  cannot fully reflect it; there must be a return to acquisition. This is the
  theoretical licence for the whole approach.
- **Huang (2018, *JFE*)** — 14.5M Amazon reviews; *abnormal* customer ratings
  predict revenue and earnings surprises, long/short spread 56–73bp per month,
  no reversal. The Paramount+ trade, generalised.
- **Green, Huang, Wen & Zhou (2019, *JFE*)** — crowdsourced employer reviews
  predict earnings surprises.
- **Hong & Stein (1999)**; **Hong, Lim & Stein (2000)** — information diffuses
  gradually and the effect is strongest in low-coverage names. This is
  "hasn't reached mainstream yet," formalised, and it dictates the universe
  design in §3.1.
- **Van Nieuwerburgh & Veldkamp (2010, *RES*)** — rational under-diversification
  where an information advantage exists. Concentration is a feature here.

### 2.2 What is NOT claimed

- **No claim of a general-purpose alpha engine.** The edge is Chris's judgment
  in three named sectors. The machine supplies breadth, memory and bookkeeping.
- **No claim that the vol premium is beaten.** See §4. Options into a known
  event are systematically expensive; the design minimises what we pay, it does
  not make it free.
- **No validated hit rate.** Two remembered winners is not a track record, and
  survivorship is the obvious failure mode. See §8, open question 1.
- **Decay is assumed.** McLean & Pontiff (2016) find predictor returns 26% lower
  out-of-sample and 58% lower post-publication. Expect a fraction of any
  published effect size.

---

## 3. Architecture

Four new components. The dispatcher, write-path scripts and agent construction
are **reused unchanged** — `scheduled-run.sh` carries locking, window guards,
heartbeat, verdict classification, the Discord relay and 230 passing tests. v1's
failure was never the plumbing.

### 3.1 Universe — sector-scoped, deliberately not liquidity-ranked

The weekly sweep qualifies ~3,196 names on liquidity and then truncates to the
500 most liquid. **v2 draws from the qualified set, not the ranked 500.** A
liquidity ranking systematically discards low-coverage mid-caps, which is
precisely where Hong/Lim/Stein locate the diffusion edge — and where both of
Chris's worked examples live.

The binding floor becomes **options tradability** (CLAUDE.md §3.2 open-interest
and spread floors), not size or coverage. That floor is the honest one: an untradeable
option ends an idea regardless of how good the thesis is.

A sector tagger classifies the qualified set into the three sectors once, then
incrementally as the weekly sweep refreshes. Schwab exposes no sector field
(verified against `get_instruments` fundamental projection), so classification
is done by model over well-known tickers.

**Sectors in scope:** consumer software / streaming / apps; airlines / travel /
transport; semiconductors / hardware / infrastructure.

### 3.2 Cohort builder — the earnings calendar as scheduler

`last_earnings + ~1 quarter` estimates the next print. Names reporting in 3–6
weeks form the active cohort. Three consequences, all intended:

- Work is paced to a few deep dives per session instead of an unbounded sweep.
- Every name is revisited each quarter, which is the correct sampling rate for
  an abnormal-sentiment signal — this quarter's customer evidence is compared
  against last quarter's.
- The research window *is* the entry window from §4. The scheduler and the
  trade structure agree rather than fighting.

The weekly sweep must be extended to emit qualified sector names **with**
last-earnings dates rather than truncating to the top 500.

### 3.3 Evidence ledger — the mechanism, not a log

Append-only, per name, in the existing gitignored store. Each observation
records the claim, URL, source type, observation date, and **the agent's stated
reason for treating the source as independent**.

This is load-bearing. The signal is the **delta against a name's own baseline,
not the level** — Huang's result is about *abnormal* ratings. Without stored
history there is no baseline, no delta, and no signal. It also means the system
compounds in value over quarters rather than restarting each pass, which is the
opposite of v1's behaviour.

### 3.4 Scout and catalyst agents

Two read-only agents, no order tools, constructed like the existing ones:

- **Scout** works the calendar cohort (§3.2).
- **Catalyst** runs an always-on channel for merger chatter, product launches,
  supply agreements and outages — the origin of Chris's airline trade, and the
  only channel that is live between earnings seasons.

Neither forms a thesis. They assemble corroborated evidence and propose the
option structure that would express it. **Chris makes every thesis call and
approves every order.**

### 3.5 Cadence

| Job | When | Output |
|---|---|---|
| Weekly sweep + sector refresh | weekly | qualified sector universe with earnings dates |
| Scout cohort dive | daily | 2–3 names deepened in the ledger |
| Catalyst sweep | daily | non-calendar stories |
| Escalation | on bar clear | Discord ping with evidence trail + proposed structure |

**Seasonality is expected, not a fault.** Q2 closed in early August; Q3 begins
mid-October. The calendar channel is quiet for roughly six weeks from now and
the catalyst channel carries that gap. A quiet scout in September is the system
working.

---

## 4. Trade expression

CLAUDE.md §1.2 and §1.3 forbid selling options and spreads, so vega cannot be hedged. The
only available levers are **delta, expiry and entry timing.**

The problem being solved: implied volatility rises into a scheduled print and
collapses after, often 30–40%+ in a session. Underlyings move *less* than the
implied move roughly 70–75% of the time. **A correct directional call can still
lose**, because the crush destroys more than the move creates.

| Lever | Setting | Reason |
|---|---|---|
| Entry | **3–6 weeks before the print** | IV ramps in the final ~2 weeks; entering early buys vol before it is bid up, and is long vega into the ramp |
| Delta | **band 0.45–0.75** | Crush destroys *extrinsic* value; ITM is mostly intrinsic. Below ~0.45 is a lottery ticket into a known vol collapse; above ~0.75 pays option spreads to own something that behaves like stock |
| Expiry | **2–4 weeks past the earnings date**, never the front weekly | Front expiry carries the most concentrated event vol. Expiry past the print means we are never *forced* to sell into the crush and can hold into the drift |
| Size | **~10% of account value per thesis; ~30% aggregate** | Sized assuming total loss of premium, because that is the modal outcome |

**On sizing.** This *reduces* the existing CLAUDE.md §3.2 single-position cap from 20%.
Kelly logic: with an uncertain edge and a binary-ish payoff, fractional Kelly is
the discipline — at a ~50% hit rate and 2:1 payoff, full Kelly is ~25% and
quarter-Kelly ~6%. 10% is already aggressive against an unvalidated edge; it
should fall further if §8's open question resolves badly, and may rise if it
resolves well.

**Note the honest framing:** this structure minimises what we pay for the
earnings volatility premium. It does not defeat it. The premium is real and it
is against us, and the thesis must be right often enough to clear it.

---

## 5. The corroboration standard

The escalation bar is **2+ independent sources, a specific falsifiable claim,
and absence from mainstream coverage**. Everything below the bar still lands in
the ledger; only bar-clearing items interrupt.

Independence is enforced **structurally, by requiring 2+ distinct source
types** — counting URLs is what makes these systems rot, because five outlets
recycling one press release reads as five confirmations and is one.

| Source type | Examples |
|---|---|
| End-user | app store reviews, subreddit reports, review platforms |
| Employee | employer-review trends, job postings, role changes |
| Counterparty / supply | supplier announcements, contracts, trade press |
| Enthusiast / technical | teardowns, benchmarks, specialist blogs |
| Primary documents | filings, patents, regulatory dockets |
| **Mainstream financial press** | **disqualifier — not a source** |

Mainstream coverage is a **kill switch**: if the story is there, it is priced,
and the idea is dead by definition. The scout must search mainstream outlets
explicitly and record their silence as a positive finding.

Accumulating corroboration ("trajectory") is a **ranking** input, not a separate
trigger — it sharpens the queue without adding interrupts.

---

## 6. Rule amendments (CLAUDE.md §9 record)

All § references in this section are to CLAUDE.md.
To be committed as one amendment, with Chris's words quoted verbatim per CLAUDE.md §9.2.

> "The competition is over, i'm dropping out."
> "Ditch 3.7 entirely" — refined on scope to: *kill earnings bar, keep halt rule only.*

| Rule | Change |
|---|---|
| **§8** scoring & endgame | **Deleted.** No competition; a scoring rule with no contest still constrains trades. |
| **§3.7** event risk | Reduced to the **halt rule only**. The earnings prohibition is removed for all instruments. The corporate-action *exit* requirement is replaced by a **stop re-pricing** requirement — same arithmetic protection, without forcing an exit from a merger thesis. |
| **§3.2** option sizing & quality | Delta `≥0.35` floor → **band 0.45–0.75**. Single-thesis premium cap 20% → **~10%**. Aggregate remains ~30%. |
| **"Competition capital"** | → **account value** throughout; every §3 percentage re-anchors. The $900 reserve stops being a scoring artifact and becomes a working **settlement buffer**. |
| **§3.6** circuit breaker | Retained at −20%, measured against an **account-value** high-water mark. A drawdown brake is sound independent of any contest. |

**Explicitly retained.** CLAUDE.md §1 in full — no margin, no selling options, no spreads,
no shorting, no penny/OTC. These are what make the system safe to run
unattended, and none of them constrains the strategy.

**CLAUDE.md §3.3 is retained and matters more than before.** Closing every long option at
5 DTE and never holding into expiration week is unchanged. We are about to hold
options through binary events; OCC auto-exercise on a cash account remains the
largest tail risk in the account.

CLAUDE.md §5 settlement discipline, §7 logging and §9 amendment procedure are
unchanged.

---

## 7. Source access — measured, not assumed

Probed 2026-08-30 against ROKU.

| Source | Result |
|---|---|
| WebSearch over user sentiment | **Strong.** Surfaced Reddit threads, Trustpilot, specialist press, and consolidated complaint patterns with dates |
| Review-aggregator pages | **Fetch cleanly**, and return multi-platform time series — directly useful as baseline pointers |
| Glassdoor direct | **403 Forbidden.** Employee signal is weak; lean on job postings and search summaries |
| Publisher articles (direct fetch) | **Unreliable.** WhatHiFi returned navigation furniture with the body truncated |

**Consequences for the build:** WebSearch is the workhorse; direct fetch is
best-effort and extraction failures must be recorded rather than silently
dropped. The employee source type is the weakest of the six and should not be
required for a bar clear. Aggregator sites are pointers of unknown provenance,
possibly model-generated — the ledger cites the underlying platform, never the
aggregator alone.

**The probe also validated the central design choice.** Roku's Trustpilot score
has been flat at 1.4–1.5 from 2024 through early 2026: a bad *level* with no
*delta*. Under the delta-not-level rule in §3.3 above, this correctly yields **no signal** — the
dissatisfaction is old news and priced. Separately, the same company shows 4.7
on app stores against 1.4 on Trustpilot, different populations entirely, which
is why the ledger tracks **specific claim frequencies** rather than aggregate
star ratings. A naive sentiment score would be worse than useless.

---

## 8. Open questions

1. **Chris's real hit rate — unresolved and material.** Two remembered winners
   cannot distinguish a genuine edge from survivorship, and §4's sizing rests on
   it. Options: reconstruct past trades from records, or paper-track the first
   N escalations before committing capital. **This should be settled before
   size increases beyond the initial ~10%.**
2. **Sector tagging stability** — does model classification of the qualified
   universe stay consistent week over week, or does the sector universe churn?
3. **Base rate of bar clears** — if the moderate bar produces far more or far
   fewer than the expected 2–4 per week, it needs recalibration against reality
   rather than against expectation.

## 9. Success criteria

- The system surfaces bar-clearing candidates with an auditable evidence trail,
  at a rate Chris finds usable rather than noisy.
- Escalations are *specific and falsifiable* — a claim that can later be scored
  right or wrong, not a sentiment impression.
- Every escalation is scored after the fact, so a hit rate accumulates and §8.1
  becomes answerable from our own data within a few quarters.
- **Explicit non-criterion:** short-run P&L. At this breadth, a handful of
  outcomes cannot distinguish edge from luck, and judging the system by early
  P&L would be the same error as v1's design.

## 10. Out of scope

Backtesting infrastructure; broad discovery outside the three sectors;
automated thesis formation; any change to CLAUDE.md §1, §5 or §7.
