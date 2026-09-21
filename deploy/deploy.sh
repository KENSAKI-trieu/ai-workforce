#!/usr/bin/env bash
# Builds and (re)starts the production stack on the VPS.
#
#   ./deploy/deploy.sh          # build, start, migrate
#   ./deploy/deploy.sh --pull   # also git pull first
#
# Run from the repository root as the deploy user.
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml)

log() { printf '\n==> %s\n' "$1"; }
fail() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

[ -f .env ] || fail "missing .env - start from deploy/env.prod.example"
[ -f backend/.env ] || fail "missing backend/.env - start from deploy/backend.env.prod.example"

# Fail fast on the settings that silently break a production deploy.
grep -q '^APP_ENV=production' backend/.env \
  || fail "backend/.env must set APP_ENV=production (secure cookie + CORS depend on it)"
grep -q '^APP_DEBUG=false' backend/.env \
  || fail "backend/.env must set APP_DEBUG=false (otherwise every SQL statement is logged)"
grep -q '^COOKIE_SECURE=true' backend/.env \
  || fail "backend/.env must set COOKIE_SECURE=true (the app refuses to start in production without it)"
for var in APP_HOSTNAME API_HOSTNAME AI_HOSTNAME TUNNEL_TOKEN AI_SERVICE_INTERNAL_TOKEN; do
  grep -qE "^${var}=.+" .env || fail ".env is missing a value for ${var}"
done

if [ "${1:-}" = "--pull" ]; then
  log "Pulling latest code"
  git pull --ff-only
fi

log "Building images"
# NEXT_PUBLIC_* are baked into the frontend bundle, so a hostname change
# requires a rebuild, not just a restart.
"${COMPOSE[@]}" build

log "Starting services"
"${COMPOSE[@]}" up -d --remove-orphans

log "Waiting for the backend to become healthy"
for _ in $(seq 1 60); do
  status=$("${COMPOSE[@]}" ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk '$1=="backend"{print $2}')
  [ "${status}" = "healthy" ] && break
  sleep 5
done
[ "${status:-}" = "healthy" ] || fail "backend did not become healthy - check: ${COMPOSE[*]} logs backend"

log "Applying database migrations"
# Never run app.db.init_db here: it seeds a demo tenant with shared passwords.
"${COMPOSE[@]}" exec -T backend alembic upgrade head

log "Status"
"${COMPOSE[@]}" ps
