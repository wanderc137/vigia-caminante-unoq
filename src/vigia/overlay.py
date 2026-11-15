"""Render del overlay sobre el frame: borde de riesgo, cajas y HUD.

Todo se dibuja en la placa; el navegador solo recibe JPEG ya anotados.
El borde de color es la señal principal de alerta (verde / ámbar / rojo / gris).
"""

from __future__ import annotations

import base64
import logging
from dataclasses import replace
from typing import Tuple

import cv2
import numpy as np

from .config import Config
from .estado import EstadoFrame, ObjetoRiesgo

log = logging.getLogger(__name__)

# JPEG mínimo válido (16x16 gris) para garantizar que NUNCA se envíen 0 bytes
# al stream MJPEG, ni siquiera si cv2.imencode falla de forma global.
_JPEG_MINIMO = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEyccHhcgLikxMC4pLSwzOko+"
    "MzZGNywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBDAQ4ODhMREyYVFSZPNS01T09PT09PT09PT09PT09PT09P"
    "T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0//wAARCAAQABADASIAAhEBAxEB/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKB"
    "kaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1"
    "dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl"
    "5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQA"
    "AQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNE"
    "RUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3"
    "uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDhKKKKAP/Z"
)

# I-006: último JPEG válido, compartido con el stream como respaldo.
_ultimo_jpeg_valido: bytes = b""

# Colores en BGR (OpenCV)
COLORES = {
    "NO_SIGNAL": (128, 114, 107),  # #6b7280 gris
    "GREEN": (94, 197, 34),        # #22c55e verde
    "AMBER": (11, 158, 245),       # #f59e0b ámbar
    "RED": (68, 68, 239),          # #ef4444 rojo
}
ETIQUETAS = {
    "NO_SIGNAL": "SIN SENAL",
    "GREEN": "PASO LIBRE",
    "AMBER": "ATENCION",
    "RED": "PELIGRO",
}
BLANCO = (245, 245, 245)
NEGRO = (18, 18, 18)

# Cache del frame de reemplazo (se dibuja una sola vez por tamaño)
_cache_sin_senal: dict = {}


def _color_nivel(nivel: str) -> Tuple[int, int, int]:
    return COLORES.get(nivel, COLORES["NO_SIGNAL"])


def _texto(
    frame: np.ndarray,
    texto: str,
    origen: Tuple[int, int],
    escala: float,
    color: Tuple[int, int, int],
    grosor: int = 2,
) -> None:
    cv2.putText(
        frame, texto, origen, cv2.FONT_HERSHEY_SIMPLEX, escala, color, grosor, cv2.LINE_AA
    )


def _dibujar_borde(frame: np.ndarray, color: Tuple[int, int, int], grosor: int) -> None:
    """Borde interior de ``grosor`` px en el color del nivel de riesgo."""
    alto, ancho = frame.shape[:2]
    grosor = max(2, int(grosor))
    cv2.rectangle(frame, (0, 0), (ancho - 1, alto - 1), color, grosor)


def _panel(
    frame: np.ndarray,
    origen: Tuple[int, int],
    tamano: Tuple[int, int],
    alfa: float = 0.55,
    color: Tuple[int, int, int] = NEGRO,
) -> None:
    x, y = origen
    w, h = tamano
    alto, ancho = frame.shape[:2]
    x2 = min(ancho, x + w)
    y2 = min(alto, y + h)
    if x2 <= x or y2 <= y:
        return
    roi = frame[y:y2, x:x2]
    capa = np.full(roi.shape, color, dtype=np.uint8)
    cv2.addWeighted(capa, alfa, roi, 1.0 - alfa, 0, roi)


def _dibujar_bbox(frame: np.ndarray, obj: ObjetoRiesgo, color: Tuple[int, int, int]) -> None:
    alto, ancho = frame.shape[:2]
    x, y, w, h = obj.bbox
    x = max(0, min(ancho - 1, x))
    y = max(0, min(alto - 1, y))
    w = max(1, min(ancho - x, w))
    h = max(1, min(alto - y, h))
    grosor = max(2, int(round(min(ancho, alto) / 260)))
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, grosor)

    ttc = "—" if obj.ttc is None or obj.ttc != obj.ttc or obj.ttc == float("inf") else f"{obj.ttc:.1f}s"
    etiqueta = f"ID{obj.id} {obj.score * 100:.0f}% TTC {ttc}"
    escala = max(0.45, min(ancho, alto) / 1500.0)
    grosor_txt = max(1, int(round(escala * 3)))
    (tw, th), base = cv2.getTextSize(etiqueta, cv2.FONT_HERSHEY_SIMPLEX, escala, grosor_txt)
    ty = y - th - base - 6
    if ty < 4:
        ty = min(alto - 4, y + h + th + 6)
    _panel(frame, (x, ty - th - 4), (tw + 12, th + base + 8), 0.7, NEGRO)
    _texto(frame, etiqueta, (x + 6, ty), escala, color, grosor_txt)


def _dibujar_hud(frame: np.ndarray, estado: EstadoFrame) -> None:
    alto, ancho = frame.shape[:2]
    escala = max(0.5, min(ancho, alto) / 1450.0)
    grosor = max(1, int(round(escala * 3)))
    color = _color_nivel(estado.nivel)

    lineas = [
        (ETIQUETAS.get(estado.nivel, estado.nivel), color, True),
        (f"score {estado.score * 100:.0f}%   TTC {_fmt_ttc(estado.ttc)}", BLANCO, False),
        (f"{estado.fps:.1f} fps   {estado.latencia_ms:.0f} ms", BLANCO, False),
        (f"objetos {len(estado.objetos)}   modo {estado.modo}", BLANCO, False),
    ]

    interlinea = int(round(34 * escala))
    alto_panel = interlinea * len(lineas) + int(18 * escala)
    ancho_panel = min(ancho - 8, int(round(560 * escala)))
    _panel(frame, (4, 4), (ancho_panel, alto_panel), 0.55, NEGRO)

    y = 4 + int(round(28 * escala))
    for texto, col, destacado in lineas:
        _texto(
            frame,
            texto,
            (int(round(16 * escala)), y),
            escala * (1.25 if destacado else 1.0),
            col,
            grosor + (1 if destacado else 0),
        )
        y += interlinea


def _fmt_ttc(ttc) -> str:
    if ttc is None:
        return "—"
    try:
        valor = float(ttc)
    except (TypeError, ValueError):
        return "—"
    if valor != valor or valor == float("inf"):
        return "—"
    return f"{valor:.1f}s"


def _encode_jpeg(frame: np.ndarray, calidad: int) -> bytes:
    """Codifica a JPEG; devuelve ``b""`` si OpenCV falla (I-006)."""
    try:
        ok, buffer = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(max(20, min(100, calidad)))]
        )
    except cv2.error as exc:  # noqa: PERF203 - fallo aislado de OpenCV
        log.error("JPEG: imencode lanzó excepción: %s", exc)
        return b""
    if not ok or buffer is None or getattr(buffer, "size", 0) == 0:
        log.error("JPEG: imencode falló (frame %s)", getattr(frame, "shape", "?"))
        return b""
    return buffer.tobytes()


def _reducir_salida(frame: np.ndarray, ancho_salida: int):
    """Reduce el frame de salida al ancho configurado.

    Codificar y transmitir 1080x1920 en la placa consume ~75 ms por frame; a
    540 px de ancho baja a ~25 ms y el celular lo ve igual de grande.
    Devuelve ``(frame, escala)``. Con ``ancho_salida=0`` devuelve una COPIA
    (I-008): ``renderizar`` dibuja in-place y nunca debe mutar el frame
    compartido del capturador.
    """
    alto, ancho = frame.shape[:2]
    if ancho_salida and ancho > ancho_salida:
        escala = ancho_salida / float(ancho)
        nuevo = (int(ancho_salida), max(2, int(round(alto * escala))))
        return cv2.resize(frame, nuevo, interpolation=cv2.INTER_AREA), escala
    return frame.copy(), 1.0


def _escalar_objetos(estado: EstadoFrame, escala: float) -> EstadoFrame:
    """Copia el estado con las cajas escaladas al tamaño de salida (sin mutar)."""
    if escala == 1.0 or not estado.objetos:
        return estado
    objetos = []
    for obj in estado.objetos:
        x, y, w, h = obj.bbox
        objetos.append(
            replace(
                obj,
                bbox=(
                    int(round(x * escala)),
                    int(round(y * escala)),
                    max(1, int(round(w * escala))),
                    max(1, int(round(h * escala))),
                ),
            )
        )
    return replace(estado, objetos=objetos)


def renderizar(frame: np.ndarray, estado: EstadoFrame, config: Config) -> np.ndarray:
    """Dibuja borde, cajas y HUD sobre ``frame`` (in-place) y lo devuelve.

    El llamador debe pasarle un buffer propio: ``renderizar_y_codificar``
    siempre le entrega una copia (ver ``_reducir_salida``, I-008).
    """
    color = _color_nivel(estado.nivel)
    _dibujar_borde(frame, color, config.borde_grosor)

    for obj in estado.objetos:
        _dibujar_bbox(frame, obj, _color_nivel(obj.nivel_individual))

    if not estado.objetos:
        alto, ancho = frame.shape[:2]
        if estado.nivel == "NO_SIGNAL":
            _mensaje_central(frame, estado.mensaje or "SIN SENAL", color)
        elif estado.modo == "detenido":
            _mensaje_central(frame, "ANALISIS DETENIDO", color)

    _dibujar_hud(frame, estado)
    return frame


def _mensaje_central(frame: np.ndarray, texto: str, color: Tuple[int, int, int]) -> None:
    alto, ancho = frame.shape[:2]
    escala = max(0.8, min(ancho, alto) / 900.0)
    grosor = max(2, int(round(escala * 3)))
    (tw, th), base = cv2.getTextSize(texto, cv2.FONT_HERSHEY_SIMPLEX, escala, grosor)
    x = max(6, (ancho - tw) // 2)
    y = max(6, (alto - th) // 2)
    _panel(frame, (x - 16, y - th - 14), (tw + 32, th + base + 28), 0.6, NEGRO)
    _texto(frame, texto, (x, y), escala, color, grosor)


def renderizar_y_codificar(frame: np.ndarray, estado: EstadoFrame, config: Config) -> bytes:
    """Renderiza el overlay y devuelve el JPEG listo para el stream.

    Garantía (I-006): nunca devuelve ``b""``. Si la codificación falla se
    reutiliza el último JPEG válido y, si no hay, el frame de respaldo.
    """
    global _ultimo_jpeg_valido

    salida, escala = _reducir_salida(frame, config.ancho_salida)
    renderizar(salida, _escalar_objetos(estado, escala), config)
    jpeg = _encode_jpeg(salida, config.jpeg_calidad)
    if jpeg:
        _ultimo_jpeg_valido = jpeg
        return jpeg

    if _ultimo_jpeg_valido:
        log.error(
            "JPEG: se reenvía el último frame válido (%d bytes) por fallo de codificación",
            len(_ultimo_jpeg_valido),
        )
        return _ultimo_jpeg_valido
    log.error("JPEG: sin frame válido previo; se envía el JPEG mínimo de respaldo")
    return _JPEG_MINIMO


def frame_sin_senal(config: Config, mensaje: str = "SIN SENAL") -> np.ndarray:
    """Frame sintético para cuando no hay cámara (cacheado por tamaño)."""
    clave = (config.ancho_salida, mensaje, config.borde_grosor)
    if clave in _cache_sin_senal:
        return _cache_sin_senal[clave].copy()

    ancho = int(config.ancho_salida) or 1080
    alto = max(2, int(round(ancho * 1920 / 1080)))
    frame = np.full((alto, ancho, 3), 24, dtype=np.uint8)
    # rejilla tenue para que se note que es un reemplazo, no imagen real
    paso = max(40, int(round(ancho / 9.0)))
    for y in range(0, alto, paso):
        cv2.line(frame, (0, y), (ancho, y), (38, 38, 38), 1)
    for x in range(0, ancho, paso):
        cv2.line(frame, (x, 0), (x, alto), (38, 38, 38), 1)

    color = _color_nivel("NO_SIGNAL")
    _dibujar_borde(frame, color, config.borde_grosor)
    _mensaje_central(frame, mensaje, color)
    _texto(
        frame,
        "Esperando camara IP Webcam",
        (int(ancho * 0.08), int(alto * 0.58)),
        max(0.5, ancho / 1400.0),
        BLANCO,
        2,
    )
    _cache_sin_senal[clave] = frame
    return frame.copy()


def jpeg_sin_senal(config: Config, mensaje: str = "SIN SENAL") -> bytes:
    """JPEG de respaldo garantizado no vacío (I-006)."""
    return _encode_jpeg(frame_sin_senal(config, mensaje), config.jpeg_calidad) or _JPEG_MINIMO
