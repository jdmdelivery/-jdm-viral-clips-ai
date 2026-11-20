"""Miniaturas de clips del trabajador y estados honestos del panel:

- La ingesta en vivo guarda el thumb_path que reporta el TRABAJADOR (antes
  el esquema ni lo aceptaba y la API lo «calculaba» contra el disco del
  servidor: siempre None para rutas de Windows).
- El JPG vive en la PC: sin copia en el servidor, tiene_miniatura es False
  pero archivo_existe es True (el archivo SÍ existe, en la PC; antes la
  API lo comprobaba contra su propio disco y el panel enseñaba
  «archivo no encontrado» en clips reales).
- POST /clips/{id}/miniatura-archivo (trabajador) guarda la copia del JPG
  y GET /clips/{id}/miniatura la sirve al dueño del clip.
- ApiClient.subir_miniatura manda el JPG crudo con el token de trabajador.
- Al completar un job, el clip hereda session_id del payload del job si
  el resultado no lo trae (si no, salía «Sin sesión» en la biblioteca).
"""
import json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

import main as apimain
from sync import ApiClient

client = TestClient(apimain.app)
EMAIL, PW = "miniaturas@test.do", "clave-segura-123"
JPG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4  # bytes «JPEG» de prueba
RUTA_MP4 = "C:\\Users\\thene\\JDMViralClips\\clips\\sesion-3\\clip-radar-120s.mp4"
RUTA_JPG = "C:\\Users\\thene\\JDMViralClips\\clips\\sesion-3\\clip-radar-120s.jpg"


def _usuario():
    client.post("/auth/register", json={"email": EMAIL, "password": PW})
    r = client.post("/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h):
    r = client.post("/workers/register", json={"name": "PC miniaturas", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _ingestar_clip_radar(h, wh):
    ses = client.post("/sessions", json={"title": "Radar miniaturas"}, headers=h).json()
    r = client.post(f"/workers/sessions/{ses['id']}/live-events", headers=wh, json={
        "events": [{"start_s": 100, "end_s": 130, "type": "general", "mode": "general",
                    "score": 90, "breakdown": {}}],
        "clips": [{"event_index": 0, "file_path": RUTA_MP4, "thumb_path": RUTA_JPG,
                   "format": "9:16", "duration_s": 45.0, "score": 90,
                   "title": "Momento destacado en el LIVE", "titles": {}, "subtitles": False}]})
    assert r.status_code == 200, r.text
    return ses, r.json()["clip_ids"][0]


def test_ingesta_guarda_thumb_path_y_sesion():
    h = _usuario(); wh = _worker(h)
    ses, cid = _ingestar_clip_radar(h, wh)
    s = apimain.SessionLocal()
    try:
        c = s.get(apimain.Clip, cid)
        assert c.thumb_path == RUTA_JPG  # la ruta que reportó el trabajador
        assert c.session_id == ses["id"]
    finally:
        s.close()
    d = next(c for c in client.get("/clips", headers=h).json() if c["id"] == cid)
    # El archivo existe (en la PC del trabajador): nada de «no encontrado».
    assert d["archivo_existe"] is True
    assert d["session_id"] == ses["id"]
    # Pero la miniatura aún no se puede MOSTRAR: su copia no ha subido.
    assert d["tiene_miniatura"] is False
    r = client.get(f"/clips/{cid}/miniatura", headers=h)
    assert r.status_code == 404


def test_subir_miniatura_y_servirla():
    h = _usuario(); wh = _worker(h)
    _, cid = _ingestar_clip_radar(h, wh)
    # Sin token de trabajador no se puede subir.
    r = client.post(f"/clips/{cid}/miniatura-archivo", headers=h, content=JPG)
    assert r.status_code in (401, 403)
    # Vacía no se guarda.
    r = client.post(f"/clips/{cid}/miniatura-archivo", headers=wh, content=b"")
    assert r.status_code == 400
    # Subida real del trabajador.
    r = client.post(f"/clips/{cid}/miniatura-archivo", headers=wh, content=JPG)
    assert r.status_code == 200, r.text
    assert r.json()["bytes"] == len(JPG)
    d = next(c for c in client.get("/clips", headers=h).json() if c["id"] == cid)
    assert d["tiene_miniatura"] is True
    r = client.get(f"/clips/{cid}/miniatura", headers=h)
    assert r.status_code == 200 and r.content == JPG


def test_clip_viejo_sin_sesion_muestra_la_de_su_evento():
    """Los clips completados ANTES del arreglo de herencia quedaron con
    session_id vacío en la base de datos aunque su evento sí tiene
    sesión (los dos clips de las 13:39 del 2026-11-19): el listado debe
    mostrar la sesión del evento, no «Sin sesión». La fila no se toca."""
    h = _usuario(); wh = _worker(h)
    ses = client.post("/sessions", json={"title": "Sesión heredada"}, headers=h).json()
    r = client.post(f"/workers/sessions/{ses['id']}/live-events", headers=wh, json={
        "events": [{"start_s": 200, "end_s": 230, "type": "general", "mode": "general",
                    "score": 85, "breakdown": {}}],
        "clips": []})
    assert r.status_code == 200, r.text
    s = apimain.SessionLocal()
    try:
        ev = s.query(apimain.Event).filter_by(session_id=ses["id"]).order_by(
            apimain.Event.id.desc()).first()
        assert ev is not None
        c = apimain.Clip(event_id=ev.id, session_id=None, file_path=RUTA_MP4,
                         format="9:16", duration_s=45.0, score=85,
                         title="Momento destacado en el LIVE")
        s.add(c); s.commit(); cid = c.id
    finally:
        s.close()
    d = next(c for c in client.get("/clips", headers=h).json() if c["id"] == cid)
    assert d["session_id"] == ses["id"]
    assert d["sesion_titulo"] == "Sesión heredada"
    assert d["archivo_existe"] is True


def test_apiclient_subir_miniatura_contra_stub(tmp_path):
    recibido = {}
    class Manejador(BaseHTTPRequestHandler):
        def do_POST(self):
            recibido["ruta"] = self.path
            recibido["token"] = self.headers.get("X-Worker-Token")
            recibido["tipo"] = self.headers.get("Content-Type")
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
        jpg = tmp_path / "clip-9.jpg"
        jpg.write_bytes(JPG)
        api = ApiClient(f"http://127.0.0.1:{srv.server_address[1]}", "token-stub")
        r = api.subir_miniatura(9, str(jpg))
        assert r == {"ok": True, "bytes": len(JPG)}
        assert recibido["ruta"] == "/clips/9/miniatura-archivo"
        assert recibido["token"] == "token-stub"
        assert recibido["tipo"] == "image/jpeg"
        assert recibido["cuerpo"] == JPG
    finally:
        srv.shutdown()


def test_clip_hereda_sesion_del_payload_al_completar():
    h = _usuario(); wh = _worker(h)
    ses = client.post("/sessions", json={"title": "Sesión del job"}, headers=h).json()
    s = apimain.SessionLocal()
    try:
        j = apimain.Job(type="generar_clip", payload=json.dumps(
            {"session_id": ses["id"], "event_id": None, "file_path": RUTA_MP4}))
        s.add(j); s.commit(); jid = j.id
    finally:
        s.close()
    job = client.post("/jobs/claim", headers=wh).json()["job"]
    assert job["id"] == jid
    r = client.post(f"/jobs/{jid}/complete", headers=wh, json={"result": {"clips": [
        {"file_path": RUTA_MP4, "format": "9:16", "duration_s": 30.0, "score": 85,
         "title": "Momento destacado en el LIVE", "titles": {}, "subtitles": False}]}})
    assert r.status_code == 200, r.text
    cid = r.json()["clip_ids"][0]
    d = next(c for c in client.get("/clips", headers=h).json() if c["id"] == cid)
    assert d["session_id"] == ses["id"]  # no sale «Sin sesión»
    assert d["archivo_existe"] is True
