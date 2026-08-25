#!/bin/bash
# Bring a fresh Ubuntu box up to a running trade-challenge server.
#
# This exists so a rebuilt server is REPRODUCIBLE rather than remembered. The
# steps that need a human — filling in secrets, completing the Schwab OAuth flow
# — are prompted rather than faked, and the script is idempotent: re-running it
# on a working server checks and reports rather than clobbering.
#
# Usage: scripts/bootstrap-server.sh [--check]
#   --check   verify an existing install; change nothing.
set -uo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
check_only=0; [ "${1:-}" = "--check" ] && check_only=1

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*"; exit 1; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

step "1. Prerequisites"
command -v docker >/dev/null 2>&1 || die "docker not installed — see https://docs.docker.com/engine/install/ubuntu/"
docker compose version >/dev/null 2>&1 || die "docker compose v2 plugin missing (apt install docker-compose-plugin)"
command -v git >/dev/null 2>&1 || die "git not installed"
docker info >/dev/null 2>&1 || die "cannot talk to the docker daemon — is your user in the 'docker' group? (newgrp docker)"
ok "docker $(docker version --format '{{.Server.Version}}'), compose v2, git"

step "2. Secrets"
env_file="$repo_root/docker/.env"
if [ ! -f "$env_file" ]; then
  [ "$check_only" -eq 1 ] && die "docker/.env missing"
  cp "$repo_root/docker/.env.example" "$env_file"
  chmod 600 "$env_file"
  warn "created docker/.env from the example — FILL IT IN, then re-run:"
  printf '      %s\n' "$env_file"
  exit 1
fi
perms="$(stat -c %a "$env_file" 2>/dev/null || stat -f %A "$env_file")"
[ "$perms" = "600" ] || { [ "$check_only" -eq 1 ] && warn "docker/.env is mode $perms, want 600" || { chmod 600 "$env_file"; ok "tightened docker/.env to 600"; }; }
missing=()
for k in CLAUDE_CODE_OAUTH_TOKEN SCHWAB_CLIENT_ID SCHWAB_CLIENT_SECRET \
         SCHWAB_MCP_DISCORD_TOKEN SCHWAB_MCP_DISCORD_CHANNEL_ID; do
  grep -qE "^${k}=.+" "$env_file" || missing+=("$k")
done
[ "${#missing[@]}" -eq 0 ] || die "docker/.env is missing values for: ${missing[*]}"
ok "docker/.env present, mode 600, all required keys set"

step "3. Sidecar data repo"
# shellcheck disable=SC1090
data_dir="$(grep -E '^TC_DATA_DIR=' "$env_file" | cut -d= -f2-)"
data_dir="${data_dir:-$repo_root/../trade-challenge-store}"
if [ ! -d "$data_dir/.git" ]; then
  warn "no git repo at $data_dir"
  printf '      Create the PRIVATE sidecar repo and clone it there:\n'
  printf '        git clone git@github.com:<you>/trade-challenge-store.git %s\n' "$data_dir"
  printf '      It holds research/, status/ and trade-log.csv — never the public repo.\n'
  [ "$check_only" -eq 1 ] || exit 1
else
  ok "sidecar data repo at $data_dir"
fi

step "3b. Data symlinks"
# The jobs write to $repo/research, $repo/status and $repo/trade-log.csv — the
# paths every command file and script already names. On the server those must
# land in the PRIVATE sidecar repo instead, or nothing is ever pushed and the
# output dies on the box. Symlinks keep every existing path working unchanged;
# all three are gitignored in the public repo, so the links are never committed.
if [ -d "$data_dir/.git" ]; then
  for target in research status trade-log.csv; do
    link="$repo_root/$target"
    if [ -L "$link" ]; then
      ok "$target -> $(readlink "$link")"
    elif [ -e "$link" ]; then
      warn "$target exists as a real file/dir, not a link to the sidecar."
      printf '      Move it into %s and re-run, or the server writes where nothing syncs:\n' "$data_dir"
      printf '        mv %s %s/ && ln -s %s/%s %s\n' "$link" "$data_dir" "$data_dir" "$target" "$link"
    else
      [ "$check_only" -eq 1 ] && { warn "$target missing"; continue; }
      mkdir -p "$data_dir/$(dirname "$target")" 2>/dev/null || true
      [ "$target" = "trade-log.csv" ] || mkdir -p "$data_dir/$target"
      ln -s "$data_dir/$target" "$link" && ok "linked $target -> $data_dir/$target"
    fi
  done
fi

step "4. Image"
cd "$repo_root/docker" || die "no docker/ directory"
if [ "$check_only" -eq 1 ]; then
  docker compose config >/dev/null 2>&1 && ok "compose config valid" || die "compose config invalid"
else
  docker compose pull --quiet 2>/dev/null || {
    warn "pull failed (image may not be published yet) — building locally"
    docker compose build || die "build failed"
  }
  ok "image ready"
fi

step "5. Schwab token"
if docker volume inspect trade-challenge_schwab-token >/dev/null 2>&1 \
   && docker run --rm -v trade-challenge_schwab-token:/t alpine test -f /t/token.yaml 2>/dev/null; then
  ok "token volume present"
else
  warn "no Schwab token yet. From your LAPTOP:"
  printf '        ssh -L 8182:127.0.0.1:8182 %s\n' "$(hostname)"
  printf '      then on the server, in that same ssh session:\n'
  printf '        cd %s && docker compose run --rm schwab-auth\n' "$repo_root/docker"
  printf '      Paste the printed authorization URL into your laptop browser and\n'
  printf '      accept the self-signed certificate warning. The service declares\n'
  printf '      network_mode: host (compose run has no --network flag), which is\n'
  printf '      what lets the SSH forward reach the callback server — auth.py:106-112\n'
  printf '      rejects any callback host but 127.0.0.1.\n'
  [ "$check_only" -eq 1 ] || exit 1
fi

step "6. Start"
if [ "$check_only" -eq 1 ]; then
  docker compose ps --format '  {{.Name}}  {{.State}}  {{.Status}}'
else
  docker compose up -d || die "compose up failed"
  ok "broker and scheduler started"
fi

step "Verify"
cat <<'EOF'
  docker compose ps                       # broker healthy, scheduler running
  docker compose logs -f scheduler        # supercronic's view of the schedule
  ./scripts/token-watchdog.sh             # token age
  ./scripts/check-consistency.sh          # rules agree
  docker compose run --rm -it claude      # an interactive session

  Read-only proof (Phases 1-3): ask that session to place_previewed_order.
  That tool must be ABSENT. preview_equity_order IS present and will work —
  allow_write=False withholds exactly place_previewed_order and cancel_order
  (23 tools vs 25), so the container can price an order but cannot send one.
EOF
