"""Servidor HTTP: UI estática, stream MJPEG anotado, API JSON y snapshots.

Usa ``ThreadingHTTPServer``: cada cliente se atiende en su propio hilo. El
stream no hace busy-wait: espera sobre una ``Condition`` a que la visión
publique un JPEG nuevo.
"""

from __future__ import annotations

import http.server
import json
import logging
import math
import time
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import overlay
from .camara import es_url_valida
from .config import Config
from .estado import Estado, MODOS

log = logging.getLogger(__name__)

TIPOS_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

# Parámetros ajustables en caliente desde POST /api/config
CLAVES_AJUSTABLES = (
    "umbral_rojo_in",
    "umbral_rojo_out",
    "umbral_ambar_in",
    "umbral_ambar_out",
    "min_tiempo_rojo_ms",
    "ttc_critico",
    "vel_y_critica",
    "vel_y_min_ttc",
    "loom_critico",
    "tau_ema",
    "area_minima_px",
    "area_maxima_rel",
    "watchdog_ms",
)


def _validar_ajustes(ajustes: Dict[str, Any], config: Config) -> Optional[str]:
    """Valida el estado *resultante* de los ajustes ya fusionados (I-002/I-003/I-005).

    ``ajustes`` llega **fusionado** (vigentes + lote nuevo) por
    ``Estado.actualizar_ajustes_si``. Esta función no consulta el estado
    compartido: se ejecuta dentro de ``Estado._lock_config`` (``Lock`` no
    reentrante) y un ``get_ajustes()`` aquí provocaría un interbloqueo.

    Devuelve el mensaje de error (HTTP 400) o ``None`` si es aplicable. Se
    valida el estado resultante completo, no solo los valores enviados, para
    que un ajuste parcial no deje la configuración incoherente.
    """
    efectivo: Dict[str, float] = {}
    for clave in CLAVES_AJUSTABLES:
        try:
            efectivo[clave] = float(getattr(config, clave))
        except (TypeError, ValueError, AttributeError):
            return f"configuración base inválida para {clave}"
    for clave, valor in ajustes.items():
        if clave in efectivo:
            try:
                efectivo[clave] = float(valor)
            except (TypeError, ValueError):
                pass

    if not all(math.isfinite(valor) for valor in efectivo.values()):
        return "los ajustes deben ser números finitos"

    if not (
        0.0
        < efectivo["umbral_ambar_out"]
        < efectivo["umbral_ambar_in"]
        < efectivo["umbral_rojo_out"]
        < efectivo["umbral_rojo_in"]
        < 1.0
    ):
        return (
            "umbrales incoherentes: se exige 0 < umbral_ambar_out < "
            "umbral_ambar_in < umbral_rojo_out < umbral_rojo_in < 1"
        )
    if efectivo["tau_ema"] <= 0.01:
        return "tau_ema debe ser > 0.01"
    if efectivo["ttc_critico"] <= 0.1:
        return "ttc_critico debe ser > 0.1"
    if efectivo["vel_y_critica"] <= 1.0:
        return "vel_y_critica debe ser > 1.0"
    if efectivo["vel_y_min_ttc"] < 0.0:
        return "vel_y_min_ttc no puede ser negativo"
    if efectivo["min_tiempo_rojo_ms"] < 0.0:
        return "min_tiempo_rojo_ms no puede ser negativo"
    if efectivo["area_minima_px"] < 1.0:
        return "area_minima_px debe ser >= 1"
    if not 0.0 < efectivo["area_maxima_rel"] <= 0.99:
        return "area_maxima_rel debe estar en (0, 0.99]"
    if efectivo["loom_critico"] <= 1e-3:
        return "loom_critico debe ser > 0.001"
    if efectivo["watchdog_ms"] < 200.0:
        return "watchdog_ms debe ser >= 200"
    return None


class VigiaHandler(http.server.BaseHTTPRequestHandler):
    """Manejador de una sola petición. Comparte ``estado`` y ``config``."""

    server_version = "VigiaUNOQ/1.0"
    protocol_version = "HTTP/1.1"

    estado: Estado = None            # type: ignore[assignment]
    config: Config = None            # type: ignore[assignment]
    inicio: float = 0.0
    peticiones = 0

    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - API de BaseHTTPRequestHandler
        try:
            ruta = urlparse(self.path)
            camino = ruta.path.rstrip("/") or "/"
            if camino == "/":
                self._serve_static("index.html")
            elif camino in ("/index.html", "/app.js", "/style.css", "/favicon.svg"):
                self._serve_static(camino.lstrip("/"))
            elif camino == "/stream":
                self._serve_stream()
            elif camino in ("/api/estado", "/api/status"):
                self._serve_api_estado()
            elif camino == "/api/config":
                self._serve_api_config()
            elif camino == "/snapshot.jpg":
                self._serve_snapshot()
            elif camino == "/health":
                self._serve_health()
            else:
                self._responder_json({"error": "no encontrado", "ruta": camino}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            log.exception("HTTP GET %s: %s", self.path, exc)
            try:
                self._responder_json({"error": "error interno"}, 500)
            except Exception:  # noqa: BLE001
                pass

    def do_POST(self) -> None:  # noqa: N802
        try:
            ruta = urlparse(self.path).path.rstrip("/") or "/"
            if ruta != "/api/config":
                self._responder_json({"error": "no encontrado"}, 404)
                return
            largo = int(self.headers.get("Content-Length") or 0)
            if largo <= 0 or largo > 8192:
                self._responder_json({"error": "cuerpo inválido"}, 400)
                return
            cuerpo = self.rfile.read(largo)
            self._aplicar_config(cuerpo)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            log.exception("HTTP POST %s: %s", self.path, exc)
            try:
                self._responder_json({"error": "error interno"}, 500)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Rutas
    # ------------------------------------------------------------------
    def _serve_static(self, nombre: str) -> None:
        permitidos = {"index.html", "app.js", "style.css", "favicon.svg"}
        if nombre not in permitidos:
            self._responder_json({"error": "no encontrado"}, 404)
            return
        archivo = self.config.web_dir / nombre
        try:
            datos = archivo.read_bytes()
        except OSError:
            self._responder_json({"error": f"falta {nombre}"}, 404)
            return
        sufijo = "." + nombre.rsplit(".", 1)[-1]
        self.send_response(200)
        self.send_header("Content-Type", TIPOS_MIME.get(sufijo, "application/octet-stream"))
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(datos)

    def _serve_health(self) -> None:
        self._responder_json(
            {
                "status": "ok",
                "servicio": "vigia-caminante-unoq",
                "version": "1.0.0",
                "timestamp": round(time.time(), 3),
                "uptime_s": round(self.estado.uptime_s(), 1),
            }
        )

    def _serve_api_estado(self) -> None:
        datos = self.estado.get_estado().a_dict()
        # La configuración vigente manda sobre el snapshot: si la cámara está
        # caída el hilo de visión no republica y el snapshot puede quedar
        # desfasado respecto de lo que el POST acaba de aplicar.
        datos["camara_url"] = self.estado.get_camara_url() or datos.get("camara_url", "")
        datos["modo"] = self.estado.get_modo() or datos.get("modo", "")
        datos["uptime_s"] = round(self.estado.uptime_s(), 1)
        datos["ajustes"] = self.estado.get_ajustes()
        self._responder_json(datos)

    def _serve_api_config(self) -> None:
        self._responder_json(
            {
                "puerto": self.config.puerto,
                "camara_url": self.estado.get_camara_url(),
                "modo": self.estado.get_modo(),
                "ajustes": self.estado.get_ajustes(),
                "umbrales": {
                    "rojo_in": self.config.umbral_rojo_in,
                    "rojo_out": self.config.umbral_rojo_out,
                    "ambar_in": self.config.umbral_ambar_in,
                    "ambar_out": self.config.umbral_ambar_out,
                },
            }
        )

    def _serve_snapshot(self) -> None:
        jpeg = self.estado.get_frame_anotado()
        if not jpeg:
            estado = self.estado.get_estado()
            jpeg = overlay.jpeg_sin_senal(self.config, mensaje=overlay.ETIQUETAS.get(estado.nivel, "SIN SENAL"))
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(jpeg)

    def _serve_stream(self) -> None:
        """MJPEG multipart: envía cada nuevo JPEG anotado a este cliente."""
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        estado = self.estado.get_estado()
        respaldo = overlay.jpeg_sin_senal(
            self.config, mensaje=overlay.ETIQUETAS.get(estado.nivel, "SIN SENAL")
        )
        jpeg, version = self.estado.get_frame_anotado_con_version()
        if not jpeg:
            jpeg = respaldo

        while True:
            self.wfile.write(
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(jpeg)).encode("ascii")
                + b"\r\n\r\n"
            )
            self.wfile.write(jpeg)
            self.wfile.write(b"\r\n")
            # esperar un frame NUEVO (sin busy-wait); si no llega en 2 s,
            # se reenvía el último para que el cliente no corte el vídeo
            self.estado.esperar_frame_anotado(version, timeout=2.0)
            nuevo, nueva_version = self.estado.get_frame_anotado_con_version()
            if nuevo:
                jpeg = nuevo
            version = nueva_version

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _responder_json(self, datos: Dict[str, Any], codigo: int = 200) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _aplicar_config(self, cuerpo: bytes) -> None:
        try:
            datos = json.loads(cuerpo.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._responder_json({"error": "JSON inválido"}, 400)
            return
        if not isinstance(datos, dict):
            self._responder_json({"error": "se esperaba un objeto JSON"}, 400)
            return

        cambios: Dict[str, Any] = {}
        url_nueva: Optional[str] = None
        modo_nuevo: Optional[str] = None
        ajustes: Dict[str, float] = {}

        # 1) validar TODO antes de tocar el estado: el lote es atómico.
        if "camara_url" in datos:
            url = str(datos["camara_url"]).strip()
            if not es_url_valida(url):
                self._responder_json(
                    {
                        "error": (
                            "camara_url inválida: use http(s) con host de la LAN "
                            "(no se admite loopback, IP propia ni link-local)"
                        )
                    },
                    400,
                )
                return
            url_nueva = url

        if "modo" in datos:
            modo = str(datos["modo"]).strip().lower()
            if modo not in MODOS:
                self._responder_json({"error": f"modo inválido (use {', '.join(MODOS)})"}, 400)
                return
            modo_nuevo = modo

        for clave in CLAVES_AJUSTABLES:
            if clave in datos:
                valor = datos[clave]
                if isinstance(valor, bool):
                    self._responder_json({"error": f"{clave} debe ser numérico"}, 400)
                    return
                try:
                    ajustes[clave] = float(valor)
                except (TypeError, ValueError):
                    self._responder_json({"error": f"{clave} debe ser numérico"}, 400)
                    return

        # 2) Ajustes: compare-and-set atómico (I-013). Leer + fusionar +
        #    validar + escribir ocurre bajo un único _lock_config, de modo que
        #    dos POST concurrentes no pueden dejar umbrales incoherentes. El
        #    validador recibe el diccionario ya fusionado y no consulta el
        #    estado (Lock no reentrante) ni toma _lock_estado.
        if ajustes:
            error = self.estado.actualizar_ajustes_si(
                ajustes, lambda fusionados: _validar_ajustes(fusionados, self.config)
            )
            if error:
                self._responder_json({"error": error, "campo": sorted(ajustes)}, 400)
                return
            cambios["ajustes"] = ajustes

        # 3) URL y modo ya se validaron arriba: nada de esto puede fallar, así
        #    que no queda un lote a medio aplicar.
        if url_nueva is not None:
            self.estado.set_camara_url(url_nueva)
            cambios["camara_url"] = url_nueva
        if modo_nuevo is not None:
            self.estado.set_modo(modo_nuevo)
            cambios["modo"] = modo_nuevo

        log.info("Config actualizada en caliente: %s", cambios)
        self._responder_json(
            {
                "ok": True,
                "cambios": cambios,
                "camara_url": self.estado.get_camara_url(),
                "modo": self.estado.get_modo(),
                "ajustes": self.estado.get_ajustes(),
            }
        )

    # ------------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        log.debug("%s - %s", self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args: Any) -> None:
        log.warning("%s - %s", self.address_string(), fmt % args)


class ServidorVigia(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer con ``allow_reuse_address`` y hilos daemon."""

    daemon_threads = True
    allow_reuse_address = True


def make_handler(estado: Estado, config: Config):
    """Devuelve una clase de handler con estado y config ya ligados."""
    return type(
        "HandlerVigia",
        (VigiaHandler,),
        {"estado": estado, "config": config, "inicio": time.time()},
    )


def crear_servidor(estado: Estado, config: Config) -> ServidorVigia:
    handler = make_handler(estado, config)
    servidor = ServidorVigia((config.host, config.puerto), handler)
    log.info("HTTP: escuchando en http://%s:%d", config.host, config.puerto)
    return servidor


def run_server(estado: Estado, config: Config, servidor: Optional[ServidorVigia] = None) -> None:
    srv = servidor or crear_servidor(estado, config)
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        srv.server_close()
