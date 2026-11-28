"""v1.3.17 — Regresión: el video frisado de la v1.3.15/v1.3.16.

El 2026-11-27 Jose grabó con la 1.3.16 y el clip guardado salía con la
IMAGEN CONGELADA mientras el audio seguía («se frisa y sigue hablando»).
Causa: el «use_wallclock_as_timestamps» de la v1.3.15 sellaba cada
paquete con su hora de llegada; las señales en vivo llegan por pedazos
y los paquetes de un pedazo comparten casi la misma hora, así que la
línea de tiempo del video quedaba rota a tramos.

La v1.3.17:
- RETIRA «use_wallclock_as_timestamps» y «start_at_zero» para siempre:
  los tiempos propios de cada señal no se tocan.
- La sincronía con dos señales en vivo la hace el AUDIO con
  «aresample=async=1» (estira/encoge muestras de forma imperceptible
  para seguir el reloj del video), solo en vivo y con dos entradas.
- La rotación de captura cada 30 min de la v1.3.15 se queda.
"""
import json
import subprocess
from pathlib import Path

import worker as worker_mod
from recorder import _cmd_grabar

ROOT = Path(__file__).resolve().parents[1]
DOS = "https://v.example.com/video\nhttps://v.example.com/audio"
UNA = "https://v.example.com/live.m3u8"


def test_nunca_reloj_de_pared():
    for cmd in (_cmd_grabar(DOS, "salida", en_vivo=True),
                _cmd_grabar(DOS, "salida"),
                _cmd_grabar(UNA, "salida", en_vivo=True)):
        assert "-use_wallclock_as_timestamps" not in cmd
        assert "-start_at_zero" not in cmd


def test_dos_senales_en_vivo_ajustan_por_audio():
    cmd = _cmd_grabar(DOS, "salida", en_vivo=True)
    i = cmd.index("-af")
    assert cmd[i + 1] == "aresample=async=1"
    # El filtro va ANTES del códec de audio: ajusta la pista al codificar.
    assert i < cmd.index("-c:a")


def test_sin_ajuste_fuera_de_dos_senales_en_vivo():
    assert "aresample=async=1" not in _cmd_grabar(DOS, "salida")
    assert "aresample=async=1" not in _cmd_grabar(UNA, "salida", en_vivo=True)


def test_grabacion_real_corta_no_se_congela(tmp_path):
    """Grabación REAL con el comando de producción (dos archivos locales
    como si fueran las dos señales): el segmento resultante debe tener
    sus frames de video repartidos por TODA la duración — si los tiempos
    se rompieran como con el reloj de pared, el video quedaría en un
    solo instante o con huecos enormes entre frames."""
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=320x240:rate=10:duration=12",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
                   check=True, capture_output=True, timeout=120)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=12",
                    "-c:a", "aac", str(audio)],
                   check=True, capture_output=True, timeout=120)
    destino = tmp_path / "segs"
    destino.mkdir()
    cmd = _cmd_grabar(f"{video}\n{audio}", destino, en_vivo=True)
    # Sin red en el test: se quitan las opciones -reconnect (pensadas
    # para HTTP) junto con su valor, sin tocar ningún otro token.
    limpio, saltar = [], False
    for tok in cmd:
        if tok in ("-reconnect", "-reconnect_streamed", "-reconnect_delay_max"):
            saltar = True
            continue
        if saltar:
            saltar = False
            continue
        limpio.append(tok)
    subprocess.run(limpio, check=True, capture_output=True, timeout=300)
    segs = sorted((tmp_path / "segs").glob("seg_*.mp4"))
    assert segs, "la grabación no produjo segmentos"
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "packet=pts_time", "-of", "csv=p=0",
                        str(segs[0])], capture_output=True, text=True, timeout=60)
    tiempos = [float(x) for x in r.stdout.split() if x.strip()]
    assert len(tiempos) > 50, f"muy pocos frames de video: {len(tiempos)}"
    huecos = [b - a for a, b in zip(tiempos, tiempos[1:])]
    assert max(huecos) < 1.0, f"hueco de video de {max(huecos):.2f}s: imagen congelada"


def test_version_1_3_17_y_manifiesto():
    assert worker_mod.VERSION == "1.3.20"
    manifiesto = json.loads((ROOT / "worker-windows" / "manifest.json")
                            .read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.20"
    assert "recorder.py" in manifiesto["files"]
