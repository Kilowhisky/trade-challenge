# Handoff — 2026-09-08, 18:45 ET

**Plan 0c is merged and running on the Pi.** `main` = `1e3eadd` (33 commits, engine
670 tests, runner 88, mypy --strict, ruff, both consistency checkers clean); the
server adopted it, both images were rebuilt, `tc-engine` and the new `tc-runner`
are up. The engine now mounts the MCP server (`/mcp/research`, `/mcp/decide`,
bearer-gated — a request without a bearer is 401), the v2 research store was
imported once (19 documents, 54 tombstones, 267 screen rows, 61 IV, 62 OI, 62
events, the 500-name universe), and the first runs were started by hand:
`weekly_universe` (engine code, no Claude) and `postclose` (the first Claude job
through the runner). Their verdicts land in `job_runs`; logs on the Pi under
`/home/knome/tc-logs/`. Runbook: `docs/superpowers/plans/2026-09-08-v3-plan0c-bootstrap.md`.

**What runs on its own from now on** (`config.yml` schedule, ET): tick every 15 min
09:32–15:47, session close 16:04, token check 07:05, expectations 07:30, backup
23:30, scout 07:12, catalyst 18:33, preopen 08:17, postclose 16:22, research
hourly 09:57–14:57, weekly universe Sat 07:40, sector tag Sat 09:40.

**Still not possible: trading.** No order path exists until Plan 1
(`docs/superpowers/plans/2026-09-08-v3-plan1-order-path.md`, written, not
started). Resting GTC stops at Schwab are the only protection.

## Needs you

1. **Discord webhook** — still none. The engine and the host probe cannot reach
   you. Create a webhook in `#llm-yolo` (or `#engine-shadow`) named `tc-engine`,
   append `TC_DISCORD_WEBHOOK_URL=` and `TC_DISCORD_SHADOW_WEBHOOK_URL=` with it
   to `/srv/tc/.env`, put `PROBE_WEBHOOK=` in `/etc/tc/probe.env`, then
   `docker compose -f docker/docker-compose.yml restart engine` and
   `sudo systemctl enable --now tc-healthprobe.timer`. Or grant the bot
   Manage Webhooks and I do it.
2. **§9 amendment (your words needed):** `CLAUDE.md` line 13 still resolves the
   high-water mark "per tick.md §B5"; that document is a tombstone. The mark
   lives in the engine's `session_status` table (`tc/loops/session.py`) and is
   read through `status_latest`. Same for the header's `scripts/*.sh` mentions
   (spec §14). Say the word and I write the amendment commit quoting you.
3. Token: fresh, dies 2026-09-14 ~21:10 PDT; the engine posts the re-auth link
   at day 5 once the webhook exists; `tc auth-url` prints it any time.

---

# Handoff — 2026-09-08, 00:15 ET

**The engine has its token (installed 2026-09-07 ~21:10 PDT via the tailnet
callback; dies 2026-09-14 ~21:10 PDT, re-auth prompt at day 5).** First real
reconcile at 00:10 ET: 4 positions / 4 stops, account value $3,718.11, HWM
$3,800.00, drawdown −2.16%, no flags. The 09:32 tick on 09-08 is the first
watched session under v3. Still no Discord webhook, so trips reach nobody
until `/srv/tc/.env` has `TC_DISCORD_SHADOW_WEBHOOK_URL`; check
`https://brewmaster.tail14458e.ts.net:8443/health` and `/api/ticks?date=` instead.

**Plan 0c (the Claude runner + research tools) has its bootstrap runbook and
exit checklist written:**
`docs/superpowers/plans/2026-09-08-v3-plan0c-bootstrap.md`. It covers writing
the two new secrets files (`/srv/tc/.env` gains `TC_RUNNER_TOKEN`,
`TC_RUNNER_URL`, `TC_MCP_RESEARCH_TOKEN`, `TC_MCP_DECIDE_TOKEN`; a new
`/srv/tc/runner.env` holds `CLAUDE_CODE_OAUTH_TOKEN`, `TC_RUNNER_TOKEN`,
`TC_ENGINE_URL`), building and starting the two new containers (`runner`,
and the `engine` container's `/mcp/research` + `/mcp/decide` mounts), the v2
store import, and the seeding order (`weekly_universe` → `sector_tag` →
`postclose`/`research`) that gets `scout` off `cohort 0`. Not yet run on the
Pi — this is the next thing to do once the token/webhook items above are
settled.

---

# Earlier — 2026-09-07 (Labor Day), 13:15 ET

**The old stack is retired.** Chris, 10:03 PDT: *"The old broker is dead."*
`tc-broker` and `tc-scheduler` are stopped (not deleted — rollback is
`docker compose up -d broker scheduler` plus a schwab-mcp re-auth), and the
host's nightly `deploy.sh` cron is commented out so it cannot restart them.
Nothing in v2 runs any more: no ticks, no research, no execute passes.

**The v3 engine is running on the Pi** (`tc-engine`, image built from
`docker/Dockerfile.engine` at a3dd3f4, shadow mode, no Discord yet):
`curl -s http://127.0.0.1:8080/health` on the Pi. High-water mark seeded at
$3,800.00 (basis account value, from `status/2026-09-04.md`). It is **BLIND
until a token exists** — every tick writes a BLIND row and the book is
watched only by the GTC stops resting at Schwab (AMH, CSX, USB, IQV).

## Needs you, now

1. **Mint the engine's token.** Run on the Pi:
   `cd ~/trade-challenge && docker compose -f docker/docker-compose.yml exec engine tc --config /app/repo/config.yml --env /srv/tc/.env auth-url`
   Open the printed URL in any browser, log in with MFA, accept. The browser
   is sent to `https://127.0.0.1:8182/?code=…` and fails to load — that is
   expected (it is the old app's registered callback). Copy the ENTIRE
   address-bar URL and run
   `… exec engine tc --config /app/repo/config.yml --env /srv/tc/.env auth-complete '<that url>'`
   (single quotes). `token-status` should then read `state=fresh`. The engine
   picks the token up on its next call; no restart.
2. **A Discord webhook** (optional but strongly wanted): channel
   `#engine-shadow` → Integrations → Webhooks → new → copy URL; append
   `TC_DISCORD_WEBHOOK_URL=<url>` and `TC_DISCORD_SHADOW_WEBHOOK_URL=<url>` to
   `/srv/tc/.env` on the Pi, `PROBE_WEBHOOK=<url>` to `/etc/tc/probe.env`, then
   `docker compose -f docker/docker-compose.yml restart engine` and
   `sudo systemctl enable --now tc-healthprobe.timer`.
3. **Tailscale is DONE** (2026-09-07 13:20 ET): the Pi is `brewmaster.tail14458e.ts.net`,
   HTTPS certs enabled, `tailscale serve` fronts the engine on **port 8443**
   (443 is Pi-hole). The phone callback to register on the Schwab app is
   `https://brewmaster.tail14458e.ts.net:8443/oauth/callback (Chris's choice for now; brewhouse.wetzelrice.com via Caddy later)`; changing an
   app's callback triggers Schwab re-approval (1–3 days), so either register
   it on a NEW app and switch keys when approved, or accept a possible pause.

## What the engine cannot do yet — and the plan

It watches (reconcile, seven tick watches, clocks, close + HWM, expectations,
`/health`). It **cannot place, replace or cancel an order, and runs no
research** — those are Plan 0c (Claude runner + research tools) and Plan 1
(order path). With v2 gone those two plans are the only path back to a system
that trades; they start next. Until Plan 1 lands, a stop that fills, a partial
fill, or an option clock cannot be acted on by anything but you at Schwab.

Labor Day note: the engine's ticks today were recorded `missed`/`BLIND`
because a blind engine assumes a weekday is a trading day (by design).

---

# Handoff — 2026-09-06 (Sunday), 17:30 ET

**The old stack is down until you re-auth.** The Pi rebooted Saturday ~17:44 ET.
When it came back, the containers had no DNS for a while (pihole started after
docker); `tc-broker` crashed on a Discord lookup and never listened on :8000, so
`tc-scheduler` (killed by the reboot, exit 255) could not restart behind it.
Saturday's 07:40 `weekly-universe` and 09:40 `sector-tag` had already failed
that morning on the same outage (`Request timed out`, `fetch of origin/deploy
failed`) — so **no qualified set and no sector tags exist yet**.

What I did at 17:10 ET today: DNS was healthy again, so I restarted
`tc-broker` and started `tc-scheduler` directly. The scheduler is up (no jobs
until Tuesday; Monday is Labor Day). The broker is **not starting on purpose**:
its entrypoint reports `token is 5d old, past the 5-day forced re-auth` and
waits for a fresh token.

## Needs you before Tuesday 09:30 ET

1. **Re-auth the old broker** (runbook unchanged): `ssh -L 8182:127.0.0.1:8182 brewmaster`,
   then `cd ~/trade-challenge && docker compose -f docker/docker-compose.yml run --rm schwab-auth`.
   The broker self-starts when the token appears. Hard expiry of the current
   token is Tuesday 2026-09-08 ~10:48 ET; after that the account is blind
   either way.
2. **After re-auth, force the missed weekend jobs** (they need the broker):
   `docker exec tc-scheduler /app/scripts/scheduled-run.sh weekly-universe --force`
   then `… sector-tag --force`. Until they run, Tuesday's scout reports `cohort 0`.
3. Watch `#llm-yolo` at 09:57 ET Tuesday for the first research pass of the week.

## v3 engine — Phase 0b merged to `main` (6d75f63), not deployed

See the section below for what it is and the runbook
(`docs/superpowers/plans/2026-09-04-v3-phase0-runbook.md`) for how to bring it
up beside the old stack once the three prerequisites are done. `main` has NOT
been pushed to `deploy`: the new compose `engine` service is behind a profile
so the nightly deploy cannot trip over it, but I want you to see this note
before the server adopts 22 commits.

---

# Handoff — 2026-09-04, afternoon

## v3 engine — Phase 0b built, not yet deployed

**What exists.** Phase 0b of the v3 engine is complete on
`feat/v3-engine-0b`: an asyncio `tc run` service — scheduler, §4.5
reconciliation, the tick watches, session close and the §3.6 high-water
mark, the §3.3/§3.5 clocks, the token lifecycle with the phone re-auth
callback, expectations checks — with outbound Discord and healthchecks
pings, an HTTP `/health` + `/oauth/callback` + `/api/status` + `/api/ticks`
surface on `127.0.0.1:8080`, a `shadow-diff` tool that compares its view
against the old `status/`/`status/ticks/` ledgers, a docker service
(`docker/Dockerfile.engine`, the `engine:` entry in
`docker/docker-compose.yml`), and a host probe outside docker
(`host/healthprobe.py` on a systemd timer). It is **built and reviewed, not
running anywhere yet** — nothing has deployed it to the Pi.

**What it does NOT do.** No orders — `Broker` is read-only by construction
(`grep -rn "place_order\|replace_order\|cancel_order" engine/` is empty);
there is no runner and no scheduled Claude job (that is Plan 0c); and in
shadow mode (`config.yml`'s `shadow.enabled: true`) it posts only to a
separate `#engine-shadow` webhook, never to `#llm-yolo` — the old broker's
Discord bot and approval gate are completely undisturbed. It does not write
to the old stack's sidecar store, and it coexists beside `tc-broker` /
`tc-scheduler`, which keep running exactly as before.

**Three prerequisites Chris owns, none satisfied yet:**

1. A **second Schwab app**, registered in the developer portal with
   callback `https://<pi>.<tailnet>.ts.net/oauth/callback` (approval takes
   one to three days) — a second app gives the engine its own refresh
   token so shadow mode cannot split-brain the live broker's.
2. **Tailscale on the Pi and on the phone**, with MagicDNS and HTTPS
   certificates enabled in the admin console.
3. **A healthchecks.io project**, or an explicit decision to decline it and
   rely on the host probe and expectations checks alone.

Full deploy sequence — host layout, bring-up beside the old stack, seeding
the high-water mark, the first token, Tailscale, the nightly shadow diff,
and the Phase 0 exit checklist — is
`docs/superpowers/plans/2026-09-04-v3-phase0-runbook.md`.

---

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
