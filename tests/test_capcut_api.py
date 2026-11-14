"""CapCut + versión del motor (lote 2026-11-14):
- API: POST /clips/{id}/capcut encola «enviar_capcut_clip» con el primer
  título sugerido de titles_json; 404 sin clip y 400 sin archivo.
- API: POST /sessions/{id}/capcut encola «enviar_capcut_sesion» con los
  clips de la sesión en orden; 400 sin clips y 404 sin sesión.
- API: POST /workers/heartbeat acepta body opcional {"version": ...} y
  GET /workers expone la versión; sin body sigue funcionando (trabajadores
  viejos).
La verificación del payload se hace directo en la BD, como en
test_fix_panel.py, porque GET /jobs no expone el payload."""
import json

from fastapi.testclient import TestClient

import main as apimain

client = TestClient(apimain.app)
EMAIL, PW = "capcut@test.do", "clave-segura-123"


# ---------- utilidades ----------
def _usuario():
    client.post("/auth/register", json={"email": EMAIL, "password": PW})
    r = client.post("/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h, nombre="PC capcut"):
    r = client.post("/workers/register", json={"name": nombre, "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    return d["id"], {"X-Worker-Token": d["worker_token"]}


def _sesion(h, titulo="Sesión capcut"):
    r = client.post("/sessions", json={"title": titulo, "mode": "general"}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _crear_clip(**kw):
    """Inserta un Clip directo en la BD (POST /clips no acepta titles_json)."""
    s = apimain.SessionLocal()
    try:
        c = apimain.Clip(**kw)
        s.add(c); s.commit(); s.refresh(c)
        return c.id
    finally:
        s.close()


def _job(job_id):
    """Lee un Job directo de la BD y devuelve (type, payload dict)."""
    s = apimain.SessionLocal()
    try:
        j = s.get(apimain.Job, job_id)
        assert j is not None, f"No existe el job {job_id} en la BD"
        return j.type, json.loads(j.payload or "{}")
    finally:
        s.close()


# ---------- POST /clips/{id}/capcut ----------
def test_clip_capcut_usa_primer_titulo_sugerido():
    h = _usuario()
    cid = _crear_clip(file_path="C:/clips/clip-capcut.mp4", title="Título propio",
                      titles_json=json.dumps({"titulos": ["Titulo Uno", "Otro"]}),
                      duration_s=42.5, score=90)
    r = client.post(f"/clips/{cid}/capcut", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["job_id"] and d["status"] == "pendiente"
    assert "CapCut" in d["nota"] and "borradores" in d["nota"]
    tipo, payload = _job(d["job_id"])
    assert tipo == "enviar_capcut_clip"
    assert payload["clip_id"] == cid
    assert payload["title"] == "Titulo Uno"
    assert payload["file_path"] == "C:/clips/clip-capcut.mp4"
    assert payload["duration_s"] == 42.5
    assert "project_id" in payload


def test_clip_capcut_titulo_de_respaldo():
    h = _usuario()
    # titles_json inválido -> cae a c.title; sin ninguno -> «Clip {cid}».
    cid = _crear_clip(file_path="/tmp/con-titulo.mp4", title="Solo título propio",
                      titles_json="esto no es json")
    d = client.post(f"/clips/{cid}/capcut", headers=h).json()
    _, payload = _job(d["job_id"])
    assert payload["title"] == "Solo título propio"
    cid2 = _crear_clip(file_path="/tmp/sin-titulo.mp4", title=None,
                       titles_json=json.dumps({"titulos": []}))
    d = client.post(f"/clips/{cid2}/capcut", headers=h).json()
    _, payload = _job(d["job_id"])
    assert payload["title"] == f"Clip {cid2}"


def test_clip_capcut_inexistente_404():
    h = _usuario()
    r = client.post("/clips/999999/capcut", headers=h)
    assert r.status_code == 404


def test_clip_capcut_sin_archivo_400():
    h = _usuario()
    cid = _crear_clip(file_path=None, title="Sin archivo")
    r = client.post(f"/clips/{cid}/capcut", headers=h)
    assert r.status_code == 400
    assert r.json()["detail"] == "El clip no tiene archivo asociado"


# ---------- POST /sessions/{id}/capcut ----------
def test_sesion_capcut_con_dos_clips_en_orden():
    h = _usuario()
    ses = _sesion(h, "Sesión para CapCut")
    sid = ses["id"]
    c1 = _crear_clip(session_id=sid, file_path="C:/clips/primero.mp4",
                     title="Primero propio",
                     titles_json=json.dumps({"titulos": ["Primero sugerido"]}),
                     duration_s=30.0)
    c2 = _crear_clip(session_id=sid, file_path="C:/clips/segundo.mp4",
                     title="Segundo propio", titles_json="{}", duration_s=35.0)
    # Un clip de OTRA sesión no debe colarse en el proyecto.
    _crear_clip(session_id=None, file_path="C:/clips/ajeno.mp4", title="Ajeno")
    r = client.post(f"/sessions/{sid}/capcut", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["clips"] == 2 and "CapCut" in d["nota"]
    tipo, payload = _job(d["job_id"])
    assert tipo == "enviar_capcut_sesion"
    assert payload["session_id"] == sid
    assert payload["titulo_sesion"] == "Sesión para CapCut"
    assert [c["file_path"] for c in payload["clips"]] == [
        "C:/clips/primero.mp4", "C:/clips/segundo.mp4"]  # orden por id asc
    assert payload["clips"][0]["title"] == "Primero sugerido"
    assert payload["clips"][1]["title"] == "Segundo propio"
    assert payload["clips"][0]["duration_s"] == 30.0
    assert c1 < c2  # la inserción fue en ese orden


def test_sesion_capcut_sin_clips_400():
    h = _usuario()
    ses = _sesion(h, "Sesión vacía")
    r = client.post(f"/sessions/{ses['id']}/capcut", headers=h)
    assert r.status_code == 400
    assert r.json()["detail"] == "La sesión no tiene clips generados todavía"


def test_sesion_capcut_inexistente_404():
    h = _usuario()
    r = client.post("/sessions/999999/capcut", headers=h)
    assert r.status_code == 404


# ---------- Heartbeat con versión del motor ----------
def test_heartbeat_con_y_sin_version():
    h = _usuario()
    wid, wh = _worker(h, "PC versión motor")
    # Sin body: compatibilidad con trabajadores viejos.
    r = client.post("/workers/heartbeat", headers=wh)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # Con versión: se guarda y GET /workers la expone.
    r = client.post("/workers/heartbeat", json={"version": "1.1.0"}, headers=wh)
    assert r.status_code == 200, r.text
    workers = client.get("/workers", headers=h).json()
    w = next(x for x in workers if x["id"] == wid)
    assert w["version"] == "1.1.0"
    # Un latido posterior sin body no borra la versión guardada.
    r = client.post("/workers/heartbeat", headers=wh)
    assert r.status_code == 200, r.text
    workers = client.get("/workers", headers=h).json()
    w = next(x for x in workers if x["id"] == wid)
    assert w["version"] == "1.1.0"
