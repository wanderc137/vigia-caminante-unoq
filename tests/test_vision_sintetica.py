"""Pruebas de integración de visión: demo sintética, escena vacía y HTTP."""

from __future__ import annotations

import json
import sys
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from vigia.config import load_config  # noqa: E402
from vigia.estado import Estado  # noqa: E402
from vigia.demo import DemoThread  # noqa: E402
from vigia.servidor import crear_servidor  # noqa: E402
from vigia.vision import ModeloRiesgo  # noqa: E402


class TestDemoSintetica(unittest.TestCase):
    def test_demo_genera_red(self) -> None:
        """El ciclo del demo debe pasar por GREEN, AMBER y RED en 10 s."""
        config = load_config()
        estado = Estado()
        demo = DemoThread(estado, config)
        modelo = ModeloRiesgo(config)

        dt = 1.0 / max(1.0, config.fps_target)
        niveles = []
        previo = None
        t0 = 1000.0
        for i in range(int(10.0 / dt)):
            t = t0 + i * dt
            frame = demo._generar_frame(t)
            previo = modelo.procesar(frame, t, previo)
            niveles.append(previo.nivel)

        self.assertIn("RED", niveles, f"no alcanzó RED: {sorted(set(niveles))}")
        self.assertIn("AMBER", niveles, f"no alcanzó AMBER: {sorted(set(niveles))}")
        self.assertIn("GREEN", niveles, f"no alcanzó GREEN: {sorted(set(niveles))}")
        # el ciclo debe volver a verde después del pico
        primer_rojo = niveles.index("RED")
        self.assertIn("GREEN", niveles[primer_rojo:], "no vuelve a GREEN tras el pico")
        self.assertGreater(niveles.count("GREEN"), 10)


class TestEscenaVacia(unittest.TestCase):
    def test_escena_vacia(self) -> None:
        """Frames estáticos negros => GREEN, score ~0 y sin objetos."""
        config = replace(load_config(), fps_target=15.0)
        modelo = ModeloRiesgo(config)
        frame = np.zeros((854, 480, 3), dtype=np.uint8)
        previo = None
        for i in range(20):
            previo = modelo.procesar(frame, 2000.0 + i / 15.0, previo)
        self.assertEqual(previo.nivel, "GREEN")
        self.assertLess(previo.score, 0.05)
        self.assertEqual(previo.objetos, [])


class TestServidorHTTP(unittest.TestCase):
    """Levanta el servidor real en un puerto efímero y consulta los endpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = replace(load_config(), puerto=0, host="127.0.0.1")
        cls.estado = Estado(camara_url=cls.config.cam_url, modo="camara")
        cls.servidor = crear_servidor(cls.estado, cls.config)
        cls.puerto = cls.servidor.server_address[1]
        cls.hilo = threading.Thread(
            target=cls.servidor.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        cls.hilo.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.servidor.shutdown()
        cls.servidor.server_close()

    def _get(self, ruta: str) -> bytes:
        with urlopen(f"http://127.0.0.1:{self.puerto}{ruta}", timeout=5) as r:
            return r.read()

    def test_health(self) -> None:
        datos = json.loads(self._get("/health"))
        self.assertEqual(datos["status"], "ok")

    def test_sin_senal(self) -> None:
        """Sin cámara ni demo: nivel NO_SIGNAL y snapshot gris no negro."""
        datos = json.loads(self._get("/api/estado"))
        self.assertEqual(datos["nivel"], "NO_SIGNAL")
        self.assertEqual(datos["objetos"], [])
        self.assertIn("score", datos)
        self.assertIn("fps", datos)
        self.assertIn("modo", datos)

        jpeg = self._get("/snapshot.jpg")
        self.assertTrue(jpeg.startswith(b"\xff\xd8"), "el snapshot no es un JPEG")
        imagen = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(imagen)
        self.assertGreater(float(imagen.mean()), 5.0, "el frame de respaldo está negro")
        # el borde gris del nivel NO_SIGNAL debe estar dibujado arriba
        borde = imagen[6, imagen.shape[1] // 2]
        self.assertGreater(int(borde.mean()), 40, f"borde no dibujado: {borde}")

    def test_ui_estatica(self) -> None:
        html = self._get("/").decode("utf-8")
        self.assertIn("Vigía Caminante", html)
        self.assertIn("/app.js", html)
        self.assertIn("data-modo=\"camara\"", html)
        self.assertIn(b"Vig", self._get("/app.js"))
        self.assertIn(b"--nivel", self._get("/style.css"))

    def test_config_cambia_modo(self) -> None:
        peticion = urlopen(
            f"http://127.0.0.1:{self.puerto}/api/config",
            data=json.dumps({"modo": "demo"}).encode("utf-8"),
            timeout=5,
        )
        datos = json.loads(peticion.read())
        self.assertTrue(datos["ok"])
        self.assertEqual(datos["modo"], "demo")
        self.assertEqual(self.estado.get_modo(), "demo")

        # una URL inválida debe rechazarse con 400
        from urllib.error import HTTPError

        with self.assertRaises(HTTPError) as ctx:
            urlopen(
                f"http://127.0.0.1:{self.puerto}/api/config",
                data=json.dumps({"camara_url": "no-es-una-url"}).encode("utf-8"),
                timeout=5,
            )
        self.assertEqual(ctx.exception.code, 400)
        self.estado.set_modo("camara")


if __name__ == "__main__":
    unittest.main()
