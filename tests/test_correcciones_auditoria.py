"""Pruebas de las correcciones de auditoría (ciclo 1, I-001 a I-012).

Cubre los casos exigidos por ``PLAN.md``:
  * ``POST /api/config`` con ``loom_critico=0`` -> 400 y el servicio sigue
    publicando estado.
  * ``POST /api/config`` con ``watchdog_ms=0`` -> 400.
  * ``POST /api/config`` con umbrales incoherentes -> 400 (lote completo).
  * Objeto sintético que cubre >95 % del cuadro -> nivel RED.
  * URL de loopback rechazada; ``http://192.168.1.9:8080/video`` aceptada.
  * Frame vacío -> no lanza excepción.
  * Extras de bajo coste: fallback JPEG, copia del frame de salida y parser
    de ``config.env`` con ``#`` dentro de una URL.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from vigia import overlay  # noqa: E402
from vigia.camara import es_url_valida  # noqa: E402
from vigia.config import load_config  # noqa: E402
from vigia.estado import Estado, EstadoFrame, ObjetoRiesgo  # noqa: E402
from vigia.servidor import crear_servidor  # noqa: E402
from vigia.vision import ModeloRiesgo  # noqa: E402

ANCHO = 1080
ALTO = 1920


def objeto(area_rel: float, cy: float = 0.6, vy: float = 220.0, ttc: float = 0.8) -> ObjetoRiesgo:
    """Construye un ``ObjetoRiesgo`` coherente para probar el score."""
    w = int(round((area_rel**0.5) * ANCHO))
    h = int(round((area_rel * ANCHO * ALTO) / max(1, w)))
    x = int(round(0.5 * ANCHO - w / 2))
    y = int(round(cy * ALTO - h / 2))
    return ObjetoRiesgo(
        id=1,
        bbox=(x, y, w, h),
        bbox_norm=(x / ANCHO, y / ALTO, w / ANCHO, h / ALTO),
        area_rel=area_rel,
        velocidad_px_s=abs(vy),
        velocidad_y=vy,
        crecimiento_area_s=2.0,
        ttc=ttc,
        en_corredor=True,
        nivel_individual="GREEN",
    )


class TestI001ColisionInminente(unittest.TestCase):
    """I-001: un objeto que llena el cuadro NUNCA se descarta."""

    def setUp(self) -> None:
        self.config = load_config()
        self.modelo = ModeloRiesgo(self.config)

    def test_objeto_que_llena_el_cuadro_es_amenaza_maxima(self) -> None:
        grande = objeto(area_rel=0.98)
        grande.score = self.modelo._score_individual(grande)
        self.assertEqual(grande.score, 1.0)

        resultado = self.modelo._riesgo_global([grande], None, 0.1)
        self.assertEqual(resultado.nivel, "RED")
        self.assertGreaterEqual(resultado.score, self.config.umbral_rojo_in)

    def test_filtro_no_descarta_contorno_gigante(self) -> None:
        alto, ancho = 480, 854
        mascara = np.zeros((alto, ancho), dtype=np.uint8)
        # rectángulo que cubre ~98 % del cuadro (bordes de 4 px)
        cv2.rectangle(mascara, (4, 4), (ancho - 5, alto - 5), 255, -1)
        contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detecciones = self.modelo._filtrar_detecciones(contornos, 1.0, (alto, ancho, 3))
        self.assertEqual(len(detecciones), 1, "el objeto gigante fue descartado (I-001)")

        objetos = self.modelo.tracker.update(detecciones, 100.0, ancho, alto)
        self.assertEqual(len(objetos), 1)
        self.assertGreater(objetos[0].area_rel, 0.9)

    def test_pipeline_completo_frame_lleno_va_a_red(self) -> None:
        """Un frame que cambia de oscuro a casi blanco debe dar RED."""
        alto, ancho = 480, 854
        oscuro = np.full((alto, ancho, 3), 20, dtype=np.uint8)
        previo = None
        for i in range(15):
            previo = self.modelo.procesar(oscuro, 3000.0 + i / 15.0, previo)
        self.assertNotEqual(previo.nivel, "RED", "la escena estática no debe dar RED")

        lleno = np.full((alto, ancho, 3), 245, dtype=np.uint8)
        previo = self.modelo.procesar(lleno, 3000.0 + 15 / 15.0, previo)
        self.assertEqual(previo.nivel, "RED", f"nivel={previo.nivel} score={previo.score}")

    def test_arranque_no_dispara_falsa_alarma(self) -> None:
        """El warm-up de MOG2 no debe publicar RED con una escena estática."""
        alto, ancho = 480, 854
        escena = np.full((alto, ancho, 3), 70, dtype=np.uint8)
        escena[:120, :] = (30, 40, 50)
        previo = None
        for i in range(40):
            previo = self.modelo.procesar(escena.copy(), 4000.0 + i / 15.0, previo)
            self.assertNotEqual(previo.nivel, "RED", f"falsa alarma en el frame {i}")
        self.assertEqual(previo.objetos, [])


class TestI010FrameVacio(unittest.TestCase):
    """I-010: un frame sin dimensiones no debe lanzar excepción."""

    def test_procesar_frame_vacio(self) -> None:
        modelo = ModeloRiesgo(load_config())
        vacio = np.zeros((0, 0, 3), dtype=np.uint8)
        resultado = modelo.procesar(vacio, 1.0, None)
        self.assertEqual(resultado.objetos, [])
        self.assertEqual(resultado.nivel, "NO_SIGNAL")

    def test_filtrar_forma_vacia(self) -> None:
        modelo = ModeloRiesgo(load_config())
        self.assertEqual(modelo._filtrar_detecciones([], 1.0, (0, 0, 3)), [])


class TestI004Url(unittest.TestCase):
    """I-004: SSRF bloqueado sin bloquear la LAN legítima."""

    def test_loopback_y_propia_ip_rechazadas(self) -> None:
        prohibidas = [
            "http://127.0.0.1:8000/",
            "http://127.0.0.1:8030/api/estado",
            "http://127.8.9.10/video",
            "http://localhost:8030/",
            "http://LOCALHOST/video",
            "http://[::1]:8030/",
            "http://0.0.0.0:8030/",
            "http://[::]/",
            "http://169.254.169.254/latest/meta-data/",
            "http://192.168.1.5:8030/",
            "http://192.168.1.5/",
            "file:///etc/passwd",
            "ftp://192.168.1.9/video",
        ]
        for url in prohibidas:
            with self.subTest(url=url):
                self.assertFalse(es_url_valida(url), f"debería rechazar {url}")

    def test_camara_lan_aceptada(self) -> None:
        for url in (
            "http://192.168.1.9:8080/video",
            "http://192.168.1.9:8080",
            "http://192.168.100.23:8080/video",
            "https://camara.local:8080/video",
        ):
            with self.subTest(url=url):
                self.assertTrue(es_url_valida(url), f"debería aceptar {url}")


class TestValidacionConfigHTTP(unittest.TestCase):
    """I-002/I-003/I-005: validación del lado servidor, lote atómico."""

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

    def setUp(self) -> None:
        # Los tests inválidos no aplican nada; se parte del estado por defecto.
        self.assertNotIn("loom_critico", self.estado.get_ajustes())


    def _post(self, datos) -> tuple:
        cuerpo = json.dumps(datos).encode("utf-8")
        try:
            respuesta = urlopen(
                f"http://127.0.0.1:{self.puerto}/api/config", data=cuerpo, timeout=5
            )
            return respuesta.status, json.loads(respuesta.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _estado(self) -> dict:
        with urlopen(f"http://127.0.0.1:{self.puerto}/api/estado", timeout=5) as r:
            return json.loads(r.read())

    def _assert_rechazo_sin_romper(self, datos, campo: str) -> None:
        codigo, cuerpo = self._post(datos)
        self.assertEqual(codigo, 400, f"se esperaba 400 para {datos}: {cuerpo}")
        self.assertIn("error", cuerpo)
        self.assertIn(campo, cuerpo.get("campo", [campo]), f"campo inesperado: {cuerpo}")
        # El lote completo se rechaza: nada quedó aplicado.
        for clave in datos:
            if clave in ("camara_url", "modo"):
                continue
            self.assertNotIn(clave, self.estado.get_ajustes())
        # El servicio sigue publicando estado normalmente.
        estado = self._estado()
        self.assertIn("nivel", estado)
        self.assertIn("fps", estado)

    def test_loom_critico_cero(self) -> None:
        self._assert_rechazo_sin_romper({"loom_critico": 0}, "loom_critico")
        self._assert_rechazo_sin_romper({"loom_critico": -3.0}, "loom_critico")
        self._assert_rechazo_sin_romper({"loom_critico": 0.0001}, "loom_critico")

    def test_watchdog_cero(self) -> None:
        self._assert_rechazo_sin_romper({"watchdog_ms": 0}, "watchdog_ms")
        self._assert_rechazo_sin_romper({"watchdog_ms": -100}, "watchdog_ms")
        self._assert_rechazo_sin_romper({"watchdog_ms": 199.9}, "watchdog_ms")

    def test_umbrales_incoherentes(self) -> None:
        self._assert_rechazo_sin_romper(
            {"umbral_rojo_in": 0.3, "umbral_rojo_out": 0.9}, "umbral_rojo_in"
        )
        self._assert_rechazo_sin_romper({"umbral_ambar_out": 0.8}, "umbral_ambar_out")
        self._assert_rechazo_sin_romper({"umbral_rojo_in": 1.0}, "umbral_rojo_in")

    def test_otros_rangos(self) -> None:
        self._assert_rechazo_sin_romper({"tau_ema": 0.0}, "tau_ema")
        self._assert_rechazo_sin_romper({"ttc_critico": 0.0}, "ttc_critico")
        self._assert_rechazo_sin_romper({"vel_y_critica": 0.5}, "vel_y_critica")
        self._assert_rechazo_sin_romper({"area_minima_px": 0}, "area_minima_px")
        self._assert_rechazo_sin_romper({"area_maxima_rel": 2.0}, "area_maxima_rel")
        # NaN/Infinity son aceptados por json.loads: deben rechazarse igual.
        codigo, _ = self._post({"loom_critico": float("nan")})
        self.assertEqual(codigo, 400)

    def test_url_loopback_por_api(self) -> None:
        codigo, cuerpo = self._post({"camara_url": "http://127.0.0.1:8000/"})
        self.assertEqual(codigo, 400, cuerpo)
        self.assertNotEqual(self.estado.get_camara_url(), "http://127.0.0.1:8000/")

    def test_url_lan_aceptada_por_api(self) -> None:
        previa = self.estado.get_camara_url()
        try:
            codigo, cuerpo = self._post({"camara_url": "http://192.168.1.9:8080/video"})
            self.assertEqual(codigo, 200, cuerpo)
            self.assertEqual(self.estado.get_camara_url(), "http://192.168.1.9:8080/video")
        finally:
            self.estado.set_camara_url(previa)


class TestI006FallbackJpeg(unittest.TestCase):
    """I-006: nunca se devuelve un JPEG de 0 bytes."""

    def test_fallback_al_ultimo_valido(self) -> None:
        config = replace(load_config(), ancho_salida=320)
        estado = EstadoFrame(nivel="GREEN", modo="camara", score=0.1)
        frame = np.full((480, 854, 3), 90, dtype=np.uint8)

        bueno = overlay.renderizar_y_codificar(frame, estado, config)
        self.assertTrue(bueno.startswith(b"\xff\xd8"))
        self.assertGreater(len(bueno), 100)

        with mock.patch.object(overlay.cv2, "imencode", return_value=(False, None)):
            respaldo = overlay.renderizar_y_codificar(frame, estado, config)
        self.assertTrue(respaldo.startswith(b"\xff\xd8"), "el respaldo no es un JPEG")
        self.assertGreater(len(respaldo), 100)
        self.assertEqual(respaldo, bueno, "debería reutilizar el último JPEG válido")

        # jpeg_sin_senal tampoco puede quedar vacío
        with mock.patch.object(overlay.cv2, "imencode", return_value=(False, None)):
            sintetico = overlay.jpeg_sin_senal(config)
        self.assertTrue(sintetico.startswith(b"\xff\xd8"))
        self.assertGreater(len(sintetico), 50)


class TestI008CopiaFrame(unittest.TestCase):
    """I-008: con ANCHO_SALIDA=0 nunca se muta el frame del capturador."""

    def test_reducir_salida_devuelve_copia(self) -> None:
        config = replace(load_config(), ancho_salida=0)
        frame = np.full((240, 320, 3), 40, dtype=np.uint8)
        original = frame.copy()
        estado = EstadoFrame(nivel="RED", modo="camara", score=0.9)
        salida, escala = overlay._reducir_salida(frame, config.ancho_salida)
        self.assertEqual(escala, 1.0)
        self.assertIsNot(salida, frame)
        overlay.renderizar(salida, estado, config)
        np.testing.assert_array_equal(frame, original, "el frame original fue mutado")


class TestI009ParserEnv(unittest.TestCase):
    """I-009: '#' solo comenta al inicio o tras un espacio."""

    def test_url_con_fragmento_no_se_trunca(self) -> None:
        contenido = "\n".join(
            [
                "# comentario de cabecera",
                "PS_PORT=8030",
                "LOG_LEVEL=INFO # comentario al final",
                "CAM_URL=http://192.168.1.9:8080/video#alt",
                'CAM_MJPEG_PATH="/video#x"',
            ]
        )
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write(contenido)
            ruta = fh.name
        try:
            cfg = load_config(ruta)
        finally:
            Path(ruta).unlink(missing_ok=True)
        self.assertEqual(cfg.puerto, 8030)
        self.assertEqual(cfg.log_level, "INFO")
        self.assertEqual(cfg.cam_url, "http://192.168.1.9:8080/video#alt")
        self.assertEqual(cfg.cam_mjpeg_path, "/video#x")


if __name__ == "__main__":
    unittest.main()
