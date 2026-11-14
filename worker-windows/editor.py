"""Editor de clips (Etapa 3, lado trabajador): edición REAL con FFmpeg.

aplicar_edicion(fuente, salida, params) soporta:
  - recorte: trim_start_s / trim_end_s (segundos dentro del clip fuente)
  - reencuadre: formato ("9:16"/"16:9"/"1:1", reutiliza la composición de
    clipper con fondo desenfocado), zoom (factor >= 1, crop central) y
    offset_x / offset_y en -1..1 que desplazan la ventana de crop
  - volumen: volumen_factor (p. ej. 0.5) o volumen_db (p. ej. -6)
  - texto: texto + texto_activo (drawtext con fuente del sistema; posición
    texto_posicion "arriba"/"centro"/"abajo" y tamaño texto_tamano)
  - logo: logo_path existente -> overlay escalado en una esquina
    (logo_esquina, logo_margen, logo_ancho)
  - subtítulos: SOLO si subtitulos es True. Se queman desde `transcript`
    (se construye un .srt partiendo el texto en frases sobre la duración) o
    desde transcribe.py si está disponible. Sin flag, JAMÁS se queman. Si no
    hay forma real de obtenerlos, se exporta sin subtítulos y el resultado
    lo dice (subtitles: False + nota).
  - miniatura: clipper.miniatura en miniatura_segundo (defecto 1 s)
Devuelve {file_path, duration_s (ffprobe), format, subtitles, thumb_path, nota}.
"""
import re, subprocess, tempfile
from pathlib import Path

from clipper import FORMATOS, miniatura

FUENTES_CANDIDATAS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _duracion(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
    return float(r.stdout.strip())


def _dimensiones(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                        str(path)], capture_output=True, text=True, timeout=60)
    try:
        w, h = r.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return None, None


def _tiene_audio(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True, timeout=60)
    return bool(r.stdout.strip())


def _fuente_sistema():
    for f in FUENTES_CANDIDATAS:
        if Path(f).exists():
            return f
    try:
        r = subprocess.run(["fc-list", ":style=Bold", "file"], capture_output=True, text=True, timeout=30)
        for linea in r.stdout.splitlines():
            p = linea.split(":")[0].strip()
            if p.endswith(".ttf"):
                return p
    except Exception:
        pass
    return None


def _esc_ruta_filtro(p):
    """Escapa una ruta para usarla dentro de un filtro de FFmpeg."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _fmt_ts(segundos):
    ms = int(round(max(0.0, segundos) * 1000))
    h, resto = divmod(ms, 3600000)
    m, resto = divmod(resto, 60000)
    s, ms = divmod(resto, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def construir_srt_desde_texto(texto, duracion_s, salida_srt):
    """Parte el texto en frases (o trozos de ~7 palabras) y reparte la duración
    proporcionalmente a la longitud de cada cue. Devuelve la ruta del .srt."""
    frases = [f.strip() for f in re.split(r"(?<=[.!?…])\s+", str(texto).strip()) if f.strip()]
    if not frases:
        return None
    if len(frases) == 1 and len(frases[0].split()) > 8:
        palabras = frases[0].split()
        frases = [" ".join(palabras[i:i + 7]) for i in range(0, len(palabras), 7)]
    total_chars = sum(len(f) for f in frases) or 1
    lineas, cursor = [], 0.0
    for i, f in enumerate(frases):
        tramo = duracion_s * (len(f) / total_chars)
        ini, fin = cursor, (duracion_s if i == len(frases) - 1 else cursor + tramo)
        lineas += [str(i + 1), f"{_fmt_ts(ini)} --> {_fmt_ts(fin)}", f, ""]
        cursor = fin
    salida_srt = Path(salida_srt)
    salida_srt.parent.mkdir(parents=True, exist_ok=True)
    salida_srt.write_text("\n".join(lineas), encoding="utf-8")
    return str(salida_srt)


def aplicar_edicion(fuente, salida, params=None):
    params = dict(params or {})
    fuente, salida = Path(fuente), Path(salida)
    if not fuente.exists():
        raise FileNotFoundError(f"Fuente no existe: {fuente}")
    salida.parent.mkdir(parents=True, exist_ok=True)
    dur_fuente = _duracion(fuente)
    ini = max(0.0, float(params.get("trim_start_s", 0) or 0))
    fin_param = params.get("trim_end_s")
    fin = min(dur_fuente, float(fin_param)) if fin_param is not None else dur_fuente
    if fin <= ini:
        raise ValueError("trim_end_s debe ser mayor que trim_start_s")
    dur_clip = fin - ini
    notas = []

    formato = params.get("formato") or params.get("format") or params.get("formato_destino")
    if formato and formato not in FORMATOS:
        raise ValueError(f"Formato no soportado: {formato} (usa {sorted(FORMATOS)})")
    zoom = max(1.0, float(params.get("zoom", 1.0) or 1.0))
    off_x = max(-1.0, min(1.0, float(params.get("offset_x", 0) or 0)))
    off_y = max(-1.0, min(1.0, float(params.get("offset_y", 0) or 0)))

    tmp = Path(tempfile.mkdtemp(prefix="jdm-editor-"))
    # --- Cadena de video ---
    cadena = []
    entrada_v = "[0:v]"
    if zoom > 1.0 or off_x or off_y:
        # Ventana de crop de tamaño iw/zoom x ih/zoom, centrada y desplazada
        # por offset (-1..1 = del centro a cada borde).
        cadena.append(
            f"{entrada_v}crop=w='iw/{zoom}':h='ih/{zoom}':"
            f"x='(iw-iw/{zoom})/2+({off_x})*(iw-iw/{zoom})/2':"
            f"y='(ih-ih/{zoom})/2+({off_y})*(ih-ih/{zoom})/2'[vz]")
        entrada_v = "[vz]"
    if formato:
        w, h = FORMATOS[formato]
        cadena.append(f"{entrada_v}scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=40[bg]")
        cadena.append(f"{entrada_v}scale={w}:{h}:force_original_aspect_ratio=decrease[fg]")
        cadena.append("[bg][fg]overlay=(W-w)/2:(H-h)/2[vref]")
        entrada_v = "[vref]"
    else:
        cadena.append(f"{entrada_v}null[vref]")
        entrada_v = "[vref]"

    # --- Subtítulos (solo con flag explícito) ---
    subtitulos_ok = False
    if params.get("subtitulos") is True:
        srt = None
        texto_tr = params.get("transcript") or params.get("transcripcion")
        if isinstance(texto_tr, dict):
            texto_tr = texto_tr.get("texto")
        if texto_tr:
            srt = construir_srt_desde_texto(texto_tr, dur_clip, tmp / "subs.srt")
        else:
            try:
                from transcribe import transcribir, generar_srt
                tr = transcribir(fuente)
                if tr.get("disponible") and tr.get("segmentos"):
                    segs = [{"start_s": max(0.0, s["start_s"] - ini),
                             "end_s": min(dur_clip, s["end_s"] - ini), "texto": s["texto"]}
                            for s in tr["segmentos"] if s["end_s"] > ini and s["start_s"] < fin]
                    srt = generar_srt(segs, tmp / "subs.srt")
            except Exception as e:
                notas.append(f"Transcripción para subtítulos no disponible: {e}")
        if srt:
            cadena.append(f"{entrada_v}subtitles='{_esc_ruta_filtro(srt)}'[vsub]")
            entrada_v = "[vsub]"
            subtitulos_ok = True
        else:
            notas.append("subtitulos=True pero no hubo texto ni transcripción real disponible; "
                         "se exporta SIN subtítulos.")
    # Por defecto (sin flag) nunca se queman subtítulos.

    # --- Texto (drawtext) ---
    if params.get("texto") and params.get("texto_activo", True):
        fuente_ttf = _fuente_sistema()
        if fuente_ttf:
            txt_file = tmp / "texto.txt"
            txt_file.write_text(str(params["texto"]), encoding="utf-8")
            tam = int(params.get("texto_tamano", 48))
            pos = str(params.get("texto_posicion", "abajo")).lower()
            y = {"arriba": "40", "centro": "(h-text_h)/2"}.get(pos, "h-text_h-40")
            cadena.append(
                f"{entrada_v}drawtext=fontfile='{_esc_ruta_filtro(fuente_ttf)}':"
                f"textfile='{_esc_ruta_filtro(txt_file)}':fontsize={tam}:fontcolor=white:"
                f"borderw=3:bordercolor=black:x=(w-text_w)/2:y={y}[vtxt]")
            entrada_v = "[vtxt]"
        else:
            notas.append("No se encontró fuente del sistema para drawtext; texto omitido.")

    # --- Logo (overlay) ---
    logo = params.get("logo_path")
    usar_logo = bool(logo and Path(logo).exists())
    if logo and not usar_logo:
        notas.append(f"logo_path no existe ({logo}); logo omitido.")
    if usar_logo:
        ancho_logo = int(params.get("logo_ancho", 180))
        margen = int(params.get("logo_margen", 20))
        esquina = str(params.get("logo_esquina", "superior_derecha")).lower()
        pos = {"superior_izquierda": (f"{margen}", f"{margen}"),
               "inferior_derecha": (f"W-w-{margen}", f"H-h-{margen}"),
               "inferior_izquierda": (f"{margen}", f"H-h-{margen}")}.get(
                   esquina, (f"W-w-{margen}", f"{margen}"))
        cadena.append(f"[1:v]scale={ancho_logo}:-1[logo]")
        cadena.append(f"{entrada_v}[logo]overlay={pos[0]}:{pos[1]}[vlogo]")
        entrada_v = "[vlogo]"

    cadena.append(f"{entrada_v}format=yuv420p[vout]")

    # --- Audio / volumen ---
    tiene_audio = _tiene_audio(fuente)
    filtro_vol = None
    if params.get("volumen_db") is not None:
        filtro_vol = f"volume={float(params['volumen_db'])}dB"
    elif params.get("volumen_factor") is not None:
        filtro_vol = f"volume={float(params['volumen_factor'])}"
    elif params.get("volumen") is not None:
        filtro_vol = f"volume={float(params['volumen'])}"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-ss", str(ini), "-to", str(fin), "-i", str(fuente)]
    if usar_logo:
        cmd += ["-i", str(logo)]
    cmd += ["-filter_complex", ";".join(cadena), "-map", "[vout]"]
    if tiene_audio:
        if filtro_vol:
            cmd += ["-map", "0:a:0", "-af", filtro_vol]
        else:
            cmd += ["-map", "0:a:0?"]
        cmd += ["-c:a", "aac", "-ar", "48000"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-shortest", str(salida)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0 or not salida.exists():
        raise RuntimeError(f"FFmpeg editando falló: {r.stderr[-800:]}")

    dur_out = _duracion(salida)
    w_out, h_out = _dimensiones(salida)
    formato_out = formato or (f"{w_out}x{h_out}" if w_out else "desconocido")
    thumb = miniatura(salida, float(params.get("miniatura_segundo", 1)),
                      salida.with_suffix(".jpg"))
    return {"file_path": str(salida), "duration_s": round(dur_out, 2),
            "format": formato_out, "subtitles": subtitulos_ok,
            "thumb_path": thumb, "nota": " ".join(notas) if notas else "Edición aplicada con FFmpeg."}
