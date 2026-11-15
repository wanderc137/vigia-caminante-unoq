"""Generador sintético de frames para probar la UI y el pipeline sin teléfono.

Dibuja un pasillo en perspectiva y un objeto que se acerca desde el fondo hasta
la cámara en 10 s, con ciclo ``GREEN -> AMBER -> RED -> AMBER -> GREEN``.
"""

from __future__ import annotations

import logging
import math
import threading
import time

import cv2
import numpy as np

from .config import Config
from .estado import Estado

log = logging.getLogger(__name__)

ANCHO = 1080
ALTO = 1920
PERIODO_S = 10.0

# Modelo de perspectiva simple: el objeto va del fondo (6 m) a la cámara (0,6 m)
Z_FONDO = 6.0
Z_CERCA = 0.6
HORIZONTE_PX = int(ALTO * 0.30)
K_PERSPECTIVA = 624.0   # px·m: y = HORIZONTE + K/z
K_TAMANO = 260.0        # px·m: radio = K/z


def _triangular(t: float) -> float:
    """0 -> 1 -> 0 en un período de 10 s (acercamiento y alejamiento)."""
    p = (t % PERIODO_S) / PERIODO_S
    return p / 0.5 if p < 0.5 else (1.0 - p) / 0.5


class DemoThread(threading.Thread):
    """Hilo generador: produce frames cuando el modo activo es ``demo``."""

    def __init__(self, estado: Estado, config: Config) -> None:
        super().__init__(name="demo", daemon=True)
        self.estado = estado
        self.config = config
        self._parar = threading.Event()
        self._fondo = self._crear_fondo()

    # ------------------------------------------------------------------
    def run(self) -> None:
        periodo = 1.0 / max(1.0, self.config.fps_target)
        log.info("Demo: generador sintético listo (objetivo %.1f fps)", self.config.fps_target)
        while not self._parar.is_set():
            if self.estado.get_modo() != "demo":
                self._parar.wait(0.2)
                continue
            inicio = time.perf_counter()
            t = time.time()
            frame = self._generar_frame(t)
            self.estado.set_frame_crudo(frame, t)
            sobrante = periodo - (time.perf_counter() - inicio)
            if sobrante > 0:
                self._parar.wait(sobrante)

    def detener(self) -> None:
        self._parar.set()

    # ------------------------------------------------------------------
    def _crear_fondo(self) -> np.ndarray:
        """Fondo de pasillo pre-renderizado (se copia al inicio de cada frame)."""
        img = np.zeros((ALTO, ANCHO, 3), dtype=np.uint8)
        # degradado vertical: suelo claro, fondo oscuro
        for y in range(ALTO):
            f = y / float(ALTO)
            img[y, :] = (
                int(40 + 70 * f),
                int(42 + 72 * f),
                int(48 + 78 * f),
            )
        # perspectiva: líneas del piso convergiendo al punto de fuga
        fuga = (ANCHO // 2, int(ALTO * 0.30))
        for i in range(-6, 7):
            x_piso = ANCHO // 2 + i * 190
            cv2.line(img, fuga, (x_piso, ALTO - 1), (150, 150, 150), 2, cv2.LINE_AA)
        for k in range(1, 9):
            y = int(ALTO * 0.30 + (ALTO * 0.70) * (k / 9.0) ** 1.7)
            cv2.line(img, (0, y), (ANCHO, y), (120, 120, 120), 2, cv2.LINE_AA)
        # paredes laterales y techo
        cv2.rectangle(img, (0, 0), (ANCHO - 1, int(ALTO * 0.30)), (60, 58, 55), -1)
        cv2.line(img, (0, 0), fuga, (90, 88, 85), 4, cv2.LINE_AA)
        cv2.line(img, (ANCHO - 1, 0), fuga, (90, 88, 85), 4, cv2.LINE_AA)
        # corredor de riesgo resaltado tenue (y>0.35, x 0.25..0.75)
        cv2.line(
            img,
            (int(ANCHO * 0.25), int(ALTO * 0.35)),
            (int(ANCHO * 0.25), ALTO - 1),
            (70, 70, 60),
            2,
        )
        cv2.line(
            img,
            (int(ANCHO * 0.75), int(ALTO * 0.35)),
            (int(ANCHO * 0.75), ALTO - 1),
            (70, 70, 60),
            2,
        )
        return img

    def _generar_frame(self, t: float) -> np.ndarray:
        frame = self._fondo.copy()
        a = _triangular(t)

        # El objeto se acerca: profundidad lineal, tamaño y altura 1/z.
        z = Z_FONDO - (Z_FONDO - Z_CERCA) * a
        radio = int(max(12, K_TAMANO / z))
        y = int(min(ALTO - radio - 4, HORIZONTE_PX + K_PERSPECTIVA / z))
        # leve oscilación lateral para que el tracker tenga que seguirlo
        x = int(ANCHO * 0.5 + math.sin(t * 1.7) * ANCHO * 0.05 * (1.0 - a * 0.5))
        x = max(radio + 2, min(ANCHO - radio - 2, x))

        color = (40, int(90 + 120 * a), int(200 - 60 * a))
        cv2.circle(frame, (x, y), radio, color, -1, cv2.LINE_AA)
        cv2.circle(frame, (x, y), radio, (250, 250, 250), max(2, radio // 60), cv2.LINE_AA)
        # "cara" simplificada para que el blob no sea perfectamente uniforme
        cv2.circle(frame, (x - radio // 3, y - radio // 4), max(4, radio // 7), (30, 30, 30), -1)
        cv2.circle(frame, (x + radio // 3, y - radio // 4), max(4, radio // 7), (30, 30, 30), -1)
        cv2.ellipse(
            frame,
            (x, y + radio // 4),
            (max(6, radio // 2), max(3, radio // 3)),
            0,
            0,
            180,
            (30, 30, 30),
            max(2, radio // 18),
        )

        # pequeños objetos de ruido (deben ser filtrados o ignorados)
        for k in range(3):
            fase = (t * 0.7 + k * 2.1) % 6.0
            rx = int(ANCHO * (0.1 + 0.8 * ((k * 0.37 + t * 0.05) % 1.0)))
            ry = int(ALTO * (0.45 + 0.1 * math.sin(fase)))
            cv2.rectangle(frame, (rx, ry), (rx + 22, ry + 22), (200, 210, 220), -1)

        return frame
