# Handoff — 2026-09-01, overnight

**Read this first. Two things need you, and the account cannot trade until the
first one is done.**

---

## 1. THE SCHWAB TOKEN IS DEAD — the account is blind

The broker container has been refusing to start since Saturday night:

```
broker-entrypoint: NOT STARTING: token is 6d old, past the 5-day forced re-auth
```

Token created 2026-08-24 22:34, now ~7 days old. The 5-day forced re-auth
passed **Saturday 2026-08-29**; the 7-day hard expiry passed **22:34 Monday**.

The account went blind mid-session Monday. Ticks returned `BLIND` and the
executor refused every pass — `REFUSE §1.7 — broker unreadable`. **That is the
system working**: it detected the failure and declined to act on unknown state
rather than guessing. Nothing was traded blind, and the three positions
(AMH/CSX/USB) still have their GTC stops resting at Schwab, which live there
and are unaffected by our token.

**Fix — about two minutes, needs your browser. Do it before 09:30 ET.**

```bash
ssh -L 8182:127.0.0.1:8182 brewmaster
cd trade-challenge/docker
docker compose run --rm schwab-auth
# paste the printed URL into your laptop browser, accept the cert warning
```

**No restart needed.** The broker polls every five minutes and starts itself
once a good token appears.

### My part in this

On 2026-08-27 I computed the token's age correctly and then told you it was
"healthy — no action needed", when what the same numbers meant was **"re-auth
by Saturday or the system goes dark."** `token-watchdog.sh` then warned in
Discord three days running (8/29 "1d until", 8/30 "0d until", 8/31 "-1d"), and
I had already primed you to read those as routine. The watchdog did its job;
my framing undid it.

---

## 2. THE DEPLOY FAST-FORWARD NEEDS YOUR APPROVAL

`main` is pushed and verified at `b364c3c`. The push of `main` → `deploy` was
**blocked by the permission gate**, which is correct: the server auto-adopts
`origin/deploy` at every job fire, so that push IS the deployment.

```bash
cd trade-challenge
git push origin main:deploy
```

The server adopts it at the next job fire, gated on `check-consistency.sh` and
`test-pre-order-check.sh`, and rolls back automatically if either fails.

**One manual step after that**, because supercronic reads the crontab only at
container start and there are two new jobs:

```bash
ssh brewmaster 'cd trade-challenge && scripts/deploy.sh'
```

`deploy.sh` refuses inside 09:15–16:15 ET, so run it before the open or after
the close.

---

## What shipped (11 commits)

| Area | What |
|---|---|
| Rules | §8 deleted, §3.7 reduced to the halt rule, delta floor → band 0.45–0.75, option single-position cap 20% → 10%, competition capital → account value throughout |
| Data layer | `sector-write.sh`, `evidence-append.sh`, `escalation-log.sh`, `cohort.sh`, `universe-filter.sh --emit-qualified-set` |
| Agents | `scout` (07:12 ET, calendar-driven) and `catalyst` (18:33 ET, source-driven) |
| Safety | `check-consistency.sh` now fails on a crossed capital basis or a surviving endgame date; `repo-update.sh` refuses to detach a working checkout |

**320 assertions across 11 suites, all green. `CONSISTENT`.**

## The bug that nearly shipped

Re-anchoring §3.6 to account value while leaving its consumers alone **would
have halted all trading, permanently.** `tick.md` computed
`drawdown = (comp_capital − hwm)/hwm`, and `comp_capital` is account value
minus $900. Against an account-value high-water mark that reports about
**−24% on a completely flat book** — through the −20% halt, every session,
with nothing having lost a cent. `session-close.md` had the mirror defect.

Neither file carries a `<!--rule:-->` marker, so `check-consistency.sh`
reported `CONSISTENT` through both. That gap — the safety net cannot reach
`.claude/` — was the real defect; the individual bugs were symptoms. It is now
closed by two direct checks, both mutation-verified.

## What tomorrow looks like

Until the 16:04 session close, ticks read the **old-basis** high-water mark
($2,900) from the last status file. I traced the arithmetic: the halt test is
`account_value ≤ 0.80 × hwm` → `$3,758 ≤ $2,320` → false. **It fails safe.**
The drawdown *display* will read oddly positive until the close, then the
conversion (+$900 → $3,800) happens once, correctly.

The scout will no-op until `/weekly-universe` next runs (Saturday 07:40) and
writes `research/universe-qualified.tsv`. An empty cohort exits 0 by design.

## Two incidents on my side tonight, both recovered

1. **Codex ran `scripts/repo-update.sh` during its review**, which detached my
   branch onto `origin/deploy` and removed eleven files from the working tree.
   The commits survived. It also wrote a **false deployment record** into
   `status/cron/deployed.jsonl` claiming the server adopted `483a5e5` from
   `e73ab1c` — the server was never on `e73ab1c`. I reverted that line; the
   store is clean. `repo-update.sh` now refuses to run on a named branch, with
   a regression test that fails if the suite itself moves HEAD.
2. **I introduced a bug mid-session**: `$900` inside a double-quoted prompt
   parsed as `$9` + `00`, and `set -u` made it fatal. `bash -n` could not see
   it; the suite caught it.

## Open, and yours to decide

- **Your real hit rate is still unmeasured.** Design spec §8.1. Every thesis is
  sized at 10% *because* of that, and it should not rise until the escalation
  ledger has scored enough predictions to answer it.
- **`event_date` in the past is currently accepted** by `escalation-log.sh`.
  Arguably it contradicts "recorded before the outcome", but same-day catalysts
  have legitimate raises. Flagged, not decided.
- **The final Codex review did not finish.** I killed it after it ran that
  destructive script — an unsupervised process that can move HEAD should not be
  running during a deployment. Its first pass found nine defects, all fixed.
