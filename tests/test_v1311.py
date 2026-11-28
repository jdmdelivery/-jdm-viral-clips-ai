"""Tests de la v1.3.11 — retención en vivo: solo se guardan los clips
(orden de Jose, 2026-11-23, noche del estreno de La Casa de Alofoke 3:
«Revisa que solo guarde los clips, no el vídeo completo, así no se me
llena el disco duro»). El en vivo es 24/7 hasta el 2 de enero.

Auditoría previa (lo que había hasta la v1.3.10):
- Durante la grabación NINGÚN segmento se borraba jamás:
  BufferCircular.podar() nunca era llamada por nadie en vivo y, aun
  llamada, su guarda exige que grabacion_completa.mp4 ya exista — la
  película solo nace al detener. La «ventana» del buffer (5 min por
  defecto) era inoperante durante el en vivo.
- Al detener, Recorder.stop() unía SIEMPRE todos los segmentos en
  grabacion_completa.mp4 y los segmentos TAMBIÉN quedaban en disco:
  tras detener, el disco guardaba ~2x la grabación.
- Crecimiento sin poda: segmentos de 10 s a ~15-27 MB/min =
  0,9-1,6 GB/h = 21,6-38,9 GB/día (hasta el 2 ene: 0,86-1,56 TB).

Arreglo (solo trabajador):
1. Poda rodante en vivo (Recorder.podar_rodante, cada 20 s en el bucle
   de grabación): conserva solo VENTANA_PODA_S = 45 min de segmentos —
   cubre ~11x lo que el radar necesita (ventana de análisis + clip de
   60-120 s con pre 30/post 25 + intervalo del radar + generar el
   clip) — y acota la carpeta de la sesión a ~0,7-1,2 GB. Un segmento
   solo se borra fuera de la ventana Y sin estar en uso (el radar
   marca en uso la ventana caliente abierta y los segmentos que un
   clip está concatenando). La grabación nunca se detiene por la poda.
2. Al detener una sesión EN VIVO no se crea la película: stop() sin
   unión, pasada final del radar con los segmentos aún en disco y
   descarte de los segmentos restantes. La API ya salta el registro
   como proyecto cuando grabacion_completa es None (sin tocarla).
   Las fuentes de archivo/importadas no cambian.
3. Los clips y sus miniaturas (carpeta de clips) NO se tocan: son lo
   único que se guarda a largo plazo.
"""
import json, os, subprocess, time
from pathlib import Path

import pytest

import worker as worker_mod
from buffer import BufferCircular
from radar import RadarSesion
from recorder import Recorder, VENTANA_PODA_S

ENC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]


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


def _manifest(carpeta, entradas):
    (carpeta / "manifest.json").write_text(json.dumps(entradas), encoding="utf-8")


def _entradas(carpeta, indices):
    return [{"path": str(carpeta / f"seg_{i:05d}.mp4"), "indice": i,
             "inicio_s": i * 10.0, "duracion_s": 10.0, "creado": time.time()}
            for i in indices]


# ---------------------------------------------------------------------------
# 1. La poda rodante acota el disco en una grabación larga simulada
# ---------------------------------------------------------------------------
def test_poda_rodante_acota_el_disco_en_una_grabacion_larga(tmp_path):
    carpeta = tmp_path / "grabaciones" / "sesion-31"
    rec = Recorder("https://8.8.8.8/live.m3u8", carpeta, en_vivo=True)
    # 721 segmentos de 10 s = 2 horas de línea de tiempo ya grabadas.
    for i in range(721):
        (carpeta / f"seg_{i:05d}.mp4").write_bytes(b"x" * 2048)
    # Restos sueltos: uno viejo de otra corrida y uno fresco (podría ser
    # el segmento que el FFmpeg está escribiendo ahora mismo).
    viejo = carpeta / "seg_00999.mp4"; viejo.write_bytes(b"x" * 2048)
    hace_2h = time.time() - 7200
    os.utime(viejo, (hace_2h, hace_2h))
    fresco = carpeta / "seg_00998.mp4"; fresco.write_bytes(b"x" * 2048)
    _manifest(carpeta, _entradas(carpeta, range(721)))
    # Un clip EN CURSO está leyendo el segmento 200: intocable.
    rec.buffer.marcar_en_uso({200})
    # El 100 es de un clip YA generado: su archivo vive en la carpeta de
    # clips y NO bloquea la poda rodante (si bloqueara, un 24/7 crecería
    # sin límite con cada clip).
    rec.buffer.proteger({100})

    n = rec.podar_rodante()

    # Fin de línea de tiempo 7210 s; ventana 2700 s -> fuera <= 4510 s:
    # caen los índices 0..450 (451), salvo el 200 en uso; más el suelto
    # viejo de otra corrida: 451 borrados en total.
    assert n == 451
    assert not (carpeta / "seg_00449.mp4").exists()
    assert not (carpeta / "seg_00450.mp4").exists()
    assert (carpeta / "seg_00451.mp4").exists()
    assert not (carpeta / "seg_00100.mp4").exists(), "el protegido de un clip ya generado bloqueó la poda"
    assert (carpeta / "seg_00200.mp4").exists(), "se borró un segmento EN USO por un clip en curso"
    assert not viejo.exists(), "el resto viejo de otra corrida no se barrió"
    assert fresco.exists(), "se borró un segmento fresco que puede estar escribiéndose"
    # El manifest y el disco quedan acotados a la ventana (+ el suelto fresco).
    assert len(rec.buffer.segmentos()) == 721 - 450
    en_disco = sorted(carpeta.glob("seg_*.mp4"))
    assert len(en_disco) == 271 + 1
    esperado = sum(p.stat().st_size for p in en_disco
                   if p.stat().st_mtime >= rec._t_inicio - 1.0)
    assert rec.bytes_en_disco() == esperado, "bytes_en_disco no refleja la ventana real"
    assert rec.bytes_en_disco() <= len(en_disco) * 2048
    # Al liberarse el uso, el segmento 200 ya sí sale en la siguiente poda.
    rec.buffer.liberar_en_uso({200})
    assert rec.podar_rodante() == 1
    assert not (carpeta / "seg_00200.mp4").exists()


def test_poda_clasica_sigue_respetando_protegidos(tmp_path):
    """La poda clásica (flujo de archivo/análisis, con película) no
    cambia: allí los protegidos NUNCA se borran."""
    carpeta = tmp_path / "sesion-clasica"
    b = BufferCircular(carpeta, minutos=5)
    for i in range(3):
        p = carpeta / f"seg_{i:05d}.mp4"
        p.write_bytes(b"x" * 2048)
        b.registrar(str(p), i, i * 10.0, 10.0)
    (carpeta / "grabacion_completa.mp4").write_bytes(b"x" * 9000)
    b.proteger({0})
    assert b.podar(ahora_s=1000) == 2
    assert (carpeta / "seg_00000.mp4").exists()
    assert len(b.segmentos()) == 1


def test_poda_rodante_no_aplica_a_fuentes_de_archivo(tmp_path):
    """Una fuente de archivo se segmenta a toda velocidad y su película
    se une al terminar: podarla por línea de tiempo la rompería."""
    carpeta = tmp_path / "grabaciones" / "sesion-32"
    rec = Recorder(str(tmp_path / "video.mp4"), carpeta, en_vivo=False)
    for i in range(400):
        (carpeta / f"seg_{i:05d}.mp4").write_bytes(b"x" * 1024)
    _manifest(carpeta, _entradas(carpeta, range(400)))
    assert rec.podar_rodante() == 0
    assert len(list(carpeta.glob("seg_*.mp4"))) == 400


# ---------------------------------------------------------------------------
# 2. Tras la poda, el radar sigue cortando su clip de la ventana reciente
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"),
                    reason="sin ffmpeg en esta máquina")
def test_radar_genera_su_clip_tras_la_poda(tmp_path):
    carpeta = tmp_path / "grabaciones" / "sesion-25"
    rec = Recorder("https://8.8.8.8/live.m3u8", carpeta, en_vivo=True)
    # Casi 2 horas «ya grabadas»: segmentos dummy con su línea de tiempo.
    for i in range(714):
        (carpeta / f"seg_{i:05d}.mp4").write_bytes(b"x" * 1024)
    # La ventana reciente es REAL: amarillo tranquilo y el momento
    # fuerte blanco/morado en los índices 717-718 (s 7170-7190).
    for i in range(714, 721):
        p = carpeta / f"seg_{i:05d}.mp4"
        if i in (717, 718):
            _seg_fuerte(p, tmp_path, colores=("white", "purple"))
        else:
            _seg_tranquilo(p, color="yellow")
    _manifest(carpeta, _entradas(carpeta, range(721)))

    # La poda deja la carpeta en la ventana: caen los dummy 0..450 y
    # quedan 270 entradas; los segmentos recientes, intactos.
    rec.podar_rodante()
    assert not (carpeta / "seg_00450.mp4").exists()
    assert (carpeta / "seg_00714.mp4").exists() and (carpeta / "seg_00720.mp4").exists()
    assert len(rec.buffer.segmentos()) == 270

    # El radar lleva toda la transmisión puntuando: solo le faltan los
    # segmentos recientes. Detecta el momento y corta su clip SOLO de
    # segmentos que sobrevivieron a la poda.
    api = _ApiFalsa()
    radar = RadarSesion(rec, 25, api, tmp_path / "clips",
                        umbral=40, intervalo_s=1)
    radar._puntuados.update(range(714))
    radar.pasada()
    assert len(api.reportes) == 1, "el radar no generó el clip tras la poda"
    clips = [c for _, cs in api.reportes for c in cs]
    assert len(clips) == 1
    clip = Path(clips[0]["file_path"])
    assert clip.exists() and Path(clips[0]["thumb_path"]).exists()
    assert 60 <= _duracion(clip) <= 120
    r, g, b = _color_medio(clip, 5)
    assert r > 150 and g > 150 and b < 100, f"frame 5 s no es la ventana reciente: {(r, g, b)}"
    r, g, b = _color_medio(clip, 37)
    assert b > 100, f"frame 37 s no es el momento fuerte reciente: {(r, g, b)}"


# ---------------------------------------------------------------------------
# 3. Detener una sesión en vivo: ni película ni segmentos; clips intactos
# ---------------------------------------------------------------------------
def test_detener_en_vivo_no_deja_pelicula_ni_segmentos(tmp_path):
    carpeta = tmp_path / "grabaciones" / "sesion-77"
    rec = Recorder("https://8.8.8.8/live.m3u8", carpeta, en_vivo=True)
    for i in range(5):
        (carpeta / f"seg_{i:05d}.mp4").write_bytes(b"x" * 4096)
    # Un clip ya generado vive en la carpeta de clips: NO se toca.
    clip = tmp_path / "clips" / "sesion-77" / "clip-radar-10s.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"clip-guardado")
    worker_mod.grabaciones[77] = rec
    cfg = {"carpeta_grabaciones": str(tmp_path / "grabaciones"),
           "carpeta_clips": str(tmp_path / "clips")}
    res = worker_mod.procesar(
        {"type": "detener_grabacion", "payload": {"session_id": 77}}, cfg)
    assert res["estado"] == "detenida"
    # Sin película: la API saltará el registro como proyecto (None) y la
    # nota lo dice con honestidad.
    assert res["grabacion_completa"] is None
    assert "clips" in res["nota"]
    assert not (carpeta / "grabacion_completa.mp4").exists()
    assert list(carpeta.glob("seg_*.mp4")) == [], "quedaron segmentos de la grabación en vivo"
    assert rec.buffer.segmentos() == []
    assert clip.exists() and clip.read_bytes() == b"clip-guardado"


# ---------------------------------------------------------------------------
# 4. Versión visible 1.3.11 y ventana definida en un solo sitio
# ---------------------------------------------------------------------------
def test_version_1_3_11_y_ventana():
    assert worker_mod.VERSION == "1.3.17"
    manifiesto = json.loads((Path(__file__).resolve().parents[1]
                             / "worker-windows" / "manifest.json").read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.17"
    assert VENTANA_PODA_S == 45 * 60
