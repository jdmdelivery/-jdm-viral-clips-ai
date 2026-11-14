"""Tests del generador propio de borradores de CapCut (worker-windows/capcut.py)
y de los trabajos enviar_capcut_* del trabajador. Videos reales con FFmpeg."""
import json, subprocess
from pathlib import Path

import pytest

import capcut


def _video_prueba(path, dur=2):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c=blue:size=360x640:duration={dur}:rate=10",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


def test_crear_borrador_dos_items(tmp_path):
    v1 = _video_prueba(tmp_path / "uno.mp4")
    v2 = _video_prueba(tmp_path / "dos.mp4")
    items = [{"ruta_video": str(v1), "titulo": "Primer clip", "duracion_s": 1.5},
             {"ruta_video": str(v2), "titulo": "Segundo clip viral", "duracion_s": 2.0}]
    carpeta = tmp_path / "borradores"
    ruta = capcut.crear_borrador("Mi sesión", items, carpeta)
    assert ruta.is_dir() and ruta.parent == carpeta

    contenido = json.loads((ruta / "draft_content.json").read_text(encoding="utf-8"))
    assert contenido["name"] == "Mi sesión"
    assert contenido["canvas_config"]["width"] == 1080
    assert contenido["canvas_config"]["height"] == 1920
    total_esperado = int(round((1.5 + 2.0) * 1_000_000))
    assert contenido["duration"] == total_esperado

    tracks = {t["type"]: t for t in contenido["tracks"]}
    assert set(tracks) == {"video", "text"}
    segmentos_video = tracks["video"]["segments"]
    assert len(segmentos_video) == 2
    suma = sum(s["target_timerange"]["duration"] for s in segmentos_video)
    assert suma == contenido["duration"] == total_esperado
    assert segmentos_video[0]["target_timerange"]["start"] == 0
    assert segmentos_video[1]["target_timerange"]["start"] == 1_500_000
    assert segmentos_video[0]["source_timerange"] == {"start": 0, "duration": 1_500_000}
    # El texto se renderiza por encima del video
    assert tracks["text"]["segments"][0]["render_index"] > segmentos_video[0]["render_index"]

    materiales = contenido["materials"]
    assert len(materiales["videos"]) == 2
    assert materiales["videos"][0]["material_name"] == "uno.mp4"
    assert materiales["videos"][0]["path"] == str(v1)
    assert materiales["videos"][0]["duration"] == 1_500_000
    assert materiales["videos"][0]["width"] == 360
    assert materiales["videos"][0]["height"] == 640
    assert len(materiales["texts"]) == 2
    textos = [json.loads(m["content"])["text"] for m in materiales["texts"]]
    assert textos == ["Primer clip", "Segundo clip viral"]
    # Los segmentos referencian materiales existentes
    ids_videos = {m["id"] for m in materiales["videos"]}
    assert {s["material_id"] for s in segmentos_video} == ids_videos

    meta = json.loads((ruta / "draft_meta_info.json").read_text(encoding="utf-8"))
    assert meta["draft_id"] == contenido["id"]
    assert meta["draft_name"] == "Mi sesión"
    assert meta["tm_duration"] == total_esperado
    assert meta["draft_fold_path"] == str(ruta)
    assert meta["draft_timeline_materials_size"] == v1.stat().st_size + v2.stat().st_size


def test_sanear_nombre_y_no_sobrescribe(tmp_path):
    v = _video_prueba(tmp_path / "v.mp4")
    items = [{"ruta_video": str(v), "titulo": "Clip", "duracion_s": 1.0}]
    carpeta = tmp_path / "borradores"
    r1 = capcut.crear_borrador('Clip: "malo" / prueba*?', items, carpeta)
    for caracter in '<>:"/\\|?*':
        assert caracter not in r1.name
    r2 = capcut.crear_borrador('Clip: "malo" / prueba*?', items, carpeta)
    assert r1 != r2 and r2.name.endswith("(2)")
    assert r1.is_dir() and r2.is_dir()
    assert capcut.sanear_nombre("") == "borrador"
    assert capcut.sanear_nombre("   ") == "borrador"
    assert capcut.sanear_nombre("a   b") == "a b"


def test_detectar_carpeta_por_config(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert capcut.detectar_carpeta_borradores(
        {"capcut_carpeta": str(tmp_path)}) == tmp_path
    # Config apuntando a algo que no existe -> sigue buscando y da None
    assert capcut.detectar_carpeta_borradores(
        {"capcut_carpeta": str(tmp_path / "no-existe")}) is None


def test_detectar_carpeta_por_localappdata(tmp_path, monkeypatch):
    destino = (tmp_path / "CapCut" / "User Data" / "Projects"
               / "com.lveditor.draft")
    destino.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert capcut.detectar_carpeta_borradores({}) == destino


def test_detectar_carpeta_ninguna(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert capcut.detectar_carpeta_borradores({}) is None
    assert capcut.detectar_carpeta_borradores(None) is None


def test_copiar_respaldo(tmp_path):
    v = _video_prueba(tmp_path / "clip.mp4")
    items = [{"ruta_video": str(v), "titulo": "Respaldo", "duracion_s": 1.0},
             {"ruta_video": str(tmp_path / "fantasma.mp4"),
              "titulo": "No existe", "duracion_s": 1.0}]
    ruta = capcut.copiar_respaldo("Sesión respaldo", items, tmp_path / "datos")
    assert ruta == tmp_path / "datos" / "capcut" / "Sesión respaldo"
    assert (ruta / "clip.mp4").is_file()
    assert not (ruta / "fantasma.mp4").exists()
    leeme = (ruta / "LEEME.txt").read_text(encoding="utf-8")
    assert "CapCut" in leeme and "Importa" in leeme


def test_worker_enviar_capcut_clip_con_borrador(tmp_path):
    import worker as worker_mod
    v = _video_prueba(tmp_path / "clip.mp4")
    (tmp_path / "borradores").mkdir()
    cfg = {"carpeta_clips": str(tmp_path / "clips"),
           "capcut_carpeta": str(tmp_path / "borradores")}
    job = {"type": "enviar_capcut_clip",
           "payload": {"clip_id": 5, "file_path": str(v),
                       "title": "Momento viral", "duration_s": 2.0}}
    r = worker_mod.procesar(job, cfg)
    assert r["capcut"]["via"] == "borrador"
    assert r["capcut"]["borrador"] == "JDM Clip 5"
    assert r["capcut"]["clips"] == 1
    assert Path(r["capcut"]["carpeta"], "draft_content.json").is_file()


def test_worker_enviar_capcut_sesion_sin_capcut_va_a_respaldo(tmp_path, monkeypatch):
    import worker as worker_mod
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    v = _video_prueba(tmp_path / "clip.mp4")
    cfg = {"carpeta_clips": str(tmp_path / "clips")}
    job = {"type": "enviar_capcut_sesion",
           "payload": {"session_id": 9, "titulo_sesion": "Directo del viernes",
                       "clips": [{"file_path": str(v), "title": "Uno",
                                  "duration_s": 1.0},
                                 {"file_path": str(v), "title": "Dos",
                                  "duration_s": 1.0}]}}
    r = worker_mod.procesar(job, cfg)
    assert r["capcut"]["via"] == "copia"
    assert r["capcut"]["clips"] == 2
    assert "CapCut no detectado" in r["capcut"]["nota"]
    respaldo = Path(r["capcut"]["respaldo"])
    assert (respaldo / "LEEME.txt").is_file() and (respaldo / "clip.mp4").is_file()


def test_worker_enviar_capcut_sesion_vacia_es_error_honesto(tmp_path):
    import worker as worker_mod
    job = {"type": "enviar_capcut_sesion",
           "payload": {"session_id": 1, "titulo_sesion": "X", "clips": []}}
    with pytest.raises(ValueError):
        worker_mod.procesar(job, {"carpeta_clips": str(tmp_path)})
