#!/usr/bin/env bash
#
# Stop FinAlly (macOS / Linux).
#
# Stops and removes the container. The `finally-data` volume is deliberately
# left alone, so the portfolio, watchlist and chat history survive. To discard
# the data as well, run explicitly:
#
#     docker volume rm finally-data
#
# Idempotent: stopping something already stopped is a no-op, not an error.

set -euo pipefail

CONTAINER="finally"
VOLUME="finally-data"

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running — nothing to stop."
  exit 0
fi

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "==> Stopping and removing '$CONTAINER'"
  docker rm -f "$CONTAINER" >/dev/null
  echo "Stopped. Data kept in the '$VOLUME' volume."
else
  echo "No '$CONTAINER' container is running. Data kept in the '$VOLUME' volume."
fi
