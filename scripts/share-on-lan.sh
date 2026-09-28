#!/usr/bin/env bash
# Share the Control Center with everyone on your local network (macOS / Linux).
#
#   Run from the AI_Team folder:  ./scripts/share-on-lan.sh
#
# It makes sure .env has an access code (creating a random one if not), starts everything with
# Docker, then prints the address and code to give your colleagues.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example - add your AI keys there later."
fi

code="$(sed -n 's/^AITC_ACCESS_CODE=//p' .env | tail -1 | tr -d '[:space:]')"
if [ -z "$code" ]; then
  hex="$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
  code="${hex:0:4}-${hex:4:4}-${hex:8:4}"
  if grep -q '^AITC_ACCESS_CODE=' .env; then
    tmp="$(mktemp)"
    sed "s/^AITC_ACCESS_CODE=.*/AITC_ACCESS_CODE=$code/" .env > "$tmp" && cat "$tmp" > .env && rm -f "$tmp"
  else
    printf '\nAITC_ACCESS_CODE=%s\n' "$code" >> .env
  fi
  echo "Set a new access code in .env."
fi

echo "Starting the Control Center (the first build takes a few minutes)..."
docker compose up -d --build

echo "Waiting for the web app..."
for _ in $(seq 1 90); do
  curl -fs -o /dev/null http://localhost:3000/access && break
  sleep 2
done
curl -fs -o /dev/null http://localhost:3000/access || { echo "The web app didn't start. Check: docker compose logs web api"; exit 1; }

if command -v ipconfig >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
  ips="$(ipconfig getifaddr en0 || true) $(ipconfig getifaddr en1 || true)"
else
  ips="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | grep -vE '^(127|169\.254|172\.(1[7-9]|2[0-9]|3[01]))\.' || true)"
fi

echo
echo "The Control Center is shared on your network."
for ip in $ips; do echo "  Address:     http://$ip:3000"; done
echo "  Access code: $code"
echo
echo "The code keeps strangers out; anyone who has it can still choose who they act as."
echo "Stop sharing any time with: docker compose down"
