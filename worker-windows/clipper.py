"""Generación/exportación de clips. Vertical 9:16 1080x1920 por defecto: la imagen
llena todo el cuadro (escala y recorte del sobrante), sin fondo borroso. También 16:9 y 1:1.
Subtítulos DESACTIVADOS por defecto: solo se queman si el usuario pasa un .srt y lo activa."""
import subprocess
from pathlib import Path

FORMATOS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}

# Regla de Jose (2026-11-14): todo corte dura entre 30 y 120 segundos,
# «depende» del momento. Ver contexto().
MIN_CLIP_S = 30.0
MAX_CLIP_S = 120.0

def exportar_clip(fuente, inicio_s, fin_s, salida, formato="9:16", subtitulos=False, srt=None):
    if fin_s <= inicio_s: raise ValueError("fin debe ser mayor que inicio")
    w, h = FORMATOS[formato]
    fc = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[v]"
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

LARGOS_ELEGIBLES = (3, 5, 10, 15, 20)

def ventana_solicitada(evento_inicio, evento_fin, duracion_total, largo_s=None, pre_s=20, post_s=15):
    """Si largo_s es 3, 5, 10, 15 o 20, el clip dura eso (centrado en el momento
    y recortado al video real). Si no, se usa la ventana automática de contexto()."""
    if largo_s in LARGOS_ELEGIBLES:
        total = float(duracion_total)
        largo = float(largo_s)
        centro = (float(evento_inicio) + float(evento_fin)) / 2
        ini = max(0.0, centro - largo / 2)
        fin = min(total, ini + largo)
        ini = max(0.0, fin - largo)
        if fin - ini < 0.4:
            raise ValueError("El video es demasiado corto para generar ese clip.")
        return ini, fin
    return contexto(evento_inicio, evento_fin, duracion_total, pre_s, post_s)

def clip_tiene_video(path):
    """True si el MP4 existe, pesa algo y ffprobe ve una pista de video."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 1024:
        return False
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(p)],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0 and "video" in (r.stdout or "")
    except Exception:
        return False

def contexto(evento_inicio, evento_fin, duracion_total, pre_s=20, post_s=15):
    """Ventana del clip: el evento con pre_s de contexto antes y post_s después,
    ajustada a la regla de duración de Jose (30–120 s «depende» del momento):
    - Si el video completo dura menos de 30 s, el clip es el video entero
      (no se inventa duración que no existe).
    - Ventana base = evento + pre + post. Si queda por debajo de 30 s, se
      extiende primero por el post y luego por el pre hasta llegar a 30 s
      o agotar el video.
    - Si se pasa de 120 s, se recorta primero el post, luego el pre y por
      último la cola del evento; el inicio del evento nunca se mueve."""
    total = float(duracion_total)
    if total <= MIN_CLIP_S:
        return 0.0, total
    inicio = max(0.0, float(evento_inicio) - pre_s)
    fin = min(total, float(evento_fin) + post_s)
    dur = fin - inicio
    if dur < MIN_CLIP_S:
        falta = MIN_CLIP_S - dur
        extra_post = min(falta, total - fin)
        fin += extra_post; falta -= extra_post
        if falta > 0:
            extra_pre = min(falta, inicio)
            inicio -= extra_pre
    elif dur > MAX_CLIP_S:
        sobra = dur - MAX_CLIP_S
        # 1) recortar el post: del final de la ventana hacia el fin del evento
        recorte = min(sobra, max(0.0, fin - float(evento_fin)))
        fin -= recorte; sobra -= recorte
        # 2) recortar el pre: del inicio del evento hacia el inicio de la ventana
        if sobra > 0:
            recorte = min(sobra, max(0.0, float(evento_inicio) - inicio))
            inicio += recorte; sobra -= recorte
        # 3) recortar la cola del evento (su inicio queda intacto)
        if sobra > 0:
            fin -= sobra
    return inicio, fin
