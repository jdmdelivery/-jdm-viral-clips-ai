"""Progreso REAL de la captura (caso trabajo #71, 2026-11-20).

Lo que mostraba el panel a la vez: «RADAR ANALIZANDO — 00:02:55 ·
316.8 MB», «Motor conectado v1.3.2», 0 momentos y «Iniciando…
esperando confirmación del trabajador (trabajo #71)». Y el dato clave:
en una captura anterior la píldora ya decía 316.8 MB con 00:00:39 —
el tamaño NO creció mientras el reloj avanzó.

Tres causas en código, fijadas por estas pruebas:

1. recorder.bytes_en_disco() devolvía PRIMERO la grabacion_completa.mp4
   de la carpeta. La carpeta de la sesión (sesion-<id>) se reutiliza
   entre grabaciones: una película de una captura ANTERIOR tapaba la
   suma de segmentos de la captura en curso, y cada reporte de progreso
   del trabajador repetía el tamaño de la película vieja. La API solo
   acepta subidas (contador monótono), así que el panel quedaba fijo en
   ese número desde el primer reporte tras confirmar.

2. recorder._captura_lista() aceptaba CUALQUIER segmento presente en la
   carpeta como prueba de captura de ESTA corrida: un segmento viejo
   (de una captura anterior o de un FFmpeg huérfano) confirmaba
   «grabando» aunque el FFmpeg nuevo hubiera muerto al arrancar.

3. El frontend (seguirEstadoGrabacion) nunca cerraba el aviso
   «Iniciando… esperando confirmación del trabajador (trabajo #N)»
   cuando la sesión pasaba a «grabando»: el texto de espera convivía
   con la píldora de grabando indefinidamente, como si el trabajo
   siguiera sin confirmar.
"""
import os, time
from pathlib import Path

from recorder import Recorder

RAIZ = Path(__file__).resolve().parents[1]
MB_316 = 316_800_000


def _escribir(path, bytes_):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(bytes_)
    return path


def test_bytes_en_disco_ignora_pelicula_de_otra_captura(tmp_path):
    carpeta = tmp_path / "sesion-9"
    _escribir(carpeta / "grabacion_completa.mp4", MB_316)  # película VIEJA
    _escribir(carpeta / "seg_00000.mp4", 1000)
    _escribir(carpeta / "seg_00001.mp4", 2000)
    rec = Recorder("https://8.8.8.8/live.m3u8", carpeta)
    assert rec.bytes_en_disco() == 3000  # la suma de segmentos, NO la película
    _escribir(carpeta / "seg_00002.mp4", 4000)
    assert rec.bytes_en_disco() == 7000  # y crece con la captura real


def test_bytes_en_disco_sin_segmentos_usa_la_pelicula(tmp_path):
    carpeta = tmp_path / "sesion-10"
    _escribir(carpeta / "grabacion_completa.mp4", 5000)
    rec = Recorder("https://8.8.8.8/live.m3u8", carpeta)
    assert rec.bytes_en_disco() == 5000
    vacia = Recorder("https://8.8.8.8/live.m3u8", tmp_path / "sesion-11")
    assert vacia.bytes_en_disco() == 0


def test_esperar_datos_no_confirma_con_segmentos_viejos(tmp_path, monkeypatch):
    carpeta = tmp_path / "sesion-12"
    seg = _escribir(carpeta / "seg_00000.mp4", 9000)
    viejo = time.time() - 3600
    os.utime(seg, (viejo, viejo))  # segmento de una captura ANTERIOR
    rec = Recorder("https://8.8.8.8/live.m3u8", carpeta)  # la corrida empieza AHORA
    monkeypatch.setattr(Recorder, "_pista", lambda self, p, sel: True)
    ok, _info = rec.esperar_datos(timeout=1.2, min_bytes=8192)
    assert ok is False  # nada de ESTA corrida ha escrito video: no se confirma


def test_esperar_datos_confirma_con_segmento_de_esta_corrida(tmp_path, monkeypatch):
    carpeta = tmp_path / "sesion-13"
    rec = Recorder("https://8.8.8.8/live.m3u8", carpeta)
    _escribir(carpeta / "seg_00000.mp4", 9000)  # mtime fresco: lo escribió esta corrida
    monkeypatch.setattr(Recorder, "_pista", lambda self, p, sel: True)
    ok, info = rec.esperar_datos(timeout=3, min_bytes=8192)
    assert ok is True and info["bytes"] == 9000 and info["tiene_video"] is True


def test_panel_cierra_el_aviso_de_espera_al_confirmar():
    js = (RAIZ / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    cuerpo = js.split("function seguirEstadoGrabacion", 1)[1].split(
        "function restaurarEstadoGrabacion", 1)[0]
    # Al pasar la sesión a «grabando» durante un inicio, el seguimiento
    # sustituye el aviso de espera por la confirmación, con el nº de trabajo.
    assert 'g.modo === "inicio"' in cuerpo
    assert 'setLiveMsg("Grabación confirmada por el trabajador' in cuerpo
    # El texto de espera solo nace en iniciarGrab(); dentro del seguimiento
    # no debe quedar ninguna vía que lo deje conviviendo con la píldora.
    assert "esperando confirmación del trabajador" not in cuerpo
