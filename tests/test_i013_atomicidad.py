"""I-013: la actualización de ajustes en caliente es un compare-and-set atómico.

Antes del fix, ``servidor._aplicar_config`` hacía ``get_ajustes()`` ->
``_validar_ajustes()`` -> ``set_ajustes()`` sin mantener el candado entre la
lectura y la escritura. Dos ``POST /api/config`` concurrentes podían leer la
misma base, pasar ambos la validación y escribir una combinación incoherente
(``rojo_out >= rojo_in``), anulando la garantía de I-005.

Los tests usan ``threading.Barrier`` (sin ``sleep`` arbitrarios) para que los
hilos salgan a la vez hacia el servicio HTTP real en proceso:

* lotes complementarios ``{umbral_rojo_in: 0.60}`` y
  ``{umbral_rojo_out: 0.65}``: ambos son válidos contra la base
  (``0.55 < 0.60 < 0.70`` y ``0.60 > 0.65`` es falso, pero contra la base
  ``0.70 > 0.65`` sí), pero **incompatibles entre sí**. Sólo el
  compare-and-set garantiza que no se apliquen los dos.
* tras cada ronda se verifica que los umbrales efectivos cumplen
  ``ambar_out < ambar_in < rojo_out < rojo_in < 1``.
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from vigia.config import load_config  # noqa: E402
from vigia.estado import Estado  # noqa: E402
from vigia.servidor import crear_servidor  # noqa: E402

N_HILOS = 16
RONDAS = 10

# Par mutuamente excluyente: A exige rojo_out < 0.60, B exige rojo_in > 0.65.
LOTE_ROJO_IN = {"umbral_rojo_in": 0.60}
LOTE_ROJO_OUT = {"umbral_rojo_out": 0.65}

CLAVES_UMBRAL = (
    "umbral_ambar_out",
    "umbral_ambar_in",
    "umbral_rojo_out",
    "umbral_rojo_in",
)


def umbrales_efectivos(config, estado) -> dict:
    """Umbrales que ve la visión: defaults de ``Config`` + ajustes en caliente."""
    valores = {clave: float(getattr(config, clave)) for clave in CLAVES_UMBRAL}
    for clave, valor in estado.get_ajustes().items():
        if clave in valores:
            valores[clave] = float(valor)
    return valores


class TestI013AtomicidadHTTP(unittest.TestCase):
    """Concurrencia real contra ``POST /api/config`` (servicio en proceso)."""

    def setUp(self) -> None:
        self.config = replace(load_config(), puerto=0, host="127.0.0.1")
        self.estado = Estado(camara_url=self.config.cam_url, modo="camara")
        self.servidor = crear_servidor(self.estado, self.config)
        self.puerto = self.servidor.server_address[1]
        self.hilo = threading.Thread(
            target=self.servidor.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.hilo.start()

    def tearDown(self) -> None:
        self.servidor.shutdown()
        self.servidor.server_close()
        self.hilo.join(timeout=5)

    # ------------------------------------------------------------------
    def _post(self, datos) -> tuple:
        cuerpo = json.dumps(datos).encode("utf-8")
        try:
            with urlopen(
                f"http://127.0.0.1:{self.puerto}/api/config", data=cuerpo, timeout=10
            ) as respuesta:
                return respuesta.status, json.loads(respuesta.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def _reiniciar_ajustes(self) -> None:
        """Vuelve a la base de ``config`` (equivale a no tener ajustes)."""
        self.estado.set_ajustes(
            {
                "umbral_rojo_in": self.config.umbral_rojo_in,
                "umbral_rojo_out": self.config.umbral_rojo_out,
            }
        )

    def _assert_invariantes(self, contexto: str) -> None:
        u = umbrales_efectivos(self.config, self.estado)
        self.assertTrue(
            0.0 < u["umbral_ambar_out"] < u["umbral_ambar_in"]
            < u["umbral_rojo_out"] < u["umbral_rojo_in"] < 1.0,
            f"umbrales incoherentes tras {contexto}: {u}",
        )
        # La API expone exactamente los ajustes que ve la visión.
        with urlopen(f"http://127.0.0.1:{self.puerto}/api/estado", timeout=10) as r:
            datos = json.loads(r.read())
        self.assertEqual(datos["ajustes"], self.estado.get_ajustes(), contexto)

    # ------------------------------------------------------------------
    def test_post_concurrentes_cruzados_no_dejan_umbrales_incoherentes(self) -> None:
        """N hilos con lotes cruzados: nunca queda ``rojo_out >= rojo_in``."""
        self._reiniciar_ajustes()

        for ronda in range(RONDAS):
            barrera = threading.Barrier(N_HILOS)
            codigos: list = [None] * N_HILOS
            errores: list = []

            def trabajador(indice: int) -> None:
                lote = LOTE_ROJO_IN if indice % 2 == 0 else LOTE_ROJO_OUT
                try:
                    barrera.wait(timeout=10)  # salida simultánea: máxima ventana
                    codigos[indice] = self._post(lote)[0]
                except Exception as exc:  # noqa: BLE001 - se reporta en el hilo principal
                    errores.append(exc)

            hilos = [
                threading.Thread(target=trabajador, args=(i,), daemon=True)
                for i in range(N_HILOS)
            ]
            for hilo in hilos:
                hilo.start()
            for hilo in hilos:
                hilo.join(timeout=30)

            self.assertFalse(errores, f"ronda {ronda}: errores en hilos: {errores}")
            self.assertNotIn(None, codigos, f"ronda {ronda}: hilo sin respuesta")
            self.assertTrue(
                set(codigos) <= {200, 400},
                f"ronda {ronda}: códigos inesperados {codigos}",
            )
            # Al menos un lote se aplica; el resto puede rechazarse con 400.
            self.assertIn(200, codigos, f"ronda {ronda}: ningún POST fue aceptado")
            self._assert_invariantes(f"ronda {ronda} ({codigos})")

            self._reiniciar_ajustes()

    def test_cas_rechaza_lote_validado_contra_una_base_ya_cambiada(self) -> None:
        """Secuencial: el segundo lote se valida contra el primero, no contra la base."""
        self._reiniciar_ajustes()

        codigo, cuerpo = self._post(LOTE_ROJO_IN)
        self.assertEqual(codigo, 200, cuerpo)
        self.assertEqual(self.estado.get_ajustes()["umbral_rojo_in"], 0.60)

        # 0.65 quedaría por encima de rojo_in=0.60: debe rechazarse y no mutar.
        antes = self.estado.get_ajustes()
        codigo, cuerpo = self._post(LOTE_ROJO_OUT)
        self.assertEqual(codigo, 400, cuerpo)
        self.assertIn("error", cuerpo)
        self.assertIn("umbral_rojo_out", cuerpo.get("campo", []))
        self.assertEqual(self.estado.get_ajustes(), antes)

        # Y el lote complementario en sentido válido sí se acepta.
        codigo, cuerpo = self._post({"umbral_rojo_out": 0.50})
        self.assertEqual(codigo, 200, cuerpo)
        self._assert_invariantes("secuencial")

    def test_aplicar_config_escribe_por_compare_and_set(self) -> None:
        """Guardia determinista de I-013: la ruta HTTP nunca usa ``set_ajustes``.

        Con el código pre-fix, ``_aplicar_config`` escribía con
        ``estado.set_ajustes(...)`` tras validar fuera del candado; aquí eso
        dispara el ``AssertionError`` y la respuesta sería 500. Con el fix la
        escritura pasa únicamente por ``actualizar_ajustes_si``.
        """
        original = Estado.actualizar_ajustes_si
        llamadas: list = []

        def espia(instancia, nuevos, validador):
            llamadas.append(dict(nuevos))
            return original(instancia, nuevos, validador)

        with mock.patch.object(
            Estado,
            "set_ajustes",
            side_effect=AssertionError("set_ajustes no debe usarse (I-013)"),
        ), mock.patch.object(Estado, "actualizar_ajustes_si", espia):
            codigo, cuerpo = self._post({"tau_ema": 0.2})

        self.assertEqual(codigo, 200, cuerpo)
        self.assertEqual(llamadas, [{"tau_ema": 0.2}])
        self.assertEqual(self.estado.get_ajustes().get("tau_ema"), 0.2)


class TestI013EstadoCompareAndSet(unittest.TestCase):
    """Propiedades unitarias de ``Estado.actualizar_ajustes_si``."""

    def setUp(self) -> None:
        self.estado = Estado()
        self.estado.set_ajustes({"tau_ema": 0.05})

    def test_validador_recibe_el_diccionario_fusionado(self) -> None:
        visto = {}

        def validador(fusionado):
            visto.update(fusionado)
            return None

        error = self.estado.actualizar_ajustes_si({"watchdog_ms": 300.0}, validador)
        self.assertIsNone(error)
        # Fusionado = vigentes + lote nuevo (recibe ambos, sin tocar el estado).
        self.assertEqual(visto, {"tau_ema": 0.05, "watchdog_ms": 300.0})
        self.assertEqual(self.estado.get_ajustes(), {"tau_ema": 0.05, "watchdog_ms": 300.0})

    def test_rechazo_no_muta_los_ajustes(self) -> None:
        error = self.estado.actualizar_ajustes_si(
            {"loom_critico": 0.0}, lambda fusionado: "loom_critico inválido"
        )
        self.assertEqual(error, "loom_critico inválido")
        self.assertEqual(self.estado.get_ajustes(), {"tau_ema": 0.05})

    def test_validador_se_ejecuta_dentro_del_candado_de_config(self) -> None:
        """Prueba directa de exclusión mutua: el lock ya está tomado."""
        libre = {}

        def validador(fusionado):
            libre["lock"] = self.estado._lock_config.acquire(blocking=False)  # noqa: SLF001
            if libre["lock"]:
                self.estado._lock_config.release()  # noqa: SLF001
            return None

        self.estado.actualizar_ajustes_si({"tau_ema": 0.06}, validador)
        self.assertFalse(
            libre["lock"], "el validador debe correr bajo _lock_config (I-013)"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
