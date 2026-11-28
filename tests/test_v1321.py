"""v1.3.21 — DETENER en vivo responde al instante (cierre en 2º plano).

El 2026-11-28 Jose reportó: «cuando le doy a detener Grabación dura
demasiado… y se pone todo pesado». Causa: la pasada final del radar
(esperar a su hilo hasta 35 s + puntuar los últimos segmentos +
generar el último clip) corría DENTRO del comando detener_grabacion,
bloqueando al trabajador (y con él los latidos al panel) durante
minutos. Desde esta versión el comando responde en cuanto el
Recorder para, y el cierre del radar + el descarte de segmentos
corren en un hilo aparte, en ese orden (la pasada final necesita los
segmentos en disco).
"""
import json
import threading
import time
from pathlib import Path

import worker as worker_mod

ROOT = Path(__file__).resolve().parents[1]


class _RecFalso:
    en_vivo = True

    def __init__(self, orden):
        self.orden = orden

    def stop(self, crear_pelicula=True):
        self.orden.append("recorder_stop")
        return None

    def bytes_en_disco(self):
        return 1234

    def descartar_segmentos(self):
        self.orden.append("descartar")


class _RadarLento:
    def __init__(self, orden, espera=2.5):
        self.orden = orden
        self.espera = espera

    def detener(self):
        time.sleep(self.espera)
        self.orden.append("radar_detener")


def test_detener_en_vivo_responde_sin_esperar_al_radar():
    orden = []
    worker_mod.grabaciones[991] = _RecFalso(orden)
    worker_mod.radares[991] = _RadarLento(orden)
    try:
        t0 = time.time()
        res = worker_mod.procesar(
            {"type": "detener_grabacion", "payload": {"session_id": 991}}, {})
        tarda = time.time() - t0
        assert res["estado"] == "detenida"
        assert res["grabacion_completa"] is None
        assert tarda < 1.5, f"el comando tardó {tarda:.1f} s: sigue bloqueando"
        # Al responder, el radar aún está cerrando en segundo plano y
        # los segmentos NO se han descartado (su pasada los necesita).
        assert "descartar" not in orden
        # El cierre aterriza solo, en orden: primero el radar, luego
        # el descarte.
        for _ in range(60):
            if "descartar" in orden:
                break
            time.sleep(0.1)
        assert orden == ["recorder_stop", "radar_detener", "descartar"], orden
    finally:
        worker_mod.grabaciones.pop(991, None)
        worker_mod.radares.pop(991, None)


def test_version_1_3_21_y_manifiesto():
    assert worker_mod.VERSION == "1.3.21"
    manifiesto = json.loads((ROOT / "worker-windows" / "manifest.json")
                            .read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.21"
    assert "worker.py" in manifiesto["files"]
