#!/bin/bash
# Starts Clipeo on a Mac and publishes it on your domain with Cloudflare Tunnel.
# Run it from the project folder in Terminal:   bash ops/start-mac.sh
# Safe to run again: it never overwrites your .env (only fills what is missing).
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
setvar() {  # setvar NAME VALUE  (replaces the line NAME=... or adds it)
  NAME="$1" VALUE="$2" perl -0pi -e 's/^#?\s*\Q$ENV{NAME}\E=.*$/$ENV{NAME}=$ENV{VALUE}/m or $_ .= "$ENV{NAME}=$ENV{VALUE}\n"' .env
}
getvar() { sed -n "s/^$1=//p" .env | tail -1; }

say "Docker"
if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is not running. Open it (Applications → Docker), wait for the whale to stop moving and run this again."
  exit 1
fi

say "Settings (.env)"
if [ ! -f .env ]; then
  cp ops/env.production .env
  chmod 600 .env
  setvar SECRET_KEY "$(openssl rand -hex 32)"
  setvar POSTGRES_PASSWORD "$(openssl rand -hex 24)"
  setvar BACKUP_PASSPHRASE "$(openssl rand -hex 24)"
  echo "Created .env with new random passwords."
fi
setvar SITE_ADDRESS ":80"          # Cloudflare does HTTPS in front of the tunnel

if [ -z "$(getvar CLOUDFLARE_TUNNEL_TOKEN)" ]; then
  echo
  echo "Paste the Cloudflare Tunnel token (the long text after --token). It won't show on screen:"
  read -r -s TOKEN; echo
  TOKEN="${TOKEN##* }"              # in case the whole command was pasted
  [ -n "$TOKEN" ] || { echo "No token, stopping."; exit 1; }
  setvar CLOUDFLARE_TUNNEL_TOKEN "$TOKEN"
fi

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.tunnel.yml)

say "Building (the first time takes 15-30 minutes)"
"${COMPOSE[@]}" build

if [ "$(getvar VAPID_PUBLIC_KEY)" = "__AUTO__" ]; then
  say "Push notification keys"
  keys=$("${COMPOSE[@]}" run --rm --no-deps -T api python -m app.cli vapid-keys)
  setvar VAPID_PUBLIC_KEY "$(echo "$keys" | sed -n 's/^VAPID_PUBLIC_KEY=//p')"
  setvar VAPID_PRIVATE_KEY "$(echo "$keys" | sed -n 's/^VAPID_PRIVATE_KEY=//p')"
fi

say "Starting"
"${COMPOSE[@]}" up -d
sleep 10
"${COMPOSE[@]}" ps --format '{{.Service}}: {{.Status}}'

cat <<EOF

Listo. Prueba en este Mac:  http://localhost:8080
Y desde cualquier lugar:     $(getvar WEB_URL)

Siguiente (solo la primera vez), crea tu cuenta de dueño:
  docker compose exec api python -m app.cli create-owner TU_CORREO

Para que no se apague: Ajustes del Sistema → Batería/Energía → evita el reposo con la pantalla apagada.
Para detener Clipeo:   docker compose -f docker-compose.yml -f docker-compose.tunnel.yml stop
EOF
