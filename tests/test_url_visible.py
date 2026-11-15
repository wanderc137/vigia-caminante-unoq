"""Regresión: la URL de cámara aplicada debe verse en /api/estado al instante.

Bug reportado en uso real (feria): se cambiaba la IP de la cámara desde el
panel, el POST devolvía ``ok`` y la cámara se reconectaba a la URL nueva, pero
la página volvía a mostrar la URL vieja. Causa: ``set_camara_url`` actualizaba
solo la configuración interna y no el ``EstadoFrame`` que sirve
``/api/estado``; con la cámara caída (NO_SIGNAL) el watchdog republica estados
derivados del anterior y el snapshot quedaba congelado en la URL inicial.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vigia.estado import Estado  # noqa: E402


class TestUrlVisibleEnEstado(unittest.TestCase):
    def test_set_camara_url_actualiza_el_snapshot(self):
        estado = Estado(camara_url="http://192.168.1.9:8080/video", modo="camara")
        estado.set_camara_url("http://192.168.1.7:8090/video")

        self.assertEqual(estado.get_camara_url(), "http://192.168.1.7:8090/video")
        self.assertEqual(estado.get_estado().camara_url, "http://192.168.1.7:8090/video")

    def test_actualizar_estado_no_pisa_la_url_vigente(self):
        """El watchdog republica NO_SIGNAL: no debe revivir la URL vieja."""
        estado = Estado(camara_url="http://192.168.1.9:8080/video", modo="camara")
        estado.set_camara_url("http://192.168.1.7:8090/video")

        # Así construye vision.py los estados sin señal: desde el snapshot.
        estado.actualizar_estado(
            nivel="NO_SIGNAL",
            mensaje="SIN SEÑAL: sin frames de la cámara",
            camara_url=estado.get_estado().camara_url,
        )

        self.assertEqual(estado.get_estado().camara_url, "http://192.168.1.7:8090/video")

    def test_set_modo_actualiza_el_snapshot(self):
        estado = Estado(camara_url="http://192.168.1.9:8080/video", modo="camara")
        estado.set_modo("demo")
        self.assertEqual(estado.get_estado().modo, "demo")

    def test_url_vacia_se_normaliza(self):
        estado = Estado(camara_url="http://192.168.1.9:8080/video", modo="camara")
        estado.set_camara_url("   ")
        self.assertEqual(estado.get_camara_url(), "")
        self.assertEqual(estado.get_estado().camara_url, "")


if __name__ == "__main__":
    unittest.main()
