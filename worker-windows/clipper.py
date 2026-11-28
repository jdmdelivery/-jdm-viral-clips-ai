"""Generación/exportación de clips. Vertical 9:16 1080x1920 por defecto: la imagen
llena todo el cuadro (escala y recorte del sobrante), sin fondo borroso. También 16:9 y 1:1.
Subtítulos: NUNCA se queman en el MP4 exportado (aunque se pase srt).
La transcripción interna se conserva aparte para IA/títulos."""
import subprocess
from pathlib import Path
from procesos import SIN_VENTANA

FORMATOS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}

# Regla de Jose (2026-11-14, ajustada el 2026-11-23: «tiene que hacer
# short de un minuto y más»): todo corte dura entre 60 y 120 segundos,
# «depende» del momento. Ver contexto().
MIN_CLIP_S = 60.0
MAX_CLIP_S = 120.0

def _ventana_crop(src_w, src_h, out_w, out_h, centro=None, zoom=1.0):
    """Ventana de recorte (ancho, alto, x, y) en píxeles de la FUENTE
    para llenar el formato de salida, centrada en «centro» (x, y de la
    fuente; por defecto el centro del cuadro) y con «zoom» < 1 para
    acercar (la ventana se encoge alrededor del centro). Dimensiones
    pares y ventana siempre dentro del cuadro: matemática pura."""
    escala = max(out_w / src_w, out_h / src_h)
    base_w, base_h = out_w / escala, out_h / escala
    cw = max(16.0, min(float(src_w), base_w * zoom))
    ch = max(16.0, min(float(src_h), base_h * zoom))
    # Mantener la proporción de salida tras el zoom/recorte.
    prop = out_w / out_h
    if cw / ch > prop:
        ch = cw / prop
    else:
        cw = ch * prop
    if cw > src_w:
        cw = float(src_w); ch = cw / prop
    if ch > src_h:
        ch = float(src_h); cw = ch * prop
    cx, cy = centro if centro else (src_w / 2, src_h / 2)
    x = min(max(0.0, cx - cw / 2), src_w - cw)
    y = min(max(0.0, cy - ch / 2), src_h - ch)
    par = lambda v: max(2, int(round(v / 2.0)) * 2)
    cw, ch = par(cw), par(ch)
    # El redondeo a pares puede empujar la ventana 1-2 px fuera: se
    # re-encaja dentro del cuadro para que crop nunca falle.
    x = min(par(x) if x > 0 else 0, max(0, src_w - cw))
    y = min(par(y) if y > 0 else 0, max(0, src_h - ch))
    return cw, ch, x, y


def _datos_fuente(fuente):
    """(ancho, alto, hay_audio) de la fuente según ffprobe."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height", "-of", "csv=p=0", str(fuente)],
                       capture_output=True, text=True, timeout=60, creationflags=SIN_VENTANA)
    ancho = alto = 0
    audio = False
    for linea in (r.stdout or "").splitlines():
        partes = [p.strip() for p in linea.split(",")]
        if "video" in partes:
            nums = [p for p in partes if p.isdigit()]
            if len(nums) >= 2:
                ancho, alto = int(nums[0]), int(nums[1])
        if "audio" in partes:
            audio = True
    return ancho, alto, audio


ZOOM_GANCHO = 0.62   # acercamiento del gancho sobre la cara/el centro


def _exportar_con_gancho(fuente, inicio_s, fin_s, salida, formato, pico_s, gancho_s):
    """Exportación con GANCHO + ZOOM A LA CARA (v1.3.20, pedido de Jose
    el 2026-11-28: «que los videos sean más duros y más virales»):
    el clip abre con los «gancho_s» segundos del momento pico (con un
    zoom fuerte a la cara detectada en ese instante) y luego corre el
    clip completo con el encuadre centrado en esa misma cara (o el
    centro de siempre si no se detecta rostro). Una sola corrida de
    FFmpeg con las dos entradas y concat. Cualquier fallo burla esta
    vía y el llamador cae a la exportación clásica."""
    import vision
    w, h = FORMATOS[formato]
    src_w, src_h, hay_audio = _datos_fuente(fuente)
    if not src_w or not src_h:
        raise RuntimeError("Sin dimensiones de la fuente para el gancho.")
    centro = None
    ub = vision.ubicar_rostro(fuente, segundo=pico_s)
    if ub.get("disponible") and ub.get("rostro"):
        x, y, rw, rh = ub["rostro"]
        centro = (x + rw / 2, y + rh / 2)
    bw, bh, bx, by = _ventana_crop(src_w, src_h, w, h, centro, 1.0)
    hw, hh, hx, hy = _ventana_crop(src_w, src_h, w, h, centro, ZOOM_GANCHO)
    g_ini = min(max(float(pico_s) - 0.4, float(inicio_s)), max(float(inicio_s), float(fin_s) - gancho_s))
    g_fin = g_ini + gancho_s
    # setsar=1: las dos ventanas tienen proporciones casi idénticas
    # pero no exactas; sin normalizar el SAR, concat rechaza la mezcla.
    fc = (f"[0:v]crop={hw}:{hh}:{hx}:{hy},scale={w}:{h},setsar=1[v0];"
          f"[1:v]crop={bw}:{bh}:{bx}:{by},scale={w}:{h},setsar=1[v1];")
    if hay_audio:
        fc += "[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]"
        mapas = ["-map", "[v]", "-map", "[a]"]
    else:
        fc += "[v0][v1]concat=n=2:v=1:a=0[v]"
        mapas = ["-map", "[v]"]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", str(g_ini), "-to", str(g_fin), "-i", str(fuente),
           "-ss", str(inicio_s), "-to", str(fin_s), "-i", str(fuente),
           "-filter_complex", fc, *mapas,
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "48000", "-shortest", str(salida)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, creationflags=SIN_VENTANA)
    if r.returncode != 0 or not Path(salida).exists():
        raise RuntimeError(f"FFmpeg con gancho falló: {r.stderr[-400:]}")
    return str(salida)


def exportar_clip(fuente, inicio_s, fin_s, salida, formato="9:16", subtitulos=False, srt=None,
                  pico_s=None, gancho_s=2.2):
    if fin_s <= inicio_s: raise ValueError("fin debe ser mayor que inicio")
    # subtitulos/srt se ignoran a propósito: no quemar SRT/ASS/VTT.
    _ = (subtitulos, srt)
    # Gancho + zoom (v1.3.20): solo si el llamador marca el pico y la
    # ventana da para ello; si la vía nueva falla, se cae a la clásica.
    if pico_s is not None and (fin_s - inicio_s) >= 10.0:
        try:
            return _exportar_con_gancho(fuente, inicio_s, fin_s, salida, formato,
                                        float(pico_s), float(gancho_s))
        except Exception:
            pass
    w, h = FORMATOS[formato]
    fc = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[v]"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", str(inicio_s), "-to", str(fin_s), "-i", str(fuente),
           "-filter_complex", fc, "-map", "[v]", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "48000", "-shortest", str(salida)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, creationflags=SIN_VENTANA)
    if r.returncode != 0 or not Path(salida).exists():
        raise RuntimeError(f"FFmpeg exportando clip falló: {r.stderr[-600:]}")
    return str(salida)

def miniatura(fuente, segundo, salida):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(segundo),
                        "-i", str(fuente), "-frames:v", "1", str(salida)],
                       capture_output=True, text=True, timeout=120, creationflags=SIN_VENTANA)
    return str(salida) if r.returncode == 0 and Path(salida).exists() else None

LARGOS_ELEGIBLES = (60, 75, 90, 120)

def ventana_solicitada(evento_inicio, evento_fin, duracion_total, largo_s=None, pre_s=30, post_s=25):
    """Si largo_s es 60, 75, 90 o 120, el clip dura eso (centrado en el momento
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
                           capture_output=True, text=True, timeout=30, creationflags=SIN_VENTANA)
        return r.returncode == 0 and "video" in (r.stdout or "")
    except Exception:
        return False

def contexto(evento_inicio, evento_fin, duracion_total, pre_s=30, post_s=25):
    """Ventana del clip: el evento con pre_s de contexto antes y post_s después,
    ajustada a la regla de duración de Jose (60–120 s «depende» del momento):
    - Si el video completo dura menos de 60 s, el clip es el video entero
      (no se inventa duración que no existe).
    - Ventana base = evento + pre + post. Si queda por debajo de 60 s, se
      extiende primero por el post y luego por el pre hasta llegar a 60 s
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
