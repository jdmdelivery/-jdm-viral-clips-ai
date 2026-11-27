"""Sesión de YouTube PROPIA del motor (v1.3.14).

El problema (sesión #34, 2026-11-26): YouTube cerró el muro de
login/anti-bots a TODAS las vías del resolver — clientes alternativos,
--cookies-from-browser (Chrome/Edge modernos cifran su base de cookies
con App-Bound Encryption y yt-dlp no puede leerla) y el cookies.txt
exportado a mano por Jose, que solo traía las cookies NO-HttpOnly
(PREF/SOCS): las de sesión (SAPISID, __Secure-1PSID, LOGIN_INFO…)
son HttpOnly y su extensión exportadora ni las veía.

La salida: el motor tiene SU PROPIO navegador, con un perfil dedicado
y persistente en la carpeta de datos (JDMViralClips\\perfil-youtube),
distinto del Edge/Chrome de uso diario de Jose:

1. Jose inicia sesión en YouTube UNA vez dentro de ese navegador
   (el motor abre la ventana visible cuando hace falta).
2. El motor exporta las cookies de ese perfil vía CDP (Chrome
   DevTools Protocol, http://127.0.0.1:9223), que SÍ entrega las
   cookies HttpOnly, y las escribe en JDMViralClips\\cookies.txt en
   formato Netscape, el que yt-dlp ya sabe usar (vía ya existente
   del resolver desde la v1.3.13).
3. En cada grabación, si todas las vías caen ante el muro, el
   resolver pide UN refresco: exportar del perfil y reintentar una
   vez. Si el perfil aún no tiene sesión, se abre la ventana visible
   y el aviso queda en el error del trabajo y en el log.

Nada sale de esta PC: el CDP escucha solo en 127.0.0.1 y las cookies
se quedan en la carpeta de datos del motor, igual que el cookies.txt
manual de la v1.3.13. Solo biblioteca estándar."""
import base64, json, os, secrets, shutil, socket, struct, subprocess, time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from procesos import SIN_VENTANA

PUERTO_CDP = 9223
URL_YOUTUBE = "https://www.youtube.com"

# Cookies que SOLO existen con la sesión de YouTube/Google iniciada.
# Un cookies.txt sin ninguna de estas NO trae la sesión (es el caso
# del archivo de Jose con solo PREF/SOCS) y no sirve contra el muro.
COOKIES_DE_SESION = ("SAPISID", "__Secure-1PSID", "__Secure-3PSID",
                     "LOGIN_INFO", "SID", "HSID", "SSID", "APISID")

# Dominios cuyas cookies le importan a yt-dlp para YouTube: la sesión
# vive entre .youtube.com y .google.com (SAPISID es de Google).
DOMINIOS_UTILES = ("youtube.com", "google.com")

# La ventana visible se abre como máximo UNA vez por proceso del
# motor: si Jose la cerró sin iniciar sesión, no se le persigue con
# ventanas en cada orden de grabación; el aviso queda en el log.
_ventana_abierta = False


# --- Rutas -----------------------------------------------------------------

def carpeta_datos():
    """Carpeta de datos del motor: $JDM_CARPETA_DATOS (la fija
    worker.py desde la configuración) o ~/JDMViralClips, la misma
    convención que resolver._ruta_cookies_txt."""
    env = os.environ.get("JDM_CARPETA_DATOS")
    if env:
        return Path(env)
    return Path.home() / "JDMViralClips"


def ruta_perfil(carpeta=None):
    """El perfil de navegador dedicado del motor (persistente)."""
    return (Path(carpeta) if carpeta else carpeta_datos()) / "perfil-youtube"


def ruta_cookies_txt(carpeta=None):
    """Dónde vive el cookies.txt que consume el resolver: la misma
    precedencia que resolver._ruta_cookies_txt ($JDM_COOKIES_TXT,
    luego la carpeta de datos). Junto al motor NO se escribe: ese
    directorio lo reemplaza el auto-actualizador."""
    env = os.environ.get("JDM_COOKIES_TXT")
    if env:
        return Path(env)
    return (Path(carpeta) if carpeta else carpeta_datos()) / "cookies.txt"


# --- Validación del cookies.txt ---------------------------------------------

def _dominio_util(dominio):
    d = (dominio or "").lower().lstrip(".")
    return any(d == u or d.endswith("." + u) for u in DOMINIOS_UTILES)


def leer_cookies_netscape(texto):
    """Parsea el texto de un cookies.txt (formato Netscape) y devuelve
    la lista de cookies como dicts {domain, name, value, ...}. Las
    líneas #HttpOnly_ se aceptan (así las escribe yt-dlp y así las
    escribimos nosotros); el resto de comentarios se ignoran."""
    cookies = []
    for linea in (texto or "").splitlines():
        if not linea.strip():
            continue
        httponly = False
        if linea.startswith("#HttpOnly_"):
            linea = linea[len("#HttpOnly_"):]
            httponly = True
        elif linea.startswith("#"):
            continue
        partes = linea.split("\t")
        if len(partes) < 7:
            continue
        dominio, _flag, ruta, seguro, expira, nombre, valor = partes[:7]
        try:
            expira = int(expira)
        except ValueError:
            expira = 0
        cookies.append({"domain": dominio, "path": ruta,
                        "secure": seguro.upper() == "TRUE",
                        "expires": expira, "name": nombre,
                        "value": valor, "httpOnly": httponly})
    return cookies


def hay_cookies_de_sesion(cookies):
    """True si la lista trae alguna cookie de sesión de YouTube/Google
    con valor (las de la lista COOKIES_DE_SESION)."""
    for c in cookies:
        if (c.get("name") in COOKIES_DE_SESION and c.get("value")
                and _dominio_util(c.get("domain", ""))):
            return True
    return False


def cookies_txt_validas(ruta):
    """(válido, motivo) de un cookies.txt en disco. Inválido si no
    existe, está vacío, no se puede leer o no trae NINGUNA cookie de
    sesión — el archivo solo con PREF/SOCS de las exportaciones sin
    HttpOnly cae aquí, con el motivo explicado en español."""
    ruta = Path(ruta)
    if not ruta.is_file():
        return False, f"no existe el archivo {ruta}"
    try:
        texto = ruta.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"no se pudo leer {ruta}: {e}"
    if not texto.strip():
        return False, f"el archivo {ruta} está vacío"
    cookies = leer_cookies_netscape(texto)
    if not cookies:
        return False, f"el archivo {ruta} no trae cookies legibles"
    if not hay_cookies_de_sesion(cookies):
        return False, ("el cookies.txt no trae la sesión de YouTube: solo "
                       "hay cookies sin sesión (tipo PREF/SOCS). Las de "
                       "sesión son HttpOnly y una exportación normal no "
                       "las incluye; usa la sesión propia del motor "
                       "(perfil dedicado), que las exporta por CDP.")
    return True, "trae cookies de sesión de YouTube"


def aviso_cookies_invalidas(carpeta=None):
    """Aviso para el log del motor al arrancar: si YA hay un
    cookies.txt pero no trae la sesión, se dice claro (y una sola
    vez) en vez de descubrirlo grabación a grabación. None si no hay
    archivo o si el que hay es válido."""
    ruta = ruta_cookies_txt(carpeta)
    if not ruta.is_file():
        return None
    valido, motivo = cookies_txt_validas(ruta)
    if valido:
        return None
    return f"Sesión de YouTube: {motivo}"


# --- Cookies CDP -> Netscape -------------------------------------------------

def cookies_a_netscape(cookies):
    """Convierte las cookies del CDP (dicts de Network.getAllCookies)
    al texto Netscape que entiende yt-dlp. Solo pasan las de los
    dominios útiles (youtube.com/google.com); las HttpOnly llevan el
    prefijo #HttpOnly_ que yt-dlp respeta. Las de sesión sin caducidad
    (expires -1) se escriben con 0, como hace el propio yt-dlp."""
    lineas = ["# Netscape HTTP Cookie File",
              "# Sesión de YouTube del motor JDM Viral Clips AI "
              "(exportada por CDP desde su perfil dedicado)."]
    for c in cookies:
        dominio = c.get("domain", "")
        if not _dominio_util(dominio):
            continue
        nombre, valor = c.get("name", ""), c.get("value", "")
        if not nombre:
            continue
        flag = "TRUE" if dominio.startswith(".") else "FALSE"
        seguro = "TRUE" if c.get("secure") else "FALSE"
        try:
            expira = int(c.get("expires") or 0)
        except (TypeError, ValueError):
            expira = 0
        if expira < 0:
            expira = 0
        ruta = c.get("path") or "/"
        linea = "\t".join([dominio, flag, ruta, seguro, str(expira),
                           nombre, valor])
        if c.get("httpOnly"):
            linea = "#HttpOnly_" + linea
        lineas.append(linea)
    return "\n".join(lineas) + "\n"


# --- Cliente WebSocket mínimo para el CDP ------------------------------------
# El CDP solo habla por WebSocket y requirements.txt no trae cliente
# WS; esto es lo mínimo del RFC 6455 para mandar UN comando y leer su
# respuesta: frames del cliente enmascarados (obligatorio), lectura
# con fragmentos, ping->pong y cierre. Solo biblioteca estándar.

class ErrorCDP(Exception):
    """El CDP no respondió o la respuesta no sirve."""


def _ws_enviar(sock, carga, opcode=0x1):
    datos = carga.encode("utf-8") if isinstance(carga, str) else carga
    mascara = secrets.token_bytes(4)
    cab = bytearray([0x80 | opcode])
    n = len(datos)
    if n < 126:
        cab.append(0x80 | n)
    elif n <= 0xFFFF:
        cab.append(0x80 | 126)
        cab += struct.pack(">H", n)
    else:
        cab.append(0x80 | 127)
        cab += struct.pack(">Q", n)
    cab += mascara
    enmascarado = bytes(b ^ mascara[i % 4] for i, b in enumerate(datos))
    sock.sendall(bytes(cab) + enmascarado)


def _leer_exacto(sock, buf, n):
    while len(buf) < n:
        trozo = sock.recv(65536)
        if not trozo:
            raise ErrorCDP("el navegador cerró la conexión CDP")
        buf += trozo
    return buf[:n], buf[n:]


def _ws_recibir(sock, buf):
    """Lee UN mensaje completo (uniendo fragmentos). Devuelve
    (mensaje_str, buffer_restante). Responde a los ping con pong."""
    mensaje = bytearray()
    while True:
        cab, buf = _leer_exacto(sock, buf, 2)
        fin, opcode = cab[0] & 0x80, cab[0] & 0x0F
        enmascarado = cab[1] & 0x80
        n = cab[1] & 0x7F
        if n == 126:
            ext, buf = _leer_exacto(sock, buf, 2)
            n = struct.unpack(">H", ext)[0]
        elif n == 127:
            ext, buf = _leer_exacto(sock, buf, 8)
            n = struct.unpack(">Q", ext)[0]
        if enmascarado:
            mascara, buf = _leer_exacto(sock, buf, 4)
        else:
            mascara = None
        carga, buf = _leer_exacto(sock, buf, n)
        if mascara:
            carga = bytes(b ^ mascara[i % 4] for i, b in enumerate(carga))
        if opcode == 0x9:      # ping -> pong y a seguir esperando
            _ws_enviar(sock, carga, opcode=0xA)
            continue
        if opcode == 0x8:
            raise ErrorCDP("el navegador cerró la conexión CDP")
        mensaje += carga
        if fin:
            return mensaje.decode("utf-8", errors="replace"), buf


def _ws_conectar(url, timeout=10):
    """Abre el WebSocket del CDP y hace el handshake HTTP. Devuelve
    (socket, buffer_inicial). El socket queda con el timeout dado."""
    p = urlparse(url)
    host = p.hostname or "127.0.0.1"
    puerto = p.port or 80
    ruta = p.path or "/"
    if p.query:
        ruta += "?" + p.query
    sock = socket.create_connection((host, puerto), timeout=timeout)
    clave = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    peticion = (f"GET {ruta} HTTP/1.1\r\nHost: {host}:{puerto}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {clave}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n")
    sock.sendall(peticion.encode("ascii"))
    respuesta = b""
    while b"\r\n\r\n" not in respuesta:
        trozo = sock.recv(4096)
        if not trozo:
            sock.close()
            raise ErrorCDP("el navegador no aceptó la conexión CDP")
        respuesta += trozo
    cabecera, _, resto = respuesta.partition(b"\r\n\r\n")
    if b" 101" not in cabecera.split(b"\r\n", 1)[0]:
        sock.close()
        raise ErrorCDP("el navegador rechazó el WebSocket del CDP: "
                       + cabecera.split(b"\r\n", 1)[0].decode("ascii", "replace"))
    return sock, resto


def cdp_comando(url_ws, metodo, params=None, timeout=15):
    """Manda un comando al navegador por CDP y devuelve su «result».
    Los eventos intermedios (sin «id» de respuesta) se descartan."""
    sock, buf = _ws_conectar(url_ws, timeout=timeout)
    try:
        _ws_enviar(sock, json.dumps({"id": 1, "method": metodo,
                                     "params": params or {}}))
        while True:
            texto, buf = _ws_recibir(sock, buf)
            try:
                mensaje = json.loads(texto)
            except ValueError:
                continue
            if mensaje.get("id") == 1:
                if "error" in mensaje:
                    raise ErrorCDP(f"el CDP devolvió un error en {metodo}: "
                                   f"{mensaje['error']}")
                return mensaje.get("result") or {}
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _url_ws_navegador(puerto=PUERTO_CDP, timeout=3):
    """webSocketDebuggerUrl del navegador desde /json/version, o
    None si en ese puerto no hay un navegador con CDP escuchando."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{puerto}/json/version", timeout=timeout) as r:
            datos = json.loads(r.read().decode("utf-8", "replace"))
        return datos.get("webSocketDebuggerUrl")
    except Exception:
        return None


def cdp_todas_las_cookies(puerto=PUERTO_CDP):
    """Todas las cookies del perfil vía CDP (Network.getAllCookies),
    INCLUIDAS las HttpOnly — justo lo que la exportación manual no
    puede dar. None si el CDP no responde."""
    url_ws = _url_ws_navegador(puerto)
    if not url_ws:
        return None
    try:
        resultado = cdp_comando(url_ws, "Network.getAllCookies")
    except Exception:
        return None
    cookies = resultado.get("cookies")
    return cookies if isinstance(cookies, list) else None


# --- Lanzamiento del navegador dedicado ---------------------------------------

_RUTAS_EDGE = ("Microsoft\\Edge\\Application\\msedge.exe",)
_RUTAS_CHROME = ("Google\\Chrome\\Application\\chrome.exe",)


def _buscar_navegador():
    """Ruta de Edge (preferido) o Chrome en esta PC, o None. En
    Windows se miran el PATH y las rutas de instalación habituales;
    en otro SO, el PATH (el desarrollo y los tests no lanzan nada:
    solo se usa cuando el motor real lo pide)."""
    for nombre in ("msedge", "msedge.exe"):
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    for nombre in ("chrome", "chrome.exe", "google-chrome", "chromium"):
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    if os.name == "nt":
        bases = [os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LocalAppData")]
        for base in bases:
            if not base:
                continue
            for parcial in _RUTAS_EDGE + _RUTAS_CHROME:
                ruta = Path(base) / parcial
                if ruta.is_file():
                    return str(ruta)
    return None


def _lanzar_navegador(carpeta=None, visible=False, url=None):
    """Lanza el navegador del motor con su perfil dedicado y el
    puerto CDP. visible=True abre la ventana para que Jose inicie
    sesión (SIN el flag de «sin ventana»: la ventana ES el objetivo);
    visible=False lo levanta headless solo para exportar cookies.
    Devuelve True si el proceso arrancó."""
    exe = _buscar_navegador()
    if not exe:
        return False
    perfil = ruta_perfil(carpeta)
    try:
        perfil.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    args = [exe, f"--user-data-dir={perfil}",
            f"--remote-debugging-port={PUERTO_CDP}",
            "--no-first-run", "--no-default-browser-check"]
    if visible:
        args.append(url or URL_YOUTUBE)
        flags = 0
    else:
        args += ["--headless=new", "--disable-gpu", "about:blank"]
        flags = SIN_VENTANA
    try:
        subprocess.Popen(args, creationflags=flags,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def abrir_ventana_login(carpeta=None):
    """Abre la ventana VISIBLE del navegador del motor en YouTube
    para que Jose inicie sesión una vez. Si el CDP ya responde, el
    navegador del perfil ya está abierto (con o sin ventana) y no se
    lanza otro. Devuelve True si la ventana quedó abierta."""
    if _url_ws_navegador():
        return True
    return _lanzar_navegador(carpeta, visible=True)


def _esperar_cdp(segundos=20):
    """Espera a que el CDP del puerto responda (tras lanzar)."""
    limite = time.time() + segundos
    while time.time() < limite:
        if _url_ws_navegador(timeout=2):
            return True
        time.sleep(0.5)
    return False


# --- Exportación y refresco ----------------------------------------------------

def exportar_cookies_cdp(carpeta=None):
    """Exporta las cookies del perfil dedicado al cookies.txt del
    resolver. Si el CDP no está levantado, lanza el navegador en
    headless con el perfil (la sesión persiste en el perfil aunque
    la ventana visible esté cerrada). Devuelve (ok, motivo):
    ok=True solo si el archivo escrito trae cookies de sesión."""
    cookies = cdp_todas_las_cookies()
    if cookies is None:
        if not _lanzar_navegador(carpeta, visible=False):
            return False, "no se encontró Edge ni Chrome en esta PC"
        if not _esperar_cdp():
            return False, "el navegador del motor no abrió el puerto CDP"
        cookies = cdp_todas_las_cookies()
    if cookies is None:
        return False, "el CDP no entregó las cookies del perfil"
    if not hay_cookies_de_sesion(cookies):
        return False, ("el perfil del motor aún no tiene la sesión de "
                       "YouTube iniciada")
    destino = ruta_cookies_txt(carpeta)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(cookies_a_netscape(cookies), encoding="utf-8")
    except OSError as e:
        return False, f"no se pudo escribir {destino}: {e}"
    valido, motivo = cookies_txt_validas(destino)
    if not valido:
        return False, motivo
    return True, f"cookies de sesión exportadas a {destino}"


def refrescar_sesion(carpeta=None):
    """El gancho que el resolver llama UNA vez cuando todas las vías
    caen ante el muro de login (lo conecta worker.py al arrancar).
    Devuelve (reintentar, aviso):

    - (True, ""): se exportaron cookies de sesión frescas al
      cookies.txt; el resolver reintenta la cadena una vez.
    - (False, aviso): no hay sesión exportable todavía. Si el perfil
      aún no tiene sesión, se abre la ventana visible (máximo una
      vez por proceso) y el aviso —en español, para el panel y el
      log— dice qué hacer. Sin navegador disponible, aviso vacío y
      manda el error honesto de siempre del resolver."""
    global _ventana_abierta
    ok, motivo = exportar_cookies_cdp(carpeta)
    if ok:
        print(f"Sesión de YouTube del motor: {motivo}.")
        return True, ""
    print(f"Sesión de YouTube del motor: no se pudo refrescar ({motivo}).")
    if "aún no tiene la sesión" not in motivo and "no entregó" not in motivo:
        # Sin navegador, sin puerto CDP o sin poder escribir el
        # archivo: no hay ventana que abrir que lo arregle; manda el
        # error honesto de siempre del resolver.
        return False, ""
    if _ventana_abierta:
        return False, ("La ventana de inicio de sesión del motor ya se "
                        "abrió antes: inicia sesión en YouTube en esa "
                        "ventana (es solo una vez) y vuelve a darle a "
                        "GRABAR.")
    if abrir_ventana_login(carpeta):
        _ventana_abierta = True
        return False, ("Se abrió una ventana del navegador del motor "
                       "con YouTube: inicia sesión ahí con tu cuenta "
                       "(es solo una vez) y vuelve a darle a GRABAR. "
                       "El motor guarda esa sesión en su propio perfil "
                       "y no te la volverá a pedir.")
    return False, ""
