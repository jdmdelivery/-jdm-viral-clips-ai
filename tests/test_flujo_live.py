"""Lote FLUJO LIVE (auditoría JDM VIRAL CLIPS AI) — flujo extremo a extremo.

Contrato probado (fuente de verdad):

- POST /sessions/{id}/start deja la sesión en «iniciando» (NO «grabando»);
  cuando el trabajador completa el job iniciar_grabacion la sesión pasa
  a «grabando» y started_at queda fijado. POST /sessions/{id}/stop deja
  «deteniendo»; al completar detener_grabacion pasa a «detenida» y la
  grabación aparece como Project «Grabación sesión {id}». Si un job
  iniciar/detener llega a «fallido», la sesión pasa a «error».
- GET /events enriquece cada evento con duracion_s, tiene_clip, clip_id,
  clip_file_path y clip_descargable, y excluye los descartados salvo
  ?incluir_descartados=true.
- POST /events/{id}/descartar, POST /events/{id}/clip (job generar_clip
  con anti-duplicado {job_id, duplicado} y 409 si ya hay clip),
  POST /sessions/{id}/generar-clips ({jobs, encolados, omitidos, total_eventos}).
- GET /jobs incluye «result» parseado (dict); GET /clips acepta ?session_id=.
- Trabajador VERSION "1.3.7".

Patrón (como tests/test_api.py y test_fix_panel.py):
TestClient, registro de usuario + headers Bearer, registro de trabajador
+ header X-Worker-Token, claim/complete del trabajador, videos reales
con FFmpeg. La URL HLS de prueba es la IP pública literal que ya usan
los tests existentes (https://8.8.8.8/live/stream.m3u8): validate_source
la acepta como HLS sin necesidad de red/DNS.
"""
import json
import subprocess
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

import main as apimain
import worker as worker_mod

client = TestClient(apimain.app)
PW = "clave-segura-123"
URL_HLS = "https://8.8.8.8/live/stream.m3u8"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _usuario():
    email = f"flujo-{uuid.uuid4().hex[:10]}@test.do"
    client.post("/auth/register", json={"email": email, "password": PW})
    r = client.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h):
    r = client.post("/workers/register",
                    json={"name": "PC flujo live", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _reclamar(wh, job_id, intentos=300):
    """Reclama hasta llegar a MI job; los ajenos se completan vacíos
    (patrón de test_fix_panel.py)."""
    for _ in range(intentos):
        r = client.post("/jobs/claim", headers=wh)
        assert r.status_code == 200, r.text
        job = r.json()["job"]
        assert job is not None, f"La cola se vació antes de llegar al job {job_id}"
        if job["id"] == job_id:
            return job
        client.post(f"/jobs/{job['id']}/complete", json={"result": {}}, headers=wh)
    raise AssertionError(f"El job {job_id} no fue reclamado")


def _completar(wh, job_id, result):
    r = client.post(f"/jobs/{job_id}/complete", json={"result": result}, headers=wh)
    assert r.status_code == 200, r.text


def _sesion_estado(h, sid):
    sesiones = client.get("/sessions?limit=500", headers=h).json()
    ses = next((s for s in sesiones if s["id"] == sid), None)
    assert ses is not None, f"La sesión {sid} no aparece en GET /sessions"
    return ses


def _contar_jobs_db(tipo):
    s = apimain.SessionLocal()
    try:
        return s.query(apimain.Job).filter_by(type=tipo).count()
    finally:
        s.close()


def _job_db(job_id):
    s = apimain.SessionLocal()
    try:
        j = s.get(apimain.Job, job_id)
        assert j is not None, f"No existe el job {job_id} en la BD"
        return j.type, json.loads(j.payload or "{}")
    finally:
        s.close()


def _video_prueba(path, dur=2):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c=blue:size=360x640:duration={dur}:rate=10",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)],
                   check=True, capture_output=True, timeout=120)
    return Path(path)


def _crear_sesion_grabando(h, wh, titulo="Flujo LIVE"):
    """Pasos 1-3 del contrato: sesión + fuente HLS -> start deja
    «iniciando» -> claim/complete iniciar_grabacion -> «grabando» con
    started_at. Devuelve (sid, job_id_de_iniciar)."""
    r = client.post("/sessions", json={"title": titulo, "mode": "general"}, headers=h)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    r = client.post(f"/sessions/{sid}/sources", json={"url": URL_HLS}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["allowed"] is True and r.json()["kind"] == "hls", r.text

    r = client.post(f"/sessions/{sid}/start", headers=h)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    ses = _sesion_estado(h, sid)
    assert ses["status"] == "iniciando", (
        f"Tras POST start la sesión debe quedar «iniciando», no «{ses['status']}»")
    job = _reclamar(wh, job_id)
    assert job["type"] == "iniciar_grabacion"
    _completar(wh, job["id"], {"estado": "grabando", "bytes": 120000, "tiene_video": True, "tiene_audio": True})
    ses = _sesion_estado(h, sid)
    assert ses["status"] == "grabando", (
        f"Tras completar iniciar_grabacion debe quedar «grabando», no «{ses['status']}»")
    assert ses.get("started_at") is not None, (
        "GET /sessions debe serializar LiveSession.started_at y debe "
        "quedar fijado al pasar a «grabando»")
    return sid, job_id


def _ingestar_eventos(h, wh, sid):
    """Paso 5: ingesta en vivo de 2 eventos y 0 clips."""
    r = client.post(f"/workers/sessions/{sid}/live-events",
                    json={"events": [
                        {"start_s": 10, "end_s": 25, "type": "general",
                         "mode": "general", "score": 85},
                        {"start_s": 60, "end_s": 75, "type": "general",
                         "mode": "general", "score": 70}],
                        "clips": []},
                    headers=wh)
    assert r.status_code == 200, r.text
    assert r.json()["eventos_guardados"] == 2, r.text
    eventos = client.get(f"/events?session_id={sid}", headers=h).json()
    assert len(eventos) == 2
    por_score = sorted(eventos, key=lambda e: -e["score"])
    return por_score[0], por_score[1]  # e1 (score 85), e2 (score 70)


def _eventos_por_id(h, sid, incluir_descartados=False):
    ruta = f"/events?session_id={sid}&limit=500"
    if incluir_descartados:
        ruta += "&incluir_descartados=true"
    return {e["id"]: e for e in client.get(ruta, headers=h).json()}


def _clip_evento_completado(h, wh, sid, evento, tmp_path):
    """Paso 6: POST /events/{id}/clip + claim + complete con un clip de
    archivo REAL (para que clip_descargable tenga sentido en servidor)."""
    clip_file = _video_prueba(tmp_path / f"clip-evento-{evento['id']}.mp4")
    r = client.post(f"/events/{evento['id']}/clip", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("job_id"), f"POST /events/{{id}}/clip debe dar job_id: {d}"
    assert d.get("duplicado") is False, f"Primer clip no puede ser duplicado: {d}"
    job = _reclamar(wh, d["job_id"])
    assert job["type"] == "generar_clip"
    _completar(wh, job["id"], {"clips": [{
        "file_path": str(clip_file), "format": "9:16", "duration_s": 2.0,
        "score": evento["score"], "title": "Clip del evento",
        "event_id": evento["id"], "session_id": sid}]})
    return clip_file, d["job_id"]


def _sesion_con_eventos(h, wh, tmp_path=None):
    sid, _ = _crear_sesion_grabando(h, wh)
    e1, e2 = _ingestar_eventos(h, wh, sid)
    return sid, e1, e2


def _limpiar_sesion(h, wh, sid):
    """Deja sin sesión grabando al terminar cada test (otra sesión en
    «grabando» haría 409 en los start siguientes). Nunca falla."""
    try:
        ses = _sesion_estado(h, sid)
        if ses["status"] in ("grabando", "iniciando", "deteniendo"):
            r = client.post(f"/sessions/{sid}/stop", headers=h)
            if r.status_code == 200 and r.json().get("job_id"):
                job = _reclamar(wh, r.json()["job_id"], intentos=50)
                _completar(wh, job["id"], {
                    "estado": "detenida",
                    "grabacion_completa": f"/tmp/jdm-flujo-limpieza-{sid}.mp4"})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pasos 1-3: start -> «iniciando»; complete -> «grabando» + started_at
# ---------------------------------------------------------------------------
def test_flujo_start_iniciando_y_complete_grabando_con_started_at():
    h, wh = _usuario(), None
    wh = _worker(h)
    sid = None
    try:
        sid, _ = _crear_sesion_grabando(h, wh)
        ses = _sesion_estado(h, sid)
        assert "started_at" in ses
        assert ses["started_at"] is not None
    finally:
        if sid is not None:
            _limpiar_sesion(h, wh, sid)


# ---------------------------------------------------------------------------
# Contrato: un job iniciar que llega a «fallido» deja la sesión en «error»
# ---------------------------------------------------------------------------
def test_flujo_fallo_al_iniciar_pasa_sesion_a_error():
    h = _usuario()
    wh = _worker(h)
    sid = client.post("/sessions", json={"title": "Fallo al iniciar"},
                      headers=h).json()["id"]
    r = client.post(f"/sessions/{sid}/sources", json={"url": URL_HLS}, headers=h)
    assert r.json()["allowed"] is True
    r = client.post(f"/sessions/{sid}/start", headers=h)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert _sesion_estado(h, sid)["status"] == "iniciando"
    # fail reintenta (pendiente) hasta agotar los intentos -> «fallido»
    for _ in range(3):
        job = _reclamar(wh, job_id)
        r = client.post(f"/jobs/{job['id']}/fail",
                        json={"error": "señal caída (prueba)"}, headers=wh)
        assert r.status_code == 200, r.text
    s = apimain.SessionLocal()
    try:
        estado_job = s.get(apimain.Job, job_id).status
    finally:
        s.close()
    assert estado_job == "fallido", f"El job debía quedar fallido: {estado_job}"
    assert _sesion_estado(h, sid)["status"] == "error", (
        "Un job iniciar_grabacion «fallido» debe dejar la sesión en «error»")


# ---------------------------------------------------------------------------
# Pasos 4-5: radar activo + ingesta en vivo con campos enriquecidos
# ---------------------------------------------------------------------------
def test_flujo_radar_e_ingesta_live():
    h, wh = _usuario(), None
    wh = _worker(h)
    sid = None
    try:
        sid, _ = _crear_sesion_grabando(h, wh)
        r = client.post(f"/sessions/{sid}/radar",
                        json={"activo": True}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["radar_activo"] is True
        e1, e2 = _ingestar_eventos(h, wh, sid)
        for ev in (e1, e2):
            assert ev["duracion_s"] == ev["end_s"] - ev["start_s"], (
                f"GET /events debe añadir duracion_s: {ev}")
            assert ev["tiene_clip"] is False
            assert ev.get("clip_id") is None
            assert "clip_file_path" in ev
            assert ev["clip_descargable"] is False
    finally:
        if sid is not None:
            _limpiar_sesion(h, wh, sid)


# ---------------------------------------------------------------------------
# Pasos 6-7: clip de evento, anti-duplicado por job en curso y 409 con clip
# ---------------------------------------------------------------------------
def test_flujo_clip_evento_duplicado_y_409(tmp_path):
    h, wh = _usuario(), None
    wh = _worker(h)
    sid = None
    try:
        sid, e1, _e2 = _sesion_con_eventos(h, wh, tmp_path)
        clip_file = _video_prueba(tmp_path / f"clip-evento-{e1['id']}.mp4")
        r = client.post(f"/events/{e1['id']}/clip", headers=h)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("job_id") and d.get("duplicado") is False, d
        # Repetir con el job aún pendiente: duplicado=true y SIN job nuevo.
        antes = _contar_jobs_db("generar_clip")
        r2 = client.post(f"/events/{e1['id']}/clip", headers=h)
        assert r2.status_code == 200, r2.text
        assert r2.json().get("duplicado") is True, r2.text
        assert _contar_jobs_db("generar_clip") == antes, (
            "El anti-duplicado no debe crear un segundo job generar_clip")
        # El trabajador completa el job con el clip real.
        job = _reclamar(wh, d["job_id"])
        assert job["type"] == "generar_clip"
        _completar(wh, job["id"], {"clips": [{
            "file_path": str(clip_file), "format": "9:16", "duration_s": 2.0,
            "score": e1["score"], "title": "Clip del evento",
            "event_id": e1["id"], "session_id": sid}]})
        ev = _eventos_por_id(h, sid)[e1["id"]]
        assert ev["tiene_clip"] is True, ev
        assert ev.get("clip_id") is not None, ev
        assert ev.get("clip_file_path") == str(clip_file), ev
        assert ev["clip_descargable"] is True, ev
        # Con clip ya persistido: 409.
        r3 = client.post(f"/events/{e1['id']}/clip", headers=h)
        assert r3.status_code == 409, (
            f"Un evento con clip debe dar 409, no {r3.status_code}: {r3.text}")
    finally:
        if sid is not None:
            _limpiar_sesion(h, wh, sid)


# ---------------------------------------------------------------------------
# Paso 8: generar-clips de la sesión encola SOLO los eventos sin clip
# ---------------------------------------------------------------------------
def test_flujo_generar_clips_sesion_solo_sin_clip(tmp_path):
    h, wh = _usuario(), None
    wh = _worker(h)
    sid = None
    try:
        sid, e1, _e2 = _sesion_con_eventos(h, wh, tmp_path)
        _clip_evento_completado(h, wh, sid, e1, tmp_path)
        r = client.post(f"/sessions/{sid}/generar-clips", headers=h)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "jobs" in d and "encolados" in d and "omitidos" in d \
            and "total_eventos" in d, d
        assert d["encolados"] == 1, (
            f"Solo e2 (sin clip) debe encolarse: {d}")
        assert d["omitidos"] >= 1, f"e1 (con clip) debe omitirse: {d}"
        assert d["total_eventos"] == 2, d
        # Repetir: el job de e2 está en curso -> anti-duplicado, 0 nuevos.
        r2 = client.post(f"/sessions/{sid}/generar-clips", headers=h)
        assert r2.status_code == 200, r2.text
        assert r2.json()["encolados"] == 0, r2.text
    finally:
        if sid is not None:
            _limpiar_sesion(h, wh, sid)


# ---------------------------------------------------------------------------
# Paso 9: descartar un evento lo saca del listado (salvo incluir_descartados)
# ---------------------------------------------------------------------------
def test_flujo_descartar_evento():
    h, wh = _usuario(), None
    wh = _worker(h)
    sid = None
    try:
        sid, e1, e2 = _sesion_con_eventos(h, wh)
        r = client.post(f"/events/{e2['id']}/descartar", headers=h)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True and d.get("id") == e2["id"] \
            and d.get("descartado") is True, d
        visibles = _eventos_por_id(h, sid)
        assert e2["id"] not in visibles, (
            "GET /events debe excluir los descartados por defecto")
        assert e1["id"] in visibles
        con_descartados = _eventos_por_id(h, sid, incluir_descartados=True)
        assert e2["id"] in con_descartados, (
            "GET /events?incluir_descartados=true debe incluir el descartado")
        assert con_descartados[e2["id"]].get("descartado") is True
    finally:
        if sid is not None:
            _limpiar_sesion(h, wh, sid)


# ---------------------------------------------------------------------------
# Paso 11: versión del trabajador
# ---------------------------------------------------------------------------
def test_version_trabajador_1_3_7():
    assert worker_mod.VERSION == "1.3.7", (
        f"VERSION del trabajador debe ser «1.3.7», no «{worker_mod.VERSION}»")


# ---------------------------------------------------------------------------
# Paso 12: stop -> «deteniendo»; complete -> «detenida» + Project
# ---------------------------------------------------------------------------
def test_flujo_stop_deteniendo_y_complete_crea_proyecto(tmp_path):
    h, wh = _usuario(), None
    wh = _worker(h)
    sid, _ = _crear_sesion_grabando(h, wh)
    grabacion = _video_prueba(tmp_path / "grabacion_completa.mp4")
    r = client.post(f"/sessions/{sid}/stop", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deteniendo", (
        f"Tras POST stop la sesión debe quedar «deteniendo»: {r.json()}")
    assert _sesion_estado(h, sid)["status"] == "deteniendo"
    job = _reclamar(wh, r.json()["job_id"])
    assert job["type"] == "detener_grabacion"
    _completar(wh, job["id"], {"estado": "detenida",
                               "grabacion_completa": str(grabacion)})
    assert _sesion_estado(h, sid)["status"] == "detenida"
    nombres = [p["name"] for p in client.get("/projects", headers=h).json()]
    assert f"Grabación sesión {sid}" in nombres, nombres


# ---------------------------------------------------------------------------
# Paso 13: GET /jobs incluye «result» parseado (dict, no texto)
# ---------------------------------------------------------------------------
def test_flujo_jobs_incluye_result_parseado():
    h, wh = _usuario(), None
    wh = _worker(h)
    sid = None
    try:
        sid, job_iniciar = _crear_sesion_grabando(h, wh)
        trabajos = client.get("/jobs?limit=500", headers=h).json()
        job = next((j for j in trabajos if j["id"] == job_iniciar), None)
        assert job is not None, "El job iniciar_grabacion no aparece en GET /jobs"
        assert "result" in job, "GET /jobs debe incluir la clave «result»"
        assert isinstance(job["result"], dict), (
            f"«result» debe venir parseado como dict, no {type(job['result'])}")
        assert job["result"].get("estado") == "grabando", job["result"]
    finally:
        if sid is not None:
            _limpiar_sesion(h, wh, sid)
