#!/usr/bin/env bash
# One-time VM setup for Minutes in Seconds (Ubuntu 24.04, x86_64 or ARM64).
# Run as the default user (ubuntu) with sudo rights:
#   curl -fsSL https://raw.githubusercontent.com/TheBooleanJulian/minutes-in-seconds/master/deploy/vm/setup.sh | bash
# Then fill in ~/minutes-in-seconds/deploy/vm/.env and run: cd ~/minutes-in-seconds/deploy/vm && docker compose up -d --build
set -euo pipefail

REPO=https://github.com/TheBooleanJulian/minutes-in-seconds.git
DIR="$HOME/minutes-in-seconds"

# Docker Engine + compose plugin (official convenience script; idempotent enough to re-run).
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

# Swap: pyannote + Whisper medium peak around 6-8 GB; swap keeps a small shape (12 GB) from OOM-killing a long job.
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 4G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# Unattended security updates (Ubuntu default package, make sure it's on).
sudo apt-get update -y
sudo apt-get install -y unattended-upgrades git
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

if [ ! -d "$DIR/.git" ]; then
  git clone "$REPO" "$DIR"
fi
if [ ! -f "$DIR/deploy/vm/.env" ]; then
  cp "$DIR/deploy/vm/.env.example" "$DIR/deploy/vm/.env"
  chmod 600 "$DIR/deploy/vm/.env"
fi

echo
echo "Done. Next:"
echo "  1. Log out and back in (docker group)."
echo "  2. Fill in $DIR/deploy/vm/.env (TUNNEL_TOKEN at least)."
echo "  3. cd $DIR/deploy/vm && docker compose up -d --build   (first build: 10-20 min, ARM)"
