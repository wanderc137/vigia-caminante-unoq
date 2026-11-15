"""Estado compartido y thread-safe entre los hilos del servicio.

Contiene tres bloques independientes, cada uno con su propio candado para no
serializar todo el pipeline:

* **frame crudo** (lo escribe el capturador / el demo, lo lee la visión),
* **frame anotado JPEG** (lo escribe la visión, lo leen los clientes MJPEG),
* **estado lógico** (nivel, score, objetos…) más los ajustes en caliente.

La espera de un frame nuevo usa ``threading.Condition`` para no hacer
busy-wait en los hilos del servidor HTTP.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from threading import Condition, Lock
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

NIVELES = ("NO_SIGNAL", "GREEN", "AMBER", "RED")
MODOS = ("camara", "demo", "detenido")


def _num(valor: float, decimales: int = 3) -> Optional[float]:
    """Convierte a float JSON-seguro (``inf``/``nan`` -> ``None``)."""
    if valor is None:
        return None
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, decimales)


@dataclass
class ObjetoRiesgo:
    """Objeto en movimiento detectado y su evaluación de riesgo."""

    id: int
    bbox: Tuple[int, int, int, int]              # x, y, w, h en px del frame original
    bbox_norm: Tuple[float, float, float, float]  # x, y, w, h en 0..1
    area_rel: float
    velocidad_px_s: float
    velocidad_y: float          # px/s, positivo hacia abajo (acercamiento)
    crecimiento_area_s: float   # (dA/dt)/A  en 1/s
    ttc: float                  # segundos estimados hasta el contacto
    en_corredor: bool
    nivel_individual: str       # GREEN / AMBER / RED
    score: float = 0.0

    def a_dict(self) -> Dict[str, Any]:
        return {
            "id": int(self.id),
            "bbox": [int(v) for v in self.bbox],
            "bbox_norm": [round(float(v), 4) for v in self.bbox_norm],
            "area_rel": _num(self.area_rel, 4),
            "velocidad_px_s": _num(self.velocidad_px_s, 1),
            "velocidad_y": _num(self.velocidad_y, 1),
            "crecimiento_area_s": _num(self.crecimiento_area_s, 3),
            "ttc": _num(self.ttc, 2),
            "en_corredor": bool(self.en_corredor),
            "nivel_individual": self.nivel_individual,
            "score": _num(self.score),
        }


@dataclass
class EstadoFrame:
    """Fotografía lógica del sistema en un instante."""

    nivel: str = "NO_SIGNAL"
    score: float = 0.0
    ttc: float = float("inf")
    fps: float = 0.0
    latencia_ms: float = 0.0
    frame_age_ms: float = 0.0
    objetos: List[ObjetoRiesgo] = field(default_factory=list)
    modo: str = "camara"
    camara_url: str = ""
    mensaje: str = ""
    timestamp: float = 0.0

    def a_dict(self) -> Dict[str, Any]:
        return {
            "nivel": self.nivel,
            "score": _num(self.score),
            "ttc": _num(self.ttc, 2),
            "fps": _num(self.fps, 2),
            "latencia_ms": _num(self.latencia_ms, 1),
            "frame_age_ms": _num(self.frame_age_ms, 0),
            "objetos": [o.a_dict() for o in self.objetos],
            "n_objetos": len(self.objetos),
            "modo": self.modo,
            "camara_url": self.camara_url,
            "mensaje": self.mensaje,
            "timestamp": _num(self.timestamp, 3),
        }

    def copia(self) -> "EstadoFrame":
        return replace(self, objetos=list(self.objetos))


class Estado:
    """Contenedor compartido. Todas las operaciones son seguras entre hilos."""

    def __init__(self, camara_url: str = "", modo: str = "camara") -> None:
        # --- frames (crudo + anotado) bajo un mismo candado con condición
        self._cond = Condition(Lock())
        self._frame_crudo: Optional[np.ndarray] = None
        self._ts_crudo: float = 0.0
        self._version_crudo: int = 0

        self._jpeg: Optional[bytes] = None
        self._version_jpeg: int = 0

        # --- estado lógico
        self._lock_estado = Lock()
        self._estado = EstadoFrame(
            nivel="NO_SIGNAL",
            modo=modo if modo in MODOS else "camara",
            camara_url=camara_url,
            mensaje="Esperando señal de cámara",
            timestamp=time.time(),
        )

        # --- ajustes en caliente (POST /api/config)
        self._lock_config = Lock()
        self._modo = modo if modo in MODOS else "camara"
        self._camara_url = camara_url
        self._ajustes: Dict[str, Any] = {}

        self.t_inicio = time.time()

    # ------------------------------------------------------------------
    # Frames
    # ------------------------------------------------------------------
    def set_frame_crudo(self, frame: np.ndarray, timestamp: float) -> int:
        """Publica un frame BGR recién capturado. Devuelve su versión."""
        with self._cond:
            self._frame_crudo = frame
            self._ts_crudo = float(timestamp)
            self._version_crudo += 1
            version = self._version_crudo
            self._cond.notify_all()
        return version

    def get_frame_crudo(self) -> Optional[Tuple[np.ndarray, float]]:
        with self._cond:
            if self._frame_crudo is None:
                return None
            return self._frame_crudo, self._ts_crudo

    def get_frame_crudo_con_version(self) -> Optional[Tuple[np.ndarray, float, int]]:
        with self._cond:
            if self._frame_crudo is None:
                return None
            return self._frame_crudo, self._ts_crudo, self._version_crudo

    def version_crudo(self) -> int:
        with self._cond:
            return self._version_crudo

    def edad_frame_ms(self) -> float:
        """Antigüedad del último frame crudo en milisegundos."""
        with self._cond:
            ts = self._ts_crudo
            hay = self._frame_crudo is not None
        if not hay or ts <= 0:
            return float("inf")
        return max(0.0, (time.time() - ts) * 1000.0)

    def esperar_frame_crudo(self, version: int, timeout: float = 1.0) -> bool:
        """Bloquea hasta que cambie la versión del frame crudo o venza el plazo."""
        with self._cond:
            if self._version_crudo != version:
                return True
            self._cond.wait(timeout)
            return self._version_crudo != version

    def set_frame_anotado(self, jpeg_bytes: bytes) -> int:
        with self._cond:
            self._jpeg = jpeg_bytes
            self._version_jpeg += 1
            version = self._version_jpeg
            self._cond.notify_all()
        return version

    def get_frame_anotado(self) -> Optional[bytes]:
        with self._cond:
            return self._jpeg

    def get_frame_anotado_con_version(self) -> Tuple[Optional[bytes], int]:
        with self._cond:
            return self._jpeg, self._version_jpeg

    def esperar_frame_anotado(self, version: int, timeout: float = 5.0) -> bool:
        """Bloquea hasta que haya un JPEG nuevo. Evita busy-wait en /stream."""
        with self._cond:
            if self._version_jpeg != version:
                return True
            self._cond.wait(timeout)
            return self._version_jpeg != version

    # ------------------------------------------------------------------
    # Estado lógico
    # ------------------------------------------------------------------
    def set_estado(self, estado: EstadoFrame) -> None:
        with self._lock_estado:
            self._estado = estado

    def get_estado(self) -> EstadoFrame:
        with self._lock_estado:
            return self._estado.copia()

    def actualizar_estado(self, **cambios: Any) -> EstadoFrame:
        """Aplica cambios parciales al estado actual y lo devuelve."""
        with self._lock_estado:
            self._estado = replace(self._estado, **cambios)
            return self._estado.copia()

    def reemplazar_estado_si(
        self, funcion: Callable[[EstadoFrame], Optional[EstadoFrame]]
    ) -> Optional[EstadoFrame]:
        """Actualiza el estado de forma atómica si ``funcion(actual)`` no es None.

        I-011: permite que el watchdog decida y escriba dentro del mismo
        candado, sin lectura-modificación-escritura que pueda pisar un estado
        recién publicado por el hilo de visión. Devuelve el estado nuevo o
        ``None`` si no hubo cambio.
        """
        with self._lock_estado:
            nuevo = funcion(self._estado)
            if nuevo is None:
                return None
            self._estado = nuevo
            return self._estado.copia()

    # ------------------------------------------------------------------
    # Modo, URL de cámara y ajustes en caliente
    # ------------------------------------------------------------------
    def get_modo(self) -> str:
        with self._lock_config:
            return self._modo

    def set_modo(self, modo: str) -> str:
        modo = (modo or "").strip().lower()
        if modo not in MODOS:
            raise ValueError(f"modo inválido: {modo!r}")
        with self._lock_config:
            self._modo = modo
        with self._lock_estado:
            self._estado.modo = modo
        return modo

    def get_camara_url(self) -> str:
        with self._lock_config:
            return self._camara_url

    def set_camara_url(self, url: str) -> str:
        url = (url or "").strip()
        # Orden de candados documentado: _lock_config -> _lock_estado.
        with self._lock_config:
            self._camara_url = url
        # El snapshot que consume /api/estado debe reflejar el cambio al
        # instante. Sin esto, con la cámara caída (NO_SIGNAL) el watchdog
        # republica estados derivados del anterior y la UI seguía mostrando
        # la URL vieja, como si el POST no se hubiera aplicado.
        with self._lock_estado:
            self._estado = replace(self._estado, camara_url=url)
        return url

    def get_ajustes(self) -> Dict[str, Any]:
        with self._lock_config:
            return dict(self._ajustes)

    def set_ajustes(self, ajustes: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock_config:
            self._ajustes.update(ajustes)
            return dict(self._ajustes)

    def actualizar_ajustes_si(
        self,
        nuevos: Dict[str, Any],
        validador: Callable[[Dict[str, Any]], Optional[str]],
    ) -> Optional[str]:
        """Compare-and-set de ajustes (I-013).

        Bajo un **único** ``_lock_config``: lee los ajustes vigentes, los
        fusiona con ``nuevos``, ejecuta ``validador(fusionado)`` y solo si este
        devuelve ``None`` aplica el lote con ``update``. De este modo dos
        ``POST /api/config`` concurrentes no pueden leer la misma base, validar
        ambos y escribir una combinación incoherente: el segundo valida ya
        contra el resultado del primero.

        ``validador`` se ejecuta **dentro** del candado, así que no puede
        llamar a ``get_ajustes``/``set_ajustes``/``actualizar_ajustes_si``
        (``Lock`` no reentrante, sería interbloqueo). Debe trabajar solo con el
        diccionario fusionado que recibe y con las constantes de ``Config``.

        Tampoco toma ``_lock_estado``: respeta el orden de adquisición
        ``_lock_config`` -> ``_lock_estado`` que usa ``set_modo``.

        Devuelve el mensaje de error si se rechazó, o ``None`` si se aplicó.
        """
        with self._lock_config:
            fusionado: Dict[str, Any] = dict(self._ajustes)
            fusionado.update(nuevos)
            error = validador(fusionado)
            if error is not None:
                return error
            self._ajustes.update(nuevos)
            return None

    def uptime_s(self) -> float:
        return max(0.0, time.time() - self.t_inicio)
