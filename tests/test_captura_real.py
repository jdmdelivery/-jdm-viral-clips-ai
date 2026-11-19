"""Captura real, error de autenticación y un MP4 generado de punta a punta.
No llama a YouTube ni intenta saltarse su inicio de sesión."""
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

import main as apimain
import analyzer
import resolver
import worker as worker_mod
from clipper import clip_tiene_video

client = TestClient(apimain.app)


def _usuario(nombre):
    email = f"{nombre}@test.local"
    r = client.post("/auth/register", json={"email": email, "password": "clave-larga"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + client.post("/auth/login", json={"email": email, "password": "clave-larga"}).json()["token"]}


def _worker(h):
    r = client.post("/workers/register", json={"name": "pc-captura", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_youtube_que_pide_sesion_no_se_reintenta_ni_marca_grabando():
    mensaje = resolver._error_desde_stderr("ERROR: Sign in to confirm you’re not a bot")
    assert "iniciar sesión" in mensaje
    assert "No se intenta" in mensaje
    h = _usuario("yt")
    wh = _worker(h)
    ses = client.post("/sessions", json={"title": "YouTube cerrado"}, headers=h).json()
    client.post(f"/sessions/{ses['id']}/sources",
                json={"url": "https://www.youtube.com/watch?v=dmd5f5knGrw"}, headers=h)
    client.post(f"/sessions/{ses['id']}/radar", json={"activo": True}, headers=h)
    inicio = client.post(f"/sessions/{ses['id']}/start", headers=h).json()
    job = client.post("/jobs/claim", headers=wh).json()["job"]
    assert job["id"] == inicio["job_id"] or job["type"] == "radar_sesion"
    # El radar se encoló primero: complétalo sin captura y luego falla el inicio.
    while job["type"] != "iniciar_grabacion":
        client.post(f"/jobs/{job['id']}/complete", json={"result": {"estado": "sin_grabacion_activa"}}, headers=wh)
        job = client.post("/jobs/claim", headers=wh).json()["job"]
    r = client.post(f"/jobs/{job['id']}/fail", json={"error": mensaje}, headers=wh)
    assert r.json()["status"] == "fallido"
    publica = next(s for s in client.get("/sessions", headers=h).json() if s["id"] == ses["id"])
    assert publica["status"] == "error"
    assert publica["estado_real"] == "ERROR DE FUENTE"
    assert publica["radar_sin_fuente"] is True
    assert publica["bytes_captura"] == 0
    assert "iniciar sesión" in publica["error_fuente"]
    assert publica["estado_real"] != "GRABANDO"


def test_completar_sin_bytes_no_es_grabando():
    h = _usuario("vacio"); wh = _worker(h)
    ses = client.post("/sessions", json={"title": "Sin bytes"}, headers=h).json()
    client.post(f"/sessions/{ses['id']}/sources",
                json={"url": "https://ejemplo.com/live/index.m3u8"}, headers=h)
    inicio = client.post(f"/sessions/{ses['id']}/start", headers=h).json()
    job = client.post("/jobs/claim", headers=wh).json()["job"]
    assert job["id"] == inicio["job_id"]
    client.post(f"/jobs/{job['id']}/complete", json={"result": {"estado": "grabando"}}, headers=wh)
    publica = next(s for s in client.get("/sessions", headers=h).json() if s["id"] == ses["id"])
    assert publica["status"] == "error"
    assert publica["estado_real"] == "ERROR DE FUENTE"
    assert publica["bytes_captura"] == 0


def test_archivo_local_genera_mp4_reproducible(tmp_path, monkeypatch):
    """Fuente local → captura → buffer → análisis → clip 1080×1920 con audio."""
    monkeypatch.setattr(worker_mod, "grabaciones", {})
    monkeypatch.setattr(worker_mod, "radares", {})
    fuente = tmp_path / "fuente.mp4"
    enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    quieto = tmp_path / "q.mp4"
    fuerte = tmp_path / "f.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:size=640x360:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=220:duration=8", "-af", "volume=0.02",
                    "-shortest", *enc, str(quieto)], check=True, timeout=120)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=white:size=640x360:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=880:duration=8", "-af", "volume=2",
                    "-shortest", *enc, str(fuerte)], check=True, timeout=120)
    lista = tmp_path / "lista.txt"
    lista.write_text(f"file '{quieto.name}'\nfile '{fuerte.name}'\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lista),
                    "-c", "copy", str(fuente)], check=True, timeout=120)
    cfg = {"carpeta_grabaciones": str(tmp_path / "grab"), "carpeta_clips": str(tmp_path / "clips"),
           "pre_s": 2, "post_s": 2, "buffer_min": 5, "espera_captura_s": 40, "radar_umbral": 40,
           "radar_intervalo_s": 1}
    res = worker_mod.procesar({"id": 1, "type": "iniciar_grabacion",
                                "payload": {"session_id": 7, "url": str(fuente), "mode": "general"}}, cfg, None)
    assert res["estado"] == "grabando" and res["bytes"] > 0 and res["tiene_video"] is True
    rec = worker_mod.grabaciones[7]
    rec.hilo.join(timeout=60)
    assert not rec.hilo.is_alive()
    completa = rec.stop()
    assert completa and Path(completa).stat().st_size > 1000
    # El archivo unido es la captura. El análisis usa la fuente ya capturada.
    out = analyzer.analizar_video(completa, tmp_path / "analisis", pre_s=1, post_s=1,
                                  umbral_evento=30, max_clips=1)
    assert out["eventos"], out["mediciones"]
    assert out["clips"], out["mediciones"]
    clip = Path(out["clips"][0]["file_path"])
    assert clip_tiene_video(clip)
    info = _ffprobe(clip)
    assert '"width": 1080' in info and '"height": 1920' in info
    assert "audio" in info
    # Descarga: leer el MP4 completo y comprobar que no está vacío.
    datos = clip.read_bytes()
    assert len(datos) > 1000 and datos[4:8] == b"ftyp"
    # Un video plano y en silencio no debe inventar un momento.
    plano = tmp_path / "plano.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:size=320x240:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=100:duration=6", "-af", "volume=0.001",
                    "-shortest", *enc, str(plano)], check=True, timeout=120)
    vacio = analyzer.analizar_video(plano, tmp_path / "plano-out", umbral_evento=80, max_clips=1)
    assert vacio["clips"] == []
