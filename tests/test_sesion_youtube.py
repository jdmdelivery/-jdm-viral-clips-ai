"""Sesión de YouTube propia del motor (v1.3.14).

El muro de login cerró TODAS las vías del resolver en la sesión #34
(2026-11-26): el cookies.txt de Jose solo traía PREF/SOCS porque las
cookies de sesión son HttpOnly y su extensión no las exporta, y
--cookies-from-browser muere con el cifrado App-Bound de Edge/Chrome.

Arreglo: perfil de navegador DEDICADO del motor
(JDMViralClips\\perfil-youtube) + exportación de cookies por CDP
(incluye las HttpOnly) al cookies.txt que la cadena ya consume, con
UN refresco cuando todo cae ante el muro y la ventana visible de
login una sola vez si el perfil aún no tiene sesión.

Todo simulado: ningún test abre un navegador de verdad ni toca la
red; el WebSocket del CDP se prueba contra un servidor falso en un
socketpair local."""
import json, socket, struct, threading
from pathlib import Path

import pytest

import resolver
import sesion_youtube as sy

MURO = ("ERROR: [youtube] Dxi1CRQMQZo: Sign in to confirm you're not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication")
OK = (0, "https://rr5.googlevideo.com/videoplayback?x=1\n", "")
FALLO_MURO = (1, "", MURO)

SOLO_PREF_SOCS = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tFALSE\t1799999999\tPREF\tf1=50000000\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1799999999\tSOCS\tCAI\n")
CON_SESION = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tFALSE\t1799999999\tPREF\tf1=50000000\n"
    "#HttpOnly_.google.com\tTRUE\t/\tTRUE\t1799999999\tSAPISID\tvalor-secreto\n"
    "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1799999999\tLOGIN_INFO\tvalor\n")


# ---------------------------------------------------------------------------
# Validación del cookies.txt
# ---------------------------------------------------------------------------

def test_validacion_solo_pref_socs_es_invalido(tmp_path):
    ruta = tmp_path / "cookies.txt"
    ruta.write_text(SOLO_PREF_SOCS, encoding="utf-8")
    valido, motivo = sy.cookies_txt_validas(ruta)
    assert not valido
    assert "no trae la sesión" in motivo


def test_validacion_con_sapisid_es_valido(tmp_path):
    ruta = tmp_path / "cookies.txt"
    ruta.write_text(CON_SESION, encoding="utf-8")
    valido, _ = sy.cookies_txt_validas(ruta)
    assert valido


def test_validacion_archivo_inexistente_y_vacio(tmp_path):
    assert not sy.cookies_txt_validas(tmp_path / "no-esta.txt")[0]
    vacio = tmp_path / "cookies.txt"
    vacio.write_text("", encoding="utf-8")
    assert not sy.cookies_txt_validas(vacio)[0]


def test_aviso_al_arrancar_solo_si_hay_archivo_invalido(tmp_path, monkeypatch):
    monkeypatch.setenv("JDM_CARPETA_DATOS", str(tmp_path))
    assert sy.aviso_cookies_invalidas() is None          # sin archivo: silencio
    (tmp_path / "cookies.txt").write_text(SOLO_PREF_SOCS, encoding="utf-8")
    aviso = sy.aviso_cookies_invalidas()
    assert aviso and "no trae la sesión" in aviso
    (tmp_path / "cookies.txt").write_text(CON_SESION, encoding="utf-8")
    assert sy.aviso_cookies_invalidas() is None          # válido: silencio


# ---------------------------------------------------------------------------
# Conversión CDP -> Netscape
# ---------------------------------------------------------------------------

def _cookie_cdp(dominio, nombre, valor="v", httponly=False, expires=1799999999):
    return {"domain": dominio, "name": nombre, "value": valor, "path": "/",
            "secure": True, "httpOnly": httponly, "expires": expires}


def test_conversion_filtra_dominios_y_marca_httponly():
    cookies = [
        _cookie_cdp(".google.com", "SAPISID", httponly=True),
        _cookie_cdp(".youtube.com", "PREF"),
        _cookie_cdp(".facebook.com", "c_user", httponly=True),   # fuera
        _cookie_cdp("ejemplo.com", "loquesea"),                  # fuera
    ]
    texto = sy.cookies_a_netscape(cookies)
    assert "#HttpOnly_.google.com\tTRUE\t/\tTRUE\t1799999999\tSAPISID\tv" in texto
    assert "facebook" not in texto and "ejemplo" not in texto
    # Y el texto generado pasa la validación de sesión.
    assert sy.hay_cookies_de_sesion(sy.leer_cookies_netscape(texto))


def test_conversion_cookie_de_sesion_sin_caducidad_va_con_cero():
    cookies = [_cookie_cdp(".youtube.com", "LOGIN_INFO", expires=-1)]
    texto = sy.cookies_a_netscape(cookies)
    assert "\t0\tLOGIN_INFO\t" in texto


def test_sin_cookies_de_sesion_en_lo_exportado():
    cookies = [_cookie_cdp(".youtube.com", "PREF"),
               _cookie_cdp(".google.com", "SOCS")]
    assert not sy.hay_cookies_de_sesion(cookies)


# ---------------------------------------------------------------------------
# WebSocket del CDP contra un servidor falso (socketpair)
# ---------------------------------------------------------------------------

def _frame_servidor(datos, fin=True, opcode=0x1):
    cab = bytearray([(0x80 if fin else 0) | opcode])
    n = len(datos)
    if n < 126:
        cab.append(n)
    elif n <= 0xFFFF:
        cab.append(126); cab += struct.pack(">H", n)
    else:
        cab.append(127); cab += struct.pack(">Q", n)
    return bytes(cab) + datos


def _servidor_cdp_falso(sock, respuesta):
    """Handshake + lee el comando del cliente + responde: primero un
    evento sin «id» (el cliente debe saltarlo) y luego el resultado,
    partido en DOS frames (el cliente debe unir los fragmentos)."""
    peticion = b""
    while b"\r\n\r\n" not in peticion:
        peticion += sock.recv(4096)
    sock.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                 b"Connection: Upgrade\r\nSec-WebSocket-Accept: x\r\n\r\n")
    cab = sock.recv(2)
    n = cab[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", sock.recv(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", sock.recv(8))[0]
    mascara = sock.recv(4)
    carga = b""
    while len(carga) < n:
        carga += sock.recv(n - len(carga))
    comando = json.loads(bytes(b ^ mascara[i % 4] for i, b in enumerate(carga)))
    assert comando["method"] == "Network.getAllCookies"
    evento = json.dumps({"method": "Network.enable",
                         "params": {}}).encode()
    sock.sendall(_frame_servidor(evento))
    mitad = len(respuesta) // 2
    sock.sendall(_frame_servidor(respuesta[:mitad], fin=False))
    sock.sendall(_frame_servidor(respuesta[mitad:], fin=True, opcode=0x0))


def test_cdp_comando_contra_servidor_falso(monkeypatch):
    cliente, servidor = socket.socketpair()
    cookies = [{"domain": ".google.com", "name": "SAPISID", "value": "secreto",
                "path": "/", "secure": True, "httpOnly": True,
                "expires": 1799999999}]
    respuesta = json.dumps({"id": 1, "result": {"cookies": cookies}}).encode()
    hilo = threading.Thread(target=_servidor_cdp_falso,
                            args=(servidor, respuesta), daemon=True)
    hilo.start()
    monkeypatch.setattr(sy.socket, "create_connection",
                        lambda addr, timeout=None: cliente)
    resultado = sy.cdp_comando("ws://127.0.0.1:9223/devtools/browser/x",
                               "Network.getAllCookies")
    assert resultado == {"cookies": cookies}
    hilo.join(timeout=5)


def test_cdp_sin_navegador_devuelve_none(monkeypatch):
    monkeypatch.setattr(sy, "_url_ws_navegador", lambda puerto=9223, timeout=3: None)
    assert sy.cdp_todas_las_cookies() is None


# ---------------------------------------------------------------------------
# Refresco de la sesión (el gancho del resolver)
# ---------------------------------------------------------------------------

def _sin_ventana(monkeypatch):
    monkeypatch.setattr(sy, "_ventana_abierta", False)


def test_refresco_exporta_y_autoriza_reintento(monkeypatch):
    _sin_ventana(monkeypatch)
    monkeypatch.setattr(sy, "exportar_cookies_cdp",
                        lambda carpeta=None: (True, "cookies exportadas"))
    reintentar, aviso = sy.refrescar_sesion()
    assert reintentar and aviso == ""


def test_refresco_sin_sesion_abre_la_ventana_una_sola_vez(monkeypatch):
    _sin_ventana(monkeypatch)
    monkeypatch.setattr(
        sy, "exportar_cookies_cdp",
        lambda carpeta=None: (False, "el perfil del motor aún no tiene "
                                     "la sesión de YouTube iniciada"))
    aperturas = []
    monkeypatch.setattr(sy, "abrir_ventana_login",
                        lambda carpeta=None: aperturas.append(1) or True)
    reintentar, aviso = sy.refrescar_sesion()
    assert not reintentar
    assert "inicia sesión" in aviso and "solo una vez" in aviso
    # Segunda vez en el mismo proceso: NO se abre otra ventana.
    reintentar, aviso = sy.refrescar_sesion()
    assert not reintentar and len(aperturas) == 1
    assert "ya se abrió" in aviso


def test_refresco_sin_navegador_no_abre_nada(monkeypatch):
    _sin_ventana(monkeypatch)
    monkeypatch.setattr(sy, "exportar_cookies_cdp",
                        lambda carpeta=None: (False, "no se encontró Edge "
                                                     "ni Chrome en esta PC"))
    monkeypatch.setattr(sy, "abrir_ventana_login",
                        lambda carpeta=None: pytest.fail("no debe abrir"))
    assert sy.refrescar_sesion() == (False, "")


def test_exportar_sin_sesion_en_el_perfil_no_escribe(tmp_path, monkeypatch):
    monkeypatch.setenv("JDM_CARPETA_DATOS", str(tmp_path))
    monkeypatch.setattr(sy, "cdp_todas_las_cookies",
                        lambda puerto=9223: [_cookie_cdp(".youtube.com", "PREF")])
    ok, motivo = sy.exportar_cookies_cdp()
    assert not ok and "aún no tiene la sesión" in motivo
    assert not (tmp_path / "cookies.txt").exists()


def test_exportar_con_sesion_escribe_cookies_validas(tmp_path, monkeypatch):
    monkeypatch.setenv("JDM_CARPETA_DATOS", str(tmp_path))
    cookies = [_cookie_cdp(".google.com", "SAPISID", httponly=True),
               _cookie_cdp(".youtube.com", "PREF")]
    monkeypatch.setattr(sy, "cdp_todas_las_cookies",
                        lambda puerto=9223: cookies)
    ok, _ = sy.exportar_cookies_cdp()
    assert ok
    valido, _ = sy.cookies_txt_validas(tmp_path / "cookies.txt")
    assert valido


# ---------------------------------------------------------------------------
# Integración con el resolver: un refresco y un reintento, nunca en bucle
# ---------------------------------------------------------------------------

def _falso(monkeypatch, guion):
    llamadas = []

    def fake(url, extra=()):
        llamadas.append((url, tuple(extra)))
        return guion[min(len(llamadas) - 1, len(guion) - 1)]

    monkeypatch.setattr(resolver, "_ejecutar_ytdlp", fake)
    monkeypatch.setattr(resolver, "_ruta_cookies_txt", lambda: None)
    return llamadas


def test_resolver_tras_refresco_ok_reintenta_y_graba(monkeypatch):
    llamadas = _falso(monkeypatch, [FALLO_MURO] * 9 + [OK])
    monkeypatch.setattr(resolver, "gancho_refresco_sesion",
                        lambda: (True, ""))
    assert resolver.resolver_fuente("https://www.youtube.com/watch?v=Dxi1CRQMQZo") == \
        "https://rr5.googlevideo.com/videoplayback?x=1"
    assert len(llamadas) == 10      # 9 de la cadena + 1 tras el refresco


def test_resolver_refresco_sin_sesion_anade_el_aviso_al_error(monkeypatch):
    llamadas = _falso(monkeypatch, [FALLO_MURO])
    monkeypatch.setattr(resolver, "gancho_refresco_sesion",
                        lambda: (False, "Se abrió una ventana: inicia sesión."))
    with pytest.raises(resolver.ErrorResolucion) as e:
        resolver.resolver_fuente("https://www.youtube.com/watch?v=Dxi1CRQMQZo")
    assert "Se abrió una ventana: inicia sesión." in str(e.value)
    assert len(llamadas) == 9       # sin reintento: no hay cookies nuevas


def test_resolver_gancho_que_explota_no_tumba_la_resolucion(monkeypatch):
    _falso(monkeypatch, [FALLO_MURO])

    def gancho_roto():
        raise RuntimeError("boom")

    monkeypatch.setattr(resolver, "gancho_refresco_sesion", gancho_roto)
    with pytest.raises(resolver.ErrorResolucion, match="siguió pidiendo iniciar sesión"):
        resolver.resolver_fuente("https://www.youtube.com/watch?v=Dxi1CRQMQZo")


def test_resolver_error_definitivo_no_llama_al_gancho(monkeypatch):
    _falso(monkeypatch, [(1, "", "ERROR: [youtube] abc: Video unavailable")])
    llamadas_gancho = []
    monkeypatch.setattr(resolver, "gancho_refresco_sesion",
                        lambda: llamadas_gancho.append(1) or (True, ""))
    with pytest.raises(resolver.ErrorResolucion, match="no está disponible"):
        resolver.resolver_fuente("https://www.youtube.com/watch?v=abc")
    assert llamadas_gancho == []


def test_resolver_sin_gancho_comportamiento_1313(monkeypatch):
    llamadas = _falso(monkeypatch, [FALLO_MURO])
    monkeypatch.setattr(resolver, "gancho_refresco_sesion", None)
    with pytest.raises(resolver.ErrorResolucion, match="siguió pidiendo iniciar sesión"):
        resolver.resolver_fuente("https://www.youtube.com/watch?v=Dxi1CRQMQZo")
    assert len(llamadas) == 9


# ---------------------------------------------------------------------------
# Versión y manifiesto
# ---------------------------------------------------------------------------

def test_version_1_3_14_y_modulo_en_manifiesto():
    import worker as worker_mod
    assert worker_mod.VERSION == "1.3.19"
    manifiesto = json.loads((Path(__file__).resolve().parents[1]
                             / "worker-windows" / "manifest.json")
                            .read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.19"
    assert "sesion_youtube.py" in manifiesto["files"]
