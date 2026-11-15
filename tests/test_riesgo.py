"""Pruebas unitarias del modelo de riesgo: score, histéresis y tracking."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from vigia.config import load_config  # noqa: E402
from vigia.estado import ObjetoRiesgo  # noqa: E402
from vigia.vision import ModeloRiesgo, Tracker  # noqa: E402

ANCHO = 1080
ALTO = 1920


def objeto(
    cy: float,
    vy: float,
    area_rel: float,
    ttc: float,
    crecimiento: float = 2.0,
    cx: float = 0.5,
    id_: int = 1,
) -> ObjetoRiesgo:
    """Construye un ObjetoRiesgo coherente con ``cy``/``area_rel`` dados."""
    w = int(round((area_rel ** 0.5) * ANCHO))
    h = int(round((area_rel * ANCHO * ALTO) / max(1, w)))
    x = int(round(cx * ANCHO - w / 2))
    y = int(round(cy * ALTO - h / 2))
    return ObjetoRiesgo(
        id=id_,
        bbox=(x, y, w, h),
        bbox_norm=(x / ANCHO, y / ALTO, w / ANCHO, h / ALTO),
        area_rel=area_rel,
        velocidad_px_s=abs(vy),
        velocidad_y=vy,
        crecimiento_area_s=crecimiento,
        ttc=ttc,
        en_corredor=True,
        nivel_individual="GREEN",
    )


class TestHisteresis(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.modelo = ModeloRiesgo(self.config)

    def test_histeresis_sube_y_baja(self) -> None:
        """0.1 -> 0.5 -> 0.8 -> 0.4 -> 0.2 debe dar GREEN/AMBER/RED/RED/GREEN."""
        niveles = [self.modelo._avanzar_nivel(score, 0.5) for score in (0.1, 0.5, 0.8, 0.4, 0.2)]
        self.assertEqual(niveles, ["GREEN", "AMBER", "RED", "RED", "GREEN"])

    def test_min_tiempo_rojo(self) -> None:
        """Un pico de score mantiene RED al menos MIN_TIEMPO_ROJO_MS."""
        self.assertEqual(self.modelo._avanzar_nivel(0.8, 0.1), "RED")
        niveles = [self.modelo._avanzar_nivel(0.2, 0.1) for _ in range(10)]
        minimo = self.config.min_tiempo_rojo_ms / 1000.0
        rojos = sum(1 for n in niveles if n == "RED")
        self.assertGreaterEqual(rojos * 0.1, minimo - 0.2, f"RED duró {rojos * 0.1:.1f}s")
        self.assertEqual(niveles[-1], "GREEN")

    def test_histeresis_no_parpadea_en_ambar(self) -> None:
        self.modelo._avanzar_nivel(0.5, 0.1)
        # 0.30 está sobre UMBRAL_AMBAR_OUT (0.25) pero bajo UMBRAL_AMBAR_IN (0.40)
        self.assertEqual(self.modelo._avanzar_nivel(0.30, 0.1), "AMBER")


class TestScore(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.modelo = ModeloRiesgo(self.config)

    def test_score_individual_objeto_cercano(self) -> None:
        cerca = objeto(cy=0.8, vy=200, area_rel=0.15, ttc=1.0)
        score = self.modelo._score_individual(cerca)
        self.assertGreater(
            score, self.config.umbral_rojo_in, f"score cercano={score:.3f} debería ser rojo"
        )
        self.assertLessEqual(score, 1.0)

    def test_score_individual_objeto_lejano(self) -> None:
        lejos = objeto(cy=0.2, vy=10, area_rel=0.01, ttc=10.0, crecimiento=0.0)
        score = self.modelo._score_individual(lejos)
        self.assertLess(
            score, self.config.umbral_ambar_in, f"score lejano={score:.3f} debería ser verde"
        )
        self.assertGreaterEqual(score, 0.0)

    def test_score_crece_con_el_acercamiento(self) -> None:
        muestras = [
            self.modelo._score_individual(objeto(cy, 60 + 40 * i, 0.01 + 0.02 * i, 4.0 - i * 0.5))
            for i, cy in enumerate((0.42, 0.52, 0.62, 0.72, 0.82))
        ]
        for anterior, siguiente in zip(muestras, muestras[1:]):
            self.assertLess(anterior, siguiente, f"score no monótono: {muestras}")


class TestTracking(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_tracking_asigna_id_persistente(self) -> None:
        tracker = Tracker(self.config)
        primero = tracker.update([(400, 800, 200, 300)], 100.0, ANCHO, ALTO)
        self.assertEqual(len(primero), 1)
        ident = primero[0].id

        segundo = tracker.update([(400, 900, 200, 300)], 100.1, ANCHO, ALTO)
        self.assertEqual(len(segundo), 1)
        self.assertEqual(segundo[0].id, ident, "el ID debe persistir entre frames")
        self.assertGreater(segundo[0].velocidad_y, 0.0, "debe detectar acercamiento")

    def test_tracking_separa_objetos_distintos(self) -> None:
        tracker = Tracker(self.config)
        a = tracker.update([(100, 100, 100, 100)], 10.0, ANCHO, ALTO)
        b = tracker.update([(800, 900, 120, 120)], 10.1, ANCHO, ALTO)
        ids = {a[0].id, b[0].id}
        self.assertEqual(len(ids), 2)

    def test_tracking_caduca_pistas_viejas(self) -> None:
        tracker = Tracker(self.config)
        tracker.update([(400, 800, 200, 300)], 1.0, ANCHO, ALTO)
        tracker.update([], 1.0 + tracker.max_age + 0.5, ANCHO, ALTO)
        self.assertEqual(len(tracker._tracks), 0)


if __name__ == "__main__":
    unittest.main()
