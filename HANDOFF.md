# Handoff — 2026-09-04, afternoon

**Why no trades: fixed at the root, live from Friday's open.** You asked why
the system places nothing. The answer was structural: the executor enters
only from a HOT candidate, HOT can only be written by the intraday research
pass, and that pass was never scheduled on the server (last run 2026-08-18,
from a laptop). Separately, the scout's earnings cohort was empty because
the weekly sweep never wrote the qualified set and nothing ever wrote the
sector tags it joins against. Both are fixed in the commit below —
your instruction, verbatim: *"Do 1 & 2. Your /goal is to be an autonomious
trading engine"* (11:25 PDT).

## 0. What changes, and when you will see it

| When (ET) | Job | What it does now |
|---|---|---|
| **Fri 09-05 09:57, then hourly to 14:57** | `research` (new) | The intraday pass: quotes WATCH names live, promotes to HOT only with the full §C checklist, writes `research/candidates.md`. A HOT is relayed to `#llm-yolo`. |
| every :07/:22/:37/:52 | `execute` (unchanged) | Reads the file. If a HOT exists it may open the entry workflow — **the order still waits for your ✅/❌**. React ❌ to decline; nothing happens without you. |
| Sat 09-05 07:40 | `weekly-universe` | Now runs the filter without the refused `bash -c` wrapper, and **must** pass `--emit-qualified-set`: writes `universe-qualified.tsv` (~3,200 names) and the new `universe-names.tsv`. |
| Sat 09-05 09:40 | `sector-tag` (new) | Classifies those names into the three scout sectors and writes `research/sectors.tsv` in batches. Reports `SECTORS … cohort N`. |
| Tue 09-08 07:12 | `scout` (unchanged) | First pass with a non-empty cohort, if any qualified name reports 21–42 days out. |

Expect the first research passes to produce **WATCH updates, not HOT**: last
night's board had no name with a measured ATR, and a HOT needs the stop
geometry written. If Friday ends with `HOT — none` again, that is the pass
doing its job, not the old failure. What to watch for instead: the `PASS`
line in `status/cron/2026-09-05-research.log` six times, each with a fresh
`Last pass:` stamp in `research/candidates.md`.

**Honest note on today's session limit.** This build ran on the laptop
from 11:25 to ~12:30 PDT, inside market hours, against the rule learned on
09-02. It was your instruction and I kept it small (no subagents), but the
14:32/14:47 ticks and 14:37/14:52 executes should be checked in the heartbeat
for session-limit failures before trusting the afternoon.

---

## 1. Needs you

1. **Token re-auth Saturday or Sunday** (unchanged). Minted 2026-09-01
   ~10:50 ET; blind Tuesday 2026-09-08 ~10:50 ET. Runbook: `ssh -L
   8182:127.0.0.1:8182 brewmaster`, `docker compose run --rm schwab-auth`.
2. **Read the Discord relays this week.** A `📈 RESEARCH` message means a
   HOT was written and the executor may request an order within 15 minutes.
   A `✅ SECTORS` line Saturday should show `cohort N`; `names 0` means the
   sweep did not emit the qualified set and the sweep log needs reading.
3. **The 09-03 false deadman alarms are fixed** (NUL bytes in the heartbeat;
   `grep -a`); nothing to do.

## 2. What was wrong, precisely

- **No writer for HOT.** `research.md` §C requires a quote timestamped inside
  regular hours. The 08:17 preopen run is barred from promotions; the 16:22
  postclose run quotes after the close. Only `/research` promotes, and it was
  only ever chained after `/tick` in a laptop session. 180 execute passes
  since 08-24 all reported `EXEC none` against `HOT — none` — correctly.
- **Empty cohort by construction.** `cohort.sh` joins
  `research/universe-qualified.tsv` against `research/sectors.tsv`. The 08-29
  sweep dropped `--emit-qualified-set` while improvising around the
  permission gate (`bash -c` is refused), so the first file never existed;
  no job ever wrote the second. `scout` reported `cohort 0` daily and said so.
- **Catalyst sleeve genuinely empty** — early September has no qualifying
  reporters; unchanged and legitimate.

## 3. What changed (all on `main` and `deploy`, adopted by the server)

- `docker/crontab`: `research` hourly at :57, 09:57–14:57 weekdays;
  `sector-tag` Saturday 09:40.
- `scripts/scheduled-run.sh`: the two job cases (read-only allowlists, no
  order tools — enforced by `test-scheduled-run.sh`, now 87 checks), and a
  research-specific `HOT-FRESH` relay that says the executor may act.
- `scripts/universe-filter.sh`: `--rank-top` defaults to `rules.yml`; on
  `--emit-qualified-set` it also writes `universe-names.tsv`.
- `scripts/sector-write.sh --batch DATE` (heredoc of `SYMBOL SECTOR`
  lines; all-or-nothing validation).
- `.claude/commands/sector-tag.md`, `.claude/agents/sector-tagger.md` (new);
  `research.md` §Scheduled; `research-scout.md` server context;
  `weekly-universe.md` §B.4 rewritten to the accepted invocation form.
- `scripts/check-consistency.sh`: the crontab must carry `research` and
  `sector-tag`, matching what their command files document.

---

## Earlier — 2026-09-02 overnight (kept for the record)

### 1. What happened on 09-02, stated plainly (§7.3)

**The server lost its Claude session from 15:17 to 17:10 ET.** Every job in
that window failed in five seconds with `You've hit your session limit ·
resets 5:10pm` — three ticks (15:17, 15:32, 15:47), three execute passes
(15:22, 15:37, 15:52), the 16:04 session close and the 16:22 postclose run.
The cause was my build session on the laptop: it ran a long chain of
subagents through the afternoon and consumed the subscription budget the
server's `CLAUDE_CODE_OAUTH_TOKEN` shares. The 18:33 catalyst run succeeded
after the reset.

Consequences, honestly: the book went **unwatched from 15:02 ET to the
close** with three positions open. Their GTC stop-limits were resting at
Schwab throughout (AMH, CSX, USB — all three re-verified live at 23:46 ET),
nothing traded, and no watch had tripped at 15:02. The §7.2 close file was
not written at 16:04; I wrote it at 23:46 ET with a forced session-close
run (same practice as the 2026-08-26 late write). Ticks before 15:02 were
normal all day.

**Operating rule learned:** heavy Claude work on this subscription during
09:30–16:30 ET starves the server. Until the server has its own account,
build sessions stay outside market hours.

### 2. What I did on the night of 09-02

- **Forced session close** at 23:46 ET → `status/2026-09-02.md`, pushed to
  the store. Close: account value $3,733.69, high-water mark $3,800.00
  carried unchanged, drawdown −1.75%, level OK, 3 positions / 3 matching
  stops, settled cash $2,393.57, no cash call, no restriction, no `ALERT.md`.
- **Deployed `main` → `origin/deploy`** (25 commits: the v3 engine spec, the
  Phase 0a plan, and the `engine/` foundation). The server adopted
  `2cf4957` through its own gate at 23:49 ET (`check-consistency.sh` and
  `test-pre-order-check.sh` passed; recorded in `status/cron/deployed.jsonl`).
  Nothing under `engine/` runs on the server yet — it is inert until Phase 0b.
- **No `deploy.sh` run was needed**: the image is unchanged and supercronic
  already carries the scout and catalyst jobs (both fired today).

### 3. Needs you

1. **Token re-auth by the weekend.** The token was minted 2026-09-01 ~10:50
   ET. The watchdog warns Saturday, pages Sunday, and the account goes blind
   Tuesday 2026-09-08 ~10:50 ET. Re-auth Saturday or Sunday, before Monday's
   open, with the standing runbook (`ssh -L 8182:127.0.0.1:8182 brewmaster`,
   `docker compose run --rm schwab-auth`). No restart needed afterwards.

Resolved overnight on your instruction ("Fix #2 and #3", 21:14 PDT), no order
placed:

2. **CSX stop discrepancy — explained and closed.** The resting stop is the
   same order since 2026-08-14, never replaced; trigger and limit are each
   exactly $0.14 below placement, and CSX went ex-dividend 2026-08-31 at
   $0.14. Schwab reduces open GTC sell stops by the cash dividend on the
   ex-date unless the order is marked Do-Not-Reduce. The live 45.20 / 42.93 is
   authoritative; a §7.1 correction row is in `trade-log.csv` and the ruling
   is in `status/2026-09-02.md`. Override available: order a re-raise to
   45.34 / 43.07 in a live session (one replace, needs ✅).
3. **§3.8 correlation — recorded.** 60-day log-return correlations to the
   2026-09-01 close: AMH–CSX 0.204, AMH–USB 0.267, CSX–USB 0.102; none
   index-correlated (max USB–SPY 0.214). Recorded in `status/2026-09-02.md`
   and `research/candidates.md`; adds are unblocked on correlation grounds.
   Next weekly check: first tick of the week of 2026-09-07.

Also open, no deadline: the v3 prerequisites — a second Schwab app with the
tailnet callback URL, Tailscale on the Pi and your phone, and a healthchecks.io
project (or a no). Phase 0b cannot run live without the first two.

## 4. Tomorrow (2026-09-03)

Scheduled jobs fire as normal from 07:05 ET; the first `scheduled-run` job
finds the server already on `2cf4957`. Ticks resolve the high-water mark from
`status/2026-09-02.md` ($3,800.00, account-value basis). No earnings, no
options, no leveraged clocks on the book. The laptop's broker tunnel had dropped
during the evening and was restarted at 00:15 ET; the server was never affected.

## 5. v3 status

Phase 0a is merged to `main`: `engine/` package `tc` with settings, rules,
property-tested arithmetic, the consistency checker (reaching `.claude/` and
`engine/`), the token store, the read-only broker with fake and redacting
recorder, the SQLite store, the clock, and the `tc` CLI. 98 tests; mypy
strict and ruff clean over code and tests. Next is the Phase 0b plan (loops,
scheduler, Discord webhook, healthchecks, HTTP `/health` and
`/oauth/callback`, shadow diff, docker, host probe). The spec is at
`docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md`.
