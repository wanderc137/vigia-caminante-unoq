"""Carga de la configuración de Vigía Caminante desde ``config.env``.

El archivo se busca, en este orden:
  1. la ruta indicada explícitamente,
  2. ``config.env`` en la raíz del proyecto,
  3. ``config.example`` en la raíz del proyecto,
  4. variables de entorno del proceso.

Cualquier valor ausente o inválido cae al valor por defecto seguro.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

# Raíz del proyecto: .../vigia-caminante-unoq  (este archivo vive en src/vigia/)
RAIZ = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    """Parámetros inmutables del servicio."""

    # --- red / servidor
    puerto: int
    host: str

    # --- cámara
    cam_url: str
    cam_mjpeg_path: str
    cam_timeout_ms: int
    cam_reconnect_backoff_sec: float

    # --- procesamiento
    ancho_proceso: int
    alto_proceso: int
    fps_target: float
    jpeg_calidad: int
    borde_grosor: int
    ancho_salida: int

    # --- sustracción de fondo
    bg_history: int
    bg_var_threshold: float
    bg_detect_shadows: bool
    morph_close_k: int
    morph_open_k: int
    area_minima_px: int
    area_maxima_rel: float
    aspecto_max: float

    # --- corredor de riesgo
    corredor_y_min: float
    corredor_x_min: float
    corredor_x_max: float

    # --- pesos del score
    peso_cercania: float
    peso_velocidad: float
    peso_looming: float
    peso_area: float
    peso_ttc: float

    # --- umbrales de movimiento
    vel_y_critica: float
    vel_y_min_ttc: float
    loom_critico: float
    area_pequena: float
    area_grande: float
    ttc_critico: float

    # --- suavizado e histéresis
    tau_ema: float
    umbral_rojo_in: float
    umbral_rojo_out: float
    umbral_ambar_in: float
    umbral_ambar_out: float
    min_tiempo_rojo_ms: float

    # --- vigilancia de señal
    watchdog_ms: float

    # --- registro y modo
    log_level: str
    log_file: Path
    modo_inicial: str

    # --- rutas
    raiz: Path
    web_dir: Path
    data_dir: Path

    # ------------------------------------------------------------------
    @property
    def url_mjpeg(self) -> str:
        """URL absoluta del stream MJPEG (acepta base o URL ya completa)."""
        base = self.cam_url.strip()
        if not base:
            base = "http://127.0.0.1:8080"
        if not base.startswith(("http://", "https://")):
            base = "http://" + base
        minus = base.lower()
        if minus.endswith((".mjpg", ".mjpeg")) or "/video" in minus or "/videofeed" in minus:
            return base
        ruta = self.cam_mjpeg_path or "/video"
        if not ruta.startswith("/"):
            ruta = "/" + ruta
        return base.rstrip("/") + ruta


# ---------------------------------------------------------------------------
# Lectura y coerción de valores
# ---------------------------------------------------------------------------
def _valor_env(valor: str) -> str:
    """Normaliza el valor de una línea ``CLAVE=VALOR`` de ``config.env``.

    I-009: ``#`` inicia comentario solo al principio del valor o precedido de
    un espacio; así una URL con fragmento (``http://host/video#alt``) no se
    trunca. Los valores entrecomillados se respetan tal cual.
    """
    valor = valor.strip()
    if valor[:1] in ('"', "'"):
        comilla = valor[0]
        fin = valor.find(comilla, 1)
        if fin > 0:
            return valor[1:fin]
    for indice, caracter in enumerate(valor):
        if caracter == "#" and (indice == 0 or valor[indice - 1].isspace()):
            valor = valor[:indice]
            break
    return valor.strip().strip('"').strip("'")


def _leer_archivo_env(ruta: Path) -> Dict[str, str]:
    datos: Dict[str, str] = {}
    try:
        texto = ruta.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return datos
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        valor = _valor_env(valor)
        if clave:
            datos[clave] = valor
    return datos


def _f(datos: Dict[str, str], clave: str, defecto: float, alias: str = "") -> float:
    for k in (clave, alias):
        if k and k in datos:
            try:
                return float(datos[k])
            except (TypeError, ValueError):
                pass
    return float(defecto)


def _i(datos: Dict[str, str], clave: str, defecto: int, alias: str = "") -> int:
    return int(round(_f(datos, clave, defecto, alias)))


def _b(datos: Dict[str, str], clave: str, defecto: bool) -> bool:
    if clave in datos:
        return datos[clave].strip().lower() in ("1", "true", "si", "sí", "yes", "on")
    return defecto


def _s(datos: Dict[str, str], clave: str, defecto: str) -> str:
    valor = datos.get(clave, "").strip()
    return valor or defecto


def load_config(env_path: str | os.PathLike | None = None) -> Config:
    """Construye la :class:`Config` a partir de ``config.env``."""
    if env_path is None:
        candidatos = [RAIZ / "config.env", RAIZ / "config.example"]
        ruta = next((c for c in candidatos if c.is_file()), candidatos[0])
    else:
        ruta = Path(env_path)
        if not ruta.is_absolute():
            ruta = RAIZ / ruta

    datos = _leer_archivo_env(ruta)

    log_file = Path(_s(datos, "LOG_FILE", "data/vigia.log"))
    if not log_file.is_absolute():
        log_file = RAIZ / log_file

    modo = _s(datos, "MODO", "camara").strip().lower()
    if modo not in ("camara", "demo", "detenido"):
        modo = "camara"

    cfg = Config(
        puerto=_i(datos, "PS_PORT", 8030, "PUERTO"),
        host=_s(datos, "PS_HOST", "0.0.0.0"),
        cam_url=_s(datos, "CAM_URL", "http://192.168.1.9:8080"),
        cam_mjpeg_path=_s(datos, "CAM_MJPEG_PATH", "/video"),
        cam_timeout_ms=_i(datos, "CAM_TIMEOUT_MS", 5000),
        cam_reconnect_backoff_sec=_f(datos, "CAM_RECONNECT_BACKOFF_SEC", 5.0),
        ancho_proceso=max(64, _i(datos, "ANCHO_PROCESO", 320)),
        alto_proceso=max(64, _i(datos, "ALTO_PROCESO", 569)),
        fps_target=min(60.0, max(1.0, _f(datos, "FPS_TARGET", 15.0))),
        jpeg_calidad=min(100, max(20, _i(datos, "JPEG_CALIDAD", 70))),
        borde_grosor=max(2, _i(datos, "BORDE_GROSOR", 12)),
        ancho_salida=max(0, _i(datos, "ANCHO_SALIDA", 540)),
        bg_history=max(1, _i(datos, "BG_HISTORY", 200)),
        bg_var_threshold=_f(datos, "BG_VAR_THRESHOLD", 25.0),
        bg_detect_shadows=_b(datos, "BG_DETECT_SHADOWS", False),
        morph_close_k=max(1, _i(datos, "MORPH_CLOSE_K", 7)),
        morph_open_k=max(1, _i(datos, "MORPH_OPEN_K", 5)),
        area_minima_px=max(1, _i(datos, "AREA_MINIMA_PX", 200)),
        area_maxima_rel=min(0.99, max(0.05, _f(datos, "AREA_MAXIMA_REL", 0.95))),
        aspecto_max=max(1.5, _f(datos, "ASPECTO_MAX", 5.0)),
        corredor_y_min=min(0.95, max(0.0, _f(datos, "CORREDOR_Y_MIN", 0.35))),
        corredor_x_min=min(0.95, max(0.0, _f(datos, "CORREDOR_X_MIN", 0.25))),
        corredor_x_max=max(0.05, min(1.0, _f(datos, "CORREDOR_X_MAX", 0.75))),
        peso_cercania=_f(datos, "PESO_CERCANIA", 0.25),
        peso_velocidad=_f(datos, "PESO_VELOCIDAD", 0.20),
        peso_looming=_f(datos, "PESO_LOOMING", 0.25),
        peso_area=_f(datos, "PESO_AREA", 0.15),
        peso_ttc=_f(datos, "PESO_TTC", 0.15),
        vel_y_critica=max(1.0, _f(datos, "VEL_Y_CRITICA", 150.0)),
        vel_y_min_ttc=max(0.0, _f(datos, "VEL_Y_MIN_TTC", 20.0)),
        loom_critico=max(0.01, _f(datos, "LOOM_CRITICO", 2.0)),
        area_pequena=max(0.0, _f(datos, "AREA_PEQUENA", 0.02, "AREA_PEQUEÑA")),
        area_grande=max(0.01, _f(datos, "AREA_GRANDE", 0.20)),
        ttc_critico=max(0.1, _f(datos, "TTC_CRITICO", 2.0)),
        tau_ema=max(0.01, _f(datos, "TAU_EMA", 0.3)),
        umbral_rojo_in=_f(datos, "UMBRAL_ROJO_IN", 0.70),
        umbral_rojo_out=_f(datos, "UMBRAL_ROJO_OUT", 0.55),
        umbral_ambar_in=_f(datos, "UMBRAL_AMBAR_IN", 0.40),
        umbral_ambar_out=_f(datos, "UMBRAL_AMBAR_OUT", 0.25),
        min_tiempo_rojo_ms=max(0.0, _f(datos, "MIN_TIEMPO_ROJO_MS", 800.0)),
        watchdog_ms=max(200.0, _f(datos, "WATCHDOG_MS", 2000.0)),
        log_level=_s(datos, "LOG_LEVEL", "INFO").upper(),
        log_file=log_file,
        modo_inicial=modo,
        raiz=RAIZ,
        web_dir=RAIZ / "web",
        data_dir=RAIZ / "data",
    )
    return cfg
