"""Punto de entrada de Vigía Caminante UNO Q.

Arranca cuatro hilos cooperativos y un bucle supervisor:

* ``CamaraThread``  — lee el MJPEG del teléfono (o queda en pausa en modo demo).
* ``DemoThread``    — genera frames sintéticos cuando el modo es ``demo``.
* ``VisionThread``  — procesa el frame, calcula el riesgo y publica el JPEG.
* ``HTTP``          — sirve la UI, el stream MJPEG y la API JSON.

El bucle principal vigila la señal (watchdog) y responde a SIGTERM/SIGINT.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

# Permite ejecutar ``python3 src/main.py`` sin instalar el paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vigia import overlay  # noqa: E402
from vigia.camara import CamaraThread  # noqa: E402
from vigia.config import load_config  # noqa: E402
from vigia.demo import DemoThread  # noqa: E402
from vigia.estado import Estado, EstadoFrame  # noqa: E402
from vigia.servidor import crear_servidor, run_server  # noqa: E402
from vigia.vision import VisionThread  # noqa: E402

VERSION = "1.0.0"
log = logging.getLogger("vigia")


# ---------------------------------------------------------------------------
def configurar_logging(nivel: str, archivo: Path) -> None:
    """Log a consola + archivo rotativo (5 MB x 2 backups)."""
    archivo.parent.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(threadName)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    raiz = logging.getLogger()
    raiz.setLevel(getattr(logging, nivel, logging.INFO))
    for manejador in list(raiz.handlers):
        raiz.removeHandler(manejador)

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    raiz.addHandler(consola)

    try:
        fichero = logging.handlers.RotatingFileHandler(
            archivo, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
        )
        fichero.setFormatter(formato)
        raiz.addHandler(fichero)
    except OSError as exc:
        log.warning("No se pudo abrir el log %s: %s", archivo, exc)


def _pidfile(config) -> Path:
    return config.data_dir / "vigia.pid"


def escribir_pid(config) -> None:
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        _pidfile(config).write_text(str(os.getpid()), encoding="ascii")
    except OSError as exc:
        log.warning("No se pudo escribir el pidfile: %s", exc)


def borrar_pid(config) -> None:
    ruta = _pidfile(config)
    try:
        if ruta.is_file() and ruta.read_text(encoding="ascii").strip() == str(os.getpid()):
            ruta.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
def _bucle_watchdog(estado: Estado, config, parar: threading.Event) -> None:
    """Fuerza NO_SIGNAL si el último frame es demasiado viejo.

    I-011: no compite con la visión. La escritura del estado lógico se hace
    con un compare-and-set atómico que solo aplica si la visión tampoco
    publicó dentro de la ventana del watchdog; así no se pisa un estado recién
    calculado (evita el flicker NO_SIGNAL por RMW).
    """
    ultimo_aviso = 0.0
    ultimo_render = 0.0
    while not parar.is_set():
        modo = estado.get_modo()
        ajustes = estado.get_ajustes()
        # I-003: defensa en profundidad; el API ya exige >= 200.
        try:
            watchdog_ms = max(200.0, float(ajustes.get("watchdog_ms", config.watchdog_ms)))
        except (TypeError, ValueError):
            watchdog_ms = max(200.0, float(config.watchdog_ms))
        edad = estado.edad_frame_ms()
        ahora = time.time()
        camara_url = estado.get_camara_url()

        sin_senal = modo == "detenido" or edad > watchdog_ms
        if sin_senal:
            mensaje = (
                "Análisis detenido" if modo == "detenido" else "SIN SEÑAL: sin frames de la cámara"
            )

            def _mutar(actual: EstadoFrame) -> Optional[EstadoFrame]:
                edad_pub_ms = (
                    (ahora - actual.timestamp) * 1000.0 if actual.timestamp > 0 else float("inf")
                )
                # La visión publicó dentro de la ventana: su estado es más
                # fresco que la estimación del watchdog, no se pisa.
                if modo != "detenido" and edad_pub_ms <= watchdog_ms:
                    return None
                if actual.nivel == "NO_SIGNAL" and actual.modo == modo and actual.mensaje == mensaje:
                    return None
                return replace(
                    actual,
                    nivel="NO_SIGNAL",
                    score=0.0,
                    ttc=float("inf"),
                    objetos=[],
                    modo=modo,
                    camara_url=camara_url,
                    mensaje=mensaje,
                    frame_age_ms=edad,
                    timestamp=ahora,
                )

            if estado.reemplazar_estado_si(_mutar) is not None:
                if modo != "detenido" and ahora - ultimo_aviso > 10.0:
                    log.warning("Watchdog: sin frames recientes (edad %.0f ms)", edad)
                    ultimo_aviso = ahora

            if estado.get_estado().nivel == "NO_SIGNAL" and ahora - ultimo_render > 1.0:
                etiqueta = "ANALISIS DETENIDO" if modo == "detenido" else "SIN SENAL"
                estado.set_frame_anotado(overlay.jpeg_sin_senal(config, mensaje=etiqueta))
                ultimo_render = ahora
        parar.wait(0.25)


def _apagar(hilos: list, servidor, parar: threading.Event) -> None:
    log.info("Apagando hilos…")
    parar.set()
    for hilo in hilos:
        detener = getattr(hilo, "detener", None)
        if callable(detener):
            try:
                detener()
            except Exception:  # noqa: BLE001
                pass
    for hilo in hilos:
        hilo.join(timeout=3.0)
    if servidor is not None:
        try:
            servidor.shutdown()
            servidor.server_close()
        except Exception:  # noqa: BLE001
            pass
    log.info("Apagado limpio completado")


# ---------------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    env_path = argv[0] if argv else None
    config = load_config(env_path)
    configurar_logging(config.log_level, config.log_file)

    log.info("=" * 66)
    log.info("Vigía Caminante UNO Q v%s", VERSION)
    log.info("Cámara: %s", config.cam_url)
    log.info("Web:    http://0.0.0.0:%d/ (puerto %d)", config.puerto, config.puerto)
    log.info("Modo inicial: %s", config.modo_inicial)
    log.info("=" * 66)

    estado = Estado(camara_url=config.cam_url, modo=config.modo_inicial)
    escribir_pid(config)

    parar = threading.Event()

    def _senal(numero, _frame=None):
        log.info("Señal %s recibida: solicitando apagado", signal.Signals(numero).name)
        parar.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _senal)
        except (ValueError, OSError):
            pass

    camara = CamaraThread(estado, config)
    demo = DemoThread(estado, config)
    vision = VisionThread(estado, config)
    hilos = [camara, demo, vision]

    servidor = None
    try:
        servidor = crear_servidor(estado, config)
    except OSError as exc:
        log.error("No se pudo abrir el puerto %d: %s", config.puerto, exc)
        borrar_pid(config)
        return 2

    hilo_http = threading.Thread(
        target=run_server, args=(estado, config, servidor), name="http", daemon=True
    )
    hilo_http.start()

    for hilo in hilos:
        hilo.start()

    hilo_watchdog = threading.Thread(
        target=_bucle_watchdog, args=(estado, config, parar), name="watchdog", daemon=True
    )
    hilo_watchdog.start()

    log.info("Servicio operativo. Ctrl+C o SIGTERM para detener.")

    try:
        while not parar.wait(0.5):
            pass
    except KeyboardInterrupt:
        parar.set()
    finally:
        _apagar(hilos, servidor, parar)
        borrar_pid(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
