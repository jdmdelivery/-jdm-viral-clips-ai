"""v1.3.20 — Gancho de 2 s + zoom a la cara en los clips.

Pedido de Jose el 2026-11-28: «¿qué más le podemos hacer pa' que los
videos sean más duros y se hagan más virales?» — eligió «Gancho + zoom
a la cara». Desde esta versión, exportar_clip con «pico_s» (el segundo
del momento fuerte, que ya conocen el radar y el analizador) abre el
clip con ~2,2 s de ese pico con un zoom fuerte (×0,62) a la cara
detectada (vision.ubicar_rostro: Haar/YuNet), y el cuerpo del clip va
encuadrado en esa misma cara en vez del centro. Sin rostro o ante
cualquier fallo, se cae a la exportación clásica de siempre.
"""
import json
import subprocess
from pathlib import Path

import pytest

import clipper
import vision
import worker as worker_mod
from clipper import _ventana_crop, exportar_clip

ROOT = Path(__file__).resolve().parents[1]
FOTO_JOSE = Path.home() / "workspace" / "profile-images" / "user-profile-reference.jpg"


def _duracion(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return float(r.stdout.strip())


def _dims(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    w, h = r.stdout.strip().split(",")
    return int(w), int(h)


# ---------- _ventana_crop: la matemática del encuadre ----------

def test_crop_clasico_centrado_16a9_a_9a16():
    cw, ch, x, y = _ventana_crop(1920, 1080, 1080, 1920)
    assert (cw, ch, x, y) == (608, 1080, 656, 0)


def test_crop_sigue_la_cara_y_se_encaja_en_el_borde():
    cw, ch, x, y = _ventana_crop(1920, 1080, 1080, 1920, centro=(1700, 540))
    assert x + cw == 1920          # pegado al borde derecho, nunca fuera
    assert y == 0
    cw2, ch2, x2, y2 = _ventana_crop(1920, 1080, 1080, 1920, centro=(100, 540))
    assert x2 == 0                 # pegado al borde izquierdo


def test_crop_con_zoom_encoge_alrededor_del_centro():
    cw, ch, x, y = _ventana_crop(1920, 1080, 1080, 1920, centro=(960, 540), zoom=0.62)
    assert cw < 608 and ch < 1080
    assert x < 960 < x + cw and y < 540 < y + ch   # la cara queda dentro
    assert cw % 2 == 0 and ch % 2 == 0


# ---------- ubicar_rostro con una cara real ----------

def _video_con_foto(tmp_path):
    salida = tmp_path / "con-foto.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(FOTO_JOSE),
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2",
         "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(salida)],
        check=True, capture_output=True, timeout=300)
    return salida


def test_ubicar_rostro_encuentra_la_cara(tmp_path):
    if not FOTO_JOSE.exists():
        pytest.skip("Sin la foto de referencia de Jose en esta máquina.")
    video = _video_con_foto(tmp_path)
    ub = vision.ubicar_rostro(str(video), segundo=3)
    if not ub["disponible"]:
        pytest.skip(f"Detección no disponible aquí: {ub['nota']}")
    assert ub["rostro"] is not None, ub["nota"]
    x, y, w, h = ub["rostro"]
    assert 0 <= x and x + w <= ub["ancho"] and w >= 40


# ---------- exportar_clip: gancho real y caída a la clásica ----------

def _fuente_70s(tmp_path):
    fuente = tmp_path / "fuente.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=70",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=70",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(fuente)],
        check=True, capture_output=True, timeout=300)
    return fuente


def test_exportar_con_gancho_dura_el_clip_mas_el_gancho(tmp_path):
    fuente = _fuente_70s(tmp_path)
    salida = tmp_path / "con-gancho.mp4"
    exportar_clip(str(fuente), 10, 40, str(salida), "9:16", pico_s=25)
    assert abs(_duracion(salida) - 32.2) < 0.7
    assert _dims(salida) == (1080, 1920)


def test_exportar_sin_pico_es_la_clasica_de_siempre(tmp_path):
    fuente = _fuente_70s(tmp_path)
    salida = tmp_path / "clasico.mp4"
    exportar_clip(str(fuente), 10, 40, str(salida), "9:16")
    assert abs(_duracion(salida) - 30.0) < 0.7
    assert _dims(salida) == (1080, 1920)


def test_version_1_3_20_y_manifiesto():
    assert worker_mod.VERSION == "1.3.20"
    manifiesto = json.loads((ROOT / "worker-windows" / "manifest.json")
                            .read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.20"
    for nombre in ("clipper.py", "vision.py", "radar.py", "analyzer.py"):
        assert nombre in manifiesto["files"]
