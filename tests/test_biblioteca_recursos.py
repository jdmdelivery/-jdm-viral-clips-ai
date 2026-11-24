"""Biblioteca local, licencias y análisis sin APIs de pago."""
import json
from pathlib import Path

import biblioteca_recursos as bib
import ollama_local
import descargar_recursos


def test_sembrar_y_detectar_duplicado(tmp_path, monkeypatch):
    monkeypatch.setenv("JDM_BIBLIOTECA_DIR", str(tmp_path))
    cfg = {"carpeta_biblioteca": str(tmp_path)}
    # Crear un seed mínimo
    (tmp_path / "sonidos").mkdir(parents=True)
    wav = tmp_path / "sonidos" / "risas-punch-cc0.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)
    r1 = bib.registrar(cfg, tipo="sonidos", ruta=wav, nombre="Risas",
                       fuente="test", licencia="CC0", emociones=["risa"],
                       situaciones=["chiste"])
    assert r1["ok"] and not r1["duplicado"]
    r2 = bib.registrar(cfg, tipo="sonidos", ruta=wav, nombre="Risas otra vez",
                       fuente="test", licencia="CC0")
    assert r2["duplicado"] is True
    lista = bib.listar(cfg, tipo="sonidos")
    assert lista["total"] == 1


def test_licencia_desconocida_se_rechaza(tmp_path, monkeypatch):
    monkeypatch.setenv("JDM_BIBLIOTECA_DIR", str(tmp_path))
    cfg = {"carpeta_biblioteca": str(tmp_path)}
    (tmp_path / "memes").mkdir(parents=True)
    f = tmp_path / "memes" / "x.png"
    f.write_bytes(b"\x89PNG" + b"\x00" * 40)
    try:
        bib.registrar(cfg, tipo="memes", ruta=f, nombre="x", fuente="x", licencia="ALL RIGHTS RESERVED")
        assert False, "debía rechazar la licencia"
    except ValueError as e:
        assert "Licencia" in str(e)


def test_heuristica_humor_dominicano():
    r = ollama_local.analizar_conversacion("Diablo, qué vaina tú sabe, jajaja wepa")
    assert "risa" in r["emociones"] or r["humor_dominicano"]
    assert r["via"] in {"heuristica_local", "ollama", "sin_texto"}


def test_catalogo_curado_tiene_licencias_ok():
    for item in descargar_recursos.CATALOGO_CURADO:
        assert item["licencia"] in bib.LICENCIAS_OK
        assert item["url"].startswith("https://")
