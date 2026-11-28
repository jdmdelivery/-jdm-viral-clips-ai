"""v1.3.18 — Compensación del corrimiento de nacimiento entre señales.

El 2026-11-27, ya con la v1.3.17 (sin frisado), Jose reportó que la
voz seguía desfasada: «primero habla y después a los dos segundos se
escucha lo que dijo». Corrimiento FIJO de ~2 s: las dos señales
separadas del directo comparten una línea de tiempo, pero el motor las
pegaba cada una desde su propio cero.

La v1.3.18 mide la marca de arranque (start_time) de cada señal con
ffprobe y retrasa la adelantada con «-itsoffset» la diferencia
(_desfase_entre), en cada lanzamiento del Recorder. Sin medición o con
una medición absurda no se toca nada.
"""
import json
import math
import subprocess
from array import array
from pathlib import Path

import worker as worker_mod
from recorder import (_cmd_grabar, _desfase_entre, _sondear_inicio)

ROOT = Path(__file__).resolve().parents[1]
DOS = "https://v.example.com/video\nhttps://v.example.com/audio"


# ---------- _desfase_entre: la regla pura ----------

def test_audio_tarde_se_retrasa_el_video():
    # Video con marca MAYOR: su contenido va adelantado, la voz llega
    # tarde (el caso de Jose) → se retrasa la entrada 0 (video).
    assert _desfase_entre(3603.4, 3601.4) == (0, 2.0)


def test_video_tarde_se_retrasa_el_audio():
    assert _desfase_entre(3601.4, 3603.4) == (1, 2.0)


def test_corrimiento_menor_o_absurdo_no_se_toca():
    assert _desfase_entre(100.0, 100.1) is None      # inaudible
    assert _desfase_entre(100.0, 130.0) is None      # absurdo: no fiar
    assert _desfase_entre(None, 100.0) is None
    assert _desfase_entre(100.0, None) is None


# ---------- _cmd_grabar: colocación del -itsoffset ----------

def test_itsoffset_va_antes_de_la_entrada_correcta():
    cmd = _cmd_grabar(DOS, "salida", en_vivo=True, desfase=(0, 2.0))
    i = cmd.index("-itsoffset")
    assert cmd[i + 1] == "2.000"
    assert cmd[:i].count("-i") == 0                  # antes de la 1ª entrada
    assert cmd.index("-i") > i
    cmd = _cmd_grabar(DOS, "salida", en_vivo=True, desfase=(1, 1.5))
    i = cmd.index("-itsoffset")
    assert cmd[i + 1] == "1.500"
    assert cmd[:i].count("-i") == 1                  # antes de la 2ª entrada
    assert cmd[i:].index("-i") >= 0


def test_sin_desfase_no_hay_itsoffset():
    assert "-itsoffset" not in _cmd_grabar(DOS, "salida", en_vivo=True)


# ---------- medición real sobre archivos con línea de tiempo ----------

def _ts_con_eventos(tmp_path):
    """Dos archivos .ts (video solo / audio solo) sobre UNA línea de
    tiempo compartida: el destello blanco (video, local 6.6) y el pitido
    (audio, local 8.6) son EL MISMO momento, porque la línea del video
    arranca 2 s más tarde (offset 3602) que la del audio (3600)."""
    video = tmp_path / "video.ts"
    audio = tmp_path / "audio.ts"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black:s=320x240:r=10:d=12",
         "-vf", "drawbox=x=0:y=0:w=iw:h=ih:c=white:t=fill:enable='between(t,6.6,7.1)'",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-output_ts_offset", "3602", str(video)],
        check=True, capture_output=True, timeout=120)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=880:duration=12",
         "-af", "volume=0:enable='not(between(t,8.6,9.1))',volume=8",
         "-c:a", "aac", "-output_ts_offset", "3600", str(audio)],
        check=True, capture_output=True, timeout=120)
    return video, audio


def _sin_reconnect(cmd):
    limpio, saltar = [], False
    for tok in cmd:
        if tok in ("-reconnect", "-reconnect_streamed", "-reconnect_delay_max"):
            saltar = True
            continue
        if saltar:
            saltar = False
            continue
        limpio.append(tok)
    return limpio


def _tiempos_evento(seg):
    """(t del destello, t del pitido) dentro de un segmento grabado.
    Solo biblioteca estándar: la suite no depende de numpy."""
    raw_v = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(seg), "-map", "0:v:0",
         "-vf", "fps=10,scale=32:24", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True, timeout=120).stdout
    tam = 32 * 24
    t_flash = None
    for i in range(len(raw_v) // tam):
        if sum(raw_v[i * tam:(i + 1) * tam]) / tam > 200:
            t_flash = i / 10
            break
    raw_a = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(seg), "-map", "0:a:0",
         "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
        capture_output=True, check=True, timeout=120).stdout
    a = array("f")
    a.frombytes(raw_a[:len(raw_a) // 4 * 4])
    w = 800
    t_beep = None
    for i in range(len(a) // w):
        ventana = a[i * w:(i + 1) * w]
        if math.sqrt(sum(x * x for x in ventana) / w) > 0.2:
            t_beep = i / 10
            break
    return t_flash, t_beep


def test_medicion_y_compensacion_extremo_a_extremo(tmp_path):
    video, audio = _ts_con_eventos(tmp_path)
    desfase = _desfase_entre(_sondear_inicio(str(video)),
                             _sondear_inicio(str(audio)))
    assert desfase is not None and desfase[0] == 0
    assert 1.4 < desfase[1] < 2.6, f"desfase medido: {desfase}"

    for nombre, df, espera in (("sin compensar", None, (1.2, 3.2)),
                               ("compensado", desfase, (0.0, 0.6))):
        destino = tmp_path / nombre
        destino.mkdir()
        cmd = _sin_reconnect(_cmd_grabar(f"{video}\n{audio}", destino,
                                         en_vivo=True, desfase=df))
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        seg = sorted(destino.glob("seg_*.mp4"))[0]
        t_flash, t_beep = _tiempos_evento(seg)
        assert t_flash is not None and t_beep is not None, nombre
        hueco = abs(t_beep - t_flash)
        assert espera[0] <= hueco <= espera[1], \
            f"{nombre}: destello {t_flash}s, pitido {t_beep}s, hueco {hueco:.2f}s"


def test_sondeo_fallido_devuelve_none():
    assert _sondear_inicio("/no/existe/nada.mp4") is None


def test_version_1_3_18_y_manifiesto():
    assert worker_mod.VERSION == "1.3.18"
    manifiesto = json.loads((ROOT / "worker-windows" / "manifest.json")
                            .read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.18"
    assert "recorder.py" in manifiesto["files"]
