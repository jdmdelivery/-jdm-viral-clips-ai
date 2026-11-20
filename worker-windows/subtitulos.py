"""Subtítulos premium locales para los Shorts de JDM Viral Clips AI.

El clip original no se modifica. La transcripción usa faster-whisper en esta
PC (sin API de pago). Si el audio no se entiende, no se inventa texto: el
original queda intacto y el error se guarda junto al clip.

La cola corre en un hilo de baja prioridad y no entra en el bucle de
grabación ni en el radar.
"""
import json, os, subprocess, threading, time
from pathlib import Path
from procesos import SIN_VENTANA, flags as _flags_proceso

ROJO = "2020FF"    # #FF2020 en BGR de ASS
AZUL = "FFBF00"    # #00BFFF en BGR de ASS
BLANCO = "FFFFFF"
CONTORNO = "000000"

# Palabras de énfasis habituales en conversación (incluido español dominicano).
# El resto del texto queda blanco; estas se resaltan solo mientras se dicen.
IMPORTANTES = {
    "no", "nunca", "jamás", "jamas", "siempre", "mira", "oyo", "oye",
    "dios", "dinero", "amor", "brutal", "loco", "loca", "increíble",
    "increible", "ahora", "ya", "alto", "fuego", "viral", "qué", "que",
    "wow", "bro", "tigre", "duro", "dura", "clave", "grave", "serio",
    "seria", "verdad", "mentira", "nadie", "todos", "todo", "nada",
    "sí", "si", "exacto", "exactamente", "ojo", "cuidado",
    "paro", "viva", "vivo", "grande", "mejor", "peor",
}
# «que» y «si» son demasiado comunes como conjunción: solo se pintan de
# rojo cuando traen tilde o van solos como respuesta. Se filtran abajo.
IMPORTANTES_ESTRICTAS = {"que", "si", "para"}
VACIAS = {
    "el", "la", "los", "las", "de", "del", "al", "a", "en", "un", "una",
    "unos", "unas", "y", "o", "u", "con", "por", "se", "su", "sus", "le",
    "les", "lo", "me", "te", "nos", "es", "son", "está", "esta", "están",
    "estan", "como", "más", "mas", "pero", "porque", "eso", "esto", "ese",
    "esa", "hay", "muy", "tan", "ya",
}
FRASES_FALSAS = (
    "subtítulos realizados", "subtitulos realizados", "gracias por ver",
    "amara.org", "suscríbete", "suscribete", "subtítulos por",
    "subtitulos por",
)
PROMPT = "Conversación en español, incluido español dominicano."
_MODELO = None
_MODELO_NOMBRE = None
_COLA = None


def rutas_de(origen):
    """Archivos derivados, siempre al lado del original y con otro nombre."""
    p = Path(origen)
    return {
        "json": p.with_name(p.stem + ".subtitulos.json"),
        "ass": p.with_name(p.stem + ".subtitulos.ass"),
        "mp4": p.with_name(p.stem + ".subtitulado.mp4"),
    }


def es_clip_original(path):
    """True solo para un MP4 de clip ya cerrado. Ignora segmentos, temporales
    del radar, la grabación completa y la propia versión subtitulada."""
    p = Path(path)
    if p.suffix.lower() != ".mp4":
        return False
    nombre = p.name.lower()
    if nombre.endswith(".subtitulado.mp4") or nombre.endswith(".part"):
        return False
    if nombre.startswith(("seg_", "ventana-", "concat")):
        return False
    if "grabacion_completa" in nombre:
        return False
    partes = {x.lower() for x in p.parts}
    if "segmentos" in partes or "_radar" in partes:
        return False
    return True


def _bajar_prioridad():
    """Este hilo cede la CPU a la grabación (proceso aparte, prioridad normal)."""
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.SetThreadPriority(kernel.GetCurrentThread(), -15)  # IDLE
    except Exception:
        pass


def _ffmpeg_baja_prioridad():
    # Baja prioridad Y sin ventana: con solo BELOW_NORMAL_PRIORITY_CLASS,
    # cada ffmpeg de subtítulos abría su consola negra en el escritorio
    # (el motor corre con pythonw, sin consola propia). Ver procesos.py.
    if os.name == "nt":
        return _flags_proceso(0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
    return 0


def _audio_como_array(path):
    import numpy as np
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, timeout=1800, creationflags=_ffmpeg_baja_prioridad())
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("FFmpeg no pudo extraer el audio: "
                           + r.stderr.decode(errors="ignore")[-300:])
    return np.frombuffer(r.stdout, dtype=np.float32)


def _tiene_audio(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30, creationflags=SIN_VENTANA)
        return r.returncode == 0 and "audio" in (r.stdout or "")
    except Exception:
        return False


def _dimensiones(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30, creationflags=SIN_VENTANA)
        if r.returncode == 0 and "," in (r.stdout or ""):
            w, h = r.stdout.strip().split(",")[:2]
            return max(2, int(w)), max(2, int(h))
    except Exception:
        pass
    return 1080, 1920


def _modelo(nombre):
    global _MODELO, _MODELO_NOMBRE
    if _MODELO is not None and _MODELO_NOMBRE == nombre:
        return _MODELO
    from faster_whisper import WhisperModel
    _MODELO = WhisperModel(nombre, device="cpu", compute_type="int8", cpu_threads=1, num_workers=1)
    _MODELO_NOMBRE = nombre
    return _MODELO


def _palabra_util(texto, prob):
    t = (texto or "").strip()
    if not t or prob is not None and prob < 0.35:
        return ""
    limpio = t.strip(".,;:!?¡¿\"'()[]").lower()
    if not limpio:
        return ""
    if any(f in limpio for f in FRASES_FALSAS):
        return ""
    return t.strip()


def _transcribir_una(modelo, audio, idioma):
    kwargs = dict(
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        condition_on_previous_text=False,
        temperature=0.0,
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        hallucination_silence_threshold=2.0,
        initial_prompt=PROMPT,
        beam_size=5,
        without_timestamps=False,
    )
    if idioma:
        kwargs["language"] = idioma
    segmentos, info = modelo.transcribe(audio, **kwargs)
    palabras = []
    for seg in segmentos:
        if getattr(seg, "no_speech_prob", 0) > 0.6:
            continue
        if getattr(seg, "avg_logprob", 0) < -1.05:
            continue
        texto_seg = (seg.text or "").strip().lower()
        if texto_seg == PROMPT.lower() or any(f in texto_seg for f in FRASES_FALSAS):
            continue
        for w in (seg.words or []):
            texto = _palabra_util(w.word, getattr(w, "probability", 1))
            if not texto:
                continue
            ini = float(w.start or 0)
            fin = float(w.end or ini)
            if fin <= ini:
                fin = ini + 0.12
            palabras.append({"t": texto, "s": round(ini, 3), "e": round(fin, 3),
                             "p": round(float(getattr(w, "probability", 1) or 0), 3)})
    idioma_detectado = getattr(info, "language", None) or idioma or ""
    return palabras, idioma_detectado


def transcribir(path, modelo="tiny"):
    """Devuelve palabras con tiempo. No rellena texto si no hubo habla clara."""
    if not _tiene_audio(path):
        return {"ok": False, "idioma": "", "palabras": [],
                "error": "El clip no tiene pista de audio. Se conservó el original."}
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except Exception:
        return {"ok": False, "idioma": "", "palabras": [],
                "error": "faster-whisper no está instalado en esta PC. Se conservó el original."}
    try:
        m = _modelo(modelo)
        try:
            palabras, idioma = _transcribir_una(m, str(path), None)
            via = "archivo"
        except Exception:
            audio = _audio_como_array(path)
            palabras, idioma = _transcribir_una(m, audio, None)
            via = "ffmpeg"
        if not palabras:
            # Segundo intento fijo en español: a veces la detección automática
            # se queda callada en un clip corto. Si tampoco hay palabras, no
            # se inventa ninguna.
            try:
                palabras, idioma = _transcribir_una(m, str(path), "es")
            except Exception:
                audio = _audio_como_array(path)
                palabras, idioma = _transcribir_una(m, audio, "es")
            via = via + "+es"
        if not palabras:
            return {"ok": False, "idioma": idioma or "", "palabras": [],
                    "error": "No se entendió el habla de este clip. No se inventó texto. "
                             "Se conservó el original."}
        return {"ok": True, "idioma": idioma or "", "palabras": palabras, "error": "", "via": via}
    except Exception as e:
        return {"ok": False, "idioma": "", "palabras": [],
                "error": f"La transcripción falló ({type(e).__name__}: {e}). Se conservó el original."[:500]}


def color_palabra(texto, indice_en_grupo):
    """Rojo = énfasis. Azul = palabra destacada. Blanco = texto principal."""
    crudo = (texto or "").strip()
    base = crudo.strip(".,;:!?¡¿\"'()[]").lower()
    if not base:
        return "blanco"
    if any(ch.isdigit() for ch in base) or "!" in crudo or "¡" in crudo:
        return "rojo"
    if base in IMPORTANTES and base not in IMPORTANTES_ESTRICTAS:
        return "rojo"
    if base in ("qué", "sí"):
        return "rojo"
    if indice_en_grupo > 0 and crudo[:1].isupper() and base not in VACIAS:
        return "azul"
    if len(base) >= 7 and base not in VACIAS and indice_en_grupo % 2 == 1:
        return "azul"
    return "blanco"


def agrupar(palabras):
    """Bloques de 3 a 5 palabras. Una pausa corta cierra el bloque en 3."""
    grupos = []
    actual = []
    for w in palabras:
        if not actual:
            actual = [w]
            continue
        pausa = float(w["s"]) - float(actual[-1]["e"])
        if len(actual) >= 5 or (len(actual) >= 3 and pausa > 0.35) or pausa > 0.55:
            grupos.append(actual)
            actual = [w]
        else:
            actual.append(w)
    if actual:
        grupos.append(actual)
    return grupos


def aplicar_correccion(palabras, texto, duracion_s=0):
    """Reemplaza el texto conservando los tiempos. Si cambia la cantidad de
    palabras, reparte el mismo tramo hablado. No inventa tiempos fuera del clip."""
    nuevas = [t for t in (texto or "").split() if t.strip()]
    if not nuevas:
        return []
    if palabras and len(nuevas) == len(palabras):
        return [{**w, "t": n} for n, w in zip(nuevas, palabras)]
    if palabras:
        ini = float(palabras[0]["s"])
        fin = float(palabras[-1]["e"])
    else:
        ini, fin = 0.2, max(0.8, float(duracion_s or 1) - 0.2)
    pesos = [max(1, len(t)) for t in nuevas]
    total = sum(pesos) or 1
    dur = max(0.24, fin - ini)
    cursor = ini
    out = []
    for i, (n, peso) in enumerate(zip(nuevas, pesos)):
        trozo = dur * peso / total
        fin_w = fin if i == len(nuevas) - 1 else cursor + trozo
        out.append({"t": n, "s": round(cursor, 3), "e": round(max(cursor + 0.08, fin_w), 3), "p": 1.0})
        cursor = fin_w
    return out


def _ass_tiempo(segundos):
    ms = int(round(max(0.0, float(segundos)) * 100))
    h, resto = divmod(ms, 360000)
    m, resto = divmod(resto, 6000)
    s, cs = divmod(resto, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc_ass(texto):
    return (texto or "").replace("\\", "").replace("{", "").replace("}", "").replace("\n", " ")


def _linea(grupo, activo):
    trozos = []
    for i, w in enumerate(grupo):
        palabra = _esc_ass(w["t"])
        if i == activo:
            color = {"rojo": ROJO, "azul": AZUL}.get(color_palabra(w["t"], i), BLANCO)
            trozos.append(f"{{\\c&H{color}&\\fscx108\\fscy108}}{palabra}{{\\r}}")
        else:
            trozos.append(f"{{\\c&H{BLANCO}&}}{palabra}{{\\r}}")
    return " ".join(trozos)


def escribir_ass(palabras, salida, ancho=1080, alto=1920):
    """ASS con entrada suave, resalte de la palabra dicha y un pop corto."""
    grupos = agrupar(palabras)
    alto = max(2, int(alto))
    ancho = max(2, int(ancho))
    tam = max(42, int(alto * 0.036))
    margen_v = max(220, int(alto * 0.20))
    margen_h = max(48, int(ancho * 0.06))
    lineas = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {ancho}",
        f"PlayResY: {alto}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Premium,Arial Black,{tam},&H00{BLANCO},&H00{BLANCO},&H00{CONTORNO},&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,8,2,2,{margen_h},{margen_h},{margen_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for grupo in grupos:
        for i, w in enumerate(grupo):
            ini = float(w["s"])
            fin = float(grupo[i + 1]["s"]) if i + 1 < len(grupo) else float(w["e"])
            if fin < ini + 0.08:
                fin = ini + 0.12
            pop = ""
            if i == 0:
                pop = "{\\fad(140,80)\\t(0,150,\\fscx110\\fscy110)\\t(150,280,\\fscx100\\fscy100)}"
            lineas.append(
                f"Dialogue: 0,{_ass_tiempo(ini)},{_ass_tiempo(fin)},Premium,,0,0,0,,{pop}{_linea(grupo, i)}")
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return str(salida)


def _esc_filtro(ruta):
    texto = Path(ruta).resolve().as_posix().replace(":", "\\:")
    return texto.replace("'", "\\'")


def quemar(origen, ass, destino):
    """Escribe el MP4 subtitulado en un archivo nuevo. Nunca pisa el original."""
    origen = Path(origen).resolve()
    destino = Path(destino).resolve()
    if destino == origen:
        raise RuntimeError("La salida subtitulada no puede ser el clip original.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.stem + ".part.mp4")
    if parcial.exists():
        parcial.unlink()
    fuentes = _esc_filtro(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    filtro = f"ass='{_esc_filtro(ass)}':fontsdir='{fuentes}'"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(origen),
           "-vf", filtro, "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-movflags", "+faststart", str(parcial)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                       creationflags=_ffmpeg_baja_prioridad())
    if r.returncode != 0:
        cmd_audio = cmd[:-1] + ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(parcial)]
        # El comando anterior ya incluye -c:a copy; se reconstruye si el copy falla.
        cmd_audio = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(origen),
                     "-vf", filtro, "-map", "0:v:0", "-map", "0:a:0?",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(parcial)]
        r = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=3600,
                           creationflags=_ffmpeg_baja_prioridad())
    if r.returncode != 0 or not parcial.is_file() or parcial.stat().st_size < 1024:
        parcial.unlink(missing_ok=True)
        raise RuntimeError("FFmpeg no pudo quemar los subtítulos: " + (r.stderr or "")[-400:])
    parcial.replace(destino)
    if not origen.is_file():
        raise RuntimeError("El clip original desapareció durante el subtitulado.")
    return str(destino)


def _guardar_json(ruta, datos):
    ruta = Path(ruta)
    tmp = ruta.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ruta)


def _leer_json(ruta):
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except Exception:
        return {}


def procesar_clip(origen, modelo="tiny", forzar=False):
    """Transcribe y, si hay palabras reales, prepara el MP4 subtitulado.
    Un fallo deja el original donde estaba y anota el error en el JSON."""
    origen = Path(origen)
    rutas = rutas_de(origen)
    if not origen.is_file():
        return {"ok": False, "error": "El clip original no está en esta PC.", "origen": str(origen)}
    previo = _leer_json(rutas["json"])
    if (not forzar and previo.get("estado") == "listo" and rutas["mp4"].is_file()
            and rutas["mp4"].stat().st_mtime >= origen.stat().st_mtime):
        return {"ok": True, "omitido": True, **previo, "origen": str(origen),
                "subtitulado": str(rutas["mp4"])}
    _guardar_json(rutas["json"], {**previo, "estado": "procesando", "origen": str(origen),
                                  "error": "", "actualizado": time.time()})
    try:
        tr = transcribir(origen, modelo=modelo)
        if not tr.get("ok"):
            datos = {"estado": "sin_habla" if "No se entendió" in tr.get("error", "") or "no tiene pista" in tr.get("error", "") else "error",
                     "idioma": tr.get("idioma") or "", "error": tr.get("error") or "Transcripción no disponible.",
                     "texto": "", "palabras": [], "origen": str(origen), "actualizado": time.time()}
            _guardar_json(rutas["json"], datos)
            return {"ok": False, **datos}
        ancho, alto = _dimensiones(origen)
        escribir_ass(tr["palabras"], rutas["ass"], ancho, alto)
        quemar(origen, rutas["ass"], rutas["mp4"])
        texto = " ".join(w["t"] for w in tr["palabras"])
        datos = {"estado": "listo", "idioma": tr.get("idioma") or "", "error": "",
                 "texto": texto, "palabras": tr["palabras"], "origen": str(origen),
                 "subtitulado": str(rutas["mp4"]), "via": tr.get("via") or "",
                 "actualizado": time.time()}
        _guardar_json(rutas["json"], datos)
        return {"ok": True, **datos}
    except Exception as e:
        datos = {"estado": "error", "error": f"No se pudieron preparar los subtítulos ({e}). Se conservó el original."[:500],
                 "texto": "", "palabras": previo.get("palabras") or [], "origen": str(origen),
                 "actualizado": time.time()}
        try:
            _guardar_json(rutas["json"], datos)
        except Exception:
            pass
        return {"ok": False, **datos}


def aplicar_texto(origen, texto, palabras=None):
    """Quema el texto indicado sin volver a transcribir y sin tocar el original."""
    origen = Path(origen)
    rutas = rutas_de(origen)
    texto = " ".join((texto or "").split())
    previo = _leer_json(rutas["json"])
    if not texto:
        return {"ok": False, "error": "No hay texto para los subtítulos.", "origen": str(origen)}
    if not origen.is_file():
        return {"ok": False, "error": "El clip original no está en esta PC.", "origen": str(origen)}
    base = list(palabras) if palabras is not None else list(previo.get("palabras") or [])
    try:
        dur = 0
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(origen)], capture_output=True, text=True, timeout=30, creationflags=SIN_VENTANA)
            dur = float((r.stdout or "0").strip() or 0)
        except Exception:
            dur = 0
        nuevas = aplicar_correccion(base, texto, dur)
        if not nuevas:
            return {"ok": False, "error": "El texto corregido quedó vacío.", "origen": str(origen)}
        ancho, alto = _dimensiones(origen)
        escribir_ass(nuevas, rutas["ass"], ancho, alto)
        quemar(origen, rutas["ass"], rutas["mp4"])
        datos = {"estado": "listo", "idioma": previo.get("idioma") or "", "error": "",
                 "texto": " ".join(w["t"] for w in nuevas), "texto_corregido": "",
                 "palabras": nuevas, "origen": str(origen), "subtitulado": str(rutas["mp4"]),
                 "sincronizado": False, "actualizado": time.time()}
        _guardar_json(rutas["json"], datos)
        return {"ok": True, **datos}
    except Exception as e:
        datos = {**previo, "estado": "error",
                 "error": f"No se pudo aplicar la corrección ({e}). Se conservó el original."[:500],
                 "actualizado": time.time()}
        try:
            _guardar_json(rutas["json"], datos)
        except Exception:
            pass
        return {"ok": False, **datos}


def aplicar_texto_corregido(origen, modelo="tiny"):
    """Requema a partir del texto corregido en el JSON, sin volver a transcribir."""
    origen = Path(origen)
    previo = _leer_json(rutas_de(origen)["json"])
    return aplicar_texto(origen, previo.get("texto_corregido") or "")


def _estable(path, espera=1.2):
    try:
        a = Path(path).stat().st_size
        time.sleep(espera)
        b = Path(path).stat().st_size
        return a > 50_000 and a == b
    except OSError:
        return False


class ColaSubtitulos:
    def __init__(self, carpeta, api, grabando, modelo="tiny"):
        self.carpeta = Path(carpeta)
        self.api = api
        self.grabando = grabando
        self.modelo = modelo or "tiny"
        self.desde = time.time() - 20
        self._parar = threading.Event()
        self.hilo = None

    def _candidatos(self):
        if not self.carpeta.is_dir():
            return
        for carpeta in self.carpeta.iterdir():
            if not carpeta.is_dir() or carpeta.name.lower() in {"descargas", "segmentos", "_radar"}:
                continue
            for base in (carpeta, carpeta / "clips"):
                if not base.is_dir():
                    continue
                for mp4 in base.glob("*.mp4"):
                    if es_clip_original(mp4):
                        yield mp4

    def _marcar_sync(self, origen, valor):
        try:
            rutas = rutas_de(origen)
            datos = _leer_json(rutas["json"])
            datos["sincronizado"] = bool(valor)
            _guardar_json(rutas["json"], datos)
        except Exception:
            pass

    def _sincronizar(self, resultado):
        """Avisa el estado y sube el MP4. La ruta de esta PC no se envía como archivo de la API."""
        origen = resultado.get("origen") or ""
        estado = resultado.get("estado") or ("listo" if resultado.get("ok") else "error")
        avisar = getattr(self.api, "reportar_subtitulos", None)
        resp = {}
        if avisar is not None:
            try:
                resp = avisar(origen, estado, resultado.get("error") or "", resultado.get("texto") or "", "") or {}
            except Exception as e:
                print(f"Subtítulos: no se pudo avisar al panel ({e}). El archivo local se conservó.")
                resp = {}
        clip_id = resp.get("clip_id") if isinstance(resp, dict) else None
        mp4 = resultado.get("subtitulado") or ""
        subir = getattr(self.api, "subir_subtitulado", None)
        if resultado.get("ok") and clip_id and subir and mp4 and Path(mp4).is_file():
            try:
                subir(int(clip_id), mp4)
                self._marcar_sync(origen, True)
                print(f"Subtítulos: clip {clip_id} quedó en el almacén de la API.")
                return
            except Exception as e:
                print(f"Subtítulos: el MP4 local quedó listo, pero no se pudo subir ({e}).")
                return
        if isinstance(resp, dict) and resp.get("ok"):
            self._marcar_sync(origen, True)

    def _paso(self):
        if self.grabando():
            # Hay grabación: se espera. No se arranca Whisper encima del radar.
            return
        for mp4 in self._candidatos():
            if self._parar.is_set() or self.grabando():
                return
            rutas = rutas_de(mp4)
            previo = _leer_json(rutas["json"]) if rutas["json"].is_file() else {}
            if previo.get("estado") == "corregir":
                if not _estable(mp4, 0.4):
                    continue
                print(f"Subtítulos: aplicando corrección de {mp4.name}")
                self._sincronizar(aplicar_texto_corregido(mp4, self.modelo))
                return
            try:
                if mp4.stat().st_mtime < self.desde:
                    continue
            except OSError:
                continue
            if previo.get("estado") == "listo" and rutas["mp4"].is_file():
                if not previo.get("sincronizado"):
                    self._sincronizar({**previo, "ok": True, "origen": str(mp4), "subtitulado": str(rutas["mp4"])})
                    return
                continue
            if previo.get("estado") in {"error", "sin_habla"}:
                if not previo.get("sincronizado"):
                    self._sincronizar({**previo, "ok": False, "origen": str(mp4)})
                    return
                continue
            if not _estable(mp4):
                continue
            print(f"Subtítulos: procesando {mp4.name} en segundo plano")
            self._sincronizar(procesar_clip(mp4, modelo=self.modelo))
            return

    def _bucle(self):
        _bajar_prioridad()
        while not self._parar.is_set():
            try:
                self._paso()
            except Exception as e:
                print(f"Subtítulos: error de la cola, la grabación sigue ({e})")
            self._parar.wait(4 if not self.grabando() else 8)

    def start(self):
        self.hilo = threading.Thread(target=self._bucle, name="subtitulos", daemon=True)
        self.hilo.start()


def iniciar_cola(cfg, api, grabando):
    """Arranca la cola una sola vez. Un fallo aquí no impide grabar."""
    global _COLA
    if _COLA is not None:
        return _COLA
    carpeta = cfg.get("carpeta_clips") or "clips"
    _COLA = ColaSubtitulos(carpeta, api, grabando, cfg.get("whisper_modelo") or "tiny")
    _COLA.start()
    print("Subtítulos: cola local en segundo plano (no bloquea la grabación ni el radar).")
    return _COLA
