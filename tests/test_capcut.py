"""CapCut retirada (orden de Jose, 2026-11-14): el generador ya no existe
y el trabajador rechaza los trabajos viejos sin crear carpetas ni borradores."""
from pathlib import Path

import pytest

import worker as worker_mod

RAIZ = Path(__file__).resolve().parents[1]
CARPETA = RAIZ / "worker-windows"


def test_crear_borrador_dos_items():
    assert not (CARPETA / "capcut.py").exists()


def test_sanear_nombre_y_no_sobrescribe():
    with pytest.raises(ModuleNotFoundError):
        __import__("capcut")


def test_detectar_carpeta_por_config():
    import json
    manifiesto = json.loads((CARPETA / "manifest.json").read_text(encoding="utf-8"))
    assert "capcut.py" not in manifiesto["files"]


def test_detectar_carpeta_por_localappdata():
    texto = (CARPETA / "worker.py").read_text(encoding="utf-8").lower()
    assert "capcut" not in texto


def test_detectar_carpeta_ninguna():
    texto = (CARPETA / "sync.py").read_text(encoding="utf-8").lower()
    assert "capcut" not in texto


def test_copiar_respaldo():
    for pagina in ("apps/web/app.js", "apps/web/index.html"):
        texto = (RAIZ / pagina).read_text(encoding="utf-8").lower()
        assert "capcut" not in texto, pagina


def test_worker_enviar_capcut_clip_con_borrador(tmp_path):
    job = {"type": "enviar_capcut_clip",
           "payload": {"clip_id": 5, "file_path": str(tmp_path / "clip.mp4"),
                       "title": "Momento viral", "duration_s": 2.0}}
    with pytest.raises(ValueError, match="no soportado"):
        worker_mod.procesar(job, {"carpeta_clips": str(tmp_path)})
    assert not (tmp_path / "capcut").exists()


def test_worker_enviar_capcut_sesion_sin_capcut_va_a_respaldo(tmp_path):
    job = {"type": "enviar_capcut_sesion",
           "payload": {"session_id": 9, "titulo_sesion": "Directo del viernes",
                       "clips": [{"file_path": str(tmp_path / "a.mp4"), "title": "Uno",
                                  "duration_s": 1.0}]}}
    with pytest.raises(ValueError, match="no soportado"):
        worker_mod.procesar(job, {"carpeta_clips": str(tmp_path)})
    assert not (tmp_path / "capcut").exists()


def test_worker_enviar_capcut_sesion_vacia_es_error_honesto(tmp_path):
    job = {"type": "enviar_capcut_sesion",
           "payload": {"session_id": 1, "titulo_sesion": "X", "clips": []}}
    with pytest.raises(ValueError, match="no soportado"):
        worker_mod.procesar(job, {"carpeta_clips": str(tmp_path)})
