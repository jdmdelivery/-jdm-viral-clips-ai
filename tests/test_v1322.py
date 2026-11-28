"""v1.3.22 — Vuelta a la 1.3.19 + SOLO encuadre enfocado en la gente.

El 2026-11-28 Jose ordenó: «Déjalo como estaba el motor 3.19, solo
trata de enfocar la gente mejor». Esta versión restaura el árbol de
la v1.3.19 (sin el gancho de la 1.3.20 ni el cierre en segundo plano
de la 1.3.21) y añade UNA sola cosa: exportar_clip busca una cara en
la ventana y centra en ella el recorte vertical, en vez del centro
del cuadro que cortaba a la gente de los lados. Sin cara, el clip
sale con el filtro clásico de siempre, con la misma duración.
"""
import json
import subprocess
from pathlib import Path

import pytest

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


def test_crop_centrado_es_el_clasico():
    assert _ventana_crop(1920, 1080, 1080, 1920) == (608, 1080, 656, 0)


def test_crop_sigue_a_la_persona_sin_salirse():
    cw, ch, x, y = _ventana_crop(1280, 720, 1080, 1920, centro=(1200, 300))
    assert x + cw == 1280 and x <= 1200 <= x + cw   # pegado al borde, persona dentro
    cw2, ch2, x2, y2 = _ventana_crop(1280, 720, 1080, 1920, centro=(1070, 300))
    assert x2 <= 1070 <= x2 + cw2 and x2 + cw2 <= 1280


def _video_persona_a_la_derecha(tmp_path):
    """1280x720: fondo de color y la foto de Jose pegada a la DERECHA
    (x=900..1240). El recorte clásico centrado (437..842) la deja
    FUERA; solo un encuadre que siga a la persona la mete en el clip."""
    salida = tmp_path / "derecha.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=teal:size=1280x720:duration=8",
         "-loop", "1", "-i", str(FOTO_JOSE),
         "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
         "-filter_complex",
         "[1:v]scale=340:-1[foto];[0:v][foto]overlay=900:150[v]",
         "-map", "[v]", "-map", "2:a", "-t", "8",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         str(salida)],
        check=True, capture_output=True, timeout=300)
    return salida


def test_el_clip_encuadra_a_la_persona_aunque_este_a_un_lado(tmp_path):
    if not FOTO_JOSE.exists():
        pytest.skip("Sin la foto de referencia de Jose en esta máquina.")
    fuente = _video_persona_a_la_derecha(tmp_path)
    ub0 = vision.ubicar_rostro(str(fuente), segundo=4)
    if not ub0["disponible"] or not ub0["rostro"]:
        pytest.skip(f"Detección no disponible aquí: {ub0['nota']}")
    salida = tmp_path / "clip.mp4"
    exportar_clip(str(fuente), 1, 7, str(salida), "9:16")
    # Sin gancho ni añadidos: la duración es la de la ventana.
    assert abs(_duracion(salida) - 6.0) < 0.7
    assert _dims(salida) == (1080, 1920)
    # La persona aparece en el clip (con el centro clásico quedaba fuera).
    ub = vision.ubicar_rostro(str(salida), segundo=3)
    assert ub["rostro"] is not None, f"la persona no quedó en el encuadre: {ub['nota']}"


def test_sin_caras_el_clip_sale_clasico(tmp_path):
    fuente = tmp_path / "patron.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(fuente)],
        check=True, capture_output=True, timeout=300)
    salida = tmp_path / "clip2.mp4"
    exportar_clip(str(fuente), 5, 20, str(salida), "9:16")
    assert abs(_duracion(salida) - 15.0) < 0.7
    assert _dims(salida) == (1080, 1920)


def test_version_1_3_22_y_manifiesto():
    assert worker_mod.VERSION == "1.3.22"
    manifiesto = json.loads((ROOT / "worker-windows" / "manifest.json")
                            .read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.22"
    for nombre in ("clipper.py", "vision.py", "worker.py"):
        assert nombre in manifiesto["files"]
