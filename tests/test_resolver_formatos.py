"""Error de fuente con videos válidos (captura de Jose, 2026-11-19): el
panel enseñaba «ERROR DE FUENTE — El video/en vivo no está disponible en
la plataforma (borrado, restringido o enlace incorrecto)» para un video
de YouTube que SÍ existe y se reproduce en el reproductor embebido.

Causa: yt-dlp se invocaba con «-f best» (un solo archivo audio+video).
En videos ya publicados cuyos formatos vienen separados, yt-dlp falla
con «Requested format is not available» y _error_desde_stderr lo
clasificaba como video borrado por contener «not available».

Arreglo: selector «best/bv*+ba» (par video+audio como respaldo), el par
se devuelve unido por salto de línea y el Recorder lo abre como dos
entradas de FFmpeg; y el error de formato ya no se clasifica como
video no disponible.
"""
import pytest

import resolver
from recorder import _cmd_grabar


def test_error_formato_no_es_video_borrado(monkeypatch):
    monkeypatch.setattr(
        resolver, "_ejecutar_ytdlp",
        lambda url: (1, "", "ERROR: Requested format is not available. "
                            "Use --list-formats for a list of available formats"))
    with pytest.raises(resolver.ErrorResolucion) as e:
        resolver.resolver_fuente("https://www.youtube.com/watch?v=abc")
    assert "no está disponible" not in str(e.value)
    assert "formato" in str(e.value).lower()


def test_error_video_unavailable_sigue_siendo_no_disponible(monkeypatch):
    monkeypatch.setattr(resolver, "_ejecutar_ytdlp",
                        lambda url: (1, "", "ERROR: [youtube] abc: Video unavailable"))
    with pytest.raises(resolver.ErrorResolucion, match="no está disponible"):
        resolver.resolver_fuente("https://www.youtube.com/watch?v=abc")


def test_resolver_par_video_audio_dos_lineas(monkeypatch):
    monkeypatch.setattr(
        resolver, "_ejecutar_ytdlp",
        lambda url: (0, "https://rr5.googlevideo.com/videoplayback?v=1\n"
                        "https://rr5.googlevideo.com/videoplayback?a=1\n", ""))
    assert resolver.resolver_fuente("https://www.youtube.com/watch?v=abc") == (
        "https://rr5.googlevideo.com/videoplayback?v=1\n"
        "https://rr5.googlevideo.com/videoplayback?a=1")


def test_resolver_una_sola_senal_sin_cambio(monkeypatch):
    monkeypatch.setattr(resolver, "_ejecutar_ytdlp",
                        lambda url: (0, "https://rr5.googlevideo.com/videoplayback?x=1\n", ""))
    assert resolver.resolver_fuente("https://www.youtube.com/watch?v=abc") == \
        "https://rr5.googlevideo.com/videoplayback?x=1"


def test_cmd_grabar_dos_entradas_video_y_audio():
    cmd = _cmd_grabar("https://v.example.com/video\nhttps://v.example.com/audio", "salida")
    assert cmd.count("-i") == 2
    i = cmd.index("-map")
    assert cmd[i:i + 4] == ["-map", "0:v:0?", "-map", "1:a:0?"]


def test_cmd_grabar_una_entrada_como_siempre():
    cmd = _cmd_grabar("https://v.example.com/live.m3u8", "salida")
    assert cmd.count("-i") == 1
    i = cmd.index("-map")
    assert cmd[i:i + 4] == ["-map", "0:v:0?", "-map", "0:a:0?"]
