# v3 Engine Architecture — design

**Status:** design approved in conversation 2026-09-02 (plan-mode approval);
spec pending Chris's review; not yet implemented. Replaces the *runtime* of
the system — dispatcher, scripts, broker wrapper, state layer, watchdogs — and
leaves the *strategy* (`strategy.md`, `rules.yml`, the research prompts, the
2026-08-30 information-edge scout design) intact. Nothing here amends
`CLAUDE.md`; §14 lists the manual text that will need a §9 amendment when the
old scripts are retired.

**Reference convention:** `CLAUDE.md §X` means a rule in the trading manual;
a bare `§X` means a section of *this document*.

---

## 1. Why this document exists

Twenty days of operation produced 87 commits, more than half of them fixes,
and the fix rate is not falling: `e7b252b` found twenty defects in work
already verified and committed, on day eighteen. Two corruptions (NUL bytes
in the heartbeat ledger, empty git objects in the store) are open as of
2026-09-01.

Grouped by root cause, seven of nine failure categories are architectural.
They are listed here because the design is judged against them, item by item,
in §10 and §12.

| Class | Concrete instances | Root cause |
|---|---|---|
| Bash as the application language | `$900` inside a double-quoted prompt parsed as `$9`+`00`, fatal under `set -u`, invisible to `bash -n`; variadic `--allowedTools` swallowing the prompt at a second call site (`6850057`), so every image deploy failed its own smoke test and rolled back; awk comparing ticker symbols numerically (`e7b252b`); bash 3.2 vs 5 and zsh vs bash divergences | the language |
| Git as a two-writer database | a laptop `git add -A` deleted five server-only files and wedged the store mid-rebase (`ea8048d`); `repo-update.sh` detached a human's checkout and dropped eleven files (`b364c3c`); the test suite pushed seven junk commits to the live store; gitignore trailing-slash vs symlink | two writers, no protocol |
| LLM in mechanical loops | recurring tool-allowlist gaps (`ea8048d`, `6fa057f`, `483a5e5`); one unattended tick in three declined to resolve the account hash and left the book unwatched (`8e19ca0`); Glob cannot see gitignored `status/`, so the high-water mark was resolved from the wrong source (`83a6fb4`); the sandbox refused every script call with no approver present | judgement calls where none is needed |
| Silent failure, decorative watchdog | `job-deadman.sh` runs inside the container it monitors; content failures recorded as `{"verdict":"ok"}` because the agent exits 0; rollback recorded an unaddressable image id and announced success (`9760370`); ~60 consecutive `EXEC none` because no candidate could ever qualify (`9d85e6f`), every job green | watchdog placement, verdict semantics |
| Token lifecycle | 5-day forced browser re-auth needing `ssh -L` plus a laptop; the server re-auth silently revoked the laptop token and a whole session ran with no reconciliation (`bdae4fe`); crash loop on an empty token; three days of correct watchdog warnings narrated as "healthy" (`HANDOFF.md`) | wrapper hard-codes; no phone path |
| State corruption | docker `json.log` NUL bytes in a JSONL ledger; seven empty git objects; a reviewer bot wrote a false line into `status/cron/deployed.jsonl`, which the deploy gate trusts | no schema, no single writer |
| Rule arithmetic drift | drawdown computed on the wrong capital basis in `tick.md` and `session-close.md`, one fixed and one not (`876ef71`); `check-consistency.sh` could not see `.claude/` and reported `CONSISTENT` through both | arithmetic in prose |

The two categories that are ordinary bugs — rule arithmetic within the
scripts, and Discord gate edges — are exactly the ones the existing
mutation-tested suites catch well. That discipline is kept and ported.

**Decisions made by Chris on 2026-09-02**, which this document does not
relitigate: Claude is judgement-only; the broker layer is our own service on
`schwab-py`, retiring `jkoelker/schwab-mcp`; Python throughout; Tailscale for
the OAuth callback; the DigitalOcean droplet is not used.

---

## 2. Principles

1. **Every failure above was a boundary drawn in the wrong place.** The
   redesign moves each boundary into typed Python and makes the wrong side
   *unreachable* rather than forbidden by instruction.
2. **Claude judges; the engine acts.** A model is invoked only where a
   decision requires reading the world and forming a view: research, thesis,
   enter or exit. Reconciliation, ticks, stops, clocks, the high-water mark,
   session close, health and the token are code.
3. **One owner per piece of state.** The token, the order state machine,
   pending approvals and the database have exactly one process that may
   write them.
4. **A watchdog lives outside the thing it watches**, and "ran" is never
   confused with "did what it was supposed to".
5. **What is kept is kept on purpose:** `rules.yml` as the single source of
   truth with `<!--rule:key-->` markers and a consistency checker that fails
   on drift; the read-only-by-construction split between research and the
   one order path; the Discord ✅/❌ gate inside the broker process; the
   research prompts and their data products; `strategy.md`.

---

## 3. Process topology

Two containers on the Pi, and not more.

**`engine`** — 512 MB limit, `oom_score_adj` 300. One asyncio Python process
owning: the `schwab-py` client and the token; SQLite; the scheduler; the
Discord bot (approvals, notifications, slash commands); the MCP server
(streamable HTTP at `/mcp`, role-scoped tools); the OAuth callback
(`/oauth/callback`); `/health`; a small read-only API (`/api/…`) for the
laptop. Every mechanical loop lives here.

**`runner`** — 1 GB limit, `oom_score_adj` 800. A ~200-line stateless service
that executes one Claude job at a time via `claude-agent-sdk` on
`POST /run {job, params}` from the engine and streams back a structured
result. It holds `CLAUDE_CODE_OAUTH_TOKEN` and nothing else: no Schwab
credential, no database. It reaches the engine only through `/mcp` with a
per-role bearer token.

Why split exactly there: the Claude CLI is the one memory-hungry process on
the box (measured 2026-09-02: ~330 MB peak over a 17-hour window that
included a full deep-research run; the schwab-mcp broker ~105 MB steady; zero
OOM kills to date). A separate cgroup is the only reliable memory fence
Docker offers on this Pi, so an out-of-memory research run can never take the
token holder, a pending approval or the stop loop with it. Everything else
stays in one process because the shared state must have one owner (§2.3).
The 1 GB runner limit is 3× measured peak and returns 1 GB to the host versus
today's 2 GB cap.

Tailscale runs on the host. `tailscale serve` terminates TLS with the
`ts.net` certificate and proxies to `127.0.0.1:8080`; the engine binds
loopback only and is never published on `0.0.0.0`.

**Scheduler.** No cron syntax. `config.yml` lists explicit ET times per job
(e.g. `tick: every 15m 09:32–15:47`, expanded at load). Each fire passes a
trading-day gate built from `get_market_hours`, cached per date; when the
broker is unreadable the fallback is weekday plus the last cached calendar,
failing *toward* monitoring rather than away from it. Jobs are `async`
functions under a per-job lock. A fire missed because the engine was down is
recorded as `missed`, not run late.

---

## 4. Claude invocation

The runner uses `claude-agent-sdk` (`query()`), pinned to the installed CLI
via `cli_path` and a version check, with `CLAUDE_CODE_OAUTH_TOKEN` passed
through `env` — the subscription auth model is unchanged.

- **Allowlists are typed.** Each job in `tc/jobs/spec.py` declares
  `allowed_tools: list[str]`, its prompt, its output schema, its budget and
  its window. Prompts are Python strings passed as `prompt=`; nothing is ever
  interpolated into a shell word. This removes the `--allowedTools` and
  `$900` classes by construction.
- **Read-only is enforced twice, both in Python.** The engine's MCP server
  registers tools *per role*: the research role sees read tools plus
  `research_*` writers; the decider role sees read tools plus `propose_*`.
  Separately, the runner installs a `PreToolUse` hook that denies any tool
  outside the job's allowlist and denies `Bash`, `Write` and `Edit` for every
  job. There is no bash script path for Claude to be refused on, so the
  "sandbox refused every script" failure has nothing to refuse.
- **Verdicts are structured.** Every job sets `output_format` to a per-job
  JSON schema (`TickVerdict` is not needed — ticks are code; `ScoutVerdict
  {observed, escalations[]}`, `CatalystVerdict`, `UniverseVerdict`,
  `DecisionVerdict{proposal_ids[], declined_reason}`) read from
  `ResultMessage.structured_output`. A run that ends without a valid
  structured result is classified `content_failed`. Nothing greps stdout.
- **Existing prompts are reused.** `.claude/agents/*.md` and
  `.claude/commands/*.md` load via `setting_sources=["project"]` with
  `cwd=/app/repo`; their `tools:` frontmatter is rewritten once to the new
  `mcp__engine__*` names. `CLAUDE.md` still loads at the start of every run.
- **Jobs Claude runs:** `scout`, `catalyst`, `deep-research preopen|postclose`,
  `weekly-universe`, `decide`. **`decide` is trigger-driven, not scheduled:**
  it fires when an undecided escalation or HOT candidate exists and the gates
  allow (10:00–15:00 ET, at most 3 per day), on `/decide SYM` from Chris in
  Discord, or when a held thesis's event date has passed. Sixty idle execute
  passes cannot recur, and if no candidate qualifies for N sessions the
  expectations check (§7) says so out loud.
- `permission_mode="bypassPermissions"` is not used anywhere; the allowlist
  plus hook is the gate. Per-job `max_turns`, token budget and a wall-clock
  timeout are enforced by the engine, which kills the runner request and
  records `failed:timeout`.

A `tc run-job <name> --via-cli` break-glass path that shells out to
`claude -p` is kept for debugging only.

---

## 5. The order path: Claude proposes, the engine disposes

**Claude never holds `place`, `cancel`, `replace` or `preview` tools.** Its
only write-shaped tools are `propose_entry`, `propose_exit`,
`propose_option_close` and `get_proposal`. A contract test asserts that no
MCP role exposes a tool whose name matches `place|cancel|replace|order`. This
is the direct consequence of "judgement only" and the single largest safety
simplification in the redesign: the one order path is deterministic, tested
code, and the agent formerly named `trader` becomes `decide`.

### 5.1 Intent schema

`tc/orders/intent.py`, pydantic strict, `extra="forbid"`:

```python
class EntryIntent(BaseModel):
    instrument: Literal["equity", "etf", "leveraged_etf", "option"]
    symbol: str                      # underlying ticker only
    option: OptionSpec | None        # expiry, strike, put/call — engine builds the OSI symbol
    quantity: PositiveInt
    limit_price: Decimal
    intent_notional: Decimal         # Claude's own arithmetic; engine compares
    sleeve: Literal["core", "catalyst", "options", "leveraged"]
    thesis_ref: str                  # escalation id or candidates entry id
    rationale: constr(max_length=1200)
    earnings_date: date | None
    corporate_action_note: str | None

class ExitIntent(BaseModel):
    position_id: str
    quantity: PositiveInt
    limit_price: Decimal
    reason: constr(max_length=600)
```

Engine-assigned, never Claude-supplied: side (entries are BUY only), time in
force (DAY), stop geometry (ATR-scaled per `rules.manual.stop_*`), the OSI
option symbol, and `client_key = sha256(session_date, symbol, side,
thesis_ref)`.

### 5.2 Gates

`tc/orders/gates.py` is a set of pure functions over `Rules` and a
`BookSnapshot`, run in this order, each producing a named pass/fail with the
numbers that decided it:

`CLAUDE.md §1.4/§2` floors → `CLAUDE.md §3.1/§3.2/§3.5/§3.8` caps in
`Decimal`, floored → `CLAUDE.md §3.6` halt → `CLAUDE.md §5` settled cash and
the reserve invariant → `CLAUDE.md §3.2` option quality floors from a live
chain read → `CLAUDE.md §4.2` no entry after 15:30 ET → `ALERT`/closing-only
posture → `CLAUDE.md §4.10` order-rate ceilings counted from
SQLite ∪ broker `get_orders`, tripping on the (N+1)th attempt → `preview_order`
→ notional three-way compare (intent vs `qty × price × multiplier` vs the
preview's `orderValue`, $0.01 tolerance) → identifier round-trip (fresh
`get_quotes`, timestamp ≤ 3 minutes, description recorded).

The semantics are ported from `scripts/pre-order-check.sh` and
`.claude/agents/trader.md` §2–§4; the arithmetic is property-tested (§10).
`propose_*` returns `ValidationResult{proposal_id, accepted, gates: [{name,
passed, detail}], state}` and Claude's run ends there.

### 5.3 State machine

`tc/orders/machine.py`; every transition appends an `order_events` row.

```
proposed → validated | rejected
validated → awaiting_approval → approved | denied | expired(600s)
approved → placing → working → partially_filled → filled | cancelled | broker_rejected
filled (entry) → stop_pending → stop_resting(verified) | stop_failed → closing(CLAUDE.md §4.3) → flat
```

Invariants enforced in code, not prose: at most one proposal is in
`awaiting_approval | placing | working` at a time (one action in flight);
protective actions in `tc/loops/stops.py` pre-empt discretionary ones; a
working entry is cancelled at 15:55 ET or on engine shutdown (`CLAUDE.md
§4.2`); a stop gets three placement attempts and a `CLAUDE.md §4.3` close two, then
`ALERT`.

### 5.4 Approval

`tc/orders/approval.py`. Discretionary orders post an embed to `#llm-yolo`;
✅/❌ are accepted from the configured approver id only, and reactions from
anyone else are removed and logged at warning level, not debug. Approval rows
persist in SQLite so an engine restart re-arms a pending approval instead of
losing it.

`is_protective_stop(spec)` — `orderType` STOP or STOP_LIMIT, every leg an
equity SELL, quantity ≤ held — auto-approves with a Discord announcement.
This puts Chris's ratified 2026-08-14 authorisation ("If there is a way for
you to work off stops without my input that would be approved") into
production for the first time; today it exists only as a hand edit in the
laptop's site-packages. Stop amendments call `replace_order` — one call, one
count against the `CLAUDE.md §4.10` replace ceiling — instead of cancel-and-re-place.

### 5.5 Idempotency (`CLAUDE.md §4.6`)

Before the HTTP call the engine writes `orders{client_key, spec,
state=placing, placing_at}`. After the call — success, timeout or exception —
it queries `get_orders(from=placing_at − 1 min)` and matches on (symbol,
quantity, price, type, entered ≥ placing_at) to attach the Schwab order id.
A `placing` row with no id at startup is resolved by query during
reconciliation before any other action, and is never resubmitted. Fill
polling and stop placement run as engine tasks with the bounded retries in
§5.3.

---

## 6. State

**SQLite** at `/data/engine.db`, WAL, `synchronous=NORMAL`, one writer
connection behind an asyncio lock, every write a short transaction.

Tables: `account_snapshots`, `position_snapshots`, `orders`, `order_events`
(append-only), `stops`, `proposals`, `approvals`, `ticks`, `trade_log`
(append-only — a trigger rejects UPDATE and DELETE; a correction is a new row
per `CLAUDE.md §7.1`; the `CLAUDE.md §4.9` row is written *before* placement),
`session_status` (the high-water mark ratchets only here, on close, from
closing marks), `token_events`, `job_runs` (verdict ∈ {done, noop,
content_failed, failed, timeout, missed} plus the structured verdict),
`alerts` (with `acked_at`), `rules_versions`, `expectations_log`, and the
research ledgers `evidence`, `escalations`, `screen_rows`, `iv_series`,
`oi_snapshots`, `tombstones`, `sectors`, `universe`, `artifacts`.

**Research artifacts split by shape.** The structured ledgers — everything
the bash writers validated as JSON or TSV — become tables written through
engine MCP tools that carry the same validation (`evidence_append` refuses a
skewed date; `escalation_raise` refuses fewer than two distinct evidence
types and dedupes by claim hash; the sector writer compares symbols as
strings). Cohort building is a pure function over `universe` + `sectors`,
ported from `cohort.sh`, exposed as `cohort(date)`. Documents Claude reads
whole — candidates, the pre-open brief, the scorecard, `universe.md` — stay
as files under `/data/research/docs/`, written via `doc_replace(kind, date,
body)` with the existing header checks and compare-and-swap on `Last pass:`,
and indexed in `artifacts`. Those prompts consume documents; the ledgers
consume rows. The Glob-on-gitignored-path failure disappears because tools
return content and `/data` is outside any checkout.

**Markdown and CSV are exports, not the store of record.** Nightly and on
order events the engine renders `status/DATE.md` in the current format (so
the old `latest-status.sh` regex still parses it during migration),
`trade-log.csv`, the documents, and JSONL dumps of the ledgers into
`/data/export/`, commits them to its own clone of the private store repo,
and pushes. It is the store's **only** writer: no pull, no rebase, and a
rejected push is an alert rather than a warning. The laptop reads via `/api`
over the tailnet and never writes. Nightly `VACUUM INTO
/data/backup/engine-DATE.db`, fourteen kept, fetchable via
`/api/backup/latest`.

---

## 7. Observability: watchdogs outside the watched thing

Three independent layers.

1. **External dead-man's switch** — healthchecks.io free tier, or a
   self-hosted equivalent if Chris prefers no third party. One check per job
   with period and grace. The engine pings `/start`, then `/` with the verdict
   word on `done | noop`, or `/fail` on `content_failed | failed | timeout`.
   "Ran" ≠ "did the thing": a scout run with no structured verdict, a tick
   that could not read the broker, a session close that produced no
   `session_status` row — all ping `/fail`. A missing ping (engine down)
   alerts by itself. Pings carry verdict words only, never account data.
2. **Host systemd timer outside Docker** — `host/healthprobe.py`, stdlib
   only, every 2 minutes: GET `/health`, and post to Discord through a
   **webhook the engine does not own** when unreachable or when any field
   breaches: `token_days_until_dead ≤ 2`, `last_broker_read_ok_at` older than
   20 minutes during regular hours, `positions_without_stop > 0`,
   `pending_approval_age > 600 s`, `runner_ok = false`, `db_ok = false`.
3. **Expectations job** — 07:30 ET daily, plus a weekly digest that always
   posts so silence is distinguishable from health. Declarative rows in
   `config.yml`, for example: `escalations_or_qualified ≥ 1 in 10 sessions`;
   `scout observed ≥ 1 in 5 sessions when cohort nonempty`; `preopen brief
   present by 09:15`; `universe age < 8 d`; `decide invoked ≥ 1 in 10 sessions
   when candidates existed`; `trade_log rows == filled orders`. A breach is a
   reported anomaly carrying the query that failed.

Token wording is fixed at the source: `/health` and every Discord message
state `days_until_dead` and the required action, never "healthy" beside a
countdown.

---

## 8. Token and re-auth

**Verified 2026-09-02 from primary sources.** Schwab refresh tokens hard-expire
seven days after the *original* login; the clock does not roll forward on
refresh; there is no offline, service-account or longer-lived credential for
individual developers; no policy change is known through September 2026.
Access tokens self-refresh silently for those seven days, after which Schwab
rejects the refresh token and only a human completing the authorization-code
login with MFA can mint a new one. `schwab-py`'s maintainer dropped Selenium;
headless-login projects exist, are MFA-dependent, and run against the spirit
of Schwab's "usage is monitored" terms. They are rejected (§12).

Two things the current system treats as Schwab constraints are not: the
**5-day** forced re-auth is `schwab-mcp`'s unconfigurable default, and the
`127.0.0.1`-only callback is `schwab-py`'s `client_from_login_flow`. Schwab
itself accepts any HTTPS callback URL (exact match, ≤ 256 characters), and
`schwab-py`'s manual flow has no host check.

**Flow.** The engine keeps the token in `/data/token.json` and swaps it
atomically. At `token.reauth_after_days` (default 5, config) it posts the
Schwab auth URL to Discord with a one-tap message. Chris opens it on his
phone with the Tailscale app connected, logs in with MFA, Schwab redirects to
`https://<pi>.<tailnet>.ts.net/oauth/callback`, the engine exchanges the code
through `schwab-py`'s received-URL path and swaps the token. The whole
exchange is expected to take under a minute and is measured in the Phase 0
drill (§11). If the callback never arrives, `/health` and the host probe
escalate at two days to dead.

**Blind mode.** An empty, expired or revoked token never crash-loops. The
engine stays up, monitoring loops report `BLIND`, protective logic that needs
the broker is suspended and says so, and only the re-auth path is active.
`option_max_blind_days` in `rules.yml` already prices this.

---

## 9. Config, secrets, deploy, repository layout

- `config.yml` (checked in: hostnames, ports, schedule, windows, token
  thresholds, expectations, healthcheck slugs) plus `.env` (Schwab app key
  and secret, Discord bot token, channel, approver id and webhook, Claude
  OAuth token, MCP role bearer tokens) via pydantic-settings. Startup fails
  fast on any missing key.
- **One public repo, no store symlinks, no laptop writer.** Code, `rules.yml`,
  `strategy.md`, `CLAUDE.md`, `.claude/`, docs, docker and host scripts live
  in `Kilowhisky/trade-challenge`. Data lives in `/srv/tc/data` on the Pi,
  bind-mounted at `/data`, never inside a checkout. The private store repo
  survives only as the engine's export mirror. The Pi's checkout is mounted
  read-only at `/app/repo` for *content* — rules, prompts, the manual — while
  **code ships in the GHCR image by digest.**
- `host/deploy.py` on a systemd timer at 18:30 ET replaces `deploy.sh`:
  fetch, `checkout --detach origin/deploy`, `compose pull`, `compose up -d`,
  and only when `/health` reports no in-flight proposal and the clock is
  outside 09:15–16:15 ET. Rollback pins the previous **registry digest**,
  verified addressable before it is recorded — the `9760370` lesson.
- The engine reloads rules and prompts on content-hash change or `SIGHUP`,
  runs the consistency checker, and adopts only on PASS, otherwise keeping
  `/data/rules-lastgood/`. No code path deploys mid-session.
- `tc/rules/consistency.py` ports every check in `scripts/check-consistency.sh`
  and adds: markers in **all** `*.md` including `.claude/`; the derived-DTE
  identity; dead-key tombstones; the capital-basis cross check; schedule-vs-doc
  agreement; and "no tool matching `place|cancel|replace|order` in any MCP
  role registry".

Section numbers inside the tree are `CLAUDE.md` rules.

```text
engine/tc/
  main.py                 asyncio entry: wires store, broker, scheduler, discord, http
  config.py               pydantic-settings: config.yml + .env, fail-fast
  rules/model.py          Rules model parsed from rules.yml
  rules/arith.py          pure Decimal arithmetic for every cap and threshold (property-tested)
  rules/consistency.py    marker / tightness / derived / dead-key / basis / tool-registry checks
  broker/client.py        schwab-py wrapper: typed reads, preview/place/replace/cancel, retries
  broker/token.py         token file, age, auth URL, callback exchange, atomic swap
  broker/fake.py          FakeSchwabClient + fixture recorder and redactor
  store/db.py             SQLite single writer, migrations, append-only triggers
  store/export.py         markdown / CSV / JSONL renders + store-repo push
  loops/reconcile.py      session-open §4.5, placing-row resolution
  loops/tick.py           the eight watches (ported from tick.md §B), ledger row, trips
  loops/stops.py          place / ratchet / replace / orphan / gapped-through
  loops/clocks.py         §3.3 DTE, §3.5 hold
  loops/session.py        close status, HWM ratchet, drawdown level
  loops/expectations.py   declarative anomaly checks
  orders/intent.py        EntryIntent / ExitIntent / OptionSpec
  orders/gates.py         §4.9 / §4.10 validation, ceilings
  orders/machine.py       proposal state machine, idempotency, fill and stop follow-through
  orders/approval.py      Discord gate, persistence, protective auto-approve
  mcp/server.py           streamable-HTTP MCP, role-scoped tool registry
  mcp/tools_read.py       market / book / rules read tools
  mcp/tools_research.py   ledger + document write tools (validation from the old scripts)
  mcp/tools_propose.py    propose_* (decider role only)
  jobs/spec.py            per-job allowlist, prompt, schema, budget, window
  jobs/dispatch.py        runner calls, verdict classification, healthcheck pings
  discord/bot.py          notifications, approvals, slash commands (/status /ack /reauth /halt /resume /decide)
  http/app.py             /health  /oauth/callback  /mcp  /api
  scheduler.py            explicit ET times, trading-day gate, locks
runner/tc_runner/app.py   claude-agent-sdk executor, PreToolUse deny hook
host/                     deploy.py, healthprobe.py, *.timer, *.service
docker/                   Dockerfile, docker-compose.yml
tests/                    unit/ property/ replay/ contract/ paper/
```

**Dependencies** (one line each): `schwab-py` (broker; `replace_order`,
`preview_order`, received-URL OAuth) · `pydantic` + `pydantic-settings`
(schemas, config) · `PyYAML` (rules, config) · `aiosqlite` (single-writer
async SQLite) · `discord.py` (bot, reactions, slash commands) · `mcp`
(official SDK, streamable HTTP server) · `starlette` + `uvicorn` (one ASGI
app) · `httpx` (healthcheck pings; already a `schwab-py` dependency) ·
`claude-agent-sdk` (runner only) · `hypothesis`, `pytest`, `pytest-asyncio`,
`mutmut`, `mypy`, `ruff` (quality gates). No cron library, no market-calendar
library, no ORM.

---

## 10. Testing

- **`FakeSchwabClient`** duck-types the `schwab-py` methods the engine uses.
  Fixtures are recorded once via a `record` mode that redacts account numbers
  and hashes; a test asserts no fixture contains an account-number pattern or
  the real hash (`CLAUDE.md §7.4`).
- **Property tests** with `hypothesis` on `rules/arith.py`: caps never round
  up; the ATR clamp is monotone; the high-water mark never decreases; the
  capital-basis conversion is idempotent. A `mutmut` gate on that module in
  CI replaces the role of `test-pre-order-check.sh`.
- **Paper mode** (`TC_MODE=paper`) runs the full engine — scheduler, gates,
  Discord approvals real or stubbed — against the fake with a scripted
  market. It is the Phase 2 rehearsal.
- **Replay tests, one per §1 catalogue item:** NUL bytes in an export are
  rejected on read; a `placing` row without an id is resolved by query and
  never resubmitted; a pending approval is re-armed across restart; an
  old-basis high-water mark is converted, not `max()`ed; exit 0 with no
  structured verdict is `content_failed`; a dead token posts the re-auth URL
  and does not crash-loop; a deploy inside the window is refused; the
  old-format `status/DATE.md` export parses with `latest-status.sh`'s regex.
- **Contract test:** for every MCP role, `list_tools()` contains no tool
  matching `place|cancel|replace|order`; research roles carry only read and
  `research_*` tools.
- `mypy --strict`, `ruff`, `pytest-asyncio`; CI on amd64 plus an arm64 image
  smoke (`tc --version`; `claude --version` equals the pin).

---

## 11. Migration: strangler, real money stays live

Each phase gets its own implementation plan; Phase 0's is written first and
nothing in a later phase is started before the earlier phase's exit criteria
are met.

**Prerequisites (Chris, before Phase 0).**

1. Register a **second Schwab app** in the developer portal with callback
   `https://<pi>.<tailnet>.ts.net/oauth/callback`. Approval takes one to
   three days. A second app gives the engine its own refresh token, so shadow
   mode cannot split-brain the live broker. If Schwab refuses a second app,
   Phase 0 runs on fixtures and paper mode without live reads, and Phase 1
   takes the single token.
2. Install Tailscale on the Pi and on the phone; enable MagicDNS and HTTPS
   certificates; `tailscale serve` → `127.0.0.1:8080`.
3. Create a healthchecks.io project, or decline it and rely on layers 2 and 3
   of §7.
4. Keep the current system alive meanwhile: weekly re-auth per `HANDOFF.md`.

**Phase 0 — shadow, read-only (about two weeks).** Deploy `engine` and
`runner` beside the old stack. The engine holds the new app's token and runs
reconciliation, ticks, high-water mark, clocks, expectations, healthchecks
and the host probe. It posts to a separate `#engine-shadow` channel via
webhook — no bot gateway yet, so the old broker's bot is undisturbed. A
nightly diff compares its tick, HWM and stop view against the old ledgers.
The Python consistency checker runs beside the bash one and both must agree.
No store writes, no orders. **Rollback:** `compose down engine runner`.

**Phase 1 — mechanical loops and research cut over.** Outside a session:
stop the old scheduler and the old broker, keeping both as the rollback path
to be started on demand. The engine takes the Discord bot; stops go live
(auto-approved protective stops: ratchet, re-place on partial, corporate-
action re-price, orphan cancel); `CLAUDE.md §3.3`/`§3.5` clocks (closes are
approval-gated); session close; alerts. Research jobs move to the runner.
Exports of `status/DATE.md` and `trade-log.csv` in the old format begin so
the old stack could resume from them. **Rollback:** stop the engine, start
the old scheduler and broker; the old token is intact if within its seven
days, otherwise the documented SSH re-auth.

**Phase 2 — the order path.** Paper mode for at least five sessions with
Chris approving paper orders in Discord; then `orders.enabled = true` and the
`decide` job on. The old broker container is removed; its token expires on
its own. **Rollback:** `orders.enabled = false` reverts to protective-only;
every position stays stopped.

**Phase 3 — decommission.** Delete `scripts/*.sh`, `docker/crontab`,
supercronic, the store symlinks and `sidecar-sync.sh`; archive the old
runbooks; the laptop switches to `/api`; delete the old Schwab app; amend
`CLAUDE.md` per §14. The token never "moves": the engine mints its own in
Phase 0 and the old one dies with the old app.

**Exit criteria.** Phase 0: ten consecutive sessions where the shadow view
matches the old ledgers (high-water mark identical; account value equal at matched timestamps; resting-stop map identical); zero `content_failed` on research
jobs; each of healthchecks and the host probe proven by deliberately stopping
the engine once and watching the alert arrive from outside it; one complete
phone re-auth drill with wall time recorded. Phase 2: one real entry with
Discord approval, its stop auto-placed and verified resting within 60 seconds
of fill, one `replace_order` ratchet, one exit with the stop cancelled first,
all reconciled by query with matching `trade_log` rows.

---

## 12. Considered and rejected

- **Fork `jkoelker/schwab-mcp`.** It would land the stop patch, add
  `replace_order`, and make the 5-day re-auth and callback host
  configurable, but it keeps ~6,400 lines of someone else's design and still
  leaves the mechanical loops without a home. The order path in §5 does not
  fit inside a tool-per-API-call wrapper.
- **Keep bash and rewrite only the dispatcher.** Every item in the first row
  of §1 is the language; a smaller bash program has the same failure modes.
- **TypeScript or Go for the engine.** Neither has a maintained Schwab
  client; both would mean writing the OAuth and API layer from scratch.
  `schwab-py`, the official MCP SDK and `discord.py` are all Python.
- **Claude in every loop with better tooling.** Cheaper to migrate, but keeps
  the entire third row of §1.
- **Fully systematic with no model at runtime.** Most reliable, and abandons
  the information-edge approach the strategy is built on.
- **Headless-browser re-auth.** Unsupported by the library, MFA-dependent,
  stores Chris's brokerage password on a server, and contrary to Schwab's
  monitored-use terms. A weekly phone tap is the honest cost.
- **Hosting on the DigitalOcean droplet** (1 vCPU, 1 GB). The engine alone
  would fit; engine plus runner would not with headroom. Dismissed by Chris
  on 2026-09-02; not to be re-raised.
- **Git as the store of record with a locking protocol.** Solving two-writer
  git is more work than not having two writers.

---

## 13. Assumptions to verify early

1. Schwab allows a second app per developer account and a non-loopback HTTPS
   callback URL. Its documentation says any HTTPS URL; this has not been
   verified by creating one. Changing an existing app's callback triggers
   re-approval, hence the new app.
2. `claude-agent-sdk` works with `cli_path` pointing at CLI 2.1.234 and
   passes `CLAUDE_CODE_OAUTH_TOKEN` through `env`. If the SDK's minimum CLI
   version exceeds the pin, both move together behind the CI smoke.
3. Chris's phone has the Tailscale app connected when he taps the auth URL;
   MagicDNS resolves only on the tailnet.
4. Schwab's Trader API has no client-supplied order id, so idempotency is
   reconcile-by-query as designed in §5.5.
5. healthchecks.io is an external dependency; it receives verdict words only.

---

## 14. Manual text that will need a §9 amendment (Phase 3, not now)

This spec does not amend `CLAUDE.md`. When the old scripts are retired, the
following passages will describe things that no longer exist and must be
amended through `CLAUDE.md §9` with Chris's words quoted — after Phase 2, in
calm conditions, never mid-migration:

- Header: "`scripts/pre-order-check.sh` reads its caps from there",
  "`scripts/check-consistency.sh` fails if any document or script disagrees",
  "`scripts/test-pre-order-check.sh` is the gate's regression suite".
- `§0` operating limitation #1: "`scripts/job-deadman.sh` and
  `scripts/token-watchdog.sh` report these to Discord".
- `CLAUDE.md §4.5`: "Then run `scripts/check-consistency.sh`".
- `CLAUDE.md §4.10` order-rate ceilings: the note that "the MCP exposes no
  `replace_order`" and the cancel-plus-re-place counting rule.
- `CLAUDE.md §7.1`: the description of `trade-log.csv` as a local file appended by
  script; it becomes an export of an append-only table.
- Verified account facts: "**`replace_order` is absent**".

The rules themselves — every percentage, every prohibition, every clock —
are unchanged by this design.
