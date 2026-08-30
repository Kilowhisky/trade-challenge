# Strategy v2 — a proposal, with the theory it rests on

**Status: proposal, not adopted.** Nothing here changes `CLAUDE.md`. Where it
would tighten or replace playbook behaviour it is marked *(strategy rule)* and
is Chris's call. Dollar figures are 2026-08-27 snapshots, never rules.

---

## 0. The binding constraint, stated first

Everything below is downstream of one number. As of 2026-08-27 there are **11
NYSE sessions left**, of which **8 permit opening a position** (§8 lockout from
09-10). Over that horizon:

| Quantity | Value |
|---|---|
| σ of a market ETF over 11 sessions | **3.3%** |
| σ of a single 30%-vol stock over 11 sessions | **6.3%** |
| Equity risk premium over 11 sessions | **0.26%** |
| Grinold expected excess, *elite* IC = 0.10, BR = 8 | **0.95%** |
| Grinold expected excess, realistic IC = 0.05 | **0.47%** |

Read those together. **The achievable edge is roughly one tenth of the noise.**
On the idle cash, the entire equity risk premium for the remaining window is
about four dollars. A realistic skill edge on the whole book is worth ten to
thirty dollars; one ordinary day's fluctuation is a hundred and fifty.

This is not pessimism, it is Grinold (1989): `IR = IC × √BR`. Breadth is the
number of *independent* bets. We have eight sessions and a handful of names, so
breadth is single digits, and √8 = 2.83 cannot rescue a small IC. No selection
process — none, however clever — produces a reliable three-week result at this
breadth.

**Therefore: the final ranking of a three-week contest is decided by variance
and luck, not by skill.** Any plan claiming otherwise is selling something. The
honest question is not "how do we pick better" but "what exposure do we want,
and how much variance do we choose to carry."

---

## 1. Why the current design cannot trade at all

Independent of strategy quality, v1 is *inoperable* on the machine we built:

- Promotion to **HOT** requires a quote timestamped **inside regular trading
  hours** (research.md §C), and a candidate may never go straight to HOT in the
  pass that discovered it — it must sit at WATCH first.
- The only research passes scheduled are **preopen 08:17** and **postclose
  16:22**. Both are outside RTH. There is **no intraday research job in the
  crontab at all**; `research.md` and `research-scout` are never dispatched.
- `trader.md` may only act on what `candidates.md` already qualifies. It cannot
  originate.

So HOT is empty by construction, permanently. The file says so itself: *"Zero
HOT is a legitimate state — the answer is a re-verification pass in regular
hours."* That pass does not exist. Every position on the book was opened in an
*attended* session (08-14, 08-24). Since going unattended the system has been
**incapable of opening a position**, which is why 52.3% of competition capital
has sat in cash and ~60 consecutive execute passes returned `EXEC none`.

**The fix is not another cron line.** The RTH-quote requirement at *research*
time is redundant: §4.10 already forces a fresh quote, an identifier
round-trip, a notional three-way compare and a staleness check at *order* time.
Requiring RTH twice — once in a window where it is structurally impossible — is
the whole defect.

> **Design principle for v2: qualification happens on daily bars at postclose;
> live verification happens at order time, where the manual already demands it.
> Build for the machine we have — a decide-at-close, execute-next-session
> system — instead of a live-desk strategy the schedule cannot run.**

---

## 2. What the literature actually supports at this horizon

Four things have enough precedent to build on. Each is listed with the reason
it fits *our* constraints, and with its strongest disconfirming evidence,
because a plan that only cites supporting papers is the "unfounded" failure
again.

### 2.1 Beta is the only return stream that needs no forecast

The equity risk premium is the one edge requiring no skill, no signal and no
breadth. Samuelson (1969) and Merton (1969) established that under CRRA utility
the optimal risky share is **horizon-independent** — a short horizon is *not* a
reason to hold cash. Holding 52% cash is therefore not neutrality; it is an
active short-beta position taken by accident.

*Against:* the premium over 11 sessions is 0.26%. Deploying fixes a leak, it
does not win a contest.

### 2.2 PEAD — the only anomaly whose horizon matches our window

Ball & Brown (1968) and Bernard & Thomas (1989): prices drift in the direction
of the earnings surprise for roughly 60 days. It fits us uniquely well:

- **Horizon matches** — the drift window is weeks, not months, unlike
  Jegadeesh–Titman momentum (3–12 month formation) which cannot resolve here.
- **It satisfies §3.7 for free.** The rule forbids *holding through* an
  earnings report. PEAD enters *after* the print, so the event is behind us and
  the next one is a quarter away. The constraint and the signal agree.
- **It is computable from daily bars at postclose** — exactly our cadence.
- Late August still has a live cohort of Q2 reporters.

*Against, and this is substantial:* McLean & Pontiff (2016) find predictor
returns 26% lower out-of-sample and **58% lower post-publication**. PEAD
specifically is among the decayed — Chordia, Subrahmanyam & Tong (2014) and
Martineau (2022) document its weakening, though Meursault et al. (2023) still
find it with text-based surprise measures. **Assume a fraction of the textbook
effect, not the textbook effect.**

### 2.3 Overnight tilt — free, because it is only a scheduling choice

Cooper, Cliff & Gulen (2008) find the equity premium is earned **entirely
overnight**; Lou, Polk & Skouras (2019, *A Tug of War*) show overnight and
intraday premia are opposite in sign across many factors. Intraday open-to-close
is flat to negative.

Why it matters here: it costs nothing. Our executor runs to 15:52 ET. Preferring
**late-session entries over early-session** captures the overnight segment
without a new signal, a new tool, or a new call. It is a preference, not a
strategy.

*Against:* widely known, and at retail spreads on a ~$3k book the bid/ask may
exceed the effect. Treat as a tiebreaker on *when* to fill, never as a reason
to trade.

### 2.4 Inverse-volatility sizing — for consistency, not for alpha

Size so each position contributes similar risk rather than similar dollars
(risk parity; Barroso & Santa-Clara 2015 on vol-scaled momentum). §3.4 already
ATR-scales *stops*; sizing on the same axis makes risk-per-trade roughly
constant instead of a function of whichever name we happened to pick.

*Against — and it is decisive about the claim:* Moreira & Muir (2017) reported
large Sharpe gains from volatility management, but **Cederburg, O'Doherty, Wang
& Yan (2020, JFE)** show that realistic out-of-sample versions generally earn
*lower* certainty-equivalent returns and Sharpe ratios than the unmanaged
portfolios, because the spanning regressions are structurally unstable. So:
adopt inverse-vol as a **risk-budgeting discipline**, and claim **no alpha from
it whatsoever**.

---

## 3. The objective function — the actual decision, and it is Chris's

The two goals below are different, and the literature is clear that they call
for opposite behaviour. Almost all confusion about "should we be more
aggressive" is a failure to pick one.

### Objective A — maximise expected final value
Deploy the idle sleeve to intended exposure, tilt selection to PEAD, size
inverse-vol, prefer late-session fills, skip options. Expected outcome: roughly
flat to modestly up, with 3–4% of noise around it. **This is the
expected-value-maximising plan and it will probably not win a contest.**

### Objective B — maximise probability of finishing first
Tournament theory is unambiguous. Brown, Harlow & Starks (1996) show mid-year
*losers* raise volatility in the second half, because the payoff is convex —
a call-option-like structure where ranking pays and mediocrity and disaster pay
alike. Chevalier & Ellison (1997) find the same risk-shifting from convex
flow-performance incentives, and the contest-design literature confirms
equilibrium risk-taking is highest when the number of winners is low.
**If only first place pays, and you are behind, buying variance is rational —
not a discipline failure.**

The rule-compliant instrument for that is *long options*: convexity with the
loss capped at premium (§1.2 forbids selling, so this is the only convex tool
available). Expected value is negative after spread and theta at our floors
(≥18 DTE, delta ≥0.35, spread ≤10%). It buys P(win), not E[value].

**These trade against each other. Pick one, explicitly, and I will build to it.**

### What I will not do
Reach Objective B by loosening §3.1, §3.2, §3.5 or §3.6. Not because of
timidity, but because §9.3 forbids amending a rule in reaction to a losing
position, and this is the exact circumstance it was written for. The caps are
also what bound the downside of Objective B: with 18.8% of room before the
§3.6 halt, an unbounded variance push does not have space to work — it has
space to end the account. Chris can amend the rules under §9 in a calm,
explicit conversation; I will not drift them.

---

## 4. Concrete plan

**Phase 1 — make the system able to trade (mechanical, no strategy content).**

1. Move qualification to **daily bars at the postclose pass**. Drop the
   RTH-timestamp requirement from HOT promotion; it duplicates §4.10, which
   re-verifies live at order time and is the gate that actually protects us.
2. Keep the two-pass rule (WATCH → HOT) — it is a real guard against acting on
   a single noisy observation, and it survives the change intact.
3. The executor re-verifies at order time exactly as today. No loosening: the
   §4.10 four gates, the earnings check and the settled-cash check all stand.

**Phase 2 — deploy the idle sleeve (Objective A baseline, either way).**

4. Bring total exposure to the intended level with a **liquid, diversified core
   holding**, sized inverse-vol. This is the beta decision and it does not
   require a forecast, so it is not gated on candidate quality.
5. Selection tilt for anything above the baseline: **PEAD** — recent reporter,
   surprise in the right direction, entered after the print, held into the
   drift window. Sized as a satellite, not the core.
6. **Late-session fills preferred** where the choice is free.

**Phase 3 — only if Objective B is chosen.**

7. Deploy the options sleeve as deliberate convexity within §3.2's existing
   caps and quality floors — long calls only, no new rule needed. Sized as
   premium we are willing to lose in full, because that is the modal outcome.

**What I will report honestly:** with 8 opening sessions left, Phase 1 + 2
plausibly recovers the 0.26% premium leak and a fraction of a PEAD tilt. In
dollars that is tens, not hundreds. If the gap to the leaders is large, Phase 2
does not close it, and Phase 3 closes it only by accepting a high chance of
finishing lower in exchange for a small chance of finishing first.

---

## 5. Sources

- Ball & Brown (1968), *JAR* — earnings announcement information content.
- Bernard & Thomas (1989), *JAR* — post-earnings-announcement drift.
- Brown, Harlow & Starks (1996), *JF* — [Of Tournaments and Temptations](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1996.tb05203.x).
- Cederburg, O'Doherty, Wang & Yan (2020), *JFE* — [On the performance of volatility-managed portfolios](https://www.sciencedirect.com/science/article/abs/pii/S0304405X2030132X).
- Chevalier & Ellison (1997), *JPE* — risk taking as a response to convex incentives.
- Chordia, Subrahmanyam & Tong (2014) — anomaly attenuation with liquidity.
- Cooper, Cliff & Gulen (2008) — the premium is earned overnight.
- Grinold (1989), *JPM* — [the Fundamental Law](https://blankcapitalresearch.com/learn/grinold-fundamental-law-active-management), `IR = IC × √BR`.
- Lou, Polk & Skouras (2019), *JFE* — [A Tug of War: Overnight versus Intraday Expected Returns](http://www.econ.yale.edu/~shiller/behfin/2015-04-11/lou_polk_skouras.pdf).
- McLean & Pontiff (2016), *JF* — [Does Academic Research Destroy Stock Return Predictability?](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365)
- Merton (1969) / Samuelson (1969) — horizon-independence of the optimal risky share.
- Moreira & Muir (2017), *JF* — [Volatility-Managed Portfolios](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513).
- Barroso & Santa-Clara (2015), *JFE* — momentum risk management.
- [A review of the Post-Earnings-Announcement Drift](https://www.sciencedirect.com/science/article/pii/S2214635020303750) — survey incl. Martineau (2022), Meursault et al. (2023).
