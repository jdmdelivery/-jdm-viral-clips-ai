"""Copia del clip en el servidor (ver/descargar desde el panel):

- API: POST /clips/{id}/archivo (trabajador) guarda la copia en streaming y
  marca clips.archivo_servidor; GET /clips expone tiene_copia_servidor.
- API: GET /clips/{id}/archivo (usuario) sirve la copia en línea con Range
  y con ?descargar=1 como attachment con el nombre original.
- API: POST /clips/{id}/solicitar-subida encola UN job «subir_clip» sin
  duplicar, y no encola si ya hay copia.
- API: complete y la ingesta en vivo devuelven clip_ids (para la subida
  automática del trabajador).
- Trabajador: job «subir_clip» (archivo correcto / ausente), ApiClient
  .subir_clip contra un servidor stub, subida automática tras completar y
  subida automática del radar tras reportar.
- Panel: cableado estático de VER/DESCARGAR (el genérico vive en
  test_cableado.py; aquí el contrato concreto de esta función).
"""
import json, re, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main as apimain
import worker as worker_mod
from sync import ApiClient
from radar import RadarSesion

client = TestClient(apimain.app)
EMAIL, PW = "clipsrv@test.do", "clave-segura-123"
BYTES = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8  # 2060 bytes «MP4»
RUTA_PC = "C:\\Users\\thene\\JDMViralClips\\clips\\sesion-3\\clip-radar-200s.mp4"


# ---------- utilidades ----------
def _usuario():
    client.post("/auth/register", json={"email": EMAIL, "password": PW})
    r = client.post("/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h, nombre="PC clips"):
    r = client.post("/workers/register", json={"name": nombre, "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    return d["id"], {"X-Worker-Token": d["worker_token"]}


def _crear_clip(**kw):
    kw.setdefault("file_path", RUTA_PC)
    kw.setdefault("title", "Momento destacado en el LIVE")
    s = apimain.SessionLocal()
    try:
        c = apimain.Clip(**kw)
        s.add(c); s.commit(); s.refresh(c)
        return c.id
    finally:
        s.close()


def _clip_bd(cid):
    s = apimain.SessionLocal()
    try:
        c = s.get(apimain.Clip, cid)
        return {"archivo_servidor": c.archivo_servidor if c else None}
    finally:
        s.close()


def _jobs_subir_clip(cid):
    s = apimain.SessionLocal()
    try:
        out = []
        for j in s.query(apimain.Job).filter_by(type="subir_clip").all():
            try: p = json.loads(j.payload or "{}")
            except Exception: continue
            if p.get("clip_id") == cid:
                out.append({"id": j.id, "status": j.status, "payload": p})
        return out
    finally:
        s.close()


def _limpiar_copia(cid):
    dest = apimain.CLIPS_DIR / f"clip_{cid}.mp4"
    dest.unlink(missing_ok=True)
    Path(str(dest) + ".part").unlink(missing_ok=True)


# ---------- API: subida y lectura de la copia ----------
def test_subida_y_lectura_de_la_copia_ida_y_vuelta():
    h = _usuario(); _worker(h)
    _, wh = _worker(h, "PC clips 2")
    cid = _crear_clip()
    _limpiar_copia(cid)
    try:
        # Sin copia al principio.
        r = client.get("/clips", headers=h)
        fila = [c for c in r.json() if c["id"] == cid][0]
        assert fila["tiene_copia_servidor"] is False
        # GET del archivo sin copia: 404 con mensaje claro.
        r = client.get(f"/clips/{cid}/archivo", headers=h)
        assert r.status_code == 404 and "copia" in r.json()["detail"]
        # Subida sin token de trabajador o con token falso: 401.
        assert client.post(f"/clips/{cid}/archivo", content=BYTES).status_code == 401
        assert client.post(f"/clips/{cid}/archivo", content=BYTES,
                           headers={"X-Worker-Token": "falso"}).status_code == 401
        # Subida real del trabajador.
        r = client.post(f"/clips/{cid}/archivo", content=BYTES, headers=wh)
        assert r.status_code == 200, r.text
        assert r.json()["bytes"] == len(BYTES)
        assert _clip_bd(cid)["archivo_servidor"]
        assert (apimain.CLIPS_DIR / f"clip_{cid}.mp4").read_bytes() == BYTES
        # GET /clips ya expone la copia.
        r = client.get("/clips", headers=h)
        fila = [c for c in r.json() if c["id"] == cid][0]
        assert fila["tiene_copia_servidor"] is True
        assert fila["descargable"] is True
        # Lectura del usuario: bytes idénticos.
        r = client.get(f"/clips/{cid}/archivo", headers=h)
        assert r.status_code == 200 and r.content == BYTES
        # Range: el reproductor puede buscar dentro del video.
        r = client.get(f"/clips/{cid}/archivo", headers={**h, "Range": "bytes=2-5"})
        assert r.status_code == 206, r.text
        assert r.content == BYTES[2:6]
        assert r.headers.get("content-range", "").startswith("bytes 2-5/")
        # Descarga: attachment con el NOMBRE ORIGINAL del clip en la PC.
        r = client.get(f"/clips/{cid}/archivo?descargar=1", headers=h)
        assert r.status_code == 200 and r.content == BYTES
        cd = r.headers.get("content-disposition", "")
        assert "attachment" in cd and "clip-radar-200s.mp4" in cd
        # Sin autenticación de usuario: 401.
        assert client.get(f"/clips/{cid}/archivo").status_code == 401
    finally:
        _limpiar_copia(cid)


def test_archivo_clip_inexistente_404():
    h = _usuario(); _, wh = _worker(h, "PC clips 3")
    assert client.post("/clips/999999/archivo", content=BYTES, headers=wh).status_code == 404
    assert client.get("/clips/999999/archivo", headers=h).status_code == 404
    assert client.post("/clips/999999/solicitar-subida", headers=h).status_code == 404


def test_subida_sobre_el_limite_413_y_sin_marca(monkeypatch):
    h = _usuario(); _, wh = _worker(h, "PC clips 4")
    cid = _crear_clip()
    _limpiar_copia(cid)
    monkeypatch.setattr(apimain, "CLIP_MAX_BYTES", 16)
    try:
        r = client.post(f"/clips/{cid}/archivo", content=BYTES, headers=wh)
        assert r.status_code == 413, r.text
        assert _clip_bd(cid)["archivo_servidor"] is None
        assert not (apimain.CLIPS_DIR / f"clip_{cid}.mp4").exists()
    finally:
        _limpiar_copia(cid)


# ---------- API: solicitar-subida ----------
def test_solicitar_subida_encola_sin_duplicar_y_no_si_hay_copia():
    h = _usuario(); _, wh = _worker(h, "PC clips 5")
    cid = _crear_clip()
    _limpiar_copia(cid)
    try:
        r = client.post(f"/clips/{cid}/solicitar-subida", headers=h)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["estado"] == "subiendo" and d["tiene_copia_servidor"] is False
        assert d["hay_trabajador"] is True  # el trabajador se acaba de registrar (latido fresco)
        jobs = _jobs_subir_clip(cid)
        assert len(jobs) == 1 and jobs[0]["id"] == d["job_id"]
        assert jobs[0]["payload"]["clip_id"] == cid
        assert jobs[0]["payload"]["file_path"] == RUTA_PC
        # Segunda petición: mismo trabajo, sin duplicar.
        r = client.post(f"/clips/{cid}/solicitar-subida", headers=h)
        d2 = r.json()
        assert d2["duplicado"] is True and d2["job_id"] == d["job_id"]
        assert len(_jobs_subir_clip(cid)) == 1
        # Con la copia ya subida: estado con_copia y ningún trabajo nuevo.
        r = client.post(f"/clips/{cid}/archivo", content=BYTES, headers=wh)
        assert r.status_code == 200, r.text
        r = client.post(f"/clips/{cid}/solicitar-subida", headers=h)
        d3 = r.json()
        assert d3["estado"] == "con_copia" and d3["tiene_copia_servidor"] is True
        assert len(_jobs_subir_clip(cid)) == 1
        # Sin autenticación: 401.
        assert client.post(f"/clips/{cid}/solicitar-subida").status_code == 401
    finally:
        _limpiar_copia(cid)


# ---------- API: clip_ids en complete y en la ingesta en vivo ----------
def test_complete_devuelve_clip_ids_en_orden():
    h = _usuario(); _, wh = _worker(h, "PC clips 6")
    r = client.post("/jobs", json={"type": "generar_clip", "payload": {"event_id": 1}}, headers=h)
    jid = r.json()["id"]
    # Se completa directamente por id: la cola compartida de la suite puede
    # tener trabajos pendientes más antiguos y «claim» no garantiza orden.
    r = client.post(f"/jobs/{jid}/complete", headers=wh, json={"result": {"clips": [
        {"file_path": "C:\\clips\\a.mp4", "format": "9:16", "duration_s": 30, "score": 90},
        {"file_path": "C:\\clips\\b.mp4", "format": "9:16", "duration_s": 40, "score": 80}]}})
    assert r.status_code == 200, r.text
    ids = r.json()["clip_ids"]
    assert len(ids) == 2
    s = apimain.SessionLocal()
    try:
        rutas = [s.get(apimain.Clip, i).file_path for i in ids]
    finally:
        s.close()
    assert rutas == ["C:\\clips\\a.mp4", "C:\\clips\\b.mp4"]


def test_ingesta_en_vivo_devuelve_clip_ids():
    h = _usuario(); _, wh = _worker(h, "PC clips 7")
    r = client.post("/sessions", json={"title": "Sesión copia", "mode": "general"}, headers=h)
    sid = r.json()["id"]
    r = client.post(f"/workers/sessions/{sid}/live-events", headers=wh, json={
        "events": [{"start_s": 10, "end_s": 40, "type": "general", "mode": "general",
                    "score": 88, "breakdown": {}}],
        "clips": [{"event_index": 0, "file_path": "C:\\clips\\radar.mp4", "format": "9:16",
                   "duration_s": 45, "score": 88, "title": "Radar", "titles": {}, "subtitles": False}]})
    assert r.status_code == 200, r.text
    ids = r.json()["clip_ids"]
    assert len(ids) == 1
    s = apimain.SessionLocal()
    try:
        c = s.get(apimain.Clip, ids[0])
        assert c is not None and c.file_path == "C:\\clips\\radar.mp4"
    finally:
        s.close()


# ---------- Trabajador: job subir_clip ----------
class _ApiSubida:
    def __init__(self):
        self.llamadas = []
    def subir_clip(self, clip_id, ruta):
        self.llamadas.append((clip_id, ruta))
        return {"ok": True, "bytes": Path(ruta).stat().st_size}


def test_job_subir_clip_ok_y_archivo_ausente(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(BYTES)
    api = _ApiSubida()
    cfg = {"carpeta_clips": str(tmp_path), "carpeta_grabaciones": str(tmp_path)}
    r = worker_mod.procesar({"id": 1, "type": "subir_clip",
                             "payload": {"clip_id": 7, "file_path": str(video)}}, cfg, api)
    assert r["subido"] is True and r["clip_id"] == 7 and r["bytes"] == len(BYTES)
    assert api.llamadas == [(7, str(video))]
    with pytest.raises(FileNotFoundError):
        worker_mod.procesar({"id": 2, "type": "subir_clip",
                             "payload": {"clip_id": 7, "file_path": str(tmp_path / "no.mp4")}}, cfg, api)


def test_apiclient_subir_clip_contra_stub(tmp_path):
    recibido = {}
    class Manejador(BaseHTTPRequestHandler):
        def do_POST(self):
            recibido["ruta"] = self.path
            recibido["token"] = self.headers.get("X-Worker-Token")
            n = int(self.headers.get("Content-Length") or 0)
            recibido["cuerpo"] = self.rfile.read(n)
            cuerpo = json.dumps({"ok": True, "bytes": n}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        video = tmp_path / "clip-stub.mp4"
        video.write_bytes(BYTES)
        api = ApiClient(f"http://127.0.0.1:{srv.server_address[1]}", "token-stub")
        r = api.subir_clip(42, str(video))
        assert r == {"ok": True, "bytes": len(BYTES)}
        assert recibido["ruta"] == "/clips/42/archivo"
        assert recibido["token"] == "token-stub"
        assert recibido["cuerpo"] == BYTES
    finally:
        srv.shutdown()


def test_subida_automatica_tras_completar(tmp_path):
    video = tmp_path / "auto.mp4"
    video.write_bytes(BYTES)
    api = _ApiSubida()
    worker_mod._subir_copias_automaticas(
        {"clips": [{"file_path": str(video)}]}, {"ok": True, "clip_ids": [9]}, api)
    assert api.llamadas == [(9, str(video))]
    # Sin detalle (la compleción quedó offline) o sin clips: no se sube nada.
    api2 = _ApiSubida()
    worker_mod._subir_copias_automaticas({"clips": [{"file_path": str(video)}]}, None, api2)
    assert api2.llamadas == []
    # Si la subida falla, NO se propaga: el clip queda creado y se pide luego.
    class _ApiQueFalla:
        def subir_clip(self, clip_id, ruta):
            raise RuntimeError("red caída")
    worker_mod._subir_copias_automaticas(
        {"clips": [{"file_path": str(video)}]}, {"clip_ids": [9]}, _ApiQueFalla())


def test_radar_sube_copia_tras_reportar(tmp_path):
    video = tmp_path / "radar.mp4"
    video.write_bytes(BYTES)
    class _ApiRadar:
        def __init__(self):
            self.subidas = []
        def reportar_en_vivo(self, session_id, events, clips):
            return {"ok": True, "clip_ids": [33]}
        def subir_clip(self, clip_id, ruta):
            self.subidas.append((clip_id, ruta))
            return {"ok": True}
    api = _ApiRadar()
    radar = RadarSesion(None, 5, api, tmp_path)
    ok = radar._reportar([{"start_s": 0, "end_s": 30}],
                         [{"file_path": str(video), "event_index": 0}])
    assert ok is True
    assert api.subidas == [(33, str(video))]


# ---------- Panel: contrato estático de VER / DESCARGAR ----------
def test_panel_botones_ver_descargar_cableados():
    root = Path(__file__).resolve().parents[1]
    appjs = (root / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    mainpy = (root / "apps" / "api" / "main.py").read_text(encoding="utf-8")
    for fn in ("accionesClip", "verClip", "descargarCopia", "pedirCopia", "seguirSubida"):
        assert f"function {fn}(" in appjs, f"Falta la función {fn} en app.js"
    assert "solicitar-subida" in appjs and "/archivo" in appjs
    assert "tiene_copia_servidor" in appjs
    # Aviso honesto junto al reproductor: copia temporal, original en la PC.
    assert "El original siempre queda en tu PC" in appjs
    # Los endpoints que el panel invoca existen en la API.
    assert re.search(r'@app\.post\(\s*"/clips/\{cid\}/solicitar-subida"', mainpy)
    assert re.search(r'@app\.get\(\s*"/clips/\{cid\}/archivo"', mainpy)
    assert re.search(r'@app\.post\(\s*"/clips/\{cid\}/archivo"', mainpy)
