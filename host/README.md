# Host probe — the layer outside docker

`healthprobe.py` is the host-side half of spec §7 layer 2. It runs on the
Pi's system Python, on a systemd timer, entirely outside the `engine`
container — because if the container is wedged, the token has died, or
docker itself is down, nothing running *inside* a container can report that.
It is stdlib-only (`urllib.request`, `json`, `hashlib`, `datetime`,
`zoneinfo`, `pathlib`, `os`) so it needs nothing beyond the python3 already
on the box: no venv, no `pip install`, no dependency to go stale.

This file covers the probe on its own. For the full Phase 0 deploy sequence
it is one step of — host layout, bring-up, seeding the high-water mark, the
first token, Tailscale, and the nightly shadow diff — see
`docs/superpowers/plans/2026-09-04-v3-phase0-runbook.md`.

It polls `http://127.0.0.1:8080/health` — the `engine` container's own
health endpoint, bound to loopback by `network_mode: host` — once every two
minutes, evaluates the rules in `evaluate()`, and posts to a Discord webhook
the `engine` process does not itself hold (`PROBE_WEBHOOK`, below). A probe
that shared the engine's own alerting path could not tell you the engine's
alerting path is what died.

## Install

Run as root on the Pi (or via `sudo`):

```
sudo mkdir -p /opt/tc
sudo cp -r host /opt/tc/host
sudo cp /opt/tc/host/tc-healthprobe.service /opt/tc/host/tc-healthprobe.timer /etc/systemd/system/
sudo mkdir -p /var/lib/tc /etc/tc
sudo $EDITOR /etc/tc/probe.env      # see below — must contain PROBE_WEBHOOK
sudo systemctl daemon-reload
sudo systemctl enable --now tc-healthprobe.timer
```

Check it fired:

```
systemctl status tc-healthprobe.timer
systemctl status tc-healthprobe.service
journalctl -u tc-healthprobe.service -n 50
```

`/var/lib/tc/probe.last` holds the dedupe state (a hash of the last posted
message plus its epoch) — safe to delete if you want the next run to post
unconditionally regardless of the last 30 minutes.

## `/etc/tc/probe.env`

One line, owned by root, not world-readable, never committed anywhere:

```
PROBE_WEBHOOK=https://discord.com/api/webhooks/...
```

Use a webhook the `engine` container itself does not have access to — the
whole point of a host-side probe is that it can still speak when the engine
cannot. It does not need to be (and should not be) `TC_DISCORD_WEBHOOK_URL`
or `TC_DISCORD_SHADOW_WEBHOOK_URL` from `/srv/tc/.env` below.

## `/srv/tc/.env` — what the `engine` container itself needs

This is a separate file, owned by the `engine` service (`docker/docker-
compose.yml`'s `env_file:` and the bind-mounted `/srv/tc/.env:ro`), not read
by the probe. Chris creates it on the host; it is never committed. Keys:

```
TC_SCHWAB_APP_KEY=...
TC_SCHWAB_APP_SECRET=...
TC_DISCORD_WEBHOOK_URL=...
TC_DISCORD_SHADOW_WEBHOOK_URL=...
TC_HEALTHCHECKS_BASE_URL=...
```

The `runner` service reads the same file (its own `env_file:` in `docker/
docker-compose.yml`) for two of its keys:

```
CLAUDE_CODE_OAUTH_TOKEN=...
TC_RUNNER_TOKEN=...
```

`CLAUDE_CODE_OAUTH_TOKEN` is the subscription token the CLI runs headless
under; `TC_RUNNER_TOKEN` is the bearer the `engine` presents when it calls the
runner's `POST /run`. Nothing else goes in the runner's environment — no
Schwab credential, no database — see `docker/docker-compose.yml` for why.

## Building and running the `engine` service

From the repo root:

```
docker compose -f docker/docker-compose.yml build engine
docker compose -f docker/docker-compose.yml up -d engine
docker compose -f docker/docker-compose.yml down engine
```

`/data` inside the container is `/srv/tc/data` on the host — create it
before the first `up` (`mkdir -p /srv/tc/data`).
