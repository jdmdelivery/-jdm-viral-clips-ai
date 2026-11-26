"""Muro de login/anti-bots de YouTube (sesión #20 de Jose, 2026-11-23):
a watch?v=Dxi1CRQMQZo yt-dlp le devolvía «Sign in to confirm you're
not a bot» y el panel enseñaba «ERROR DE FUENTE — La plataforma pide
iniciar sesión…» aunque el video es público y otros videos resolvían
bien. El resolver se rendía en el primer intento.

Arreglo: CADENA de reintentos reales en resolver_fuente —
1) vía habitual, 2) clientes alternativos de YouTube
(player_client tv/android/ios/web_safari/web_embedded),
3) el cookies.txt de la sesión del dueño si existe, 4) la sesión del
navegador de la propia PC (--cookies-from-browser chrome/edge/firefox;
yt-dlp lee las cookies localmente, nada se sube). Solo el muro de
login (o un navegador que no entrega sus cookies) pasa al siguiente
intento; el éxito corta la cadena y los errores definitivos (privado,
no disponible, formato) se reportan al momento. El error final dice
qué se intentó. Todo con el ejecutor de yt-dlp SIMULADO: ningún test
llama a YouTube de verdad.

v1.3.13 (2026-11-26): el muro volvió a cerrar TODAS las vías de la
sesión #34 (que había grabado bien la noche anterior). Se suman el
cliente «tv» (suele pasar sin sesión), «web_embedded» y la vía del
cookies.txt exportado por el dueño, inmune al cifrado de cookies de
Chrome/Edge modernos que rompe --cookies-from-browser.
"""
import pytest

import resolver

MURO = ("ERROR: [youtube] Dxi1CRQMQZo: Sign in to confirm you're not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication")
OK = (0, "https://rr5.googlevideo.com/videoplayback?x=1\n", "")
FALLO_MURO = (1, "", MURO)


def _falso(monkeypatch, guion):
    """Sustituye _ejecutar_ytdlp por un guion: lista de resultados por
    intento (el último se repite si la cadena sigue). Devuelve la
    lista de llamadas (url, extra) registradas."""
    llamadas = []

    def fake(url, extra=()):
        llamadas.append((url, tuple(extra)))
        return guion[min(len(llamadas) - 1, len(guion) - 1)]

    monkeypatch.setattr(resolver, "_ejecutar_ytdlp", fake)
    return llamadas


def test_camino_normal_sin_cambios_un_solo_intento(monkeypatch):
    llamadas = _falso(monkeypatch, [OK])
    assert resolver.resolver_fuente("https://www.youtube.com/watch?v=abc") == \
        "https://rr5.googlevideo.com/videoplayback?x=1"
    assert len(llamadas) == 1 and llamadas[0][1] == ()


def test_login_en_el_primero_pasa_al_cliente_tv(monkeypatch):
    llamadas = _falso(monkeypatch, [FALLO_MURO, OK])
    assert resolver.resolver_fuente("https://www.youtube.com/watch?v=Dxi1CRQMQZo") == \
        "https://rr5.googlevideo.com/videoplayback?x=1"
    assert len(llamadas) == 2
    assert llamadas[1][1] == ("--extractor-args", "youtube:player_client=tv")


def test_login_en_todos_error_final_honesto_con_lo_intentado(monkeypatch):
    monkeypatch.setattr(resolver, "_ruta_cookies_txt", lambda: None)
    llamadas = _falso(monkeypatch, [FALLO_MURO])
    with pytest.raises(resolver.ErrorResolucion) as e:
        resolver.resolver_fuente("https://www.youtube.com/watch?v=Dxi1CRQMQZo")
    msg = str(e.value)
    # 1 habitual + 5 clientes + 3 navegadores = 9 intentos reales.
    assert len(llamadas) == 9
    for via in ("vía habitual", "tv", "android", "ios", "web_safari",
                "web_embedded", "chrome", "edge", "firefox"):
        assert via in msg, f"el error final no cuenta el intento «{via}»"
    assert "siguió pidiendo iniciar sesión" in msg
    assert "cookies.txt" in msg


def test_error_definitivo_corta_la_cadena(monkeypatch):
    llamadas = _falso(monkeypatch, [(1, "", "ERROR: [youtube] abc: Video unavailable")])
    with pytest.raises(resolver.ErrorResolucion, match="no está disponible"):
        resolver.resolver_fuente("https://www.youtube.com/watch?v=abc")
    assert len(llamadas) == 1


def test_chrome_bloqueado_pasa_a_edge(monkeypatch):
    monkeypatch.setattr(resolver, "_ruta_cookies_txt", lambda: None)
    llamadas = _falso(monkeypatch, [
        FALLO_MURO, FALLO_MURO, FALLO_MURO, FALLO_MURO, FALLO_MURO, FALLO_MURO,
        (1, "", "ERROR: Could not copy Chrome cookie database"),
        OK,
    ])
    assert resolver.resolver_fuente("https://www.youtube.com/watch?v=Dxi1CRQMQZo") == \
        "https://rr5.googlevideo.com/videoplayback?x=1"
    assert llamadas[6][1] == ("--cookies-from-browser", "chrome")
    assert llamadas[7][1] == ("--cookies-from-browser", "edge")


def test_cookies_txt_en_la_cadena_tras_los_clientes(monkeypatch):
    monkeypatch.setattr(resolver, "_ruta_cookies_txt",
                        lambda: "C:\\Users\\thene\\JDMViralClips\\cookies.txt")
    intentos = resolver._intentos_resolucion("https://www.youtube.com/watch?v=abc")
    extras = [e for _, e in intentos]
    via_cookies = ("--cookies", "C:\\Users\\thene\\JDMViralClips\\cookies.txt")
    assert via_cookies in extras
    # Tras el último cliente de YouTube y antes de los navegadores.
    assert extras.index(via_cookies) == 6
    assert extras.index(("--cookies-from-browser", "chrome")) == 7


def test_cookies_txt_resuelve_tras_el_muro(monkeypatch):
    monkeypatch.setattr(resolver, "_ruta_cookies_txt",
                        lambda: "C:\\Users\\thene\\JDMViralClips\\cookies.txt")
    llamadas = _falso(monkeypatch, [FALLO_MURO] * 6 + [OK])
    assert resolver.resolver_fuente("https://www.youtube.com/watch?v=Dxi1CRQMQZo") == \
        "https://rr5.googlevideo.com/videoplayback?x=1"
    assert llamadas[6][1] == ("--cookies", "C:\\Users\\thene\\JDMViralClips\\cookies.txt")


def test_otra_plataforma_sin_clientes_youtube_pero_con_cookies(monkeypatch):
    monkeypatch.setattr(resolver, "_ruta_cookies_txt", lambda: None)
    intentos = resolver._intentos_resolucion("https://www.facebook.com/watch?v=1")
    extras = [e for _, e in intentos]
    assert not any("player_client" in " ".join(e) for e in extras)
    assert ("--cookies-from-browser", "chrome") in extras


def test_senal_directa_no_toca_ytdlp(monkeypatch):
    llamadas = _falso(monkeypatch, [OK])
    assert resolver.resolver_fuente("https://cdn.example.com/live.m3u8") == \
        "https://cdn.example.com/live.m3u8"
    assert llamadas == []


def test_sin_ytdlp_instalado_error_inmediato(monkeypatch):
    llamadas = _falso(monkeypatch, [(1, "", "No module named yt_dlp")])
    with pytest.raises(resolver.ErrorResolucion, match="yt-dlp no está instalado"):
        resolver.resolver_fuente("https://www.youtube.com/watch?v=abc")
    assert len(llamadas) == 1
