#!/bin/bash
# Assert what the read-only broker can actually do — against the built image.
#
# This exists because the claim was WRONG the first time it was written. The
# design note said "no Discord config means the order tools are not registered
# at all", and building the image disproved it: allow_write=False withholds
# exactly two tools, place_previewed_order and cancel_order. All seven
# preview_*_order tools are registered either way.
#
# The safety property still holds — no route from the scheduled container to a
# sent order — but "I read the code and it looked right" is how the wrong claim
# got written into four documents. This pins it to a measurement that re-runs.
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
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "  skip — image '$IMAGE' not built (docker build -f docker/Dockerfile -t $IMAGE .)"; exit 0; }

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

echo "== the broker service cannot flip allow_write via its own env =="
# schwab-mcp reads SCHWAB_MCP_DISCORD_{TOKEN,CHANNEL_ID,APPROVERS} straight from
# the environment, and ANY ONE of them sets discord_requested -> allow_write.
# The broker's entrypoint still needs Discord to report "waiting for re-auth",
# so it carries the same secrets under TC_DISCORD_* names. If someone ever
# "tidies" those back to the canonical names, the broker silently gains
# place_previewed_order. This is that tripwire.
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
  cfg="$(docker compose -f docker/docker-compose.yml --env-file "$envf" config 2>/dev/null)"
  [ -n "$tmpenv" ] && rm -f "$tmpenv"
  broker_env="$(awk '/^  broker:$/{f=1;next} /^  [a-z-]+:$/{f=0} f' <<<"$cfg")"
  if grep -q 'SCHWAB_MCP_DISCORD' <<<"$broker_env"; then
    bad "broker service exposes SCHWAB_MCP_DISCORD_* — this ENABLES order execution"
    grep -o 'SCHWAB_MCP_DISCORD[A-Z_]*' <<<"$broker_env" | sort -u | sed 's/^/       /'
  else
    ok "broker service exposes no SCHWAB_MCP_DISCORD_* var"
  fi
  grep -q 'TC_DISCORD_TOKEN' <<<"$broker_env" \
    && ok "broker still reaches Discord via the safe TC_DISCORD_* alias" \
    || bad "broker has no TC_DISCORD_TOKEN — it cannot report a re-auth stall"
else
  echo "  skip — docker not installed"
fi

echo
echo "-------------------------------------------"
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
