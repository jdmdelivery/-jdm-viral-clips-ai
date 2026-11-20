"""El botón «ACTIVAR RADAR VIRAL» del Centro LIVE (bug 2026-11-19).

Lo que pasaba: el radar entra SOLO con la grabación (POST /start pone
radar_activo=True y el payload de iniciar_grabacion lo lleva, así que el
trabajador arranca su RadarSesion sin que nadie toque el botón). Pero el
seguimiento de estado del panel (seguirEstadoGrabacion) solo repintaba
la píldora, NUNCA la etiqueta del botón ni la cuenta de momentos: el
botón se quedaba con el texto de antes de grabar, «ACTIVAR RADAR
VIRAL — 0 momentos», aunque el radar ya estaba analizando. Al pulsarlo,
activarRadar() leía el estado real (activo), calculaba «apagar» y
APAGABA el radar creyendo activarlo — sin cambio visible: la etiqueta
quedaba igual y la píldora volvía de «RADAR ANALIZANDO» a «GRABANDO
EN VIVO».

Aquí se fija el contrato completo panel -> API -> trabajador:
- el frontend repinta el botón con el estado fresco en cada paso del
  seguimiento (test estático sobre app.js);
- al grabar, la sesión ya nace con radar_activo=True y estado_real
  «RADAR ANALIZANDO» (el botón correcto es «DESACTIVAR»);
- el toggle POST /radar apaga/enciende de verdad (flag + job);
- los momentos que el trabajador ingiere llegan a GET /events, que es
  la cuenta que enseña el botón y la lista de VER MOMENTOS DETECTADOS.
"""
import json, uuid
from pathlib import Path

from fastapi.testclient import TestClient

import main as apimain

client = TestClient(apimain.app)
PW = "clave-segura-123"
URL_HLS = "https://8.8.8.8/live/stream.m3u8"
RAIZ = Path(__file__).resolve().parents[1]


def _usuario():
    email = f"radar-boton-{uuid.uuid4().hex[:10]}@test.do"
    client.post("/auth/register", json={"email": email, "password": PW})
    r = client.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h):
    r = client.post("/workers/register",
                    json={"name": "PC radar botón", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _reclamar(wh, job_id, intentos=300):
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


def _limpiar_sesion(h, wh, sid):
    try:
        ses = _sesion_estado(h, sid)
        if ses["status"] in ("grabando", "iniciando", "deteniendo"):
            r = client.post(f"/sessions/{sid}/stop", headers=h)
            if r.status_code == 200 and r.json().get("job_id"):
                job = _reclamar(wh, r.json()["job_id"], intentos=50)
                _completar(wh, job["id"], {
                    "estado": "detenida",
                    "grabacion_completa": f"/tmp/jdm-radar-boton-{sid}.mp4"})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Frontend: el seguimiento repinta el botón (la causa raíz del bug)
# ---------------------------------------------------------------------------
def test_frontend_seguimiento_repinta_boton_radar():
    app_js = (RAIZ / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    i = app_js.index("function seguirEstadoGrabacion")
    cuerpo = app_js[i:i + 6000]
    assert "pintarBtnRadar(ses, await contarMomentos(ses.id))" in cuerpo, (
        "seguirEstadoGrabacion debe repintar el botón del radar con el "
        "estado fresco y la cuenta de momentos en cada paso: sin eso la "
        "etiqueta queda vieja («ACTIVAR») con el radar ya encendido y el "
        "clic lo apaga creyendo activarlo")


def test_frontend_boton_radar_refleja_estado_real():
    app_js = (RAIZ / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    i = app_js.index("function pintarBtnRadar")
    cuerpo = app_js[i:i + 700]
    # Con radar activo la etiqueta es de DESACTIVAR (acción real inversa);
    # con fuente pero sin captura es SIN FUENTE, no un falso «analizando».
    assert "RADAR ANALIZANDO — DESACTIVAR" in cuerpo
    assert "RADAR SIN FUENTE" in cuerpo
    assert "ACTIVAR RADAR VIRAL" in cuerpo


# ---------------------------------------------------------------------------
# API: el radar nace encendido con la grabación; el toggle es real
# ---------------------------------------------------------------------------
def test_radar_automatico_al_grabar_y_toggle_real():
    h = _usuario(); wh = _worker(h); sid = None
    try:
        r = client.post("/sessions", json={"title": "Radar botón", "mode": "general"},
                        headers=h)
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        r = client.post(f"/sessions/{sid}/sources", json={"url": URL_HLS}, headers=h)
        assert r.status_code == 200 and r.json()["allowed"] is True, r.text

        r = client.post(f"/sessions/{sid}/start", headers=h)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        # El radar entra SOLO: el flag ya está activo antes de que el
        # trabajador confirme, y el payload del job lo lleva (el
        # trabajador arranca su RadarSesion con ese flag).
        assert _sesion_estado(h, sid)["radar_activo"] is True
        job = _reclamar(wh, job_id)
        assert job["type"] == "iniciar_grabacion"
        payload = job.get("payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload.get("radar_activo") is True, (
            "El payload de iniciar_grabacion debe llevar radar_activo=True: "
            "es lo que enciende el radar en el trabajador")
        _completar(wh, job["id"], {"estado": "grabando", "bytes": 120000,
                                   "tiene_video": True, "tiene_audio": True})

        ses = _sesion_estado(h, sid)
        assert ses["status"] == "grabando"
        assert ses["radar_activo"] is True
        assert ses["radar_sin_fuente"] is False
        assert ses["estado_real"] == "RADAR ANALIZANDO", (
            "Grabando + radar activo + bytes > 0 = «RADAR ANALIZANDO»: es "
            "la píldora que el dueño vio una vez; el botón en ese estado "
            "debe decir DESACTIVAR, no ACTIVAR")

        # Lo que hacía el clic con la etiqueta vieja: apagar sin saberlo.
        r = client.post(f"/sessions/{sid}/radar", json={"activo": False}, headers=h)
        assert r.status_code == 200 and r.json()["radar_activo"] is False, r.text
        ses = _sesion_estado(h, sid)
        assert ses["radar_activo"] is False
        assert ses["estado_real"] == "GRABANDO"

        # Y encender de nuevo es igual de real (flag + job radar_sesion).
        r = client.post(f"/sessions/{sid}/radar", json={"activo": True}, headers=h)
        assert r.status_code == 200 and r.json()["radar_activo"] is True, r.text
        job = _reclamar(wh, r.json()["job_id"])
        assert job["type"] == "radar_sesion"
        _completar(wh, job["id"], {"estado": "radar_activado", "radar_activo": True})
        assert _sesion_estado(h, sid)["estado_real"] == "RADAR ANALIZANDO"

        # Los momentos del trabajador llegan a la cuenta del botón y a
        # VER MOMENTOS DETECTADOS (misma fuente: GET /events?session_id=).
        r = client.post(f"/workers/sessions/{sid}/live-events",
                        json={"events": [{"start_s": 30, "end_s": 45,
                                          "type": "general", "mode": "general",
                                          "score": 88}],
                              "clips": []},
                        headers=wh)
        assert r.status_code == 200 and r.json()["eventos_guardados"] == 1, r.text
        eventos = client.get(f"/events?session_id={sid}", headers=h).json()
        assert len(eventos) == 1 and eventos[0]["score"] == 88
    finally:
        if sid is not None:
            _limpiar_sesion(h, wh, sid)
