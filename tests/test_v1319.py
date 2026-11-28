"""v1.3.19 — «The page needs to be reloaded» ya no aborta la cadena.

El 2026-11-27 Jose no pudo grabar el en vivo de siempre
(watch?v=Dxi1CRQMQZo): yt-dlp devolvió «The page needs to be
reloaded» y el panel lo enseñó como error final. Ese error es
TRANSITORIO (la página llegó incompleta en ese intento), pero el
resolver lo trataba como definitivo y abortaba toda la cadena en el
primer intento, sin probar los demás clientes ni las cookies.

Arreglo: el error transitorio pasa al siguiente intento como el muro
de login; si TODAS las vías caen con él, resolver_fuente espera un
respiro y repite la cadena una sola vez antes de rendirse con un
mensaje claro en español.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import resolver
import worker as worker_mod
from resolver import ErrorResolucion

ROOT = Path(__file__).resolve().parents[1]
URL = "https://www.youtube.com/watch?v=Dxi1CRQMQZo"
OK = (0, "https://rr5.googlevideo.com/videoplayback?x=1\n", "")
FALLO_RELOAD = (1, "", "ERROR: [youtube] Dxi1CRQMQZo: The page needs to be reloaded.")
FALLO_PRIVADO = (1, "", "ERROR: [youtube] Dxi1CRQMQZo: Video unavailable")


def _falso(monkeypatch, guion):
    llamadas = []

    def fake(url, extra=()):
        llamadas.append((url, tuple(extra)))
        return guion[min(len(llamadas) - 1, len(guion) - 1)]

    monkeypatch.setattr(resolver, "_ejecutar_ytdlp", fake)
    monkeypatch.setattr(resolver, "_ruta_cookies_txt", lambda: None)
    monkeypatch.setattr(resolver, "time",
                        SimpleNamespace(sleep=lambda s: None))
    return llamadas


def test_reload_en_el_primero_pasa_al_siguiente_cliente(monkeypatch):
    llamadas = _falso(monkeypatch, [FALLO_RELOAD, OK])
    assert resolver.resolver_fuente(URL).startswith("https://rr5")
    assert len(llamadas) == 2


def test_reload_en_todos_repite_la_cadena_una_vez(monkeypatch):
    llamadas = _falso(monkeypatch, [FALLO_RELOAD])
    with pytest.raises(ErrorResolucion) as e:
        resolver.resolver_fuente(URL)
    # Cadena de 9 intentos (habitual + 5 clientes + 3 navegadores),
    # repetida una vez por el reintento transitorio: 18 llamadas.
    assert len(llamadas) == 18
    assert "página incompleta" in str(e.value)


def test_reload_y_luego_error_definitivo_corta(monkeypatch):
    llamadas = _falso(monkeypatch, [FALLO_RELOAD, FALLO_PRIVADO])
    with pytest.raises(ErrorResolucion) as e:
        resolver.resolver_fuente(URL)
    assert len(llamadas) == 2
    assert "no está disponible" in str(e.value)


def test_version_1_3_19_y_manifiesto():
    assert worker_mod.VERSION == "1.3.19"
    manifiesto = json.loads((ROOT / "worker-windows" / "manifest.json")
                            .read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.19"
    assert "resolver.py" in manifiesto["files"]
