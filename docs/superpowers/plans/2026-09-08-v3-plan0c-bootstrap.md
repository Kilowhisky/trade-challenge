# v3 engine — Plan 0c bootstrap runbook

Documentation only — no code changes. This is the operator sequence for
bringing up the two containers Plan 0c adds (`runner`, and the `engine`
container's MCP surface once its secrets exist) on top of the `engine`
service that is **already live** on the Pi, and for driving the six research
jobs to their first passing runs. Spec:
`docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` §4, §11.
Related: `docs/superpowers/plans/2026-09-04-v3-phase0-runbook.md` (how the
`engine` container itself got here), `host/README.md` (the two secrets files
in full), `HANDOFF.md` (current state).

**Starting point, as of 2026-09-08** (read `HANDOFF.md` for the live figure):
`tc-engine` is running on the Pi with its own Schwab token, reconciling the
account, ticking every 15 minutes 09:32–15:47 ET, and closing the session at
16:04 — all of it read-only. The **old** stack (`tc-broker`, `tc-scheduler`)
is stopped, not deleted, per Chris's 2026-09-07 call ("The old broker is
dead"); nothing in `docker/crontab` executes anywhere any more, because
nothing starts the container that runs it. Plan 0c does not touch that
container or that file — it gives the **new** engine its own research path,
independent of the old one. What Plan 0c adds: two new containers (`runner`,
and MCP mounted inside `engine`), four new secrets, and the six Claude jobs
in `engine/tc/jobs/spec.py`'s `JOB_SPECS` (`scout`, `catalyst`, `preopen`,
`postclose`, `research`, `sector_tag`).

Every `tc` invocation below runs inside the `engine` container via
`docker compose exec`, with `--config /app/repo/config.yml --env
/srv/tc/.env` — the container's own copies at their mounted paths. Compose is
only ever asked for `build`, `up -d`, `exec`, `down`, or `logs` in this
document (the same restriction the Phase 0 runbook and
`scripts/check-consistency.sh` check 7 hold `README.md`/`scripts/`/`docker/`
to); where the sequence below needs to stop or restart a container, that is
`down <service>` followed by `up -d <service>` — there is no supported
`restart` or `stop` verb in this document.

Server facts: Pi user `knome`, checkout `/home/knome/trade-challenge` (the
`deploy` branch), compose file `docker/docker-compose.yml`.

## 1. Secrets — write both env files

Four new keys, generated on the Pi, never typed anywhere else and never
committed:

```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"    # run 3 times
```

**`/srv/tc/.env`** (the `engine` container — bind-mounted read-only,
`docker/docker-compose.yml`'s `env_file: [/srv/tc/.env]`) gains three new
keys alongside the ones already there from the Phase 0 runbook
(`TC_SCHWAB_APP_KEY`, `TC_SCHWAB_APP_SECRET`, `TC_DISCORD_WEBHOOK_URL`,
`TC_DISCORD_SHADOW_WEBHOOK_URL`, `TC_HEALTHCHECKS_BASE_URL`):

```
TC_RUNNER_TOKEN=<generated>
TC_RUNNER_URL=http://127.0.0.1:8090
TC_MCP_RESEARCH_TOKEN=<generated>
TC_MCP_DECIDE_TOKEN=<generated>
```

`TC_RUNNER_TOKEN` here must be the **same** value written to `runner.env`
below — it is the bearer the engine presents to the runner's `POST /run`.
`TC_MCP_RESEARCH_TOKEN` and `TC_MCP_DECIDE_TOKEN` must **differ** from each
other (`Settings.mcp_tokens()` in `engine/tc/config.py` raises at startup if
they collide) — every job in `JOB_SPECS` runs as the research role, so only
the research token is ever handed to the runner per request
(`build_engine` in `engine/tc/main.py`); the decide token has no job using it
yet (Plan 1), but it must still be present and distinct for the engine to
build the `decide` MCP server at all.

**`/srv/tc/runner.env`** (the `runner` container's own `env_file:`, new —
create it) holds exactly these three keys and nothing else — no Schwab
credential, no database, per the isolation `docker/docker-compose.yml`
documents on the `runner` service:

```
CLAUDE_CODE_OAUTH_TOKEN=<the same subscription token the old scheduler used>
TC_RUNNER_TOKEN=<same value as in /srv/tc/.env above>
TC_ENGINE_URL=http://127.0.0.1:8080
```

The server jobs share the laptop's Claude subscription (see the
`claude-session-limit-starves-server` memory) — a heavy interactive session
during 09:30–16:30 ET starves whichever runner job is scheduled at the same
time. Keep builds and other heavy laptop sessions outside market hours.

```
chmod 600 /srv/tc/.env /srv/tc/runner.env
```

Both files already carry every key `host/README.md` documents; if a future
job spec needs a fifth key, add it there first, then to the runbook.

## 2. Build and start the runner

```
cd /home/knome/trade-challenge
docker compose -f docker/docker-compose.yml build runner
docker compose -f docker/docker-compose.yml up -d runner
curl -s http://127.0.0.1:8090/health
```

Expect `{"ok": true, "busy": false, "cli_path": "/usr/local/bin/claude",
"engine_url": "http://127.0.0.1:8080"}`. `ok` is `bool(OAUTH_TOKEN) and
bool(RUNNER_TOKEN)` (`runner/tc_runner/app.py`) — `false` means one of the
two keys in `runner.env` is missing or empty, not that the engine is
unreachable. If it will not come up: `docker compose -f
docker/docker-compose.yml logs runner`.

## 3. Restart the engine so it mounts the MCP surface

The engine reads `/srv/tc/.env` once, at container start
(`Engine._build_mcp` in `engine/tc/main.py` calls `settings.mcp_tokens()`
during `start()`), so the three new keys from step 1 need a fresh container,
not a running one that happens to have a newer file on disk:

```
docker compose -f docker/docker-compose.yml up -d --force-recreate engine
curl -s http://127.0.0.1:8080/health | python3 -m json.tool
```

`runner_ok` should read `true` (the engine's own read of the runner's
`/health`, done once at `start()` — `JobRunner.health()` in
`engine/tc/jobs/dispatch.py`). Confirm both MCP mounts exist and are gated
correctly:

```
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/mcp/research/          # 401 — missing bearer
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/mcp/decide/ \
  -H "Authorization: Bearer $(grep TC_MCP_RESEARCH_TOKEN /srv/tc/.env | cut -d= -f2)"  # 403 — wrong role for this mount
```

(`RoleAuthMiddleware` in `engine/tc/mcp/server.py`: a missing/non-bearer
`Authorization` is 401; a bearer that does not match the mount's role — right
token, wrong door — is 403, same as an unrecognized token.)

## 4. Import the v2 store — before any job runs

The old bash-stack store is already mounted read-only at `/data/store`
inside the `engine` container (`docker-compose.yml`'s
`${TC_DATA_DIR:-../../trade-challenge-store}:/data/store:ro`, carried over
from the Phase 0 runbook's shadow-diff mount). Dry run first — it copies the
live store aside and reports what it *would* do without writing anything
(`cmd_import_research` / `_dry_run_copy` in `engine/tc/cli.py`):

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env \
  import-research --store-dir /data/store --dry-run
```

Read the `skipped` list it prints — every line is a legacy row the new
validators refuse (a malformed evidence date, a corrupt ledger line); that is
information, not damage, and the import is idempotent either way. Then for
real:

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env \
  import-research --store-dir /data/store
```

Prints `IMPORT OK` and exits 0 regardless of how many rows were skipped;
exit 4 only means the operator pointed `--store-dir` at a path that is not a
directory.

## 5. Seed the universe and the tags, in order

Each of these depends on the file the previous one wrote — run them in this
order, not in parallel. All three run against the engine's own broker
connection (the token installed per the Phase 0 runbook and re-auth'd since —
see `HANDOFF.md` for its current expiry), not the old, stopped broker.

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env \
  run --once weekly_universe          # ~11k fetched, ~3k qualified. Minutes, not seconds.
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env \
  run --once sector_tag                # tags the qualified names (needs the runner)
```

`sector_tag` is a Claude job (`agent="sector-tagger"` in `JOB_SPECS`) — it
needs the runner from steps 2–3, not just the broker. `run --once` exits 1
only if the job's own verdict is `failed`; `noop` and `content_failed` both
exit 0, so check the verdict itself (§6 below), not just the shell exit code.

Then one deep run — `postclose` after 16:22 ET (its scheduled window closes
at 18:00, per `JOB_SPECS["postclose"].window`), or `research` during
09:45–15:15 ET if bootstrapping mid-session:

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env \
  run --once postclose      # or: run --once research
```

## 6. Verify the cohort is non-empty

This is the single check that says the chain works end to end: the scout has
reported `cohort 0` every day since 2026-09-01 for want of a qualified
universe and `sectors.tsv`.

**`run --once JOB` does not print the verdict to stdout** — only its exit
code (`cmd_run` in `engine/tc/cli.py` returns `1 if verdict == "failed" else
0` and prints nothing else). The verdict lives in the `job_runs` table
(`detail_json`, written by `classify()` in `engine/tc/jobs/dispatch.py`), and
there is no `/api/job_runs` route yet — `/api/status` returns only the
latest session and tick. Read it from the store directly. The `engine` image
is `python:3.12-slim-bookworm` with **no `sqlite3` CLI installed** (only
`tzdata`/`ca-certificates` are added in `docker/Dockerfile.engine`) — use
Python's stdlib `sqlite3` module, not the `sqlite3` binary:

```
docker compose -f docker/docker-compose.yml exec engine python3 -c "
import sqlite3
c = sqlite3.connect('/data/engine.db')
print('universe qualified:',
      c.execute('SELECT COUNT(*) FROM universe WHERE qualified=1').fetchone()[0])
print('sectors tagged:',
      c.execute(\"SELECT COUNT(*) FROM sectors WHERE sector != 'other'\").fetchone()[0])
"
```

Both counts should be non-zero (universe: low thousands; sectors: however
many the tagger placed in the three in-scope sectors). Then run scout and
read its verdict the same way:

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env \
  run --once scout
docker compose -f docker/docker-compose.yml exec engine python3 -c "
import sqlite3, json
c = sqlite3.connect('/data/engine.db')
verdict, detail = c.execute(
    \"SELECT verdict, detail_json FROM job_runs WHERE job='scout' ORDER BY id DESC LIMIT 1\"
).fetchone()
print(verdict)
print(json.dumps(json.loads(detail), indent=2))
"
```

`verdict` should be `done` (or `noop` with `cohort: 0` and a stated,
legitimate reason — see below), never `content_failed` or `failed`. The
printed detail is the `ScoutVerdict` JSON: `cohort`, `observed`,
`escalations`, `summary`.

A `cohort: 0` after all of §5 above ran is a **legitimate** answer between
earnings seasons — it means no in-scope, tagged name has an estimated next
print 21–42 days out (`_scout_noop` in `engine/tc/jobs/spec.py` records this
as `noop`, not `done`, precisely so it stays visible rather than blending
into a row of green checks). Check the two counts from the query above
before concluding anything is broken: if `universe qualified` or `sectors
tagged` reads 0, the chain itself is broken and `cohort 0` is a downstream
symptom, not an independent finding.

## 7. Exit checklist for Plan 0c

Quoted and adapted from the Task 18 brief's self-review and spec §11's
Phase 0 exit criteria, scoped to what Plan 0c adds:

- [ ] Every role's `list_tools()` matches `ROLE_TOOLS` and contains nothing
      matching `place|cancel|replace|order` — the contract test,
      `tests/engine/contract/test_mcp_no_order_tools.py`, run against the
      **live** MCP servers (not just the declared table). The shipped
      `engine`/`runner` images install runtime dependencies only
      (`pip install .`, no `[dev]` extra), so run it from the checkout, not
      inside either container:
      ```
      cd /home/knome/trade-challenge
      python3 -m venv .venv-test && .venv-test/bin/pip install -e 'engine[dev]'
      .venv-test/bin/pytest tests/engine/contract/test_mcp_no_order_tools.py -q
      ```
- [ ] A research token on `/mcp/decide/` gets 403; no token gets 401 (§3
      above's two `curl` checks).
- [ ] One `scout`, one `catalyst`, one `research`, one `preopen` and one
      `postclose` run recorded `done` or `noop` with a structured verdict —
      zero `content_failed` — across five consecutive scheduled sessions
      once `config.yml`'s `schedule:` block is driving them (not just the
      `--once` smoke runs above). Query per job the same way as §6:
      `SELECT job, verdict, started_at FROM job_runs WHERE job=? ORDER BY id
      DESC LIMIT 5`. This is spec §11's Phase 0 research-job exit criterion,
      unreachable until this bootstrap ran (`docs/superpowers/plans/
      2026-09-04-v3-phase0-runbook.md` §10 marks it N/A for exactly that
      reason).
- [ ] The `permission_denials` field on every `RunResult` in the last five
      runs of each job is empty, or every denial is explained — read from
      the same `detail_json` (it is not stored separately; the classifier
      only surfaces its *count* into `content_failed`/`failed` detail, so a
      thorough read means checking the runner's own response at run time, or
      re-running with `docker compose -f docker/docker-compose.yml logs
      runner` open in another terminal). A non-empty list means a prompt
      still asks for a tool its job's `allowed_tools` does not grant.
- [ ] `docker stats tc-runner --no-stream` peaks under 1 GB (the container's
      own `mem_limit`, `docker/docker-compose.yml`) during a full
      `postclose` deep run — watch it live in a second terminal while §5's
      `postclose` run is in flight.
- [ ] `weekly_universe` completed with `qualified` in the low thousands
      (§6's first query), and `universe.md` renders with its ten-column
      table — read it from the container (`research_dir` is `/data/research`
      per `config.yml`):
      ```
      docker compose -f docker/docker-compose.yml exec engine cat /data/research/universe.md
      ```
- [ ] Stopping the runner container makes `/health` report `runner_ok:
      false`, and the next Claude job records `failed` with `error:
      "ConnectError"` (`RunnerClient.run`'s transport-error path in
      `engine/tc/jobs/dispatch.py` — the class name is the whole report, by
      design, so it never carries a URL or a bearer fragment):
      ```
      docker compose -f docker/docker-compose.yml down runner
      curl -s http://127.0.0.1:8080/health | python3 -m json.tool   # runner_ok: false
      docker compose -f docker/docker-compose.yml exec engine \
        tc --config /app/repo/config.yml --env /srv/tc/.env run --once research
      # job_runs: verdict=failed, detail_json contains "ConnectError"
      docker compose -f docker/docker-compose.yml up -d runner       # bring it back
      ```
      This is the proof that the host-side watchdogs see the runner from
      outside it, the same shape as the Phase 0 runbook's healthchecks/probe
      drills.

## 8. Rollback

```
docker compose -f docker/docker-compose.yml down runner
```

leaves the `engine` service ticking, closing sessions and running its
non-Claude jobs exactly as before this bootstrap. `TC_RUNNER_TOKEN` and
`TC_RUNNER_URL` are still configured in `/srv/tc/.env`, so this is **not**
the "no runner configured" deployment (`JobRunner.execute`'s first check in
`engine/tc/jobs/dispatch.py`, which is a supported no-Claude-jobs-at-all
setup and reports `noop`) — it is a configured runner that happens to be
unreachable, and every scheduled Claude job records `failed` with
`{"error": "ConnectError"}` at its own scheduled hour, exactly as the
checklist's last item deliberately provokes. Either way, nothing above ever
wrote an order or touched the account: every job in `JOB_SPECS` runs as the
`research` MCP role, which has no order tool by construction
(`engine/tc/mcp/registry.py`), and the `engine` container's own loops (tick,
session_close, the §3.6 halt) are untouched by stopping the runner. To
actually revert to a "no runner installed" deployment, remove the four keys
from step 1 instead and recreate the engine.

`docker compose -f docker/docker-compose.yml down engine` rolls the whole
bootstrap back to the state the Phase 0 runbook left the account in — read,
watch, no research, no orders.

## 9. What is deliberately still on the old stack

`docker/crontab` still names `tick`, `sessionclose` and `execute` — Plan 0c
retires their *command files' role* as the live research path, not those
cron entries. In practice this is moot right now: `tc-scheduler`, the only
container that ever executes `docker/crontab`, has been stopped since
2026-09-07 (`HANDOFF.md`, Chris: "The old broker is dead"), so nothing reads
that file today. The `engine` container's own `tick` and `session_close`
loops (`config.yml`'s `schedule:` block) have been the account's only live
monitoring since before this bootstrap ran. Phase 1 is where `docker/crontab`
and the old `tc-broker`/`tc-scheduler` services are actually removed from
`docker-compose.yml` — not written here.
