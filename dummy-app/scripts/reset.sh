#!/bin/sh
set -eu
# Explicit reset removes only this Compose project's volumes and incident file.
cd "$(dirname "$0")/.."
docker compose down -v --remove-orphans
rm -f -- ground-truth/incidents.jsonl ground-truth/incidents.tmp
docker compose up --build -d
