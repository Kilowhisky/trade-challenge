# v3 engine — Phase 0 deploy runbook

Documentation only — no code changes. This is the operator sequence for
bringing the Phase 0b `engine` container up on the Pi **beside** the old
stack (`tc-broker` / `tc-scheduler`, still running, unaffected), and for
running Phase 0 (shadow, read-only) through to its exit criteria. Spec:
`docs/superpowers/specs/2026-09-02-v3-engine-architecture-design.md` §11.
Related: `HANDOFF.md` §"v3 engine — Phase 0b built, not yet deployed",
`host/README.md` (the probe, in full).

Every `tc` invocation below runs inside the `engine` container via
`docker compose exec`, with `--config /app/repo/config.yml --env
/srv/tc/.env` — the container's own copies at their mounted paths, not the
worktree's. Compose is only ever asked for `build`, `up -d`, `exec`, `down`,
or `logs` in this document; there is no supported `--network` flag on
`compose run` and none of these steps need `run`.

Server facts assumed throughout: Raspberry Pi 4, Debian 12, Docker 29, user
`knome`, checkout at `/home/knome/trade-challenge`. Tailscale is **not**
installed yet as of this writing — §3 below installs it, before the first
token is ever minted.

## 0. Before starting — Chris's four prerequisites

These are spec §11's Phase 0 prerequisites; do not proceed past §2 without
1 and 2 (§11: "If Schwab refuses a second app, Phase 0 runs on fixtures and
paper mode without live reads").

1. **Second Schwab app**, developer portal, callback
   `https://<pi>.<tailnet>.ts.net/oauth/callback` (the exact hostname is
   only known after §3 below runs `tailscale up`; register the app once
   that hostname is in hand). Approval takes one to three days.
2. **Tailscale on the Pi and on the phone**, MagicDNS and HTTPS certificates
   enabled in the admin console.
3. **A healthchecks.io project**, or an explicit decision to decline it and
   rely on spec §7 layers 2 (the host probe, §6 below) and 3 (expectations)
   alone.
4. **Keep the current system alive meanwhile: weekly re-auth per
   `HANDOFF.md`.** (Spec §11 prerequisite 4, quoted.) Phase 0 is shadow and
   read-only — it watches, it does not trade. The old stack is what is
   actually running the account for the whole of Phase 0, and its Schwab
   token still dies every seven days. A lapsed re-auth there is a blind
   *account*, not a blind experiment.

## 1. Host layout

Run once, before the first `up`:

```
sudo mkdir -p /srv/tc/data /srv/tc/data/backup
sudo chown -R 1000:1000 /srv/tc/data
```

`1000` is the `engine` container user (`docker/Dockerfile.engine`:
`useradd -m -u 1000 … engine`) — the container cannot write its own SQLite
store or token file into a directory it does not own.

Create `/srv/tc/.env`, mode `600`, with the `TC_*` keys documented in
`host/README.md` under "`/srv/tc/.env` — what the `engine` container itself
needs" (`TC_SCHWAB_APP_KEY`, `TC_SCHWAB_APP_SECRET`,
`TC_DISCORD_WEBHOOK_URL`, `TC_DISCORD_SHADOW_WEBHOOK_URL`,
`TC_HEALTHCHECKS_BASE_URL`). Use the **new** Schwab app's key and secret —
the whole point of a second app (§0.1) is that this token cannot
split-brain the old broker's.

```
sudo chmod 600 /srv/tc/.env
```

**Do not copy `/srv/tc/data/token.json` from the old broker's docker
volume.** It does not exist yet at this point — the engine mints its own
token against the new app in §4. A copied token would be the old app's
token in the new app's clothes, and would not authenticate.

> **The `engine` service is behind the `engine` compose profile.** Every command below names the
> service explicitly (`build engine`, `up -d engine`, `exec engine`, `down engine`), which activates
> the profile. The nightly `scripts/deploy.sh` runs a bare `compose up -d` and therefore never
> touches the engine — start and stop it only by hand during Phase 0.

## 2. Bring-up

From the checkout, on the branch this repo builds `deploy` from (this
runbook assumes `feat/v3-engine-0b` has already merged and `origin/deploy`
carries the engine service):

```
cd ~/trade-challenge
git fetch
git checkout --detach origin/deploy
docker compose -f docker/docker-compose.yml build engine
docker compose -f docker/docker-compose.yml up -d engine
curl -s http://127.0.0.1:8080/health
```

The `engine` service binds `network_mode: host` and only ever listens on
`127.0.0.1:8080` — that curl should succeed from the Pi itself immediately.
`/health`'s `ok` field will read `false` until a token exists (§4) and the
first tick has run; that is expected in shadow mode before those steps.

If it does not come up: `docker compose -f docker/docker-compose.yml logs
engine`. `tc-broker` and `tc-scheduler` are untouched by any of this — they
are a different compose service and this brings up `engine` only.

## 3. Tailscale and the callback URL

**Before the first token, not after it.** The callback URL is fixed at the
moment Schwab issues the authorization request, and it must already be
registered on the app: a re-auth against a URL Schwab does not have on file
is rejected before it ever reaches the engine, and the hostname it needs
does not exist until `tailscale up` has run.

```
sudo tailscale up
# in the Tailscale admin console: enable MagicDNS and HTTPS certificates
sudo tailscale serve --bg --https=443 http://127.0.0.1:8080
```

Note the resulting hostname (`https://<pi>.<tailnet>.ts.net`). Set
`config.yml`'s `token.callback_url` to
`https://<pi>.<tailnet>.ts.net/oauth/callback` (replacing the
`https://REPLACE-ME.ts.net/oauth/callback` placeholder), redeploy the
`engine` service so it picks the edit up (`docker compose -f
docker/docker-compose.yml up -d engine`), and register that **exact** URL on
the new Schwab app from §0.1. Schwab's app approval takes one to three days
(§0.1), so this is the step to reach early — §5's re-auth cannot be
attempted until the registered callback matches this hostname exactly.

## 4. Seed the high-water mark

Read the **`### State recorded — current`** block (never a `— superseded`
block above it) of the most recent `status/YYYY-MM-DD.md` the old stack
wrote, and take its `High-water mark:` line verbatim — do not add or
subtract the $900 reserve by hand:

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env \
  seed-hwm --value <High-water mark from that block> --recorded-on <that file's date>
```

`seed-hwm` converts for you: CLAUDE.md §3.6's migration note says any mark
from a file dated before 2026-08-31 is on the old competition-capital basis
and must gain the $900 reserve before comparison; `seed-hwm` applies
exactly that conversion (`+ $900.00` when `--recorded-on` is before
2026-08-31, unchanged on or after) keyed off the date you pass, not off
guesswork. Pass the number as printed in the file either way. This can only
be run once — it refuses if `session_status` already has a row (the store
is empty on a first deploy, so this is only ever an issue on a re-seed
attempt).

## 5. First token

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env auth-url
```

prints a Schwab login URL. Open it on the phone with Tailscale connected
(§3 above put that hostname on the app and in `config.yml`) — the callback
lands on `https://<pi>.<tailnet>.ts.net/oauth/callback`, which
the `engine` container's HTTP app serves directly (no SSH tunnel needed,
unlike the old `tc-schwab-auth` flow). Confirm:

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env token-status
```

should read `state=fresh`.

## 6. Host probe

Outside docker, per `host/README.md` in full:

```
sudo mkdir -p /opt/tc
sudo cp -r host /opt/tc/host
sudo cp /opt/tc/host/tc-healthprobe.service /opt/tc/host/tc-healthprobe.timer /etc/systemd/system/
sudo mkdir -p /var/lib/tc /etc/tc
sudo $EDITOR /etc/tc/probe.env      # PROBE_WEBHOOK=... — a webhook the engine does not own
sudo systemctl daemon-reload
sudo systemctl enable --now tc-healthprobe.timer
```

Use a Discord webhook the `engine` container has no access to — not
`TC_DISCORD_WEBHOOK_URL` or `TC_DISCORD_SHADOW_WEBHOOK_URL` from
`/srv/tc/.env`. The whole point of a probe outside docker is that it can
still speak when the container, or docker itself, cannot.

## 7. Nightly shadow diff (Phase 0 only)

The `engine` service does not mount the old stack's private store (only
`..:/app/repo:ro`, `/srv/tc/data:/data`, and `/srv/tc/.env:ro` — see
`docker/docker-compose.yml`), so `shadow-diff` cannot see the legacy
`status/*.md` / `status/ticks/*.tsv` ledgers it compares against until it
can reach them.

**The one manual edit this task does not make:** add a read-only mount to
the `engine` service's `volumes:` list in `docker/docker-compose.yml`,
Phase-0-only (drop it once the old stack is decommissioned, Phase 3):

```yaml
      - ../../trade-challenge-store:/data/store:ro
```

With that mount in place (`docker compose -f docker/docker-compose.yml up
-d engine` to pick it up), run this once per session day, after the old
stack's session-close has written that day's file:

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env \
  shadow-diff $(date +%F) --store-dir /data/store
```

Prints `SHADOW OK` (exit 0) or a line per mismatch followed by `SHADOW DIFF`
(exit 1); exit 4 means the legacy file for that date does not exist yet
(operator error — the old stack hasn't closed the session, not a diff
finding).

## 8. Operator smoke tests

```
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env run --once tick
docker compose -f docker/docker-compose.yml exec engine \
  tc --config /app/repo/config.yml --env /srv/tc/.env run --once token_check
```

Each should exit 0. `--once tick` should produce a new row in the ticks
table (visible via `/api/ticks` or the shadow-diff output above); `--once
token_check` re-prints the same `state=… age_days=… days_until_dead=…
action=…` line as `token-status`. `--once` runs one job to completion with
no HTTP surface and no scheduler loop — it is the smoke test, not the
service (`run` without `--once` is the service, already started in §2).

## 9. Rollback

```
docker compose -f docker/docker-compose.yml down engine
```

The old stack (`tc-broker`, `tc-scheduler`) is a different compose service
and is never touched by this. Nothing above this point ever wrote to the
old stack's store, posted to `#llm-yolo`, or touched an order — Phase 0 is
shadow, read-only, by construction (`config.yml`'s `shadow.enabled: true`).

## 10. Phase 0 exit checklist

Quoted verbatim from spec §11 ("Exit criteria. Phase 0: …"):

- [ ] **Ten consecutive sessions** where the shadow view matches the old
      ledgers: high-water mark identical; account value equal at matched
      timestamps; resting-stop map identical.
- [ ] **Zero `content_failed`** on research jobs. *(N/A until Plan 0c —
      there is no research job running under the engine in Phase 0b.)*
- [ ] **Healthchecks** proven by deliberately stopping the engine once and
      watching the alert arrive from outside it.
- [ ] **The host probe** proven by deliberately stopping the engine once
      and watching the alert arrive from outside it (a separate proof from
      healthchecks — §6's probe and §0.3's healthchecks.io are independent
      layers per spec §7, and each must be shown to fire on its own).
- [ ] **One complete phone re-auth drill**, with wall time recorded (repeat
      §5 end-to-end from a cold token and time it).

Only after every box above is checked does Phase 1 (spec §11: the engine
takes the Discord bot, stops go live, the old scheduler and broker stop)
begin — and Phase 1 has its own implementation plan, not written here.
