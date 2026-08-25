#!/bin/bash
# Assert how the broker's write gate is configured — against the built image.
#
# This exists because the claim was WRONG the first time it was written. The
# design note said "no Discord config means the order tools are not registered
# at all", and building the image disproved it: allow_write=False withholds
# exactly two tools, place_previewed_order and cancel_order. All seven
# preview_*_order tools are registered either way. "I read the code and it
# looked right" is how the wrong claim got written into four documents, so this
# pins it to a measurement that re-runs.
#
# Renamed from test-broker-readonly.sh on 2026-08-25, when the broker moved to
# state 2 (Discord-gated writes) and a file called "readonly" would have been
# asserting the opposite of its name. The image-mode measurements below are
# unchanged — they compare both modes and are true regardless of which one the
# compose file selects. What flipped is the compose assertion at the bottom.
#
# Skips cleanly when docker or the image is unavailable, so it can live in the
# normal suite on a laptop.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)" || exit 2

IMAGE="${TC_TEST_IMAGE:-tc-test:local}"
pass=0; fail=0
ok()  { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail+1)); }

command -v docker >/dev/null 2>&1 || { echo "  skip — docker not installed"; exit 0; }

# The image-backed measurements below need a built image; the compose assertion
# at the bottom does not. It used to sit behind this same early exit, so on a
# laptop or in review — where the compose file is actually EDITED — the check
# guarding the most dangerous property in the repo silently did not run. That is
# the identical mistake the compose section's own comment describes about
# keying itself on docker/.env. So: skip the probe, keep going.
have_image=1
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "  skip — image '$IMAGE' not built (docker build -f docker/Dockerfile -t $IMAGE .); running compose checks only"
  have_image=0; }

if [ "$have_image" -eq 1 ]; then
out="$(docker run --rm -v "$PWD/docker/probe:/probe:ro" --entrypoint sh "$IMAGE" -c \
        '/opt/uv-tools/schwab-mcp/bin/python /probe/tool-registry.py diff' 2>&1)" || {
  echo "  FAIL probe did not run:"; echo "$out" | sed 's/^/    /'; exit 1; }

ro_count="$(sed -n 's/^readonly_count=//p' <<<"$out")"
rw_count="$(sed -n 's/^write_count=//p'   <<<"$out")"
write_only="$(sed -n 's/^write_only=//p'  <<<"$out" | sort | tr '\n' ' ')"

echo "== the write gate covers exactly the execution tools =="
[ "$write_only" = "cancel_order place_previewed_order " ] \
  && ok "allow_write adds exactly: cancel_order, place_previewed_order" \
  || bad "allow_write adds '$write_only' — the gate's scope CHANGED, re-check every doc that describes it"

echo "== the read-only broker cannot send or cancel an order =="
ro_list="$(docker run --rm -v "$PWD/docker/probe:/probe:ro" --entrypoint sh "$IMAGE" -c \
            '/opt/uv-tools/schwab-mcp/bin/python /probe/tool-registry.py readonly' 2>/dev/null)"
grep -qx 'place_previewed_order' <<<"$ro_list" && bad "place_previewed_order IS in the read-only registry" \
                                               || ok "place_previewed_order absent"
grep -qx 'cancel_order' <<<"$ro_list" && bad "cancel_order IS in the read-only registry" \
                                      || ok "cancel_order absent"

echo "== previews remain, and that is expected =="
# Documented deliberately: a preview is a real Schwab call that changes nothing,
# and cannot become an order without place_previewed_order. If this ever starts
# failing, the docs saying "it can price an order but not send one" are stale.
grep -qx 'preview_equity_order' <<<"$ro_list" \
  && ok "preview_equity_order present (docs say so — it prices, it cannot send)" \
  || bad "preview_equity_order vanished — README/CHANGELOG now describe the wrong behaviour"

echo "== counts are what the docs claim =="
[ "$ro_count" = "23" ] && [ "$rw_count" = "25" ] \
  && ok "23 read-only / 25 write — matches README and CHANGELOG" \
  || bad "counts are $ro_count/$rw_count, docs say 23/25 — update them or explain the drift"
fi   # have_image

echo "== the broker's write gate is enabled AND human =="
# schwab-mcp reads SCHWAB_MCP_DISCORD_{TOKEN,CHANNEL_ID,APPROVERS} straight from
# the environment, and ANY ONE of them sets discord_requested -> allow_write.
#
# Until 2026-08-25 this test asserted those names were ABSENT. They are now
# required, because a broker that cannot place also cannot CLOSE, and the
# laptop no longer holds a token of its own to close with.
#
# The tripwire did not go away, it moved. What must never regress now is that
# writes stay gated on a HUMAN: all three names present (token+channel without
# APPROVERS would accept a ✅ from anyone in the channel), and
# --jesus-take-the-wheel absent, which check-consistency.sh enforces repo-wide.
# docker/.env is gitignored and exists only on a deployed server, so keying this
# check on its presence meant the tripwire guarding the MOST dangerous property
# skipped everywhere it was most likely to be broken — on a laptop, in CI, in
# review. Synthesize a throwaway env instead so it always runs.
if command -v docker >/dev/null 2>&1; then
  envf="docker/.env"
  tmpenv=""
  if [ ! -f "$envf" ]; then
    tmpenv="$(mktemp)"; envf="$tmpenv"
    cat > "$tmpenv" <<'DUMMY'
CLAUDE_CODE_OAUTH_TOKEN=x
SCHWAB_CLIENT_ID=x
SCHWAB_CLIENT_SECRET=x
SCHWAB_MCP_DISCORD_TOKEN=x
SCHWAB_MCP_DISCORD_CHANNEL_ID=1
SCHWAB_MCP_DISCORD_APPROVERS=1
TC_DATA_DIR=/tmp
IMAGE_TAG=local
DUMMY
  fi
  cfg_err="$(docker compose -f docker/docker-compose.yml --env-file "$envf" config 2>&1 >/dev/null)"
  cfg="$(docker compose -f docker/docker-compose.yml --env-file "$envf" config 2>/dev/null)"
  [ -n "$tmpenv" ] && rm -f "$tmpenv"

  # A failed render must NOT be reported as a security-property failure. The
  # `:?` guards abort interpolation for the WHOLE file on one missing variable,
  # so an unset CLAUDE_CODE_OAUTH_TOKEN blanks the broker section too — and the
  # first version of this test then announced "broker has no TC_DISCORD_TOKEN",
  # pointing at the wrong thing entirely. A test that cries security wolf over
  # a missing env var teaches you to ignore it.
  if [ -z "$cfg" ]; then
    echo "  skip — compose config did not render; the env is incomplete, not the security property:"
    printf '         %s\n' "${cfg_err:-unknown error}" | head -4
    echo
    echo "-------------------------------------------"
    echo "$pass passed, $fail failed"
    [ "$fail" -eq 0 ]
    exit $?
  fi
  broker_env="$(awk '/^  broker:$/{f=1;next} /^  [a-z-]+:$/{f=0} f' <<<"$cfg")"
  missing=""
  for v in SCHWAB_MCP_DISCORD_TOKEN SCHWAB_MCP_DISCORD_CHANNEL_ID SCHWAB_MCP_DISCORD_APPROVERS; do
    grep -q "$v" <<<"$broker_env" || missing="$missing $v"
  done
  if [ -z "$missing" ]; then
    ok "broker has all three SCHWAB_MCP_DISCORD_* vars — writes enabled, ✅/❌ gated on named approvers"
  elif [ "$missing" = " SCHWAB_MCP_DISCORD_TOKEN SCHWAB_MCP_DISCORD_CHANNEL_ID SCHWAB_MCP_DISCORD_APPROVERS" ]; then
    bad "broker has NO Discord config — allow_write=False, so no order can be placed OR CLOSED from anywhere"
  else
    # The dangerous middle: enough to enable writes, not enough to gate them.
    bad "broker is missing$missing — writes are ENABLED but the approver list is incomplete"
  fi
  grep -q 'TC_DISCORD_TOKEN' <<<"$broker_env" \
    && ok "broker reaches Discord for re-auth stalls via the explicit TC_DISCORD_* alias" \
    || bad "broker has no TC_DISCORD_TOKEN — it cannot report a re-auth stall"
else
  echo "  skip — docker not installed"
fi

echo
echo "-------------------------------------------"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
