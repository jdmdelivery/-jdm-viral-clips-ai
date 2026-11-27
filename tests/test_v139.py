"""Tests de la v1.3.9 — aislamiento por corrida y latido independiente
(caso sesión #20, 2026-11-23, grabado en video por Jose):

Jose reutilizó la sesión #20: primero grabó un en vivo de GTA y después
editó la MISMA sesión con el enlace de La Casa de Alofoke 3 y volvió a
grabar. El Centro LIVE mostraba Alofoke grabando (344,7 MB) y el radar
marcaba momentos, pero los clips salían con patrullas y jetski del GTA.

Causa raíz confirmada en código:
- buffer.BufferCircular conservaba el manifest.json de la corrida
  anterior (nunca se reiniciaba): las entradas del GTA seguían «vivas»
  con su línea de tiempo al arrancar la corrida nueva.
- recorder.Recorder._registrar_nuevos/_finalizar_todos registraban en
  el manifest CUALQUIER seg_*.mp4 presente en la carpeta reutilizada,
  con la línea de tiempo de la corrida nueva; unir() concatenaba todos
  los segmentos de la carpeta y bytes_en_disco() los sumaba todos.
- radar.RadarSesion leía ese manifest sin filtrar por corrida: puntuaba
  los segmentos viejos y _generar_clip concatenaba sus archivos (el
  FFmpeg nuevo reescribe desde seg_00000 a ritmo real; los viejos de
  índices altos aún eran el GTA). La guarda de mtime de la v1.3.3 solo
  vivía en _captura_lista() (confirmación de inicio).

Y el «TRABAJADOR DESCONECTADO» del final del video: la API marca
desconectado si el heartbeat tiene >30 s, y en worker.main el latido
solo se enviaba al inicio de cada vuelta del bucle, que procesa los
trabajos de forma síncrona (una subida de copia puede ocupar minutos):
la grabación seguía viva pero el chip se apagaba y los MB se
congelaban (reportar_progreso va en el mismo bucle).

Arreglo:
1. BufferCircular.reiniciar(): cada Recorder nuevo empieza su manifest
   en [] (los archivos viejos NO se borran).
2. Recorder._segmentos_corrida() (mtime >= inicio de la corrida):
   registro en el manifest, unión, bytes en disco y reconexión solo
   miran los segmentos de ESTA corrida.
3. RadarSesion._entradas_corrida(): el radar filtra el manifest por
   mtime contra el inicio de la corrida del Recorder en todas sus
   rutas (puntuación, fin total y generación del clip).
4. worker._hilo_latido(): heartbeat + progreso cada 10 s en un hilo
   aparte del bucle de trabajos.
"""
import json, os, subprocess, time
from pathlib import Path

import pytest

import worker as worker_mod
from buffer import BufferCircular
from radar import RadarSesion
from recorder import Recorder

ENC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
HACE_UNA_HORA = time.time() - 3600


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)


def _seg_tranquilo(path, color="blue"):
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
          f"color=c={color}:size=640x360:duration=10", "-f", "lavfi", "-i",
          "sine=frequency=440:duration=10", "-af", "volume=0.02",
          "-shortest", *ENC, str(path)])


def _seg_fuerte(path, tmp, colores=("red", "green")):
    partes = []
    for i, c in enumerate(colores):
        p = tmp / f"parte-{path.stem}-{i}.mp4"
        _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
              f"color=c={c}:size=640x360:duration=5", "-an", *ENC, str(p)])
        partes.append(p)
    lista = tmp / f"lista-{path.stem}.txt"
    lista.write_text("".join(f"file '{p.name}'\n" for p in partes))
    v = tmp / f"v-{path.stem}.mp4"
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
          "-i", str(lista), "-c", "copy", str(v)])
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(v), "-f", "lavfi", "-i",
          r"aevalsrc=0.85*sin(2*PI*440*t)*lt(mod(t\,2)\,1):s=44100:d=10",
          "-map", "0:v", "-map", "1:a", "-shortest", *ENC, str(path)])


def _color_medio(path, segundo):
    """Color RGB medio de un frame (el export 9:16 llena el cuadro, así
    que el color medio ES el contenido: identifica la fuente del clip)."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(segundo), "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"],
                       capture_output=True, timeout=60)
    assert len(r.stdout) >= 3, "no se pudo extraer el frame"
    return r.stdout[0], r.stdout[1], r.stdout[2]


def _duracion(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    return float(json.loads(r.stdout)["format"]["duration"])


class _ApiFalsa:
    def __init__(self):
        self.reportes = []
        self._n = 0
    def reportar_en_vivo(self, session_id, events, clips):
        self.reportes.append((events, clips))
        ids = []
        for _ in clips:
            self._n += 1; ids.append(2000 + self._n)
        return {"ok": True, "clip_ids": ids}
    def subir_clip(self, clip_id, ruta): return {"ok": True}
    def subir_miniatura(self, clip_id, ruta): return {"ok": True}


# ---------------------------------------------------------------------------
# 1. El manifest de la corrida anterior NO se hereda (y nada se borra)
# ---------------------------------------------------------------------------
def test_manifest_anterior_no_se_hereda_ni_borra_archivos(tmp_path):
    carpeta = tmp_path / "sesion-20"
    carpeta.mkdir(parents=True)
    viejo = carpeta / "seg_00000.mp4"
    viejo.write_bytes(b"contenido-viejo")
    os.utime(viejo, (HACE_UNA_HORA, HACE_UNA_HORA))
    (carpeta / "manifest.json").write_text(json.dumps(
        [{"path": str(viejo), "indice": 0, "inicio_s": 0.0,
          "duracion_s": 10.0, "creado": HACE_UNA_HORA}]), encoding="utf-8")

    rec = Recorder("https://8.8.8.8/live.m3u8", carpeta)
    assert rec.buffer.segmentos() == [], "el manifest viejo se heredó a la corrida nueva"
    assert viejo.exists(), "los segmentos viejos del usuario NO se borran"


def test_buffer_reiniciar_solo_vacia_el_indice(tmp_path):
    b = BufferCircular(tmp_path, minutos=5)
    b.registrar(str(tmp_path / "seg_00000.mp4"), 0, 0.0, 10.0)
    b.proteger({0})
    assert len(b.segmentos()) == 1
    b.reiniciar()
    assert b.segmentos() == [] and b.protegidos == set()


# ---------------------------------------------------------------------------
# 2. El radar solo ve segmentos de la corrida actual
# ---------------------------------------------------------------------------
class _BufferFalso:
    def __init__(self, entradas): self.entradas = entradas
    def segmentos(self): return list(self.entradas)
    def proteger(self, indices): pass


class _RecorderFalso:
    def __init__(self, entradas):
        self.buffer = _BufferFalso(entradas)
        self._t_inicio = time.time()
        self.inicio = self._t_inicio


def test_radar_filtra_entradas_de_otra_corrida(tmp_path):
    viejo = tmp_path / "seg_00007.mp4"; viejo.write_bytes(b"x")
    os.utime(viejo, (HACE_UNA_HORA, HACE_UNA_HORA))
    nuevo = tmp_path / "seg_00000.mp4"; nuevo.write_bytes(b"x")
    entradas = [
        {"indice": 7, "inicio_s": 70.0, "duracion_s": 10.0, "path": str(viejo)},
        {"indice": 0, "inicio_s": 0.0, "duracion_s": 10.0, "path": str(nuevo)},
    ]
    radar = RadarSesion(_RecorderFalso(entradas), 20, _ApiFalsa(),
                        tmp_path / "clips", umbral=40)
    assert [e["indice"] for e in radar._segmentos(incluir_ultimo=True)] == [0]
    assert radar._fin_total() == 10.0


# ---------------------------------------------------------------------------
# 3. EL CASO DE JOSE, entero: sesión reutilizada, clips de la fuente nueva
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"),
                    reason="sin ffmpeg en esta máquina")
def test_sesion_reutilizada_clips_solo_de_la_fuente_nueva(tmp_path):
    carpeta = tmp_path / "grabaciones" / "sesion-20"
    carpeta.mkdir(parents=True)

    # --- Corrida anterior (el «GTA»): 8 segmentos FUERTES rojo/verde y
    # su manifest, todo con mtime de hace una hora.
    for i in range(8):
        _seg_fuerte(carpeta / f"seg_{i:05d}.mp4", tmp_path)
    manifest_viejo = [{"path": str(carpeta / f"seg_{i:05d}.mp4"), "indice": i,
                       "inicio_s": i * 10.0, "duracion_s": 10.0,
                       "creado": HACE_UNA_HORA} for i in range(8)]
    (carpeta / "manifest.json").write_text(json.dumps(manifest_viejo), encoding="utf-8")
    for p in carpeta.iterdir():
        os.utime(p, (HACE_UNA_HORA, HACE_UNA_HORA))

    # --- Corrida nueva (el «Alofoke»): el Recorder NO hereda el manifest.
    rec = Recorder("https://8.8.8.8/live.m3u8", carpeta, en_vivo=True)
    assert rec.buffer.segmentos() == []

    # El FFmpeg nuevo reescribe desde seg_00000 a ritmo real: índices
    # 0-6 con contenido NUEVO (amarillo tranquilo; el momento fuerte es
    # blanco/morado, jamás rojo/verde). seg_00007 queda VIEJO en disco.
    nuevos = []
    for i in range(7):
        p = carpeta / f"seg_{i:05d}.mp4"
        if i in (2, 3):
            _seg_fuerte(p, tmp_path, colores=("white", "purple"))
        else:
            _seg_tranquilo(p, color="yellow")
        nuevos.append(p)
    rec._registrar_nuevos(set())

    # El manifest solo conoce la corrida nueva: 6 entradas (el último
    # segmento aún «se está escribiendo») y todas con mtime fresco.
    entradas = rec.buffer.segmentos()
    assert len(entradas) == 6
    for e in entradas:
        assert Path(e["path"]).stat().st_mtime >= rec._t_inicio - 1.0
    # Los bytes en disco son los de ESTA corrida (7 archivos nuevos),
    # no la suma con el segmento viejo que quedó en la carpeta.
    assert rec.bytes_en_disco() == sum(p.stat().st_size for p in nuevos)

    # El radar puntúa y cierra la ventana del momento nuevo (20-40 s).
    api = _ApiFalsa()
    radar = RadarSesion(rec, 20, api, tmp_path / "clips",
                        umbral=40, intervalo_s=1)
    radar.pasada()
    assert len(api.reportes) == 1, "el radar no generó el clip del momento nuevo"
    clips = [c for _, cs in api.reportes for c in cs]
    assert len(clips) == 1
    clip = Path(clips[0]["file_path"])
    assert clip.exists()
    assert 60 <= _duracion(clip) <= 120
    # CONTENIDO: a los 5 s manda el amarillo de la fuente nueva; a los
    # 27 s el morado del momento nuevo. El rojo/verde del GTA anterior
    # (B~0 en ambos) no aparece por ningún lado.
    r, g, b = _color_medio(clip, 5)
    assert r > 150 and g > 150 and b < 100, f"frame 5 s no es la fuente nueva: {(r, g, b)}"
    r, g, b = _color_medio(clip, 27)
    assert b > 100, f"frame 27 s parece de la fuente vieja: {(r, g, b)}"

    # La película unida también es solo de esta corrida: ~70 s de
    # segmentos nuevos, no los ~80 s que daría mezclar el viejo.
    film = rec.unir()
    assert film is not None
    assert _duracion(film) < 75, f"la unión mezcló la corrida anterior: {_duracion(film)}"


# ---------------------------------------------------------------------------
# 4. El latido no depende del bucle de trabajos
# ---------------------------------------------------------------------------
class _FinLatido(Exception):
    pass


def test_latido_late_y_reporta_progreso_en_su_hilo(monkeypatch):
    llamadas = {"latidos": [], "progresos": []}

    class _ApiLatido:
        def heartbeat(self, version=None):
            llamadas["latidos"].append(version); return True
        def reportar_progreso(self, sid, bytes_):
            llamadas["progresos"].append((sid, bytes_)); return True

    class _RecFalso:
        def bytes_en_disco(self): return 1234

    monkeypatch.setitem(worker_mod.grabaciones, 99, _RecFalso())
    siestas = {"n": 0}

    def _dormir(segundos):
        siestas["n"] += 1
        if siestas["n"] > 2:
            raise _FinLatido()

    monkeypatch.setattr(worker_mod.time, "sleep", _dormir)
    with pytest.raises(_FinLatido):
        worker_mod._hilo_latido(_ApiLatido(), intervalo_s=10)
    # El hilo late y reporta progreso en cada vuelta (la excepción salta
    # en la tercera siesta, tras la tercera vuelta), sin pasar nunca por
    # el bucle principal de trabajos.
    assert llamadas["latidos"] == [worker_mod.VERSION] * 3
    assert llamadas["progresos"] == [(99, 1234)] * 3


# ---------------------------------------------------------------------------
# 5. Versión visible (pin vigente; la fijó la v1.3.9 y la mueve cada release)
# ---------------------------------------------------------------------------
def test_version_1_3_9():
    assert worker_mod.VERSION == "1.3.14"
    manifiesto = json.loads((Path(__file__).resolve().parents[1]
                             / "worker-windows" / "manifest.json").read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.14"
