"""Modelo de riesgo: sustracción de fondo, tracking y estimación de colisión.

Pipeline por frame (a ``ANCHO_PROCESO`` px de ancho, manteniendo aspecto):

1. reducción + gris + desenfoque,
2. MOG2 + morfología + contornos,
3. filtrado por área y aspecto,
4. tracking por IoU con velocidad y crecimiento de área,
5. score individual por objeto (cercanía, velocidad, looming, área, TTC),
6. score global (máximo) + EMA + histéresis con tiempo mínimo en rojo.

La dirección "abajo" (``y`` creciente) representa mayor cercanía al caminante:
un objeto que baja y crece se está acercando.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from . import overlay
from .config import Config
from .estado import Estado, EstadoFrame, ObjetoRiesgo

log = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]


def _clamp(valor: float, minimo: float = 0.0, maximo: float = 1.0) -> float:
    if valor < minimo:
        return minimo
    if valor > maximo:
        return maximo
    return valor


def _sigmoide(x: float) -> float:
    x = max(-60.0, min(60.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _iou(a: BBox, b: BBox) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = float((x2 - x1) * (y2 - y1))
    union = float(aw * ah + bw * bh) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class _Track:
    """Pista interna del tracker (una por objeto seguido)."""

    id: int
    bbox: BBox
    centro: Tuple[float, float]
    area_rel: float
    t: float
    vx: float = 0.0
    vy: float = 0.0
    crecimiento: float = 0.0
    vistos: int = 1


class Tracker:
    """Asigna IDs persistentes por IoU y estima cinemática de cada objeto."""

    def __init__(self, config: Config, iou_threshold: float = 0.3, max_age: float = 0.5) -> None:
        self.config = config
        self.iou_threshold = iou_threshold
        self.max_age = max_age
        self._tracks: Dict[int, _Track] = {}
        self._siguiente_id = 1
        self.ancho_frame = 0
        self.alto_frame = 0

    # ------------------------------------------------------------------
    def update(
        self,
        detecciones: Sequence[BBox],
        timestamp: float,
        ancho: Optional[int] = None,
        alto: Optional[int] = None,
    ) -> List[ObjetoRiesgo]:
        if ancho:
            self.ancho_frame = int(ancho)
        if alto:
            self.alto_frame = int(alto)
        ancho_f = max(1, self.ancho_frame)
        alto_f = max(1, self.alto_frame)

        libres = dict(self._tracks)
        usados = set()
        asignados: List[Tuple[int, BBox]] = []

        # 1) emparejar detecciones con pistas existentes por IoU máximo
        for bbox in detecciones:
            mejor_id: Optional[int] = None
            mejor_iou = self.iou_threshold
            for tid, tr in libres.items():
                if tid in usados:
                    continue
                valor = _iou(bbox, tr.bbox)
                if valor > mejor_iou:
                    mejor_iou = valor
                    mejor_id = tid
            if mejor_id is not None:
                usados.add(mejor_id)
                asignados.append((mejor_id, bbox))
            else:
                asignados.append((0, bbox))

        objetos: List[ObjetoRiesgo] = []
        for tid, bbox in asignados:
            x, y, w, h = (int(v) for v in bbox)
            cx = x + w / 2.0
            cy = y + h / 2.0
            area_rel = (w * h) / float(ancho_f * alto_f)

            if tid == 0:
                nuevo = _Track(
                    id=self._siguiente_id,
                    bbox=(x, y, w, h),
                    centro=(cx, cy),
                    area_rel=area_rel,
                    t=timestamp,
                )
                self._siguiente_id += 1
                self._tracks[nuevo.id] = nuevo
                track = nuevo
            else:
                track = libres.pop(tid)
                dt = timestamp - track.t
                if dt > 1e-3:
                    vx = (cx - track.centro[0]) / dt
                    vy = (cy - track.centro[1]) / dt
                    crec = ((area_rel - track.area_rel) / max(track.area_rel, 1e-6)) / dt
                    if track.vistos > 1:
                        # suavizado para no disparar por jitter de la máscara
                        vx = 0.5 * vx + 0.5 * track.vx
                        vy = 0.5 * vy + 0.5 * track.vy
                        crec = 0.5 * crec + 0.5 * track.crecimiento
                    track.vx, track.vy, track.crecimiento = vx, vy, crec
                track.bbox = (x, y, w, h)
                track.centro = (cx, cy)
                track.area_rel = area_rel
                track.t = timestamp
                track.vistos += 1
                self._tracks[track.id] = track

            norm_x = x / float(ancho_f)
            norm_y = y / float(alto_f)
            norm_w = w / float(ancho_f)
            norm_h = h / float(alto_f)
            centro_norm_x = norm_x + norm_w / 2.0
            centro_norm_y = norm_y + norm_h / 2.0

            if track.vy > self.config.vel_y_min_ttc:
                ttc = max(0.0, (alto_f - cy) / track.vy)
            else:
                ttc = float("inf")

            en_corredor = (
                centro_norm_y > self.config.corredor_y_min
                and self.config.corredor_x_min <= centro_norm_x <= self.config.corredor_x_max
            )

            objetos.append(
                ObjetoRiesgo(
                    id=track.id,
                    bbox=(x, y, w, h),
                    bbox_norm=(norm_x, norm_y, norm_w, norm_h),
                    area_rel=area_rel,
                    velocidad_px_s=math.hypot(track.vx, track.vy),
                    velocidad_y=track.vy,
                    crecimiento_area_s=track.crecimiento,
                    ttc=ttc,
                    en_corredor=en_corredor,
                    nivel_individual="GREEN",
                )
            )

        # 2) descartar pistas viejas
        caducadas = [tid for tid, tr in self._tracks.items() if timestamp - tr.t > self.max_age]
        for tid in caducadas:
            self._tracks.pop(tid, None)

        return objetos

    # compatibilidad con la firma del plan
    def _iou(self, a: BBox, b: BBox) -> float:
        return _iou(a, b)

    def reiniciar(self) -> None:
        self._tracks.clear()
        self._siguiente_id = 1


class ModeloRiesgo:
    """Modelo de riesgo clásico (sin ML): movimiento + cinemática + heurística."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=config.bg_history,
            varThreshold=config.bg_var_threshold,
            detectShadows=config.bg_detect_shadows,
        )
        self._kernel_cierre = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (config.morph_close_k, config.morph_close_k)
        )
        self._kernel_apertura = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (config.morph_open_k, config.morph_open_k)
        )
        self.tracker = Tracker(config)
        self._ajustes: Dict[str, Any] = {}
        self._nivel = "GREEN"
        self._t_en_nivel = 0.0  # segundos dentro del nivel actual
        self._ultimo_ts = 0.0
        self._frames = 0
        # MOG2 devuelve el frame entero como foreground en sus primeras
        # aplicaciones (modelo sin muestras). Con I-001 ese blob es amenaza
        # máxima, así que se descartan solo esos frames de calibración: nunca
        # se silencia riesgo real, solo el arranque del modelo.
        self._calentamiento = max(2, min(6, config.bg_history // 40))

    # ------------------------------------------------------------------
    def aplicar_ajustes(self, ajustes: Dict[str, Any]) -> None:
        """Ajustes en caliente desde ``POST /api/config``."""
        if ajustes:
            self._ajustes = dict(ajustes)

    def _p(self, clave: str) -> float:
        if clave in self._ajustes:
            try:
                return float(self._ajustes[clave])
            except (TypeError, ValueError):
                pass
        return float(getattr(self.config, clave))

    def reiniciar(self) -> None:
        self.tracker.reiniciar()
        self._nivel = "GREEN"
        self._t_en_nivel = 0.0
        self._frames = 0
        self._ultimo_ts = 0.0

    # -- 1. preproceso ---------------------------------------------------
    def _preprocesar(self, frame: np.ndarray) -> Tuple[np.ndarray, float]:
        alto0, ancho0 = frame.shape[:2]
        ancho = int(self.config.ancho_proceso)
        escala = ancho / float(max(1, ancho0))
        alto = max(2, int(round(alto0 * escala)))
        # INTER_AREA es 5x más lento en el Cortex-A53 de la placa (80 ms frente
        # a 14 ms); el desenfoque posterior compensa el aliasing de INTER_LINEAR.
        pequeno = cv2.resize(frame, (ancho, alto), interpolation=cv2.INTER_LINEAR)
        gris = cv2.cvtColor(pequeno, cv2.COLOR_BGR2GRAY)
        gris = cv2.GaussianBlur(gris, (5, 5), 0)
        return gris, escala

    # -- 2. detección ----------------------------------------------------
    def _detectar_movimiento(
        self, gris: np.ndarray, escala: float, forma_original: Tuple[int, ...]
    ) -> List[BBox]:
        mascara = self._bg.apply(gris)
        _, binaria = cv2.threshold(mascara, 200, 255, cv2.THRESH_BINARY)
        binaria = cv2.morphologyEx(binaria, cv2.MORPH_CLOSE, self._kernel_cierre)
        binaria = cv2.morphologyEx(binaria, cv2.MORPH_OPEN, self._kernel_apertura)
        contornos, _ = cv2.findContours(binaria, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return self._filtrar_detecciones(contornos, escala, forma_original)

    # -- 3. filtrado -----------------------------------------------------
    def _filtrar_detecciones(
        self, contornos: Sequence[Any], escala: float, forma_original: Tuple[int, ...]
    ) -> List[BBox]:
        alto0, ancho0 = forma_original[:2]
        area_frame = float(alto0 * ancho0)
        # I-010: un frame sin dimensiones no tiene detecciones posibles.
        if area_frame <= 0 or escala <= 0:
            return []
        aspecto_max = self.config.aspecto_max
        area_min_proceso = float(self.config.area_minima_px)
        detecciones: List[BBox] = []

        for contorno in contornos:
            area = cv2.contourArea(contorno)
            if area < area_min_proceso:
                continue
            x, y, w, h = cv2.boundingRect(contorno)
            if w < 4 or h < 4:
                continue
            # I-001: NO se descarta ningún objeto por tamaño. Un objeto que
            # llena el cuadro es exactamente el caso de colisión inminente:
            # se conserva y _score_individual lo puntúa como amenaza máxima.
            if aspecto_max > 0 and (w / float(h) > aspecto_max or h / float(w) > aspecto_max * 1.6):
                continue
            detecciones.append(
                (
                    int(round(x / escala)),
                    int(round(y / escala)),
                    int(round(w / escala)),
                    int(round(h / escala)),
                )
            )
        return detecciones

    # -- 5. score individual --------------------------------------------
    def _score_individual(self, obj: ObjetoRiesgo) -> float:
        # I-001: si el objeto cubre (casi) todo el cuadro es una colisión
        # inminente. Se puntúa como amenaza máxima —cercanía y looming al
        # tope, TTC mínimo— en lugar de descartarlo por "demasiado grande".
        if obj.area_rel >= self._p("area_maxima_rel"):
            return 1.0

        nx, ny, nw, nh = obj.bbox_norm
        centro_y = ny + nh / 2.0
        y_min = self.config.corredor_y_min
        rango = max(1e-6, 1.0 - y_min)
        w_cercania = _clamp((centro_y - y_min) / rango) ** 2

        vel_critica = max(1e-6, self._p("vel_y_critica"))
        w_vel = _sigmoide((obj.velocidad_y - vel_critica / 2.0) / max(1e-6, vel_critica / 4.0))

        # I-002: defensa en profundidad contra loom_critico <= 0 vía API.
        loom_critico = max(self._p("loom_critico"), 1e-6)
        w_loom = _clamp(max(0.0, obj.crecimiento_area_s) / loom_critico)

        a_peq = self._p("area_pequena")
        a_gran = max(a_peq + 1e-6, self._p("area_grande"))
        w_area = _sigmoide((obj.area_rel - (a_peq + a_gran) / 2.0) / max(1e-6, (a_gran - a_peq) / 4.0))

        ttc_critico = max(1e-3, self._p("ttc_critico"))
        if math.isinf(obj.ttc) or obj.ttc is None:
            w_ttc = 0.0
        else:
            w_ttc = math.exp(-max(0.0, obj.ttc) / ttc_critico)

        score = (
            w_cercania * self.config.peso_cercania
            + w_vel * self.config.peso_velocidad
            + w_loom * self.config.peso_looming
            + w_area * self.config.peso_area
            + w_ttc * self.config.peso_ttc
        )
        return _clamp(score)

    # -- 6. histéresis ---------------------------------------------------
    def _histeresis(self, score: float, nivel_actual: str, t_en_nivel_ms: float) -> str:
        """Nivel resultante para un score, con histéresis y tiempo mínimo en rojo."""
        rojo_in = self._p("umbral_rojo_in")
        rojo_out = self._p("umbral_rojo_out")
        ambar_in = self._p("umbral_ambar_in")
        ambar_out = self._p("umbral_ambar_out")
        min_rojo = self._p("min_tiempo_rojo_ms")

        if score >= rojo_in:
            return "RED"
        if nivel_actual == "RED" and (score > rojo_out or t_en_nivel_ms < min_rojo):
            return "RED"
        if score >= ambar_in:
            return "AMBER"
        if nivel_actual == "AMBER" and score > ambar_out:
            return "AMBER"
        return "GREEN"

    def _avanzar_nivel(self, score: float, dt: float) -> str:
        """Aplica la histéresis manteniendo el tiempo dentro del nivel."""
        dt = max(0.0, float(dt))
        self._t_en_nivel += dt
        nuevo = self._histeresis(score, self._nivel, self._t_en_nivel * 1000.0)
        if nuevo != self._nivel:
            self._nivel = nuevo
            self._t_en_nivel = 0.0
        return self._nivel

    # -- 6b. riesgo global ----------------------------------------------
    def _riesgo_global(
        self,
        objetos: List[ObjetoRiesgo],
        estado_previo: Optional[EstadoFrame],
        dt: float,
    ) -> EstadoFrame:
        score_crudo = max([o.score for o in objetos] + [0.0])
        # I-001: una amenaza máxima (objeto que llena el cuadro) no se suaviza.
        # La colisión inminente no admite el retardo del EMA: se publica RED ya.
        score = 1.0 if score_crudo >= 1.0 else self._suavizar(score_crudo, estado_previo, dt)

        nivel = self._avanzar_nivel(score, dt)

        ttcs = [o.ttc for o in objetos if o.ttc is not None and math.isfinite(o.ttc) and o.ttc >= 0]
        ttc = min(ttcs) if ttcs else float("inf")

        for obj in objetos:
            if obj.score >= self._p("umbral_rojo_in"):
                obj.nivel_individual = "RED"
            elif obj.score >= self._p("umbral_ambar_in"):
                obj.nivel_individual = "AMBER"
            else:
                obj.nivel_individual = "GREEN"

        mensaje = {
            "RED": "¡PELIGRO! Objeto acercándose de frente",
            "AMBER": "Atención: objeto en el corredor",
            "GREEN": "Paso libre",
        }.get(nivel, "")

        return EstadoFrame(
            nivel=nivel,
            score=score,
            ttc=ttc,
            objetos=objetos,
            mensaje=mensaje,
        )

    def _suavizar(self, score: float, estado_previo: Optional[EstadoFrame], dt: float) -> float:
        if estado_previo is None or estado_previo.score <= 0.0:
            return _clamp(score)
        dt = max(1e-3, min(0.5, float(dt)))
        alpha = 1.0 - math.exp(-dt / max(1e-3, self._p("tau_ema")))
        return _clamp(alpha * score + (1.0 - alpha) * estado_previo.score)

    # -- 7. pipeline completo -------------------------------------------
    def procesar(
        self,
        frame: np.ndarray,
        timestamp: float,
        estado_previo: Optional[EstadoFrame],
    ) -> EstadoFrame:
        # I-010: un frame vacío no debe tumbar el hilo de visión. Se publica
        # NO_SIGNAL (honesto: sin imagen no se puede evaluar el riesgo).
        if frame is None or getattr(frame, "size", 0) == 0:
            log.debug("Vision: frame vacío (size=0), se omite el análisis")
            return EstadoFrame(
                nivel="NO_SIGNAL",
                score=0.0,
                ttc=float("inf"),
                objetos=[],
                modo=estado_previo.modo if estado_previo else "camara",
                camara_url=estado_previo.camara_url if estado_previo else "",
                mensaje="Frame vacío",
                timestamp=timestamp,
            )

        alto0, ancho0 = frame.shape[:2]
        ahora = time.time()
        dt = timestamp - self._ultimo_ts if self._ultimo_ts > 0 else 1.0 / max(1.0, self.config.fps_target)
        dt = max(1e-3, min(0.5, dt))
        self._ultimo_ts = timestamp

        gris, escala = self._preprocesar(frame)

        if self._frames < self._calentamiento:
            # Solo se entrena el modelo; no se evalúa riesgo todavía.
            self._bg.apply(gris)
            self._frames += 1
            return EstadoFrame(
                nivel="NO_SIGNAL",
                score=0.0,
                ttc=float("inf"),
                objetos=[],
                modo=estado_previo.modo if estado_previo else "camara",
                camara_url=estado_previo.camara_url if estado_previo else "",
                mensaje="Calibrando fondo de escena",
                timestamp=timestamp,
            )

        detecciones = self._detectar_movimiento(gris, escala, frame.shape)
        objetos = self.tracker.update(detecciones, timestamp, ancho0, alto0)

        for obj in objetos:
            obj.score = self._score_individual(obj)

        resultado = self._riesgo_global(objetos, estado_previo, dt)
        resultado.frame_age_ms = max(0.0, (ahora - timestamp) * 1000.0)
        self._frames += 1
        return resultado


class VisionThread(threading.Thread):
    """Hilo de visión: frame crudo -> modelo -> estado + JPEG anotado."""

    def __init__(self, estado: Estado, config: Config, modelo: Optional[ModeloRiesgo] = None) -> None:
        super().__init__(name="vision", daemon=True)
        self.estado = estado
        self.config = config
        self.modelo = modelo or ModeloRiesgo(config)
        self._parar = threading.Event()
        self._ultima_version = -1
        self._fps = 0.0
        self._t_anterior = 0.0
        self.procesados = 0

    # ------------------------------------------------------------------
    def run(self) -> None:
        periodo = 1.0 / max(1.0, self.config.fps_target)
        log.info(
            "Vision: iniciando (proceso %d px de ancho, objetivo %.1f fps)",
            self.config.ancho_proceso,
            self.config.fps_target,
        )
        while not self._parar.is_set():
            modo = self.estado.get_modo()
            if modo == "detenido":
                self._publicar_detenido()
                self._parar.wait(0.2)
                continue

            self.modelo.aplicar_ajustes(self.estado.get_ajustes())

            datos = self.estado.get_frame_crudo_con_version()
            if datos is None:
                self._parar.wait(0.02)
                continue
            frame, ts_captura, version = datos
            if version == self._ultima_version:
                self._parar.wait(0.004)
                continue
            self._ultima_version = version

            inicio = time.perf_counter()
            try:
                previo = self.estado.get_estado()
                resultado = self.modelo.procesar(frame, ts_captura, previo)
                ahora = time.time()
                if self._t_anterior > 0:
                    delta = ahora - self._t_anterior
                    if delta > 1e-4:
                        instantaneo = 1.0 / delta
                        self._fps = instantaneo if self._fps <= 0 else 0.85 * self._fps + 0.15 * instantaneo
                self._t_anterior = ahora

                resultado.modo = modo
                resultado.camara_url = self.estado.get_camara_url()
                resultado.fps = self._fps
                resultado.latencia_ms = (time.perf_counter() - inicio) * 1000.0
                resultado.frame_age_ms = max(0.0, (ahora - ts_captura) * 1000.0)
                resultado.timestamp = ahora

                jpeg = overlay.renderizar_y_codificar(frame, resultado, self.config)
                self.estado.set_frame_anotado(jpeg)
                self.estado.set_estado(resultado)
                self.procesados += 1
            except Exception as exc:  # noqa: BLE001 - el hilo no debe morir
                log.exception("Vision: error procesando frame: %s", exc)

            transcurrido = time.perf_counter() - inicio
            sobrante = periodo - transcurrido
            if sobrante > 0:
                self._parar.wait(sobrante)

    def detener(self) -> None:
        self._parar.set()

    # ------------------------------------------------------------------
    def _publicar_detenido(self) -> None:
        previo = self.estado.get_estado()
        if previo.modo == "detenido" and previo.nivel == "NO_SIGNAL":
            return
        self.estado.set_estado(
            EstadoFrame(
                nivel="NO_SIGNAL",
                score=0.0,
                fps=0.0,
                modo="detenido",
                camara_url=self.estado.get_camara_url(),
                mensaje="Análisis detenido",
                timestamp=time.time(),
            )
        )
        self.estado.set_frame_anotado(
            overlay.jpeg_sin_senal(self.config, mensaje="ANALISIS DETENIDO")
        )
