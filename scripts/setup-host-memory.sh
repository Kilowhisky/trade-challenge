#!/bin/bash
# One-time HOST memory tuning for the trade-challenge server (Raspberry Pi 4).
#
# Run with sudo on the server. Idempotent — safe to re-run, and it prints what
# it changed. This is host configuration, not container configuration, which is
# why it lives outside bootstrap-server.sh.
#
# WHY THIS IS NOT JUST "ADD MORE SWAP":
#
# The box is a Pi 4 with 3.7Gi RAM, rooted on an SD card, and it is NOT
# dedicated — it also serves pihole-FTL, i.e. DNS for the whole network. Two
# consequences drive everything here:
#
#   1. Swap on an SD card is slow enough that a thrashing Pi is effectively
#      down, and it wears the card out. Adding gigabytes of SD swap converts a
#      fast, visible failure (one job OOM-killed, reported to Discord) into a
#      slow, invisible one (the box unresponsive for an hour, DNS with it).
#      That is a worse trade, not a better one.
#   2. zram is compressed swap held in RAM. It typically stores 2-3x its own
#      size, costs CPU instead of flash wear, and is orders of magnitude faster
#      than the SD card. On this box it is strictly the better first tier.
#
# So: zram at high priority for real use, a modest SD swapfile at low priority
# as a deep backstop, and swappiness tuned UP — which is correct with zram,
# because reaching for fast compressed RAM early is what avoids the OOM killer.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 2; }

ZRAM_MB="${ZRAM_MB:-1536}"        # ~1.5Gi device; holds well over that compressed
SWAPFILE_MB="${SWAPFILE_MB:-2048}"

say() { printf '  %s\n' "$*"; }
echo "== before =="
free -h | sed 's/^/  /'

echo "== zram (tier 1: compressed, in RAM, no flash wear) =="
if ! dpkg -s zram-tools >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq zram-tools
  say "installed zram-tools"
else
  say "zram-tools already installed"
fi
cat > /etc/default/zramswap <<EOF
# Managed by scripts/setup-host-memory.sh
ALGO=zstd
SIZE=${ZRAM_MB}
# Higher than the SD swapfile below, so the kernel fills compressed RAM first
# and only touches the card in genuine extremis.
PRIORITY=100
EOF
# ALWAYS restart, never `enable --now`. apt starts the service on install with
# the package defaults (256MB), and `enable --now` is a no-op on an already
# running unit — so the config written above never took effect and zram came up
# at 256MB while the script cheerfully reported 1536MB.
systemctl enable zramswap >/dev/null 2>&1 || true
systemctl restart zramswap
sleep 1
actual_mb=$(( $(cat /sys/block/zram0/disksize 2>/dev/null || echo 0) / 1024 / 1024 ))
if [ "$actual_mb" -ne "$ZRAM_MB" ]; then
  say "WARNING: zram is ${actual_mb}MB but ${ZRAM_MB}MB was requested"
else
  say "zram active: zstd, ${actual_mb}MB, priority 100 (verified from /sys)"
fi

echo "== SD swapfile (tier 2: deep backstop only) =="
if [ -f /etc/dphys-swapfile ]; then
  cur="$(grep -oE '^CONF_SWAPSIZE=[0-9]+' /etc/dphys-swapfile | cut -d= -f2 || echo 0)"
  if [ "$cur" != "$SWAPFILE_MB" ]; then
    sed -i "s/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=${SWAPFILE_MB}/" /etc/dphys-swapfile
    grep -q '^CONF_MAXSWAP=' /etc/dphys-swapfile \
      && sed -i "s/^CONF_MAXSWAP=.*/CONF_MAXSWAP=${SWAPFILE_MB}/" /etc/dphys-swapfile \
      || echo "CONF_MAXSWAP=${SWAPFILE_MB}" >> /etc/dphys-swapfile
    dphys-swapfile swapoff >/dev/null 2>&1 || true
    dphys-swapfile setup   >/dev/null 2>&1
    dphys-swapfile swapon  >/dev/null 2>&1
    say "SD swapfile ${cur}MB -> ${SWAPFILE_MB}MB"
  else
    say "SD swapfile already ${SWAPFILE_MB}MB"
  fi
fi

echo "== swappiness =="
# 100 is deliberate and is NOT the usual "lower is better" advice, which assumes
# swap means a disk. With zram as tier 1, swapping early is cheap and is what
# keeps the OOM killer away from pihole-FTL.
cat > /etc/sysctl.d/60-trade-challenge-memory.conf <<'EOF'
# Managed by scripts/setup-host-memory.sh — see that script for rationale.
vm.swappiness = 100
vm.vfs_cache_pressure = 50
# Let the kernel reclaim before a hard OOM rather than after.
vm.min_free_kbytes = 32768
EOF
sysctl -q --load=/etc/sysctl.d/60-trade-challenge-memory.conf
say "vm.swappiness=100 (correct WITH zram — it is fast compressed RAM, not a disk)"

echo "== after =="
free -h | sed 's/^/  /'
swapon --show 2>/dev/null | sed 's/^/  /' || cat /proc/swaps | sed 's/^/  /'
