"""Lote 2026-11-14 (2): enlaces de plataformas + radar EN VIVO.

Parte 1 — regla de Jose «quiero que grabe todos los en vivo de todas las
plataformas»:
- API: validate_source acepta páginas de plataformas conocidas como kind
  «plataforma» (la señal la resuelve el trabajador) y mantiene el anti-SSRF.
- resolver.py: directas tal cual; plataformas vía yt-dlp (simulado); errores
  honestos específicos (no comenzó / privado / no disponible).
- Recorder: re-resolución de la señal en las reconexiones (callable).

Parte 2 — radar en vivo:
- API: POST /sessions/{id}/radar (flag inmediato + job), ingesta del
  trabajador POST /workers/sessions/{id}/live-events con dedup >70%,
  GET /events?session_id=.
- Humo e2e: sesión + señal HLS local «en vivo» (productor -re); SIN detener
  la grabación aparecen eventos en la API y un clip 1080x1920 de 60–120 s;
  al detener se crea el Project y el radar termina."""
import json
import subprocess
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main as apimain
import resolver
import worker as worker_mod
from recorder import Recorder

client = TestClient(apimain.app)
EMAIL, PW = "radarlote@test.do", "clave-segura-123"


# ---------- utilidades ----------
def _usuario():
    client.post("/auth/register", json={"email": EMAIL, "password": PW})
    r = client.post("/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h):
    r = client.post("/workers/register", json={"name": "PC radar", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _sesion(h, titulo="Sesión radar"):
    r = client.post("/sessions", json={"title": titulo, "mode": "general"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _reclamar(wh, job_id, intentos=300):
    """Reclama hasta llegar a MI job; los ajenos se completan vacíos."""
    for _ in range(intentos):
        r = client.post("/jobs/claim", headers=wh)
        assert r.status_code == 200, r.text
        job = r.json()["job"]
        assert job is not None, f"La cola se vació antes de llegar al job {job_id}"
        if job["id"] == job_id:
            return job
        client.post(f"/jobs/{job['id']}/complete", json={"result": {}}, headers=wh)
    raise AssertionError(f"El job {job_id} no fue reclamado")


def _validar(h, url):
    r = client.post("/sessions/1/validate", json={"url": url}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- Parte 1: validación de fuentes ----------
@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc123",
    "https://www.youtube.com/live/abc123",
    "https://m.youtube.com/watch?v=abc123",
    "https://youtu.be/abc123",
    "https://www.facebook.com/pagina/videos/123",
    "https://fb.watch/abc123/",
    "https://www.twitch.tv/uncanal",
    "https://www.tiktok.com/@usuario/live",
    "https://www.instagram.com/usuario/live",
    "https://x.com/usuario/status/123",
])
def test_paginas_de_plataforma_se_aceptan(url):
    d = _validar(_usuario(), url)
    assert d["allowed"] is True
    assert d["kind"] == "plataforma"
    assert "tu PC resuelve la señal al momento de grabar" in d["explanation"]


def test_senales_directas_siguen_como_siempre():
    h = _usuario()
    d = _validar(h, "https://8.8.8.8/live.m3u8")  # IP pública literal: sin DNS
    assert d["allowed"] is True and d["kind"] == "hls"
    d = _validar(h, "rtmp://8.8.8.8/live/clave")
    assert d["allowed"] is True and d["kind"] == "rtmp"


def test_antissrf_se_mantiene():
    h = _usuario()
    assert _validar(h, "http://192.168.1.10/live.m3u8")["allowed"] is False
    assert _validar(h, "http://localhost:8080/x.m3u8")["allowed"] is False
    assert _validar(h, "http://127.0.0.1/admin")["allowed"] is False


def test_dominio_parecido_no_es_plataforma():
    # Emparejado exacto/subdominio: los imitadores NO cuelan como plataforma.
    assert apimain._es_plataforma("youtube.com.evil.com") is False
    assert apimain._es_plataforma("notyoutube.com") is False
    assert apimain._es_plataforma("www.youtube.com") is True
    d = _validar(_usuario(), "https://youtube.com.evil.com/watch?v=x")
    assert d["kind"] != "plataforma"


def test_fuente_plataforma_se_puede_iniciar():
    """Una sesión con fuente de plataforma pasa el filtro de /start (allowed)."""
    h = _usuario(); wh = _worker(h)
    ses = _sesion(h, "Con fuente YouTube")
    r = client.post(f"/sessions/{ses['id']}/sources",
                    json={"url": "https://www.youtube.com/watch?v=abc123"}, headers=h)
    assert r.json()["allowed"] is True and r.json()["kind"] == "plataforma"
    r = client.post(f"/sessions/{ses['id']}/start", headers=h)
    assert r.status_code == 200, r.text
    job = _reclamar(wh, r.json()["job_id"])
    assert job["payload"]["url"] == "https://www.youtube.com/watch?v=abc123"
    client.post(f"/jobs/{job['id']}/complete", json={"result": {}}, headers=wh)
    client.post(f"/sessions/{ses['id']}/stop", headers=h)


# ---------- Parte 1: resolver del trabajador ----------
def test_resolver_directa_tal_cual_sin_ytdlp(monkeypatch):
    def prohibido(url, *args):
        raise AssertionError("yt-dlp no debe llamarse para señales directas")
    monkeypatch.setattr(resolver, "_ejecutar_ytdlp", prohibido)
    assert resolver.resolver_fuente("https://cdn.example.com/live.m3u8") == "https://cdn.example.com/live.m3u8"
    assert resolver.resolver_fuente("rtmp://8.8.8.8/live/x") == "rtmp://8.8.8.8/live/x"
    assert resolver.resolver_fuente("https://media.otro.com/video.mp4") == "https://media.otro.com/video.mp4"


def test_resolver_pagina_youtube_devuelve_senal(monkeypatch):
    monkeypatch.setattr(resolver, "_ejecutar_ytdlp",
                        lambda url, *args: (0, "https://rr5.googlevideo.com/videoplayback?expire=1\n", ""))
    assert resolver.resolver_fuente("https://www.youtube.com/watch?v=abc") == \
        "https://rr5.googlevideo.com/videoplayback?expire=1"


def test_resolver_error_en_vivo_no_comenzo(monkeypatch):
    monkeypatch.setattr(resolver, "_ejecutar_ytdlp",
                        lambda url, *args: (1, "", "ERROR: [youtube] abc: This live event will begin in 2 hours."))
    with pytest.raises(resolver.ErrorResolucion, match="todavía no comienza"):
        resolver.resolver_fuente("https://www.youtube.com/watch?v=abc")


def test_resolver_error_privado(monkeypatch):
    monkeypatch.setattr(resolver, "_ejecutar_ytdlp",
                        lambda url, *args: (1, "", "ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video."))
    with pytest.raises(resolver.ErrorResolucion, match="privado"):
        resolver.resolver_fuente("https://youtu.be/abc")


def test_resolver_error_no_disponible(monkeypatch):
    monkeypatch.setattr(resolver, "_ejecutar_ytdlp",
                        lambda url, *args: (1, "", "ERROR: [youtube] abc: Video unavailable"))
    with pytest.raises(resolver.ErrorResolucion, match="no está disponible"):
        resolver.resolver_fuente("https://www.twitch.tv/abc")


def test_resolver_error_sin_senal(monkeypatch):
    monkeypatch.setattr(resolver, "_ejecutar_ytdlp", lambda url, *args: (0, "", ""))
    with pytest.raises(resolver.ErrorResolucion, match="ninguna señal"):
        resolver.resolver_fuente("https://www.youtube.com/watch?v=abc")


def test_resolver_sin_ytdlp_instalado(monkeypatch):
    monkeypatch.setattr(resolver, "_ejecutar_ytdlp",
                        lambda url, *args: (1, "", "/usr/bin/python: No module named yt_dlp"))
    with pytest.raises(resolver.ErrorResolucion, match="yt-dlp no está instalado"):
        resolver.resolver_fuente("https://www.youtube.com/watch?v=abc")


def test_trabajador_falla_honesto_si_no_resuelve(monkeypatch, tmp_path):
    def roto(url):
        raise resolver.ErrorResolucion("El en vivo todavía no comienza: la plataforma aún no emite señal.")
    monkeypatch.setattr(worker_mod, "resolver_fuente", roto)
    cfg = {"carpeta_grabaciones": str(tmp_path / "g"), "carpeta_clips": str(tmp_path / "c")}
    with pytest.raises(resolver.ErrorResolucion, match="todavía no comienza"):
        worker_mod.procesar({"id": 1, "type": "iniciar_grabacion",
                             "payload": {"session_id": 987654,
                                         "url": "https://www.youtube.com/watch?v=x"}}, cfg, None)


def test_recorder_reresuelve_en_reconexion(tmp_path):
    llamadas = []
    def fresco():
        llamadas.append(1)
        return "https://signal.example.com/nueva.m3u8"
    rec = Recorder("https://signal.example.com/vieja.m3u8", tmp_path / "g", resolver=fresco)
    rec._refrescar_fuente()
    assert rec.fuente == "https://signal.example.com/nueva.m3u8" and llamadas
    # Si el resolver falla, se conserva la última URL conocida.
    def roto():
        raise resolver.ErrorResolucion("sin señal")
    rec.resolver = roto
    rec._refrescar_fuente()
    assert rec.fuente == "https://signal.example.com/nueva.m3u8"
    # Sin resolver: comportamiento histórico (no toca la fuente).
    rec2 = Recorder("https://signal.example.com/fija.m3u8", tmp_path / "g2")
    rec2._refrescar_fuente()
    assert rec2.fuente == "https://signal.example.com/fija.m3u8"


# ---------- Parte 2: API del radar ----------
def test_radar_flag_inmediato_y_job():
    h = _usuario()
    ses = _sesion(h)
    assert ses["radar_activo"] is False
    r = client.post(f"/sessions/{ses['id']}/radar", json={"activo": True}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["radar_activo"] is True and d["job_id"]
    jobs = client.get("/jobs", headers=h).json()
    job = next(j for j in jobs if j["id"] == d["job_id"])
    assert job["type"] == "radar_sesion"
    lista = client.get("/sessions", headers=h).json()
    assert next(s for s in lista if s["id"] == ses["id"])["radar_activo"] is True
    r = client.post(f"/sessions/{ses['id']}/radar", json={"activo": False}, headers=h)
    assert r.json()["radar_activo"] is False
    r = client.post("/sessions/999999/radar", json={"activo": True}, headers=h)
    assert r.status_code == 404


def test_radar_sesion_sin_grabacion_es_honesto(tmp_path):
    cfg = {"carpeta_grabaciones": str(tmp_path / "g"), "carpeta_clips": str(tmp_path / "c")}
    res = worker_mod.procesar({"id": 1, "type": "radar_sesion",
                               "payload": {"session_id": 987655, "activo": True}}, cfg, None)
    assert res["radar_activo"] is False and res["estado"] == "sin_grabacion_activa"
    res = worker_mod.procesar({"id": 2, "type": "radar_sesion",
                               "payload": {"session_id": 987655, "activo": False}}, cfg, None)
    assert res["radar_activo"] is False


def test_ingesta_en_vivo_persiste_y_deduplica():
    h = _usuario(); wh = _worker(h)
    ses = _sesion(h)
    payload = {"events": [
                   {"start_s": 10, "end_s": 20, "type": "gritos", "mode": "general",
                    "score": 90, "breakdown": {"intensidad_audio": 40}},
                   {"start_s": 30, "end_s": 40, "type": "discusion", "mode": "general",
                    "score": 85, "breakdown": {}}],
               "clips": [{"event_index": 0, "file_path": "C:/clips/clip-radar-10s.mp4",
                          "format": "9:16", "duration_s": 45.0, "score": 90,
                          "title": "Momento radar", "titles": {}, "subtitles": False}]}
    r = client.post(f"/workers/sessions/{ses['id']}/live-events", json=payload, headers=wh)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["eventos_guardados"] == 2 and d["eventos_duplicados"] == 0 and d["clips_guardados"] == 1
    evs = client.get(f"/events?session_id={ses['id']}", headers=h).json()
    assert len(evs) == 2 and evs[0]["score"] == 90
    clips = [c for c in client.get("/clips", headers=h).json() if c["session_id"] == ses["id"]]
    assert len(clips) == 1 and clips[0]["event_id"] == evs[0]["id"]
    # Segunda ingesta igual (más un solape del 80%): 0 nuevos, 3 duplicados.
    payload2 = {"events": payload["events"] + [
        {"start_s": 12, "end_s": 22, "type": "gritos", "mode": "general", "score": 70, "breakdown": {}}],
        "clips": []}
    d = client.post(f"/workers/sessions/{ses['id']}/live-events", json=payload2, headers=wh).json()
    assert d["eventos_guardados"] == 0 and d["eventos_duplicados"] == 3
    assert len(client.get(f"/events?session_id={ses['id']}", headers=h).json()) == 2


def test_ingesta_requiere_token_de_trabajador():
    h = _usuario()
    ses = _sesion(h)
    r = client.post(f"/workers/sessions/{ses['id']}/live-events", json={"events": [], "clips": []})
    assert r.status_code == 401
    r = client.post(f"/workers/sessions/{ses['id']}/live-events", json={"events": [], "clips": []},
                    headers={"X-Worker-Token": "token-falso"})
    assert r.status_code == 401
    wh = _worker(h)
    r = client.post("/workers/sessions/999999/live-events", json={"events": [], "clips": []}, headers=wh)
    assert r.status_code == 404


# ---------- Parte 2: humo e2e del radar en vivo ----------
def _video_radar(path):
    """110 s: 0–30 tranquilo · 30–60 FUERTE (colores cada 5 s + audio en
    ráfagas 1 s sí / 1 s no) · 60–110 tranquilo. Keyframes densos para que el
    productor HLS por copia pueda cortar segmentos. El momento va en el
    medio para que su clip cumpla la regla de Jose de 60–120 s (v1.3.6)
    con el contexto real disponible durante la grabación."""
    tmp = Path(path).parent
    enc = ["-c:v", "libx264", "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
           "-pix_fmt", "yuv420p", "-c:a", "aac"]
    def run(cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stderr[-500:]
    q1 = tmp / "rq1.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:size=640x360:duration=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-af", "volume=0.02",
         "-shortest", *enc, str(q1)])
    partes_v = []
    for i, c in enumerate(("red", "green", "yellow", "white", "magenta", "cyan")):
        p = tmp / f"rf{i}.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={c}:size=640x360:duration=5",
             "-an", *enc, str(p)])
        partes_v.append(p)
    lista = tmp / "rlista.txt"
    lista.write_text("".join(f"file '{p.name}'\n" for p in partes_v))
    fuerte_v = tmp / "rfuerte-v.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lista),
         "-c", "copy", str(fuerte_v)])
    fuerte = tmp / "rfuerte.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(fuerte_v),
         "-f", "lavfi", "-i", r"aevalsrc=0.85*sin(2*PI*440*t)*lt(mod(t\,2)\,1):s=44100:d=30",
         "-map", "0:v", "-map", "1:a", "-shortest", *enc, str(fuerte)])
    q2 = tmp / "rq2.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=green:size=640x360:duration=50",
         "-f", "lavfi", "-i", "sine=frequency=330:duration=50", "-af", "volume=0.02",
         "-shortest", *enc, str(q2)])
    lista2 = tmp / "rlista2.txt"
    lista2.write_text("".join(f"file '{p.name}'\n" for p in (q1, fuerte, q2)))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lista2),
         *enc, str(path)])
    return path


class _ApiPuente:
    """Adaptador ApiClient-sobre-TestClient para el radar (mismo contrato
    que sync.ApiClient.reportar_en_vivo)."""
    def __init__(self, wh):
        self.wh = wh
    def reportar_en_vivo(self, session_id, events, clips):
        r = client.post(f"/workers/sessions/{session_id}/live-events",
                        json={"events": events, "clips": clips}, headers=self.wh)
        r.raise_for_status()
        return r.json()


def _ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-show_entries", "format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout)


def test_radar_en_vivo_e2e(tmp_path):
    h = _usuario(); wh = _worker(h)
    ses = _sesion(h, "Radar e2e")
    sid = ses["id"]
    video = _video_radar(tmp_path / "fuente-radar.mp4")
    # Señal «en vivo»: productor HLS en tiempo real servido por HTTP local.
    hls_dir = tmp_path / "hls"; hls_dir.mkdir()
    prod = subprocess.Popen(
        ["ffmpeg", "-re", "-i", str(video), "-c", "copy", "-f", "hls", "-hls_time", "2",
         "-hls_list_size", "0", "-hls_flags", "append_list", str(hls_dir / "live.m3u8")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    srv = ThreadingHTTPServer(("127.0.0.1", 0),
                              partial(SimpleHTTPRequestHandler, directory=str(hls_dir)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    puerto = srv.server_address[1]
    cfg = {"carpeta_grabaciones": str(tmp_path / "grabaciones"),
           "carpeta_clips": str(tmp_path / "clips"),
           "pre_s": 30, "post_s": 25, "radar_umbral": 45, "radar_intervalo_s": 3}
    try:
        for _ in range(60):
            if (hls_dir / "live.m3u8").exists(): break
            time.sleep(0.5)
        assert (hls_dir / "live.m3u8").exists(), "el productor HLS no arrancó"
        puente = _ApiPuente(wh)
        t_rec = time.time()
        res = worker_mod.procesar(
            {"id": 1, "type": "iniciar_grabacion",
             "payload": {"session_id": sid, "url": f"http://127.0.0.1:{puerto}/live.m3u8",
                         "mode": "general", "radar_activo": True}}, cfg, puente)
        assert res["estado"] == "grabando"
        assert sid in worker_mod.radares
        # SIN detener: los momentos deben aparecer en la API durante la grabación.
        eventos, visto_en_vivo = [], False
        deadline = time.time() + 150
        while time.time() < deadline:
            eventos = client.get(f"/events?session_id={sid}", headers=h).json()
            if eventos:
                visto_en_vivo = worker_mod.grabaciones[sid].hilo.is_alive()
                break
            time.sleep(3)
        assert eventos, "el radar no reportó ningún evento durante la grabación"
        assert visto_en_vivo, "el evento llegó solo tras terminar la grabación (no es en vivo)"
        ev = eventos[0]
        assert ev["score"] >= 45
        clips = [c for c in client.get("/clips", headers=h).json() if c["session_id"] == sid]
        assert clips, "el radar no generó ningún clip en vivo"
        clip = clips[0]
        datos = _ffprobe(clip["file_path"])
        st = datos["streams"][0]
        assert (st["width"], st["height"]) == (1080, 1920)
        dur = float(datos["format"]["duration"])
        assert 60 <= dur <= 120, f"duración del clip fuera de la regla 60–120: {dur}"
        latencia = Path(clip["file_path"]).stat().st_mtime - (t_rec + ev["end_s"])
        print(f"\nRADAR E2E: evento {ev['start_s']}–{ev['end_s']}s score {ev['score']}; "
              f"clip {dur:.1f}s 1080x1920; latencia momento->clip ≈ {latencia:.1f}s")
        # Detener por el flujo real de la API: job -> trabajador -> complete.
        r = client.post(f"/sessions/{sid}/stop", headers=h)
        job = _reclamar(wh, r.json()["job_id"])
        res2 = worker_mod.procesar(job, cfg, puente)
        assert res2["grabacion_completa"] and Path(res2["grabacion_completa"]).exists()
        r = client.post(f"/jobs/{job['id']}/complete", json={"result": res2}, headers=wh)
        assert r.status_code == 200, r.text
        assert sid not in worker_mod.radares and sid not in worker_mod.grabaciones
        s = apimain.SessionLocal()
        try:
            projs = s.query(apimain.Project).filter_by(file_path=res2["grabacion_completa"]).all()
        finally:
            s.close()
        assert len(projs) == 1, "al detener no se creó el Project de la grabación"
    finally:
        rec = worker_mod.grabaciones.pop(sid, None)
        if rec:
            try: rec.stop()
            except Exception: pass
        radar = worker_mod.radares.pop(sid, None)
        if radar:
            try: radar.detener()
            except Exception: pass
        prod.terminate()
        srv.shutdown()
