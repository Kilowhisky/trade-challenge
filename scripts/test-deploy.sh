#!/bin/bash
# Regression suite for scripts/deploy.sh's rollback addressing.
#
# The bug this pins: deploy.sh recorded `docker inspect --format '{{.Id}}'`,
# a LOCAL image id, and compose interpolated it as a tag — producing
# `ghcr.io/…:sha256:0f8b…`, which docker rejects as an invalid reference. The
# rollback therefore did nothing while the Discord message announced a
# successful restore. A rollback that lies is worse than none, because it is
# believed at the one moment something is already broken.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2

pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

# The exact guard deploy.sh applies before trusting a recorded rollback target.
valid_ref() {
  printf '%s' "$1" | grep -qE '^[a-z0-9./-]+(:[0-9]+)?/[a-z0-9._/-]+(@sha256:[0-9a-f]{64}|:[A-Za-z0-9._-]+)$'
}

D=0f8b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d

echo "== references docker can actually address =="
valid_ref "ghcr.io/kilowhisky/trade-challenge@sha256:$D" && ok "digest ref accepted" || bad "digest ref rejected"
valid_ref "ghcr.io/kilowhisky/trade-challenge:latest"    && ok "tag ref accepted"    || bad "tag ref rejected"
valid_ref "ghcr.io/kilowhisky/trade-challenge:sha-abc1234" && ok "sha- tag accepted" || bad "sha- tag rejected"

echo "== the exact value that caused the bug must be refused =="
valid_ref "sha256:$D" && bad "bare local image id ACCEPTED — this is the original bug" || ok "bare local image id refused"
valid_ref "$D"        && bad "bare hex ACCEPTED"                                       || ok "bare hex refused"
valid_ref ""          && bad "empty string ACCEPTED"                                   || ok "empty string refused"
valid_ref "ghcr.io/Kilowhisky/trade-challenge:latest" && bad "MIXED CASE accepted — docker rejects uppercase repo names" || ok "mixed-case repo refused"

echo "== deploy.sh no longer references the removed IMAGE_TAG variable =="
if grep -qE '^[^#]*IMAGE_TAG' scripts/deploy.sh docker/docker-compose.yml 2>/dev/null; then
  bad "IMAGE_TAG still used outside a comment — compose now reads TC_IMAGE only"
else
  ok "IMAGE_TAG gone; compose reads a single full TC_IMAGE reference"
fi

echo "== compose renders a valid image ref for a digest rollback =="
if command -v docker >/dev/null 2>&1; then
  t="$(mktemp)"
  cat > "$t" <<DUMMY
CLAUDE_CODE_OAUTH_TOKEN=x
SCHWAB_CLIENT_ID=x
SCHWAB_CLIENT_SECRET=x
SCHWAB_MCP_DISCORD_TOKEN=x
SCHWAB_MCP_DISCORD_CHANNEL_ID=1
SCHWAB_MCP_DISCORD_APPROVERS=1
TC_DATA_DIR=/tmp
TC_IMAGE=ghcr.io/kilowhisky/trade-challenge@sha256:$D
DUMMY
  rendered="$(docker compose -f docker/docker-compose.yml --env-file "$t" config 2>/dev/null | grep -m1 'image:' | awk '{print $2}')"
  rm -f "$t"
  [ "$rendered" = "ghcr.io/kilowhisky/trade-challenge@sha256:$D" ] \
    && ok "digest rollback renders as a valid ref" \
    || bad "compose rendered '$rendered'"
else
  echo "  skip — docker not installed"
fi

echo
echo "== the smoke test can actually receive its prompt =="
# `--allowedTools` is variadic. A prompt sitting after it on the command line is
# parsed as one more tool name, and claude exits with "Input must be provided
# either through stdin or as a prompt argument when using --print". deploy.sh
# discards that output and scores it as an unhealthy deploy, so the health check
# fails, the rollback fires, and the pipeline can never ship a new image — while
# every symptom points at the image rather than at the test.
#
# scheduled-run.sh was fixed for exactly this and has had a guard ever since;
# deploy.sh had the identical bug at a second call site and no guard, and it
# went unnoticed until 2026-08-25, the first deploy that reached a new image.
# Static check: dynamic ones need docker, a token and a live broker.
smoke_block="$(sed -n '/^  smoke=/,/2>&1)"/p' scripts/deploy.sh)"
if [ -z "$smoke_block" ]; then
  bad "could not find the smoke invocation in deploy.sh — this guard is now blind"
else
  grep -q 'printf .* | compose run' <<<"$smoke_block" \
    && ok "the smoke prompt is piped on stdin" \
    || bad "the smoke prompt is not piped on stdin"
  # Any bare quoted sentence left on the command line is the bug returning.
  if grep -qE -- "--allowedTools [^ ]+ +[\"']" <<<"$smoke_block" \
     || grep -qE "^\s+[\"'][A-Z][^\"']{20,}[\"']" <<<"$smoke_block"; then
    bad "a prompt string sits on the smoke command line — --allowedTools will eat it"
  else
    ok "no prompt string on the smoke command line"
  fi
fi

echo "== repo-update refuses to run on a working checkout =="
# repo-update.sh's job is `git checkout --detach origin/deploy`. On the server
# that is correct. On a laptop it silently abandons the branch you are on and
# takes the working tree with it — measured 2026-09-01, mid-session, eleven
# files. The server is always detached; a human checkout is on a named branch,
# and that shape difference is the guard.
head_before="$(git rev-parse HEAD)"
out="$(./scripts/repo-update.sh 2>&1)"; rc=$?
head_after="$(git rev-parse HEAD)"
if [ "$head_before" != "$head_after" ]; then
  bad "repo-update MOVED HEAD from ${head_before:0:8} to ${head_after:0:8} during the test suite"
  git checkout --quiet "$head_before" 2>/dev/null || true
elif [ "$rc" = "2" ] && printf '%s' "$out" | grep -q "working checkout"; then
  ok "repo-update refuses on a branch and leaves HEAD untouched"
else
  bad "repo-update did not refuse on a branch: rc=$rc out='$(printf '%s' "$out" | head -1)'"
fi

echo
echo "-------------------------------------------"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
