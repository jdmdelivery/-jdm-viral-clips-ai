"""Generación/exportación de clips. Vertical 9:16 1080x1920 por defecto con fondo
desenfocado (el video completo visible, sin recortes agresivos), también 16:9 y 1:1.
Subtítulos DESACTIVADOS por defecto: solo se queman si el usuario pasa un .srt y lo activa."""
import subprocess
from pathlib import Path

FORMATOS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}

def exportar_clip(fuente, inicio_s, fin_s, salida, formato="9:16", subtitulos=False, srt=None):
    if fin_s <= inicio_s: raise ValueError("fin debe ser mayor que inicio")
    w, h = FORMATOS[formato]
    fc = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=40[bg];"
          f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", str(inicio_s), "-to", str(fin_s), "-i", str(fuente),
           "-filter_complex", fc, "-map", "[v]", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "48000", "-shortest"]
    if subtitulos and srt and Path(srt).exists():
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-ss", str(inicio_s), "-to", str(fin_s), "-i", str(fuente),
               "-filter_complex", fc + f";[v]subtitles={srt}[v2]", "-map", "[v2]", "-map", "0:a:0?",
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-ar", "48000", "-shortest"]
    cmd.append(str(salida))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not Path(salida).exists():
        raise RuntimeError(f"FFmpeg exportando clip falló: {r.stderr[-600:]}")
    return str(salida)

def miniatura(fuente, segundo, salida):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(segundo),
                        "-i", str(fuente), "-frames:v", "1", str(salida)],
                       capture_output=True, text=True, timeout=120)
    return str(salida) if r.returncode == 0 and Path(salida).exists() else None

def contexto(evento_inicio, evento_fin, duracion_total, pre_s=20, post_s=15):
    """Clip con contexto: pre_s antes y post_s después, sin salir del archivo."""
    return max(0.0, evento_inicio - pre_s), min(duracion_total, evento_fin + post_s)
