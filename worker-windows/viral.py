"""Puntuación de viralidad DETERMINISTA y explicable (0-100).
Solo usa señales REALES medidas del segmento. No promete vistas.

Etapa 2 (v2): puntuar()/clasificar() aceptan una transcripción OPCIONAL.
Sin transcripción el resultado es IDÉNTICO al de la Etapa 1 (compatibilidad
total con tests/test_viral.py). Con transcripción se calcula un componente
«conversacion» (0-20) a partir de señales reales del texto:
  - densidad de habla: palabras/segundo -> 0-8 pts (satura a 3 palabras/s)
  - signos ¡!¿? : 1 pt por signo, máximo 4 pts
  - palabras clave por categorías (discusión/confesión/reacción): 2 pts por
    coincidencia, máximo 8 pts
  conversacion = densidad_pts + signos_pts + claves_pts   (0-20)
  total = min(100, round(total_base * 0.85 + conversacion))
donde total_base es la puntuación v1 (con la ponderación del modo). La
fórmula queda escrita en el desglose siempre que hay transcripción.

Etapa 3 (v1.3.16, «Modo Épicos»): señales OPCIONALES de movimiento
(mov_medio / mov_pico / mov_fraccion_activa, de vision.movimiento_detalle).
Sin ellas el resultado es IDÉNTICO al anterior. Con ellas:
  - clasificar() puede devolver dos tipos nuevos: «pelea» (pico de
    movimiento violento + fracción activa + audio muy alto: gritos) y
    «baile» (movimiento sostenido en la mayoría de los pares + audio
    medio alto: música). Son heurísticas de señales, no comprensión de
    la escena: el panel sigue aprobando cada clip.
  - puntuar() suma un componente «movimiento» (0-25) y el modo «epicos»
    pondera movimiento y dinámica por encima de lo visual genérico.
El radar en modo «epicos» SOLO genera clips de TIPOS_EPICOS."""
import subprocess, re, unicodedata
from procesos import SIN_VENTANA

def medir_audio(path):
    """Devuelve (rms_medio_db, rms_pico_db) con FFmpeg astats. (None,None) si falla/sin audio."""
    try:
        r = subprocess.run(["ffmpeg", "-i", str(path), "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=300, creationflags=SIN_VENTANA)
        txt = r.stderr
        rms_levels = [float(x) for x in re.findall(r"RMS level dB:\s*(-?[\d.]+|-inf)", txt) if x != "-inf"]
        peaks = [float(x) for x in re.findall(r"Peak level dB:\s*(-?[\d.]+|-inf)", txt) if x != "-inf"]
        if not rms_levels: return None, None
        return sum(rms_levels) / len(rms_levels), (max(peaks) if peaks else max(rms_levels))
    except Exception:
        return None, None

def _map(v, lo, hi, pts):
    if v is None: return 0.0
    v = max(lo, min(hi, v))
    return (v - lo) / (hi - lo) * pts

# --- Señales de conversación (Etapa 2) -------------------------------------
# Listas en forma normalizada (minúsculas, sin acentos). Categorías disjuntas.
PALABRAS_CLAVE = {
    "discusion": ["discusion", "discutir", "discutiendo", "pelea", "pelear", "peleando",
                  "enojo", "enojado", "enojada", "furia", "reclamo", "reclamar",
                  "escucha", "escuchame", "oye", "oyendo", "calmate"],
    "confesion": ["confieso", "confesion", "admito", "reconozco", "secreto",
                  "nunca quise", "la verdad es", "perdon", "perdoname", "arrepiento"],
    "reaccion": ["grito", "gritos", "gritar", "gritando", "grita",
                 "increible", "sorpresa", "no puede ser", "dios mio", "wow"],
}

def _normalizar(texto):
    t = unicodedata.normalize("NFKD", str(texto).lower())
    return "".join(c for c in t if not unicodedata.combining(c))

def _extraer_transcripcion(transcripcion, senales):
    """Acepta str, dict de transcribe.transcribir() ({texto, segmentos}) o
    dict {texto, duracion_s}. Devuelve (texto, duracion_s) o (None, None)."""
    if transcripcion is None:
        transcripcion = senales.get("transcripcion") or senales.get("texto_transcripcion")
    if transcripcion is None:
        return None, None
    if isinstance(transcripcion, str):
        return transcripcion, senales.get("duracion_s")
    if isinstance(transcripcion, dict):
        texto = transcripcion.get("texto") or transcripcion.get("text")
        dur = transcripcion.get("duracion_s") or senales.get("duracion_s")
        if dur is None and transcripcion.get("segmentos"):
            dur = max((s.get("end_s", 0) for s in transcripcion["segmentos"]), default=None)
        return texto, dur
    return None, None

def analizar_conversacion(texto, duracion_s):
    """Señales reales de la transcripción. Determinista. Devuelve el detalle
    y el componente conversacion (0-20) con su fórmula."""
    palabras = re.findall(r"\w+", _normalizar(texto), flags=re.UNICODE)
    n_palabras = len(palabras)
    dur = duracion_s if duracion_s else None
    densidad = (n_palabras / dur) if dur else 0.0
    signos = sum(str(texto).count(c) for c in "¡!¿?")
    norm = _normalizar(texto)
    claves = {}
    for cat, lista in PALABRAS_CLAVE.items():
        claves[cat] = sum(norm.count(p) for p in lista)
    total_claves = sum(claves.values())
    densidad_pts = min(8.0, densidad / 3.0 * 8.0) if dur else 0.0
    signos_pts = min(4.0, float(signos))
    claves_pts = min(8.0, total_claves * 2.0)
    conversacion = round(min(20.0, densidad_pts + signos_pts + claves_pts), 1)
    return {"conversacion": conversacion,
            "num_palabras": n_palabras,
            "densidad_palabras_s": round(densidad, 3),
            "signos_exclamacion_interrogacion": signos,
            "palabras_clave": claves,
            "puntos": {"densidad": round(densidad_pts, 1), "signos": round(signos_pts, 1),
                       "palabras_clave": round(claves_pts, 1)},
            "formula": "conversacion(0-20) = densidad(0-8, satura a 3 palabras/s) "
                       "+ signos ¡!¿? (1 pt c/u, máx 4) + palabras clave (2 pts c/u, máx 8)"}

# Tipos que el Modo Épicos deja pasar (v1.3.16): lo que Jose pidió
# detectar — discusión, baile y pelea. El radar y el analizador en modo
# «epicos» descartan cualquier ventana de otro tipo.
TIPOS_EPICOS = {"discusion", "pelea", "baile"}

def puntuar(senales, modo="general", transcripcion=None):
    """senales: rms_medio_db, rms_pico_db, cambios_escena (por segmento), duracion_s.
    transcripcion (opcional, Etapa 2): str o dict de transcribe.transcribir().
    Sin transcripción el resultado es idéntico a la v1.
    Devuelve (score int, desglose dict con explicación de cada componente)."""
    pico, medio = senales.get("rms_pico_db"), senales.get("rms_medio_db")
    dinamica = (pico - medio) if (pico is not None and medio is not None) else None
    intensidad = _map(pico, -45, -3, 40)          # gritos/reacciones fuertes
    sorpresa_audio = _map(dinamica, 2, 22, 20)     # subidas bruscas de volumen
    visual = _map(senales.get("cambios_escena", 0), 0, 8, 25)  # actividad visual
    dur = senales.get("duracion_s", 10)
    claridad = 15.0 if 20 <= dur <= 95 else (10.0 if 10 <= dur < 20 or 95 < dur <= 180 else 5.0)
    comp = {"intensidad_audio": round(intensidad, 1), "dinamica_audio": round(sorpresa_audio, 1),
            "actividad_visual": round(visual, 1), "claridad_duracion": claridad}
    # Etapa 3: componente de movimiento SOLO si la señal llegó medida
    # (fracción de pares con movimiento real, de vision.movimiento_detalle).
    if senales.get("mov_fraccion_activa") is not None:
        comp["movimiento"] = round(_map(senales["mov_fraccion_activa"], 0.1, 0.9, 25), 1)
    if modo == "gta":   # en GTA pondera más lo visual
        total = comp["intensidad_audio"] * 0.8 + comp["dinamica_audio"] * 0.8 + comp["actividad_visual"] * 1.35 + comp["claridad_duracion"]
        comp["modo"] = "gta: actividad visual x1.35, audio x0.8"
    elif modo == "alofoke":  # en realities pondera más voz/emoción
        total = comp["intensidad_audio"] * 1.2 + comp["dinamica_audio"] * 1.2 + comp["actividad_visual"] * 0.6 + comp["claridad_duracion"]
        comp["modo"] = "alofoke: audio/dinámica x1.2, visual x0.6"
    elif modo == "epicos":  # v1.3.16: movimiento y dinámica mandan
        total = (comp["intensidad_audio"] * 0.9 + comp["dinamica_audio"] * 1.0
                 + comp["actividad_visual"] * 0.5 + comp.get("movimiento", 0.0) * 1.0
                 + comp["claridad_duracion"] * 0.9)
        comp["modo"] = "epicos: movimiento x1.0, dinámica x1.0, audio x0.9, visual x0.5; solo discusión/pelea/baile"
    else:
        total = sum(comp[k] for k in ("intensidad_audio", "dinamica_audio", "actividad_visual", "claridad_duracion"))
        comp["modo"] = "general: pesos iguales"
    claves_medidas = ["rms_medio_db", "rms_pico_db", "cambios_escena", "duracion_s"]
    if any(senales.get(k) is not None for k in ("mov_medio", "mov_pico", "mov_fraccion_activa")):
        claves_medidas += ["mov_medio", "mov_pico", "mov_fraccion_activa"]
    comp["senales_medidas"] = {k: senales.get(k) for k in claves_medidas}
    comp["nota"] = "Estimación a partir de señales medidas; no garantiza visualizaciones."
    # --- Etapa 2: componente conversación (solo si hay transcripción) ---
    texto_tr, dur_tr = _extraer_transcripcion(transcripcion, senales)
    if texto_tr:
        detalle = analizar_conversacion(texto_tr, dur_tr if dur_tr else dur)
        comp["score_base_sin_conversacion"] = max(0, min(100, round(total)))
        comp["conversacion"] = detalle["conversacion"]
        comp["conversacion_detalle"] = {k: detalle[k] for k in
                                        ("num_palabras", "densidad_palabras_s",
                                         "signos_exclamacion_interrogacion", "palabras_clave", "puntos")}
        comp["formula_conversacion"] = (detalle["formula"] +
                                        "; total = min(100, round(total_base * 0.85 + conversacion))")
        total = total * 0.85 + detalle["conversacion"]
    return max(0, min(100, round(total))), comp

def clasificar(senales, modo="general", transcripcion=None):
    # --- Etapa 2: la transcripción puede afinar el tipo (confesión primero) ---
    texto_tr, dur_tr = _extraer_transcripcion(transcripcion, senales)
    if texto_tr:
        norm = _normalizar(texto_tr)
        if any(norm.count(p) for p in PALABRAS_CLAVE["confesion"]):
            return "confesion"
        if sum(norm.count(p) for p in PALABRAS_CLAVE["discusion"]) >= 2:
            return "discusion"
    pico, medio = senales.get("rms_pico_db"), senales.get("rms_medio_db")
    escenas = senales.get("cambios_escena", 0)
    if modo == "gta":
        if escenas >= 4: return "accion"
        if pico is not None and pico > -8: return "explosion"
        return "accion" if escenas >= 2 else "general"
    # Etapa 3 (v1.3.16): pelea y baile desde el movimiento, SOLO si la
    # señal llegó medida. Heurísticas de señales, no comprensión de la
    # escena (cada clip lo aprueba Jose en el panel). Calibración: una
    # escena sintética muy ocupada (testsrc2) da mov_pico ~9 y media
    # ~8; una persona quieta hablando, media < 3.
    # - pelea: un par de frames MUY violento Y en forma de PICO
    #   (mov_pico >= 25 en la serie gris 64x36 y al menos 2.2x la media
    #   del segmento: un golpe o empujón destaca sobre el movimiento de
    #   fondo; el baile sostenido no forma ese pico), movimiento real
    #   en al menos el 30% de los pares y audio muy alto (gritos > -14).
    # - baile: movimiento real SOSTENIDO (>= 60% de los pares, media
    #   >= 6) con audio medio alto (música: rms medio > -30 dB).
    mov_medio, mov_pico = senales.get("mov_medio"), senales.get("mov_pico")
    fraccion = senales.get("mov_fraccion_activa")
    if mov_pico is not None and fraccion is not None:
        if (mov_pico >= 25 and mov_pico >= 2.2 * (mov_medio or 0)
                and fraccion >= 0.30 and pico is not None and pico > -14):
            return "pelea"
        if fraccion >= 0.60 and (mov_medio or 0) >= 6 and medio is not None and medio > -30:
            return "baile"
    if pico is not None and medio is not None and (pico - medio) > 16: return "discusion"
    if pico is not None and pico > -9: return "gritos"
    if escenas >= 3: return "sorpresa"
    return "general"
