#!/usr/bin/env bash
# Prepares a fresh Ubuntu server and starts Clipeo. Safe to run again (it skips
# what is already done and never overwrites your .env).
#   sudo bash ops/setup-server.sh
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo: sudo bash ops/setup-server.sh"; exit 1; }

say "Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null

say "Firewall: only SSH, HTTP and HTTPS"
if command -v ufw >/dev/null; then
  ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null
  ufw --force enable >/dev/null
fi

# Small servers: a swap file avoids crashes while transcribing.
if [ "$(swapon --show | wc -l)" -eq 0 ] && [ ! -f /swapfile ]; then
  say "Swap 8 GB"
  fallocate -l 8G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

say "Settings (.env)"
if [ ! -f .env ]; then
  cp ops/env.production .env
  chmod 600 .env
  sed -i "s|^SECRET_KEY=__AUTO__|SECRET_KEY=$(openssl rand -hex 32)|" .env
  sed -i "s|^POSTGRES_PASSWORD=__AUTO__|POSTGRES_PASSWORD=$(openssl rand -hex 24)|" .env
  sed -i "s|^BACKUP_PASSPHRASE=__AUTO__|BACKUP_PASSPHRASE=$(openssl rand -hex 24)|" .env
  echo "Created .env with new random passwords."
else
  echo ".env already exists: not touched."
fi

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.prod.yml)
  echo "NVIDIA GPU found: using the GPU worker."
else
  echo "No GPU: videos are processed on the CPU (slower, works the same)."
fi

say "Building (the first time takes 10-20 minutes)"
"${COMPOSE[@]}" build

if grep -q '^VAPID_PUBLIC_KEY=__AUTO__' .env; then
  say "Push notification keys"
  keys=$("${COMPOSE[@]}" run --rm --no-deps -T api python -m app.cli vapid-keys)
  pub=$(echo "$keys" | sed -n 's/^VAPID_PUBLIC_KEY=//p'); priv=$(echo "$keys" | sed -n 's/^VAPID_PRIVATE_KEY=//p')
  if [ -n "$pub" ] && [ -n "$priv" ]; then
    sed -i "s|^VAPID_PUBLIC_KEY=__AUTO__|VAPID_PUBLIC_KEY=$pub|; s|^VAPID_PRIVATE_KEY=__AUTO__|VAPID_PRIVATE_KEY=$priv|" .env
  else
    echo "Could not generate them; run later: docker compose run --rm api python -m app.cli vapid-keys"
  fi
fi

say "Starting"
"${COMPOSE[@]}" up -d
sleep 10
"${COMPOSE[@]}" ps --format '{{.Service}}: {{.Status}}'

cat <<EOF

Listo. Ahora:
  1. Crea tu cuenta de dueño (te pide la contraseña):
       docker compose exec api python -m app.cli create-owner TU_CORREO
  2. Abre https://$(sed -n 's/^SITE_ADDRESS=//p' .env)/login y activa la verificación en dos pasos (Panel).
  3. Guarda en tu gestor de contraseñas el contenido de .env:  cat .env
     (sobre todo BACKUP_PASSPHRASE: sin ella no se pueden restaurar las copias).
  4. Para añadir claves (Resend, Anthropic, Stripe, R2): nano .env  y luego
       ${COMPOSE[*]} up -d
EOF
