"""Cliente del stream MJPEG de IP Webcam con reconexión automática.

Estrategia:
  1. Se intenta abrir el stream con ``cv2.VideoCapture`` (backend FFMPEG), que
     es el camino medido en la placa (~20 fps de captura a 1080x1920).
  2. Si FFMPEG no logra abrir el stream, se usa un lector MJPEG propio basado
     en ``urllib`` (parser de multipart con ``Content-Length``), que no depende
     del buffer interno del decodificador.
  3. Ante cualquier fallo se reintenta con backoff exponencial (1, 2, 4, … 30 s)
     sin bloquear al resto de los hilos.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import cv2
import numpy as np

from .config import Config
from .estado import Estado

log = logging.getLogger(__name__)

_MAX_BACKOFF = 30.0
_USER_AGENT = "VigiaCaminanteUNOQ/1.0"
# I-007: tope de acumulación al buscar el boundary de una parte MJPEG.
_MAX_PARTE = 2 * 1024 * 1024

# I-004: hosts que nunca pueden ser la cámara (SSRF / metadata cloud).
_IP_PROPIA = "192.168.1.5"  # IP del host UNO Q en la LAN de la feria
_HOSTS_PROHIBIDOS = frozenset(
    {"localhost", "localhost.localdomain", "metadata", "metadata.google.internal"}
)


@lru_cache(maxsize=1)
def _ips_propias() -> frozenset:
    """IPs del propio host (constante + las que resuelve el sistema).

    Se cachea: solo se usa para validar ``camara_url`` y la topología de la
    LAN no cambia en caliente.
    """
    ips = {_IP_PROPIA}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ips.add(str(info[4][0]))
    except OSError:
        pass
    # IP de salida a la LAN; UDP connect no envía tráfico, solo resuelve ruta.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.2)
            sock.connect(("192.168.1.1", 9))
            ips.add(sock.getsockname()[0])
    except OSError:
        pass
    return frozenset(ips)


def _host_prohibido(host: str) -> bool:
    """True para loopback, no especificado, link-local o el propio host.

    I-004: se bloquea ``127.0.0.0/8``, ``::1``, ``localhost``, ``0.0.0.0``,
    ``::``, ``169.254.0.0/16`` (incluye 169.254.169.254) y la IP propia.
    Deliberadamente NO se bloquean los rangos privados enteros: la cámara
    legítima vive en ``192.168.1.9``.
    """
    literal = (host or "").strip().strip("[]").lower().rstrip(".")
    if not literal:
        return True
    if literal in _HOSTS_PROHIBIDOS:
        return True
    try:
        ip = ipaddress.ip_address(literal)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_unspecified or ip.is_link_local:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _host_prohibido(str(ip.ipv4_mapped))
    return str(ip) in _ips_propias()


def url_mjpeg(base: str) -> str:
    """Normaliza la URL: acepta la base del teléfono o el stream completo."""
    base = (base or "").strip()
    if not base:
        return ""
    if not base.startswith(("http://", "https://")):
        base = "http://" + base
    minus = base.lower()
    if minus.endswith((".mjpg", ".mjpeg")) or "/video" in minus or "/videofeed" in minus:
        return base
    return base.rstrip("/") + "/video"


def es_url_valida(url: str) -> bool:
    """True si la URL es http(s) con host resoluble y no apunta al propio host.

    Rechaza loopback, ``localhost``, direcciones no especificadas, link-local
    y la IP propia del host (vector SSRF hacia los servicios locales).
    """
    if not url or not isinstance(url, str):
        return False
    try:
        u = urlparse(url if "://" in url else "http://" + url)
    except ValueError:
        return False
    host = (u.hostname or "").strip()
    if u.scheme not in ("http", "https") or not host:
        return False
    if "." not in host and host.lower().rstrip(".") not in ("localhost",):
        return False
    return not _host_prohibido(host)


class _LectorMjpeg:
    """Lector mínimo del multipart/x-mixed-replace de IP Webcam."""

    def __init__(self, url: str, timeout_s: float) -> None:
        self.url = url
        self.timeout_s = timeout_s
        self._resp = None
        self._resto = b""

    def abrir(self) -> bool:
        try:
            peticion = Request(self.url, headers={"User-Agent": _USER_AGENT})
            self._resp = urlopen(peticion, timeout=self.timeout_s)
            if self._resp.status != 200:
                self.cerrar()
                return False
            self._resto = b""
            return True
        except Exception as exc:  # noqa: BLE001 - se reporta y se reintenta
            log.warning("Lector MJPEG: no se pudo abrir %s (%s)", self.url, exc)
            self.cerrar()
            return False

    def leer(self) -> Optional[np.ndarray]:
        if self._resp is None:
            return None
        try:
            # Cabecera de la parte: termina en línea en blanco.
            while True:
                pos = self._resto.find(b"\r\n\r\n")
                salto = 4
                if pos < 0:
                    pos = self._resto.find(b"\n\n")
                    salto = 2
                if pos >= 0:
                    break
                trozo = self._resp.read(4096)
                if not trozo:
                    return None
                self._resto += trozo
                if len(self._resto) > 65536:  # cabecera absurda: resincronizar
                    self._resto = self._resto[-2048:]

            cabecera = self._resto[:pos].decode("latin-1", "ignore")
            self._resto = self._resto[pos + salto:]

            largo = 0
            for linea in cabecera.splitlines():
                if linea.lower().startswith("content-length:"):
                    largo = int(linea.split(":", 1)[1].strip())
                    break
            if largo <= 0:
                # I-007: sin Content-Length se acumulan chunks (hasta 2 MB)
                # hasta encontrar el boundary o vencer el timeout, en vez de
                # reconectar cuando el frame llega partido en varios ``read``.
                limite = time.monotonic() + max(0.5, self.timeout_s)
                while True:
                    fin = self._resto.find(b"\r\n--")
                    if fin >= 0:
                        break
                    if len(self._resto) > _MAX_PARTE:
                        log.warning("Lector MJPEG: parte > %d bytes, se resincroniza", _MAX_PARTE)
                        self._resto = b""
                        return None
                    if time.monotonic() > limite:
                        log.debug("Lector MJPEG: timeout esperando el boundary de la parte")
                        return None
                    trozo = self._resp.read(8192)
                    if not trozo:
                        return None
                    self._resto += trozo
                datos = self._resto[:fin]
                self._resto = self._resto[fin + 2:]
            else:
                while len(self._resto) < largo:
                    trozo = self._resp.read(largo - len(self._resto))
                    if not trozo:
                        return None
                    self._resto += trozo
                datos = self._resto[:largo]
                self._resto = self._resto[largo:]

            if datos[:2] != b"\xff\xd8":
                return None
            frame = cv2.imdecode(np.frombuffer(datos, dtype=np.uint8), cv2.IMREAD_COLOR)
            return frame
        except (socket.timeout, TimeoutError, OSError, ValueError) as exc:
            log.debug("Lector MJPEG: error de lectura (%s)", exc)
            return None

    def cerrar(self) -> None:
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:  # noqa: BLE001
                pass
        self._resp = None


class CamaraThread(threading.Thread):
    """Hilo capturador: deposita en el estado el último frame crudo."""

    def __init__(self, estado: Estado, config: Config) -> None:
        super().__init__(name="camara", daemon=True)
        self.estado = estado
        self.config = config
        self._parar = threading.Event()
        self._url_actual = ""
        self._ultimo_ok = 0.0
        self.errores_seguidos = 0
        self.modo_lector = "inactivo"

    # -- ciclo principal -------------------------------------------------
    def run(self) -> None:
        backoff = 1.0
        while not self._parar.is_set():
            if self.estado.get_modo() != "camara":
                self._soltar()
                self._parar.wait(0.25)
                continue

            url = url_mjpeg(self.estado.get_camara_url() or self.config.cam_url)
            if not es_url_valida(url):
                log.warning("Camara: URL inválida %r", url)
                self.estado.actualizar_estado(mensaje="URL de cámara inválida")
                self._parar.wait(2.0)
                continue
            if url != self._url_actual:
                log.info("Camara: conectando a %s", url)
                self._url_actual = url

            if not self._capturar(url):
                self.errores_seguidos += 1
                self.estado.actualizar_estado(
                    mensaje=f"Sin conexión con la cámara (reintento {self.errores_seguidos})"
                )
                log.warning(
                    "Camara: fallo de captura con %s, reintento en %.0f s", url, backoff
                )
                self._parar.wait(backoff)
                backoff = min(_MAX_BACKOFF, max(backoff * 2.0, self.config.cam_reconnect_backoff_sec))
                continue

            backoff = 1.0
            self.errores_seguidos = 0
            # La URL pudo cambiar mientras se capturaba: salir del bucle.
            if url_mjpeg(self.estado.get_camara_url() or self.config.cam_url) != self._url_actual:
                self._url_actual = ""

    def detener(self) -> None:
        self._parar.set()
        self._soltar()

    # -- helpers ---------------------------------------------------------
    def _capturar(self, url: str) -> bool:
        """Captura hasta que haya error, cambio de URL o parada. True si capturó."""
        cap = self._conectar_cv2(url)
        if cap is not None:
            self.modo_lector = "cv2"
            try:
                return self._bucle_cv2(cap, url)
            finally:
                cap.release()

        lector = _LectorMjpeg(url, self.config.cam_timeout_ms / 1000.0)
        if lector.abrir():
            self.modo_lector = "mjpeg"
            try:
                return self._bucle_mjpeg(lector, url)
            finally:
                lector.cerrar()

        self._ultimo_ok = 0.0
        return False

    def _conectar_cv2(self, url: str) -> Optional["cv2.VideoCapture"]:
        try:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                # segundo intento con el backend por defecto
                cap = cv2.VideoCapture(url)
            if not cap.isOpened():
                cap.release()
                return None
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, frame = cap.read()
            if not ok or frame is None or frame.size == 0:
                cap.release()
                return None
            self.estado.set_frame_crudo(frame, time.time())
            self._ultimo_ok = time.time()
            log.info("Camara: stream abierto con OpenCV (%dx%d)", frame.shape[1], frame.shape[0])
            return cap
        except Exception as exc:  # noqa: BLE001
            log.warning("Camara: OpenCV no pudo abrir el stream (%s)", exc)
            return None

    def _bucle_cv2(self, cap: "cv2.VideoCapture", url: str) -> bool:
        limite = self.config.cam_timeout_ms / 1000.0
        fallos = 0
        while not self._parar.is_set():
            if self.estado.get_modo() != "camara":
                return True
            if url_mjpeg(self.estado.get_camara_url() or self.config.cam_url) != url:
                return True
            ok, frame = cap.read()
            if ok and frame is not None and frame.size:
                self.estado.set_frame_crudo(frame, time.time())
                self._ultimo_ok = time.time()
                fallos = 0
                continue
            fallos += 1
            if time.time() - self._ultimo_ok > limite or fallos > 30:
                log.warning("Camara: stream sin datos durante %.1f s", limite)
                return False
            self._parar.wait(0.02)
        return True

    def _bucle_mjpeg(self, lector: _LectorMjpeg, url: str) -> bool:
        limite = self.config.cam_timeout_ms / 1000.0
        log.info("Camara: stream abierto con lector MJPEG propio")
        while not self._parar.is_set():
            if self.estado.get_modo() != "camara":
                return True
            if url_mjpeg(self.estado.get_camara_url() or self.config.cam_url) != url:
                return True
            frame = lector.leer()
            if frame is not None and frame.size:
                self.estado.set_frame_crudo(frame, time.time())
                self._ultimo_ok = time.time()
                continue
            if time.time() - self._ultimo_ok > limite:
                log.warning("Camara: lector MJPEG sin datos durante %.1f s", limite)
                return False
            self._parar.wait(0.01)
        return True

    def _soltar(self) -> None:
        """Suelta la conexión actual (al cambiar de modo)."""
        if self._url_actual:
            log.info("Camara: captura en pausa")
            # Se limpia para no repetir el aviso cada 250 ms y para forzar
            # la reconexión cuando se vuelva al modo cámara.
            self._url_actual = ""
            self.modo_lector = "inactivo"
        self._ultimo_ok = 0.0
