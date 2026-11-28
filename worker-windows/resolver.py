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
import os, subprocess, sys, time
from procesos import SIN_VENTANA
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

def _ejecutar_ytdlp(url, extra=()):
    """Corre yt-dlp en modo «solo dame la URL de la señal» (-g).
    Devuelve (returncode, stdout, stderr). Aislado para poder simularlo
    en los tests sin red ni yt-dlp real. «extra» son argumentos
    adicionales de yt-dlp (la cadena de reintentos ante el muro de
    inicio de sesión los usa: clientes alternativos, cookies del
    navegador); sin «extra» es EXACTAMENTE la invocación histórica.

    Selector de formato: primero «best» (un solo archivo con audio+video,
    lo normal en un directo). Si la plataforma no ofrece ese archivo
    único —pasa en videos ya publicados, cuyos formatos vienen separados
    en video y audio— se acepta el par «bv*+ba» (mejor video + mejor
    audio): -g lo devuelve como DOS líneas y el Recorder las abre como
    dos entradas de FFmpeg. Con solo «best», un video válido fallaba con
    «Requested format is not available»."""
    try:
        r = subprocess.run([sys.executable, "-m", "yt_dlp", "-g", "--no-warnings",
                            "-f", "best/bv*+ba", *extra, str(url)],
                           capture_output=True, text=True, timeout=180, creationflags=SIN_VENTANA)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        raise ErrorResolucion("La plataforma tardó demasiado en entregar la señal "
                              "(yt-dlp superó los 180 s). Inténtalo de nuevo.")

# --- Muro de inicio de sesión / anti-bots de YouTube -----------------------
# A ciertos videos, YouTube le pide a yt-dlp «Sign in to confirm you're
# not a bot» aunque el video sea público (le pasó a Jose con
# watch?v=Dxi1CRQMQZo, sesión #20, mientras otros videos resolvían
# bien). Antes, el resolver se rendía en el primer intento. Ahora hay
# una CADENA de reintentos reales, en orden:
#   1. la vía habitual (la de siempre);
#   2. clientes alternativos de YouTube (extractor-args player_client):
#      tv, android, ios, web_safari y web_embedded no pasan por el
#      mismo muro (el «tv» suele entregar la señal sin sesión);
#   3. el archivo cookies.txt de la sesión del dueño, si existe (ver
#      _ruta_cookies_txt): la vía más fiable cuando el muro aprieta,
#      porque trae la sesión de YouTube ya iniciada por el propio
#      usuario en su navegador;
#   4. la sesión del PROPIO navegador de esta PC (--cookies-from-browser
#      chrome, y si Chrome está cerrado/bloqueado, edge y firefox):
#      yt-dlp lee las cookies LOCALMENTE en esta PC, como haría el
#      propio usuario con su sesión; nada se sube a ningún sitio.
# Solo se avanza al siguiente intento cuando el fallo es el muro de
# login/bots (o, en los intentos con cookies, cuando el navegador no
# entregó sus cookies). Los demás errores (privado, no disponible,
# formato, en vivo que no comienza) son definitivos: se reportan al
# momento, sin gastar la cadena.
_PATRONES_LOGIN = ("sign in to confirm", "please sign in", "sign in to",
                   "login required", "log in to", "not a bot",
                   "use --cookies", "cookies-from-browser")
# Errores TRANSITORIOS de página (v1.3.19): «The page needs to be
# reloaded» significa que la página del video llegó incompleta en ESE
# intento; los demás clientes (que no leen esa página) suelen entregar
# la señal. Antes este error se trataba como definitivo y abortaba
# toda la cadena en el primer intento: el 2026-11-27 dejó a Jose sin
# poder grabar un en vivo válido por este motivo.
_PATRONES_TRANSITORIOS = ("needs to be reloaded",)
_PATRONES_COOKIES_FALLIDAS = ("could not copy", "failed to decrypt",
                              "unable to load cookies", "no cookies",
                              "could not find", "cookies database",
                              "failed to load cookies")

def _es_error_login(stderr):
    baja = (stderr or "").lower()
    return any(p in baja for p in _PATRONES_LOGIN)


def _es_error_transitorio(stderr):
    baja = (stderr or "").lower()
    return any(p in baja for p in _PATRONES_TRANSITORIOS)

def _es_error_cookies(stderr):
    baja = (stderr or "").lower()
    return any(p in baja for p in _PATRONES_COOKIES_FALLIDAS)

def _ruta_cookies_txt():
    """Ruta del cookies.txt de la sesión del dueño, si existe.

    El dueño lo exporta UNA vez desde su navegador (con la sesión de
    YouTube iniciada) y lo guarda en la carpeta de datos del motor
    (~/JDMViralClips/cookies.txt) o junto al motor. Es la vía más
    fiable contra el muro de login: a diferencia de
    --cookies-from-browser, no depende de que yt-dlp pueda descifrar
    la base de cookies del navegador (Chrome y Edge modernos la
    protegen con cifrado ligado a la app y la extracción falla).
    Se busca en: $JDM_COOKIES_TXT, ~/JDMViralClips/cookies.txt y la
    carpeta del propio resolver. Devuelve None si no hay ninguno."""
    candidatos = []
    env = os.environ.get("JDM_COOKIES_TXT")
    if env:
        candidatos.append(Path(env))
    candidatos.append(Path.home() / "JDMViralClips" / "cookies.txt")
    candidatos.append(Path(__file__).resolve().parent / "cookies.txt")
    for ruta in candidatos:
        try:
            if ruta.is_file() and ruta.stat().st_size > 0:
                return str(ruta)
        except OSError:
            continue
    return None

def _intentos_resolucion(url):
    """La cadena ordenada de intentos: [(descripción, argumentos extra)]."""
    intentos = [("la vía habitual", ())]
    host = _host(url)
    if host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be":
        for cliente in ("tv", "android", "ios", "web_safari", "web_embedded"):
            intentos.append((f"el cliente «{cliente}» de YouTube",
                             ("--extractor-args", f"youtube:player_client={cliente}")))
    cookies = _ruta_cookies_txt()
    if cookies:
        intentos.append(("el archivo cookies.txt de tu sesión",
                         ("--cookies", cookies)))
    for navegador in ("chrome", "edge", "firefox"):
        intentos.append((f"la sesión de {navegador} abierta en esta PC",
                         ("--cookies-from-browser", navegador)))
    return intentos

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
        return ("La plataforma pide iniciar sesión para entregar esta señal "
                "(muro de login/anti-bots).")
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

def _resolver_con_cadena(url):
    """La cadena de intentos tal como quedó en la v1.3.13. Si TODOS
    los intentos caen ante el muro de login, el ErrorResolucion lleva
    el atributo es_muro_login=True: resolver_fuente lo usa para dar
    UNA oportunidad al refresco de la sesión propia (v1.3.14)."""
    u = str(url)
    if es_archivo_local(u):
        return str(Path(u).resolve())
    if es_senal_directa(u):
        return u
    intentos = _intentos_resolucion(u)
    probados = []
    ultimo_error = ""
    hubo_login = False
    hubo_transitorio = False
    for descripcion, extra in intentos:
        es_intento_cookies = "--cookies-from-browser" in extra or "--cookies" in extra
        try:
            rc, out, err = _ejecutar_ytdlp(u, extra)
        except ErrorResolucion as e:
            # Timeout de yt-dlp: este intento no entregó; se prueba el
            # siguiente y, si era el último, se reporta el timeout.
            probados.append(descripcion)
            ultimo_error = str(e)
            continue
        if rc == 0:
            senales = [l.strip() for l in (out or "").splitlines() if l.strip()]
            if senales:
                # Una señal: la URL tal cual. Dos (video y audio por
                # separado, del selector «bv*+ba»): unidas por salto de
                # línea, en ese orden; el Recorder las abre como dos
                # entradas de FFmpeg (ver _cmd_grabar).
                return "\n".join(senales[:2])
            raise ErrorResolucion("La plataforma no entregó ninguna señal para ese enlace "
                                  "(¿es un en vivo válido y ya comenzó?).")
        if "no module named yt_dlp" in (err or "").lower():
            raise ErrorResolucion("yt-dlp no está instalado en esta PC: vuelve a correr "
                                  "el instalador del trabajador (install-worker) para "
                                  "poder grabar desde enlaces de plataformas.")
        if (_es_error_login(err) or _es_error_transitorio(err)
                or (es_intento_cookies and _es_error_cookies(err))):
            # Muro de login/bots, página incompleta transitoria (o el
            # navegador no entregó sus cookies): se pasa al siguiente
            # intento de la cadena.
            probados.append(descripcion)
            hubo_login = hubo_login or _es_error_login(err)
            hubo_transitorio = hubo_transitorio or _es_error_transitorio(err)
            ultimo_error = _error_desde_stderr(err)
            continue
        # Error definitivo (privado, no disponible, formato, etc.):
        # no tiene sentido gastar el resto de la cadena.
        raise ErrorResolucion(_error_desde_stderr(err))
    if hubo_login:
        error = ErrorResolucion(
            "La plataforma siguió pidiendo iniciar sesión tras intentarlo por "
            + ", ".join(probados) + ". Ninguna vía entregó la señal. "
            "Si es un video de una cuenta tuya, comprueba que esa cuenta tenga la "
            "sesión abierta en Chrome en esta PC y vuelve a intentarlo; la vía "
            "más segura es guardar un cookies.txt de tu sesión de YouTube en la "
            "carpeta de datos del motor (JDMViralClips\\cookies.txt): el motor lo "
            "usa solo, sin tocar tu navegador. Si no, usa "
            "un HLS o RTMP de una emisión que controles, o importa un video propio. "
            f"Último motivo de la plataforma: {ultimo_error}")
        error.es_muro_login = True
        raise error
    if hubo_transitorio:
        error = ErrorResolucion(
            "La plataforma devolvió una página incompleta («needs to be "
            "reloaded») en todas las vías intentadas: "
            + ", ".join(probados) + ". Es un fallo pasajero de la "
            "plataforma, no del enlace: espera unos segundos y vuelve a "
            "darle a GRABAR.")
        error.es_transitorio = True
        raise error
    raise ErrorResolucion(
        "No se pudo resolver la señal tras intentarlo por "
        + ", ".join(probados) + f". Último motivo: {ultimo_error}")

# --- Sesión de YouTube propia del motor (v1.3.14) ---------------------------
# Cuando TODAS las vías caen ante el muro de login, queda una carta
# más: la sesión PROPIA del motor (sesion_youtube.py) — un perfil de
# navegador dedicado del que el motor exporta las cookies por CDP,
# HttpOnly incluidas, al cookies.txt que la cadena ya sabe usar.
# worker.py conecta el gancho al arrancar el motor real asignando
# resolver.gancho_refresco_sesion = sesion_youtube.refrescar_sesion.
# Sin gancho (None: tests y usos sueltos del resolver) el
# comportamiento es EXACTAMENTE el de la v1.3.13. El refresco se
# intenta UNA sola vez por llamada y nunca en bucle: si tras
# refrescar la cadena vuelve a caer ante el muro, manda el error.

gancho_refresco_sesion = None

def _intentar_refresco_sesion():
    """Consulta el gancho de la sesión propia. Devuelve
    (reintentar, aviso). Un gancho que explota nunca tumba la
    resolución: se traga el fallo y manda el error original."""
    if gancho_refresco_sesion is None:
        return False, ""
    try:
        resultado = gancho_refresco_sesion()
    except Exception:
        return False, ""
    if isinstance(resultado, tuple) and len(resultado) == 2:
        return bool(resultado[0]), str(resultado[1] or "")
    return bool(resultado), ""

def resolver_fuente(url):
    """Devuelve la URL lista para FFmpeg. Ver docstring del módulo.

    Las páginas de plataforma se resuelven con la cadena de intentos
    de _intentos_resolucion: el éxito de CUALQUIER intento corta la
    cadena. Un error que no sea el muro de login (privado, no
    disponible, formato, en vivo sin comenzar) es definitivo y se
    reporta al momento. Si TODOS los intentos caen ante el muro, el
    error final dice con honestidad qué se intentó.

    v1.3.14: antes de rendirse ante el muro, si el motor conectó el
    gancho de su sesión propia, se intenta UN refresco (exportar las
    cookies del perfil dedicado por CDP) y la cadena se repite una
    sola vez; si el perfil aún no tiene sesión, el aviso del refresco
    (la ventana de login que se abrió) se añade al error final."""
    try:
        return _resolver_con_cadena(url)
    except ErrorResolucion as e:
        if getattr(e, "es_transitorio", False):
            # Página incompleta en todas las vías (v1.3.19): es el
            # clásico fallo que «recargar» cura. Se espera un respiro
            # y se repite la cadena UNA sola vez antes de rendirse.
            time.sleep(3)
            return _resolver_con_cadena(url)
        if not getattr(e, "es_muro_login", False):
            raise
        reintentar, aviso = _intentar_refresco_sesion()
        if reintentar:
            return _resolver_con_cadena(url)
        if aviso:
            raise ErrorResolucion(f"{e} {aviso}")
        raise
