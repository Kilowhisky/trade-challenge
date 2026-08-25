# Trading Competition

A one-month, cash-only trading competition run by an AI agent under a written
rule set. This repo publishes **the rules, the strategy, and the tooling** — the
constraints the agent operates under and the machinery that enforces them.

It does **not** publish results. The trade log, daily status notes, and research
output are untracked and stay on the operating machine. See
[Why git, and what changed](#why-git-and-what-changed).

## Capital and scoring

The account is split deliberately between money at risk and money that only
provides float:

| | |
|---|---|
| **Competition capital** | The money actually at risk — account value minus the reserve. Recomputed from the broker every session, never carried forward |
| **Settlement reserve** | $900.00 — float to bridge T+1 settlement, never deployed |
| **Score** | Account value at the final closing bell **minus the $900.00 reserve** |

**Mid-window amendment (2026-08-14).** After day one, $2,000.00 was added to
the account, roughly tripling competition capital. Every competitor made the same addition, by agreement, so the
scoring formula was left unchanged and rankings stay comparable. The manual's
no-transfers rule was ratified as a one-time exception for that transfer only.
The high-water mark was re-anchored to a round $2,900.00 at that point; it
ratchets upward from there and is not a current figure.

Every risk limit in the manual — position caps, sleeve ceilings, the high-water
mark, the drawdown circuit breaker — is computed against **competition capital**,
never against total account value. The reserve adds float, never risk. Total
position exposure never exceeds 100% of competition capital.

Marks are honest by construction: the liquidity floors mean scoring prices are
realizable prices. Nothing is liquidated for scoring.

## What's here

| File | Purpose |
|---|---|
| `CLAUDE.md` | **The operating manual** — the binding rule set. Loads automatically every Claude Code session. |
| `rules.yml` | **Every rule parameter, once.** The caps, floors, and thresholds the manual states in prose, in one machine-readable file. `pre-order-check.sh` reads its caps from here; `check-consistency.sh` fails if any document or script disagrees. |
| `strategy.md` | **The playbook** — how the box is actually traded: sleeve architecture, selection rules, order workflow, endgame calendar. Required reading at every session open. |
| `docs/superpowers/specs/2026-08-13-adversarial-review.md` | Four red-team agents attacking the strategy; every finding and its disposition. |
| `docs/superpowers/specs/2026-08-13-comparative-research.md` | Five research agents on what actually works in AI/small-account trading; the 12 tightenings adopted from it. |
| `docs/archive/` | Completed and superseded working documents, kept unedited as provenance: the day-one screen, the close review, the executed deep-research plan, an API spike, and a broker-tooling patch note. |
| `scripts/` | Pre-order compliance gate and its regression suite, the rule-consistency checker, the weekly universe fetch/filter pair, and the append-only writers used by the research and monitoring loops. |
| `.claude/` | Session commands and the read-only agents that run the research, monitoring, and deep-research loops. Those agents have no order tools by construction — they cannot place, cancel, or modify anything at the broker. |
| `docker/` | **The server.** Pinned toolchain image, Compose topology, and the ET schedule that fires the research jobs. See *Running on a server* below. |
| `ALERT.md` | Created only when something needs the account owner. An unacknowledged alert forces closing-only posture at the next session open. |

**Not in this repo** — these paths are **symlinks into a separate private
repository**, `Kilowhisky/trade-challenge-store`:

| Path | What it holds |
|---|---|
| `trade-log.csv` | Every order, appended before it is placed. |
| `status/` | Daily close-of-session status notes, tick ledgers, event data, scheduled-job logs. |
| `research/` | The tiered candidate list, screens, briefs, and standing research output. |

They were gitignored here and purged from this history on 2026-08-17 ahead of
publication. The private sidecar keeps them out of public view while restoring
the external witness §7.1 notes was lost, and carries scheduled-job output off
the server. The symlinks mean every path the manual, playbook, command files
and scripts already name keeps working unchanged; `scripts/sidecar-sync.sh`
pushes after each scheduled job.

The rest of `docs/` is working material: design specs for the research and
monitoring loops, a close review, an API spike, and a broker-tooling patch note.

The manual and the playbook are not interchangeable. **Where they disagree, the
manual wins** — and the disagreement is a defect to fix, not a choice to make.
Rules in the playbook that are stricter than the manual are marked
*(strategy rule)*: discretionary, changeable without an amendment.

## Working rhythm

1. Open a Claude Code session in this directory.
2. `CLAUDE.md` loads automatically. Read the playbook before acting.
3. Reconcile against the broker before anything else — never begin from an
   assumed state.
4. Trade within the rules; every order gets logged before it is placed.
5. At session close: write a status note. The log and the note stay local.

## Running on a server

The scheduled jobs used to be harness `CronCreate` entries. Those are in-memory
and die with the session, and the machine was a laptop that slept — so the
05:15 PT pre-open job could not fire on a closed lid at all. They now run on an
always-on Ubuntu box under Docker Compose.

**Two layers, deliberately separate.** The image holds only the toolchain
(`claude`, `schwab-mcp`, supercronic — every version pinned). The repo is
**bind-mounted, never baked in**, so a rule amendment or command-file fix takes
effect without a rebuild. If the repo were in the image, every §9 amendment
would become a Docker release and the container would drift from the manual.

| Service | What it does |
|---|---|
| `broker` | One long-lived `schwab-mcp server --http`. One token holder, one re-auth point — instead of a stdio subprocess per run, all racing on `token.yaml`. |
| `scheduler` | supercronic firing `scripts/scheduled-run.sh`, which owns the lock, the ET window guard, the heartbeat and the Discord relay. |
| `claude` | `docker compose run --rm -it claude` for interactive sessions. |

**The broker cannot place or cancel an order, and it is worth being precise
about what that means.** `schwab-mcp` enables writes only when given the Discord
approval configuration; a third mode, `--jesus-take-the-wheel`, enables them
behind a stub that approves everything. The scheduled broker is given neither.

Measured rather than assumed: that setting adds exactly two tools —
`place_previewed_order` and `cancel_order`. The seven `preview_*_order` tools
are registered either way. A preview is a real Schwab API call but changes
nothing, and it cannot become an order without `place_previewed_order`. So the
scheduled container can look at what an order would cost; it has no route to
sending one. `check-consistency.sh` fails if the bypass flag appears anywhere
in this repo.

### Schwab re-auth (~weekly)

The token dies at 7 days, and `schwab-mcp` forces a new login flow at 5.
`scripts/token-watchdog.sh` warns in Discord a day ahead so this is scheduled
rather than discovered.

```bash
ssh -L 8182:127.0.0.1:8182 <server>          # from your laptop
cd trade-challenge/docker
docker compose run --rm schwab-auth
# paste the printed authorization URL into your laptop browser,
# accept the self-signed certificate warning
```

**No restart needed.** The broker checks the token before handing control to
`schwab-mcp` and waits quietly when it is missing or stale, polling every five
minutes; it starts itself once a good token appears. That guard exists because
the alternative was measured: with no token, `schwab-mcp` falls through to an
interactive login flow that blocks for 300s waiting on a browser callback that
cannot arrive in a container, then dies — a five-minute-per-iteration crash-loop
under `restart: unless-stopped`. It also means an expired token produces one
Discord message, not one per restart forever.

Host networking is required, not convenience, and is declared on the
`schwab-auth` service itself (`network_mode: host`) rather than passed on the
command line — `docker compose run` has no `--network` flag. The callback
server only accepts `127.0.0.1`, and a published Docker port targets the
container IP rather than its loopback, so an SSH forward would never reach it.

### Deploying

```bash
scripts/bootstrap-server.sh          # fresh box, or --check to verify one
scripts/deploy.sh                    # pull, health-check, roll back on failure
```

`deploy.sh` **refuses to run inside 09:15–16:15 ET** without `--force`: a
restart mid-session drops any pending Discord approval, whose state is
in-memory and process-local, leaving an order neither placed nor cleanly
denied. It runs from the **host's crontab** (installed by
`bootstrap-server.sh`), never inside the scheduler container — the container
has no docker CLI, and a deployer living inside the thing it deploys kills
itself the moment compose recreates its own container. On nights when the
image is unchanged it still runs `docker compose up -d`, so a compose-file
edit delivered by `repo-update.sh` takes effect the same evening rather than
waiting for the next image release. The server tracks a `deploy` branch
rather than `main`, and `repo-update.sh` adopts a new commit only if the rule
suite still passes on it — otherwise it rolls back and says so in Discord.

## Why git, and what changed

Git was originally the audit trail. Every order was committed in the session it
was placed, and the pitch was that a timestamped commit history is harder to
quietly revise than a spreadsheet edited after the fact.

**That is no longer what this repo is, and the claim is withdrawn rather than
quietly left standing.** On 2026-08-17, ahead of publication, `trade-log.csv`,
`status/`, and `research/` were removed from tracking and purged from the
history, and the remaining history was squashed to a single commit. What is
published is the rule set and the machinery; the order flow is not.

The honest consequence: **nothing here externally verifies a single trade.** The
log is append-only by convention, on one machine, with no outside witness. Read
this repo as a statement of the constraints the agent operates under — which is
a real and checkable thing — and not as evidence of what it did with them.

The rules themselves are still versioned here, which is the part that matters
for reading the code: the manual can only be amended through a written procedure
that requires the account owner's own words quoted in the commit, and never in
reaction to a losing position.

**Earlier disclosed rewrite.** Before the first publication, the history was
also rewritten to remove things that should never have been in it: the brokerage
account number and Schwab API account hash (now `ACCOUNT_REDACTED` and
`HASH_REDACTED`), a reference to unrelated holdings in a personal account, and
the author's email address. No dollar figure, trade, rule, rationale, or
timestamp was altered by that pass.

## The four things to remember

1. **The agent does not run continuously** — only while a session is open. Every
   stop must be a resting order at Schwab, never something the assistant intends
   to watch for. See §4.3.
2. **Stops do not cover gaps.** They trigger only in regular-session trading. A
   stop can fill far below its trigger on overnight news. See §0 and §3.7.
3. **Long options are never held near expiration.** Auto-exercise of an
   in-the-money contract would blow through a cash account this size. See §3.3.
4. **Every percentage is against competition capital**, not account value.
   Competition capital is account value minus the $900.00 reserve — reading a
   cap against the full balance overstates the intended risk by about a third.
