"""Tests Etapas 3–6 (lado API): editor, social/OAuth honesto, piloto automático,
calendario, estadísticas, paginación, límite de subida, rate-limit y guarda de
grabación única. Sin credenciales OAuth reales en el entorno de test: todo debe
quedar en requiere-configuración / sin_conectar y NADA se marca «publicado»."""
import json
import os

from fastapi.testclient import TestClient

import main as apimain

client = TestClient(apimain.app)
EMAIL, PW = "jose@test.do", "clave-segura-123"  # mismo admin que test_api.py

CRED_VARS = ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "META_APP_ID", "META_APP_SECRET",
             "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "JDM_ENCRYPTION_KEY")


def registro_login():
    client.post("/auth/register", json={"email": EMAIL, "password": PW})
    r = client.post("/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def worker_headers(h):
    r = client.post("/workers/register", json={"name": "worker-etapas", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"x-worker-token": r.json()["worker_token"]}


def claim_hasta(wh, job_id, intentos=200):
    for _ in range(intentos):
        r = client.post("/jobs/claim", headers=wh)
        assert r.status_code == 200, r.text
        job = r.json()["job"]
        if job is None:
            break
        if job["id"] == job_id:
            return job
    raise AssertionError(f"El job {job_id} no fue reclamado")


def _db_jobs(tipo, event_id=None):
    s = apimain.SessionLocal()
    try:
        out = []
        for j in s.query(apimain.Job).filter_by(type=tipo).all():
            p = json.loads(j.payload or "{}")
            if event_id is None or p.get("event_id") == event_id:
                out.append(j.id)
        return out
    finally:
        s.close()


def _sin_credenciales(monkeypatch):
    for v in CRED_VARS:
        monkeypatch.delenv(v, raising=False)


# ---------------- Etapa 4: social / OAuth honesto ----------------

def test_social_sin_credenciales(monkeypatch):
    _sin_credenciales(monkeypatch)
    h = registro_login()
    soc = client.get("/social", headers=h).json()
    assert len(soc) == 4
    assert all(s["oauth_configurado"] is False for s in soc)
    assert all(s["status"] == "sin_conectar" for s in soc)
    assert all("Requiere configuración" in s["nota"] for s in soc)


def test_oauth_conectar_sin_credenciales_400(monkeypatch):
    _sin_credenciales(monkeypatch)
    h = registro_login()
    for plat in ("tiktok", "facebook", "instagram", "youtube"):
        r = client.get(f"/auth/{plat}/conectar", headers=h)
        assert r.status_code == 400, (plat, r.text)
        assert "requiere configuración" in r.text
    assert client.get("/auth/spotify/conectar", headers=h).status_code == 404
    # callback con state inventado: rechazado, nadie queda conectado
    r = client.get("/auth/tiktok/callback", params={"code": "x", "state": "inventado"})
    assert r.status_code == 400
    soc = client.get("/social", headers=h).json()
    assert all(s["status"] == "sin_conectar" for s in soc)


def test_publish_sin_conexion_nunca_publica(monkeypatch):
    _sin_credenciales(monkeypatch)
    h = registro_login()
    clip = client.post("/clips", json={"file_path": "/tmp/pub.mp4", "title": "C", "score": 85}, headers=h).json()
    pub = client.post("/publications", json={"clip_id": clip["id"], "platform": "youtube", "title": "T"}, headers=h).json()
    r = client.post(f"/publications/{pub['id']}/publish", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["status"] != "publicado" and "no está conectada" in body["motivo"]
    pubs = client.get("/publications", headers=h).json()
    assert all(p["status"] != "publicado" for p in pubs)


# ---------------- Etapa 3: editor ----------------

def _clip_con_evento(h, dur=60.0):
    sid = client.post("/sessions", json={"title": "Sesión editor", "mode": "general"}, headers=h).json()["id"]
    ev = client.post("/events", json={"session_id": sid, "type": "discusion", "mode": "general",
                                      "start_s": 5, "end_s": 20, "score": 10}, headers=h).json()
    clip = client.post("/clips", json={"event_id": ev["id"], "session_id": sid, "file_path": "/tmp/origen.mp4",
                                       "duration_s": dur, "score": 70, "title": "Origen"}, headers=h).json()
    return sid, ev, clip


def test_editor_edit_validaciones_y_job():
    h = registro_login()
    _, _, clip = _clip_con_evento(h)
    cid = clip["id"]
    ok = client.post(f"/clips/{cid}/edit", json={
        "trim_start_s": 2, "trim_end_s": 30, "formato": "16:9", "offset_x": 0.2, "offset_y": -0.1,
        "zoom": 1.5, "volumen": 0.8, "texto": "¡Mira esto!", "texto_activo": True,
        "subtitulos": False}, headers=h)
    assert ok.status_code == 200, ok.text
    assert ok.json()["job_id"] and "trabajador" in ok.json()["nota"]
    jobs = client.get("/jobs", headers=h).json()
    job = next(j for j in jobs if j["id"] == ok.json()["job_id"])
    assert job["type"] == "editar_clip" and job["status"] == "pendiente"
    base = {"trim_start_s": 0, "trim_end_s": 10, "formato": "9:16"}
    assert client.post(f"/clips/{cid}/edit", json={**base, "formato": "4:3"}, headers=h).status_code == 400
    assert client.post(f"/clips/{cid}/edit", json={**base, "zoom": 0.5}, headers=h).status_code == 400
    assert client.post(f"/clips/{cid}/edit", json={**base, "offset_x": 2}, headers=h).status_code == 400
    assert client.post(f"/clips/{cid}/edit", json={**base, "volumen": 0}, headers=h).status_code == 400
    assert client.post(f"/clips/{cid}/edit", json={**base, "trim_start_s": 10, "trim_end_s": 5}, headers=h).status_code == 400
    assert client.post("/clips/999999/edit", json=base, headers=h).status_code == 404


def test_editor_logo_y_miniatura():
    h = registro_login()
    _, _, clip = _clip_con_evento(h)
    cid = clip["id"]
    r = client.post(f"/clips/{cid}/logo", files={"file": ("logo.png", b"\x89PNG\r\n\x1a\nfake", "image/png")}, headers=h)
    assert r.status_code == 200, r.text
    assert os.path.exists(r.json()["logo_path"])
    r = client.post(f"/clips/{cid}/logo", files={"file": ("logo.gif", b"GIF89a", "image/gif")}, headers=h)
    assert r.status_code == 400
    assert client.post("/clips/999999/logo", files={"file": ("l.png", b"x", "image/png")}, headers=h).status_code == 404
    r = client.post(f"/clips/{cid}/thumbnail", json={"segundo": 3}, headers=h)
    assert r.status_code == 200, r.text
    jobs = client.get("/jobs", headers=h).json()
    job = next(j for j in jobs if j["id"] == r.json()["job_id"])
    assert job["type"] == "miniatura"  # elección documentada: job propio «miniatura»
    assert client.post(f"/clips/{cid}/thumbnail", json={"segundo": -1}, headers=h).status_code == 400


def test_editor_complete_hereda_del_origen():
    h = registro_login()
    sid, ev, clip = _clip_con_evento(h)
    r = client.post(f"/clips/{clip['id']}/edit", json={"trim_start_s": 0, "trim_end_s": 20, "formato": "9:16"}, headers=h)
    job_id = r.json()["job_id"]
    wh = worker_headers(h)
    claim_hasta(wh, job_id)
    done = client.post(f"/jobs/{job_id}/complete", headers=wh, json={"result": {"clips": [
        {"file_path": "/tmp/clip_editado.mp4", "format": "9:16", "duration_s": 20, "score": 70, "title": "Editado"}]}})
    assert done.status_code == 200, done.text
    clips = client.get("/clips", params={"limit": 500}, headers=h).json()
    editado = next(c for c in clips if c["file_path"] == "/tmp/clip_editado.mp4")
    assert editado["event_id"] == ev["id"] and editado["session_id"] == sid  # heredados del origen


# ---------------- Etapa 5: piloto, calendario, stats ----------------

def test_autopilot_config_defecto_y_put():
    h = registro_login()
    cfg = client.get("/autopilot", headers=h).json()
    assert cfg["activo"] is False and cfg["min_score"] == 80
    assert cfg["plataformas"] == ["tiktok", "facebook", "instagram", "youtube"]
    assert cfg["timezone"] == "America/Santo_Domingo"
    r = client.put("/autopilot", json={"min_score": 75}, headers=h)
    assert r.status_code == 200 and r.json()["min_score"] == 75
    assert client.put("/autopilot", json={"plataformas": ["myspace"]}, headers=h).status_code == 400
    client.put("/autopilot", json={"min_score": 80}, headers=h)  # restaurar


def test_piloto_flujo_completo():
    h = registro_login()
    r = client.put("/autopilot", json={"activo": True, "min_score": 80,
                                       "plataformas": ["tiktok", "facebook", "instagram", "youtube"]}, headers=h)
    assert r.status_code == 200 and r.json()["activo"] is True
    try:
        ev = client.post("/events", json={"type": "discusion", "mode": "alofoke",
                                          "start_s": 10, "end_s": 25, "score": 95}, headers=h).json()
        assert len(_db_jobs("generar_clip", ev["id"])) == 1  # el piloto encoló generar_clip
        # anti-duplicados: evaluar el MISMO evento otra vez no crea un segundo job
        s = apimain.SessionLocal()
        try:
            e = s.get(apimain.Event, ev["id"])
            assert apimain.evaluar_piloto(s, e) is None
        finally:
            s.close()
        assert len(_db_jobs("generar_clip", ev["id"])) == 1
        # score bajo: sin job
        ev2 = client.post("/events", json={"type": "general", "mode": "general",
                                           "start_s": 0, "end_s": 5, "score": 10}, headers=h).json()
        assert len(_db_jobs("generar_clip", ev2["id"])) == 0
        # simular trabajador: claim + complete del job generar_clip con un clip
        job_id = _db_jobs("generar_clip", ev["id"])[0]
        wh = worker_headers(h)
        claim_hasta(wh, job_id)
        done = client.post(f"/jobs/{job_id}/complete", headers=wh, json={"result": {"clips": [
            {"event_id": ev["id"], "file_path": "/tmp/clip_piloto.mp4", "format": "9:16",
             "duration_s": 30, "score": 95, "title": "Clip piloto"}]}})
        assert done.status_code == 200, done.text
        clips = client.get("/clips", params={"limit": 500}, headers=h).json()
        clip_id = next(c["id"] for c in clips if c["file_path"] == "/tmp/clip_piloto.mp4")
        pubs = [p for p in client.get("/publications", params={"limit": 500}, headers=h).json()
                if p["clip_id"] == clip_id]
        assert len(pubs) == 4  # una por plataforma configurada
        assert all(p["status"] in ("borrador", "requiere_conexion") for p in pubs)
        assert all(p["status"] != "publicado" for p in pubs)
    finally:
        client.put("/autopilot", json={"activo": False}, headers=h)


def test_calendario_crud():
    h = registro_login()
    clip = client.post("/clips", json={"file_path": "/tmp/cal.mp4", "title": "Cal", "score": 50}, headers=h).json()
    pub = client.post("/publications", json={"clip_id": clip["id"], "platform": "facebook", "title": "Estreno"}, headers=h).json()
    r = client.post("/calendar", json={"publication_id": pub["id"], "when_at": "2026-12-01T15:00:00",
                                       "note": "estreno"}, headers=h)
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["status"] == "programada"
    r = client.put(f"/calendar/{entry['id']}", json={"when_at": "2026-12-02T16:00:00"}, headers=h)
    assert r.status_code == 200 and r.json()["when_at"].startswith("2026-12-02T16:00")
    r = client.delete(f"/calendar/{entry['id']}", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelada"
    cal = client.get("/calendar", params={"limit": 500}, headers=h).json()
    guardada = next(c for c in cal if c["id"] == entry["id"])
    assert guardada["status"] == "cancelada"  # cancelar NO borra la fila
    assert client.post("/calendar", json={"publication_id": 999999, "when_at": "2026-12-01T15:00:00"}, headers=h).status_code == 404
    assert client.post("/calendar", json={"publication_id": pub["id"], "when_at": "no-es-fecha"}, headers=h).status_code == 400
    assert client.put("/calendar/999999", json={"note": "x"}, headers=h).status_code == 404
    assert client.delete("/calendar/999999", headers=h).status_code == 404


def test_stats_agregados():
    h = registro_login()
    st = client.get("/stats", headers=h).json()
    for k in ("sesiones", "eventos", "clips", "publicaciones", "score_promedio"):
        assert k in st  # claves viejas intactas
    assert isinstance(st["eventos_por_modo"], dict) and st["eventos_por_modo"]
    assert isinstance(st["clips_por_formato"], dict)
    assert isinstance(st["publicaciones_por_estado"], dict)
    assert st["mejor_score"] >= 95  # el evento del piloto
    assert st["eventos_sobre_umbral"] >= 1


# ---------------- Etapa 6: optimización / seguridad ----------------

def test_paginacion():
    h = registro_login()
    assert len(client.get("/events", params={"limit": 1}, headers=h).json()) <= 1
    assert len(client.get("/clips", params={"limit": 2, "offset": 0}, headers=h).json()) <= 2
    assert len(client.get("/jobs", params={"limit": 2}, headers=h).json()) <= 2
    assert len(client.get("/publications", params={"limit": 1}, headers=h).json()) <= 1
    assert len(client.get("/sessions", params={"limit": 1}, headers=h).json()) <= 1
    assert len(client.get("/projects", params={"limit": 1}, headers=h).json()) <= 1
    assert len(client.get("/calendar", params={"limit": 1}, headers=h).json()) <= 1
    assert len(client.get("/history", params={"limit": 1}, headers=h).json()) <= 1
    # el límite se capa a 500 aunque se pida más
    assert len(client.get("/history", params={"limit": 99999}, headers=h).json()) <= 500


def test_limite_subida_real(monkeypatch):
    h = registro_login()
    monkeypatch.setenv("JDM_MAX_UPLOAD_MB", "0")
    r = client.post("/projects/import", files={"file": ("peq.mp4", b"\x00" * 1024, "video/mp4")}, headers=h)
    assert r.status_code == 413, r.text
    monkeypatch.delenv("JDM_MAX_UPLOAD_MB")
    r = client.post("/projects/import", files={"file": ("peq.mp4", b"\x00" * 1024, "video/mp4")}, headers=h)
    assert r.status_code == 200, r.text  # sin env, la importación normal sigue funcionando


def test_rate_limit_login():
    email = "limite@test.do"  # correo distinto al de test_api.py para no interferir
    client.post("/auth/register", json={"email": email, "password": "clave-segura-123"})
    assert client.post("/auth/login", json={"email": email, "password": "clave-segura-123"}).status_code == 200
    for _ in range(5):
        r = client.post("/auth/login", json={"email": email, "password": "incorrecta-xx"})
        assert r.status_code == 401
    # 6.º intento: 429 aunque la contraseña sea la correcta, mientras dure la ventana
    r = client.post("/auth/login", json={"email": email, "password": "clave-segura-123"})
    assert r.status_code == 429, r.text
    # ventana expirada (timestamps antiguos): el login correcto entra y limpia el contador
    for k in list(apimain._LOGIN_FALLOS):
        if k[0] == email:
            apimain._LOGIN_FALLOS[k] = [t - 3600 for t in apimain._LOGIN_FALLOS[k]]
    assert client.post("/auth/login", json={"email": email, "password": "clave-segura-123"}).status_code == 200


def test_guarda_grabacion_unica():
    h = registro_login()
    a = client.post("/sessions", json={"title": "Grab A", "mode": "gta"}, headers=h).json()["id"]
    b = client.post("/sessions", json={"title": "Grab B", "mode": "gta"}, headers=h).json()["id"]
    for sid in (a, b):
        r = client.post(f"/sessions/{sid}/sources", json={"url": "https://8.8.8.8/live/stream.m3u8"}, headers=h).json()
        assert r["allowed"] is True
    assert client.post(f"/sessions/{a}/start", headers=h).status_code == 200
    assert client.post(f"/sessions/{b}/start", headers=h).status_code == 409  # otra ya grabando
    stop = client.post(f"/sessions/{a}/stop", headers=h)
    assert stop.status_code == 200
    # Mientras el trabajador no termina de cerrar, la otra sesión no arranca.
    assert client.post(f"/sessions/{b}/start", headers=h).status_code == 409
    wh = worker_headers(h)
    job = claim_hasta(wh, stop.json()["job_id"])
    done = client.post(f"/jobs/{job['id']}/complete", headers=wh, json={"result": {
        "estado": "detenida", "grabacion_completa": "C:/prueba/grabacion_completa.mp4", "bytes": 8192}})
    assert done.status_code == 200, done.text
    assert client.post(f"/sessions/{b}/start", headers=h).status_code == 200
    assert client.post(f"/sessions/{b}/stop", headers=h).status_code == 200
