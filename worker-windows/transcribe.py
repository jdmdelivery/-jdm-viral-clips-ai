"""Transcripción con faster-whisper SI está instalado. Si no, devuelve no-disponible SIN fingir.

Etapa 2: devuelve también los SEGMENTOS con tiempos y puede generar un .srt.
Nota real (VM de desarrollo, 2026-11-14): faster-whisper 1.2.1 instaló bien, pero su
ruta directa de decodificación usa PyAV con el argumento `metadata_errors`, que
PyAV 19 ya no acepta (TypeError al abrir el archivo). Por eso, si la ruta directa
falla, se decodifica el audio con FFmpeg a PCM float32 16 kHz mono y se pasa el
array a Whisper: misma transcripción, sin depender de PyAV."""
import subprocess
from pathlib import Path
from procesos import SIN_VENTANA


def _fmt_ts(segundos):
    ms = int(round(max(0.0, segundos) * 1000))
    h, resto = divmod(ms, 3600000)
    m, resto = divmod(resto, 60000)
    s, ms = divmod(resto, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generar_srt(segmentos, salida):
    """Escribe un .srt desde segmentos [{start_s, end_s, texto}]. Devuelve la ruta o None."""
    if not segmentos:
        return None
    lineas = []
    for i, seg in enumerate(segmentos, 1):
        lineas += [str(i), f"{_fmt_ts(seg['start_s'])} --> {_fmt_ts(seg['end_s'])}",
                   seg["texto"].strip(), ""]
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("\n".join(lineas), encoding="utf-8")
    return str(salida)


def _audio_como_array(path):
    """Decodifica cualquier audio/video a float32 16 kHz mono con FFmpeg (sin PyAV)."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                        "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                       capture_output=True, timeout=1800, creationflags=SIN_VENTANA)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"FFmpeg decodificando audio falló: {r.stderr.decode(errors='ignore')[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.float32)


def transcribir(path, modelo="tiny", idioma="es"):
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return {"disponible": False, "texto": None, "segmentos": [],
                "nota": "faster-whisper no está instalado en este trabajador; transcripción no disponible. "
                        "El resto del análisis (audio/visual) sí se realizó con señales reales."}
    try:
        m = WhisperModel(modelo, device="cpu", compute_type="int8")
        try:
            segmentos_raw, _info = m.transcribe(str(path), language=idioma)
            segmentos_raw = list(segmentos_raw)
            via = "ruta directa"
        except Exception:
            # PyAV 19 rompió la decodificación directa de faster-whisper 1.2.1:
            # se reintenta pasando el audio ya decodificado por FFmpeg como array.
            audio = _audio_como_array(path)
            segmentos_raw, _info = m.transcribe(audio, language=idioma)
            segmentos_raw = list(segmentos_raw)
            via = "fallback FFmpeg→array (PyAV incompatible con la ruta directa)"
        segmentos = [{"start_s": round(s.start, 3), "end_s": round(s.end, 3),
                      "texto": s.text.strip()} for s in segmentos_raw]
        texto = " ".join(s["texto"] for s in segmentos)
        return {"disponible": True, "texto": texto, "segmentos": segmentos,
                "nota": f"Transcrito con faster-whisper modelo {modelo} ({via})."}
    except Exception as e:
        return {"disponible": False, "texto": None, "segmentos": [],
                "nota": f"faster-whisper falló (¿modelo sin descargar o sin red?): {e}"}
