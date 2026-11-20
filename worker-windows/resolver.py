"""Resolución de fuentes en el trabajador — regla de Jose (2026-11-14):
«quiero que grabe todos los en vivo de todas las plataformas».

La API/panel aceptan enlaces de PÁGINAS de plataformas (YouTube, Facebook,
Twitch, TikTok, Instagram, X) como fuente «plataforma». Es AQUÍ, en la PC,
donde ese enlace se convierte en la señal real que FFmpeg puede grabar,
usando yt-dlp. Las señales directas (.m3u8, rtmp://, srt://, archivos y
URLs http(s) genéricas) se devuelven TAL CUAL: comportamiento histórico.

Errores honestos y específicos: si el en vivo todavía no comienza, es
privado/no disponible o la plataforma no entrega la señal, se lanza
ErrorResolucion con el motivo en español y el trabajo falla con ese
mensaje. NUNCA se finge una grabación que no existe."""
import subprocess, sys
from pathlib import Path
from urllib.parse import urlparse

DOMINIOS_PLATAFORMA = ("youtube.com", "youtu.be", "facebook.com", "fb.watch",
                       "twitch.tv", "tiktok.com", "instagram.com", "x.com", "twitter.com")

class ErrorResolucion(Exception):
    """La fuente no se pudo resolver; el mensaje es para el usuario final."""

def _host(url):
    try: return (urlparse(str(url)).hostname or "").lower()
    except Exception: return ""

def es_pagina_plataforma(url):
    """True si la URL es la página de una plataforma conocida (no la señal)."""
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in DOMINIOS_PLATAFORMA)

def es_senal_directa(url):
    """Señal que FFmpeg abre tal cual: rtmp/srt/udp, .m3u8, rutas locales y
    cualquier http(s) que NO sea página de plataforma conocida."""
    p = urlparse(str(url))
    if p.scheme in ("rtmp", "rtmps", "srt", "udp", "file"): return True
    if ".m3u8" in p.path: return True
    return not es_pagina_plataforma(url)

def _ejecutar_ytdlp(url):
    """Corre yt-dlp en modo «solo dame la URL de la señal» (-g).
    Devuelve (returncode, stdout, stderr). Aislado para poder simularlo
    en los tests sin red ni yt-dlp real.

    Selector de formato: primero «best» (un solo archivo con audio+video,
    lo normal en un directo). Si la plataforma no ofrece ese archivo
    único —pasa en videos ya publicados, cuyos formatos vienen separados
    en video y audio— se acepta el par «bv*+ba» (mejor video + mejor
    audio): -g lo devuelve como DOS líneas y el Recorder las abre como
    dos entradas de FFmpeg. Con solo «best», un video válido fallaba con
    «Requested format is not available»."""
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "-g", "--no-warnings",
                            "-f", "best/bv*+ba", str(url)],
                           capture_output=True, text=True, timeout=180)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        raise ErrorResolucion("La plataforma tardó demasiado en entregar la señal "
                              "(yt-dlp superó los 180 s). Inténtalo de nuevo.")

def _error_desde_stderr(stderr):
    txt = (stderr or "").strip()
    baja = txt.lower()
    if ("will begin" in baja or "premiere" in baja or "upcoming" in baja
            or "has not started" in baja or "not started" in baja):
        return ("El en vivo todavía no comienza: la plataforma aún no emite señal. "
                "Espera a que empiece y vuelve a iniciar la grabación.")
    if ("private video" in baja or "video is private" in baja or "is private" in baja
            or "granted access" in baja):
        return ("El video/en vivo es privado: no se puede grabar sin acceso a la "
                "cuenta dueña del contenido.")
    # ANTES del chequeo de «not available»: «Requested format is not
    # available» es un fallo de FORMATO en un video que SÍ existe (yt-dlp
    # no encontró el formato pedido), no un video borrado. Clasificarlo
    # como «no está disponible» enseñaba en el panel que el video estaba
    # borrado/restringido siendo falso.
    if "format is not available" in baja or "requested format" in baja:
        return ("La plataforma no ofreció un formato grabable para ese video "
                "(ni combinado ni por separado).")
    if ("video unavailable" in baja or "video is unavailable" in baja
            or "no longer available" in baja or "has been removed" in baja
            or "not available" in baja):
        return ("El video/en vivo no está disponible en la plataforma "
                "(borrado, restringido o enlace incorrecto).")
    if ("sign in to confirm" in baja or "login required" in baja
            or "log in to" in baja or "cookies" in baja or "age" in baja and "confirm" in baja):
        return ("La plataforma pide iniciar sesión para entregar esta señal; sin la "
                "sesión del dueño, el trabajador no puede resolverla. No se intenta "
                "entrar a la cuenta. Usa un HLS o RTMP de una emisión que controles, "
                "o importa un video propio.")
    if "certificate_verify_failed" in baja or "certificate verify failed" in baja:
        return ("La conexión con la plataforma no pasó la verificación de seguridad "
                "(certificado SSL). Suele causarlo un proxy/antivirus de la red de "
                "esta PC, no la plataforma: revisa la red e inténtalo de nuevo.")
    # Detalle útil: la primera línea «ERROR: …» de yt-dlp, sin el prefijo.
    detalle = "sin detalle de la plataforma"
    for linea in txt.splitlines():
        linea = linea.strip()
        if linea:
            detalle = linea[7:].strip() if linea.startswith("ERROR: ") else linea
            break
    return f"La plataforma no entregó la señal de ese enlace: {detalle[:300]}"

def es_archivo_local(url):
    """True si la fuente es un archivo que ya está en esta PC."""
    try:
        ruta = Path(str(url))
        return ruta.is_file() and ruta.stat().st_size > 0
    except OSError:
        return False

def resolver_fuente(url):
    """Devuelve la URL lista para FFmpeg. Ver docstring del módulo."""
    u = str(url)
    if es_archivo_local(u):
        return str(Path(u).resolve())
    if es_senal_directa(u):
        return u
    rc, out, err = _ejecutar_ytdlp(u)
    if rc != 0:
        if "no module named yt_dlp" in (err or "").lower():
            raise ErrorResolucion("yt-dlp no está instalado en esta PC: vuelve a correr "
                                  "el instalador del trabajador (install-worker) para "
                                  "poder grabar desde enlaces de plataformas.")
        raise ErrorResolucion(_error_desde_stderr(err))
    senales = [l.strip() for l in (out or "").splitlines() if l.strip()]
    if not senales:
        raise ErrorResolucion("La plataforma no entregó ninguna señal para ese enlace "
                              "(¿es un en vivo válido y ya comenzó?).")
    # Una señal: la URL tal cual. Dos (video y audio por separado, del
    # selector «bv*+ba»): unidas por salto de línea, en ese orden; el
    # Recorder las abre como dos entradas de FFmpeg (ver _cmd_grabar).
    return "\n".join(senales[:2])
