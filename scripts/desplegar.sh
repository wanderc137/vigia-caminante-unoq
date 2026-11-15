#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Despliegue de Vigía Caminante UNO Q desde la notebook a la placa.
#
#   ./scripts/desplegar.sh            # copia + config.env + restart
#   ./scripts/desplegar.sh --sin-arranque
#
# Usa la clave SSH ya configurada (~/.ssh/unoq_key) y el puerto 2222.
# No requiere sudo: todo queda en /home/arduino.
# No requiere rsync en la placa: empaqueta con tar y descomprime por SSH.
# ---------------------------------------------------------------------------
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LLAVE="${UNOQ_LLAVE:-$HOME/.ssh/unoq_key}"
USUARIO="${UNOQ_USER:-arduino}"
HOST="${UNOQ_HOST:-192.168.1.5}"
PUERTO_SSH="${UNOQ_SSH_PORT:-2222}"
DESTINO="${UNOQ_DIR:-/home/arduino/vigia-caminante-unoq}"
CAM_URL="${CAM_URL:-http://192.168.1.9:8080/video}"
PUERTO_WEB="${PS_PORT:-8030}"

SSH=(ssh -i "$LLAVE" -p "$PUERTO_SSH" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

echo "==> Copiando proyecto a $USUARIO@$HOST:$DESTINO"
"${SSH[@]}" "$USUARIO@$HOST" "mkdir -p '$DESTINO'"

# Se limpian solo los directorios versionados; config.env y data/ se conservan.
"${SSH[@]}" "$USUARIO@$HOST" "cd '$DESTINO' && rm -rf src web tests scripts"

tar -czf - \
  --exclude='.git' \
  --exclude='data' \
  --exclude='config.env' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.log' \
  --exclude='PLAN.md' \
  --exclude='AUDIT_REPORT.md' \
  --exclude='.venv' \
  -C "$RAIZ" . \
  | "${SSH[@]}" "$USUARIO@$HOST" "cd '$DESTINO' && tar -xzf -"

echo "==> Preparando config.env y data/"
"${SSH[@]}" "$USUARIO@$HOST" "cd '$DESTINO' && mkdir -p data && \
  if [ ! -f config.env ]; then cp config.example config.env; fi && \
  sed -i 's#^CAM_URL=.*#CAM_URL=$CAM_URL#' config.env && \
  sed -i 's#^PS_PORT=.*#PS_PORT=$PUERTO_WEB#' config.env && \
  chmod +x scripts/*.sh"

if [ "${1:-}" != "--sin-arranque" ]; then
  echo "==> Reiniciando servicio y habilitando @reboot"
  "${SSH[@]}" "$USUARIO@$HOST" "cd '$DESTINO' && ./scripts/servicio.sh boot-enable && ./scripts/servicio.sh restart"
fi

echo "==> Verificando"
"${SSH[@]}" "$USUARIO@$HOST" "cd '$DESTINO' && ./scripts/servicio.sh status"

echo
echo "Listo. Abrí en el navegador:  http://$HOST:$PUERTO_WEB/"
