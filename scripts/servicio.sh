#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Vigía Caminante UNO Q — control del servicio (sin sudo, sin systemd)
#
#   ./scripts/servicio.sh start|stop|restart|status|logs|boot-enable|boot-disable
#
# Usa setsid + nohup para sobrevivir al cierre de la sesión SSH, guarda el PID
# en data/vigia.pid y rota los logs por tamaño antes de cada arranque.
# ---------------------------------------------------------------------------
set -u

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATOS="$RAIZ/data"
PIDFILE="$DATOS/vigia.pid"
LOG="$DATOS/vigia.stdout.log"
LOG_APP="$DATOS/vigia.log"
PY="${PYTHON:-python3}"
MARCA_CRON="vigia-caminante-unoq/scripts/servicio.sh"
MAX_LOG_BYTES=$((5 * 1024 * 1024))

cd "$RAIZ" || exit 1

puerto() {
  local p
  p="$(sed -n 's/^[[:space:]]*PS_PORT[[:space:]]*=[[:space:]]*\([0-9]\+\).*/\1/p' config.env 2>/dev/null | tail -n1)"
  echo "${p:-8030}"
}

pid_vivo() {
  [ -f "$PIDFILE" ] || return 1
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null)"
  [ -n "${pid:-}" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

parar_pid() {
  local pid="$1"
  kill -TERM "$pid" 2>/dev/null || return 0
  for _ in $(seq 1 40); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.25
  done
  echo "  aviso: no respondió a SIGTERM, enviando SIGKILL"
  kill -KILL "$pid" 2>/dev/null || true
  sleep 0.3
}

rotar_log() {
  local archivo="$1" tamano
  [ -f "$archivo" ] || return 0
  tamano=$(stat -c %s "$archivo" 2>/dev/null || echo 0)
  if [ "$tamano" -gt "$MAX_LOG_BYTES" ]; then
    # I-012: 2 backups, igual que RotatingFileHandler(backupCount=2).
    rm -f "$archivo.2" 2>/dev/null || true
    [ -f "$archivo.1" ] && mv -f "$archivo.1" "$archivo.2" 2>/dev/null || true
    mv -f "$archivo" "$archivo.1" 2>/dev/null || true
  fi
}

esperar_http() {
  local p="$1" intentos=30
  for _ in $(seq 1 "$intentos"); do
    if curl -fsS -m 2 "http://127.0.0.1:$p/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.4
  done
  return 1
}

# ---------------------------------------------------------------------------
arrancar() {
  if pid_vivo; then
    echo "Servicio ya activo (PID $(cat "$PIDFILE")) en el puerto $(puerto)."
    return 0
  fi
  mkdir -p "$DATOS"
  rm -f "$PIDFILE"
  rotar_log "$LOG"
  rotar_log "$LOG_APP"

  echo "Arrancando Vigía Caminante…"
  setsid nohup "$PY" -u src/main.py >>"$LOG" 2>&1 &

  # El propio proceso escribe data/vigia.pid. No se usa $! porque setsid
  # puede bifurcar y dejar ese PID muerto; se espera el archivo hasta 12 s
  # (importar OpenCV en la placa tarda unos segundos).
  for _ in $(seq 1 60); do
    pid_vivo && break
    sleep 0.2
  done

  if ! pid_vivo; then
    echo "ERROR: el proceso no arrancó. Últimas líneas del log:"
    tail -n 25 "$LOG" 2>/dev/null
    return 1
  fi

  local p; p="$(puerto)"
  if esperar_http "$p"; then
    echo "OK — PID $(cat "$PIDFILE"), escuchando en http://0.0.0.0:$p/"
  else
    echo "AVISO — PID $(cat "$PIDFILE") activo, pero /health aún no responde en :$p"
    tail -n 15 "$LOG" 2>/dev/null
    return 1
  fi
  return 0
}

detener() {
  if ! pid_vivo; then
    echo "Servicio no está corriendo."
    rm -f "$PIDFILE"
    return 0
  fi
  local pid; pid="$(cat "$PIDFILE")"
  echo "Deteniendo PID $pid…"
  parar_pid "$pid"
  rm -f "$PIDFILE"
  echo "Detenido."
  return 0
}

estado() {
  local p; p="$(puerto)"
  if pid_vivo; then
    local pid; pid="$(cat "$PIDFILE")"
    local etime rss cpu
    etime=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
    rss=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ')
    cpu=$(ps -o %cpu= -p "$pid" 2>/dev/null | tr -d ' ')
    echo "Estado    : ACTIVO (PID $pid)"
    echo "Uptime    : ${etime:-?}"
    echo "CPU / RSS : ${cpu:-?}% / $(( ${rss:-0} / 1024 )) MB"
    if curl -fsS -m 2 "http://127.0.0.1:$p/health" >/dev/null 2>&1; then
      echo "Health    : ok (http://192.168.1.5:$p/health)"
      local nivel score fps
      local json
      json=$(curl -fsS -m 2 "http://127.0.0.1:$p/api/estado" 2>/dev/null)
      if [ -n "$json" ]; then
        nivel=$(printf '%s' "$json" | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("nivel"))' 2>/dev/null)
        score=$(printf '%s' "$json" | "$PY" -c 'import sys,json;print("%.2f" % (json.load(sys.stdin).get("score") or 0))' 2>/dev/null)
        fps=$(printf '%s' "$json" | "$PY" -c 'import sys,json;print("%.1f" % (json.load(sys.stdin).get("fps") or 0))' 2>/dev/null)
        echo "Nivel     : ${nivel:-?} (score ${score:-?}, ${fps:-?} fps)"
      fi
    else
      echo "Health    : SIN RESPUESTA en :$p"
    fi
  else
    echo "Estado    : DETENIDO"
    [ -f "$LOG" ] && echo "Log       : $LOG"
  fi
  local cron_linea
  cron_linea=$(crontab -l 2>/dev/null | grep -c "$MARCA_CRON" || true)
  if [ "${cron_linea:-0}" -gt 0 ]; then
    echo "Arranque  : automático activado (cron @reboot)"
  else
    echo "Arranque  : manual (ejecute: $0 boot-enable)"
  fi
}

mostrar_logs() {
  local extra="${1:-}"
  local archivo="$LOG_APP"
  [ -f "$archivo" ] || archivo="$LOG"
  echo "--- $archivo ---"
  if [ "$extra" = "-f" ]; then
    tail -n 60 -f "$archivo"
  else
    tail -n "${extra:-60}" "$archivo" 2>/dev/null || echo "(sin log todavía)"
    echo "--- $LOG (stdout) ---"
    tail -n 15 "$LOG" 2>/dev/null || true
  fi
}

boot_enable() {
  local linea
  linea="@reboot sleep 20; $RAIZ/scripts/servicio.sh start >/dev/null 2>&1"
  if crontab -l 2>/dev/null | grep -qF "$MARCA_CRON"; then
    echo "Arranque automático ya estaba configurado."
  else
    { crontab -l 2>/dev/null | grep -vF "$MARCA_CRON"; echo "$linea"; } | crontab -
    echo "Arranque automático activado (@reboot, retardo 20 s)."
  fi
}

boot_disable() {
  local actual
  actual=$(crontab -l 2>/dev/null | grep -vF "$MARCA_CRON")
  if [ -z "$actual" ]; then
    crontab -r 2>/dev/null || true
  else
    printf '%s\n' "$actual" | crontab -
  fi
  echo "Arranque automático desactivado."
}

# ---------------------------------------------------------------------------
case "${1:-}" in
  start)        arrancar ;;
  stop)         detener ;;
  restart)      detener; arrancar ;;
  status)       estado ;;
  logs)         mostrar_logs "${2:-}" ;;
  boot-enable)  boot_enable ;;
  boot-disable) boot_disable ;;
  *)
    cat <<EOF
Uso: $0 {start|stop|restart|status|logs [-f|N]|boot-enable|boot-disable}

  start         arranca el servicio en segundo plano (setsid + nohup)
  stop          detiene el servicio (SIGTERM, limpio)
  restart       detiene y arranca
  status        PID, uptime, CPU/RSS, health y nivel actual
  logs [N|-f]   últimas N líneas del log (60 por defecto) o sigue en vivo
  boot-enable   agrega @reboot al crontab del usuario (sin sudo)
  boot-disable  quita el @reboot del crontab
EOF
    exit 2
    ;;
esac
