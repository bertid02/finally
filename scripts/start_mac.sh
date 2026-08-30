#!/usr/bin/env bash
#
# Start FinAlly (macOS / Linux).
#
#   ./scripts/start_mac.sh              build if the image is missing, then run
#   ./scripts/start_mac.sh --build      force a rebuild first
#   ./scripts/start_mac.sh --no-open    don't open a browser
#
# Idempotent: an existing container is replaced, not duplicated. The database
# lives in the `finally-data` volume, so replacing the container keeps the
# portfolio. Uses the same image tag, container name and volume as
# docker-compose.yml, so the two ways of running are interchangeable.

set -euo pipefail

IMAGE="finally:latest"
CONTAINER="finally"
VOLUME="finally-data"
PORT="8000"
URL="http://localhost:${PORT}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FORCE_BUILD=false
OPEN_BROWSER=true
for arg in "$@"; do
  case "$arg" in
    --build)    FORCE_BUILD=true ;;
    --no-open)  OPEN_BROWSER=false ;;
    -h|--help)  sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not on PATH." >&2
  echo "Install Docker Desktop: https://docs.docker.com/desktop/" >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Error: the Docker daemon is not reachable." >&2
  echo "Start Docker Desktop and wait for it to report 'running', then re-run this script." >&2
  exit 1
fi

# A missing .env is a warning, not an error. The app runs fine without one --
# simulator prices, $10k portfolio, trading, charts -- and only the AI chat panel
# is dead, because that is the one thing needing OPENROUTER_API_KEY. Refusing to
# start would break the "clone and run one command" promise for a student who
# just wants to see the terminal. docker-compose.yml tolerates it the same way.
ENV_ARGS=()
if [ -f .env ]; then
  ENV_ARGS=(--env-file .env)
else
  echo
  echo "  ! No .env file found at ${ROOT}/.env"
  echo "    Starting anyway. Market data, trading and charts all work."
  echo "    The AI chat panel will NOT work until you add an OpenRouter key:"
  echo
  echo "        cp .env.example .env"
  echo "        \$EDITOR .env        # set OPENROUTER_API_KEY"
  echo
  echo "    Then re-run this script. Leave MASSIVE_API_KEY empty for the simulator."
  echo
fi

if $FORCE_BUILD || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "==> Building $IMAGE (first build takes a few minutes)"
  docker build -t "$IMAGE" .
else
  echo "==> Using existing image $IMAGE (pass --build to rebuild)"
fi

# Replace any previous container. The volume is untouched, so data survives.
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "==> Removing previous container '$CONTAINER' (the $VOLUME volume is kept)"
  docker rm -f "$CONTAINER" >/dev/null
fi

echo "==> Starting $CONTAINER on port $PORT"
docker run -d \
  --name "$CONTAINER" \
  ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} \
  -p "${PORT}:8000" \
  -v "${VOLUME}:/app/db" \
  --restart unless-stopped \
  "$IMAGE" >/dev/null

# Wait for the app rather than telling the user to refresh until it works.
echo -n "==> Waiting for ${URL}/api/health "
for _ in $(seq 1 60); do
  if curl -fsS "${URL}/api/health" >/dev/null 2>&1; then
    echo " ready"
    break
  fi
  if [ -z "$(docker ps -q -f name="^${CONTAINER}$")" ]; then
    echo
    echo "Error: the container exited during startup. Logs:" >&2
    docker logs --tail 40 "$CONTAINER" >&2
    exit 1
  fi
  echo -n "."
  sleep 1
done

echo
echo "FinAlly is running at ${URL}"
echo "  logs:  docker logs -f ${CONTAINER}"
echo "  stop:  ./scripts/stop_mac.sh"

if $OPEN_BROWSER && command -v open >/dev/null 2>&1; then
  open "$URL"
elif $OPEN_BROWSER && command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || true
fi
