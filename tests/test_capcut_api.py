"""CapCut retirada (orden de Jose, 2026-11-14) + versión del motor.
- POST /clips/{id}/capcut y POST /sessions/{id}/capcut ya no existen (404)
  y no encolan enviar_capcut_clip ni enviar_capcut_sesion.
- POST /workers/heartbeat acepta body opcional {"version": ...} y
  GET /workers expone la versión; sin body sigue funcionando (trabajadores
  viejos)."""
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


def _jobs_capcut():
    s = apimain.SessionLocal()
    try:
        tipos = ("enviar_capcut_clip", "enviar_capcut_sesion")
        return s.query(apimain.Job).filter(apimain.Job.type.in_(tipos)).count()
    finally:
        s.close()


# ---------- POST /clips/{id}/capcut ----------
def test_clip_capcut_usa_primer_titulo_sugerido():
    h = _usuario()
    cid = _crear_clip(file_path="C:/clips/clip-capcut.mp4", title="Título propio",
                      titles_json=json.dumps({"titulos": ["Titulo Uno", "Otro"]}),
                      duration_s=42.5, score=90)
    antes = _jobs_capcut()
    r = client.post(f"/clips/{cid}/capcut", headers=h)
    assert r.status_code == 404, r.text
    assert _jobs_capcut() == antes


def test_clip_capcut_titulo_de_respaldo():
    h = _usuario()
    # titles_json inválido -> cae a c.title; sin ninguno -> «Clip {cid}».
    cid = _crear_clip(file_path="/tmp/con-titulo.mp4", title="Solo título propio",
                      titles_json="esto no es json")
    antes = _jobs_capcut()
    assert client.post(f"/clips/{cid}/capcut", headers=h).status_code == 404
    cid2 = _crear_clip(file_path="/tmp/sin-titulo.mp4", title=None,
                       titles_json=json.dumps({"titulos": []}))
    assert client.post(f"/clips/{cid2}/capcut", headers=h).status_code == 404
    assert _jobs_capcut() == antes


def test_clip_capcut_inexistente_404():
    h = _usuario()
    r = client.post("/clips/999999/capcut", headers=h)
    assert r.status_code == 404


def test_clip_capcut_sin_archivo_400():
    h = _usuario()
    cid = _crear_clip(file_path=None, title="Sin archivo")
    antes = _jobs_capcut()
    r = client.post(f"/clips/{cid}/capcut", headers=h)
    assert r.status_code == 404
    assert _jobs_capcut() == antes


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
    antes = _jobs_capcut()
    r = client.post(f"/sessions/{sid}/capcut", headers=h)
    assert r.status_code == 404, r.text
    assert _jobs_capcut() == antes
    assert c1 < c2


def test_sesion_capcut_sin_clips_400():
    h = _usuario()
    ses = _sesion(h, "Sesión vacía")
    antes = _jobs_capcut()
    r = client.post(f"/sessions/{ses['id']}/capcut", headers=h)
    assert r.status_code == 404
    assert _jobs_capcut() == antes


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
