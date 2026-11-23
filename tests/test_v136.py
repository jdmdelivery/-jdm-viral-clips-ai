"""Tests de la v1.3.6 — la orden de Jose (2026-11-23): «Tiene que hacer
short de un minuto y más».

Jose abrió el desplegable «Duración del clip» del Centro LIVE, vio las
opciones viejas de 3/5/10/15/20 s (eran el largo EXACTO de la generación
MANUAL de clips: panel -> API _largo_clip -> worker ventana_solicitada)
y subió el mínimo de TODAS las rutas de clip de 30 a 60 s:

1. clipper: MIN_CLIP_S = 60 (MAX sigue en 120); LARGOS_ELEGIBLES pasa a
   (60, 75, 90, 120); contexto() con el pre/post de fábrica nuevo
   (30/25) deja el momento típico en 65–85 s.
2. API: _largo_clip solo acepta 60/75/90/120 (o vacío = automática) y el
   payload del job lleva ese largo; 20 s (la opción que vio Jose) da 400.
3. Panel: el desplegable #clip-largo ofrece Automática/60/75/90/120 y
   ninguna opción por debajo de 60.
4. Trabajador: generar_clip con largo_s=90 produce un MP4 de ~90 s
   (ffprobe) y en Automática el clip cae en la banda 60–120.
"""
import json, re, subprocess, uuid
from pathlib import Path

from fastapi.testclient import TestClient

import main as apimain
import worker as worker_mod
from clipper import (LARGOS_ELEGIBLES, MAX_CLIP_S, MIN_CLIP_S, contexto,
                     ventana_solicitada)

client = TestClient(apimain.app)
PW = "clave-segura-123"
ROOT = Path(__file__).resolve().parents[1]


def _usuario():
    email = f"v136-{uuid.uuid4().hex[:10]}@test.do"
    client.post("/auth/register", json={"email": email, "password": PW})
    r = client.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h):
    r = client.post("/workers/register", json={"name": "PC v136", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _ffprobe_duracion(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    return float(json.loads(r.stdout)["format"]["duration"])


# ---------------------------------------------------------------------------
# 1. clipper: la banda nueva y los largos elegibles
# ---------------------------------------------------------------------------
def test_banda_60_120_y_largos_elegibles():
    assert MIN_CLIP_S == 60.0 and MAX_CLIP_S == 120.0
    assert LARGOS_ELEGIBLES == (60, 75, 90, 120)
    for largo in LARGOS_ELEGIBLES:
        ini, fin = ventana_solicitada(100, 110, 600, largo)
        assert fin - ini == largo, f"largo {largo}: ventana {ini}–{fin}"
    # Un largo viejo (20 s, el que vio Jose) ya NO se honra: cae a la
    # ventana automática, que vive en la banda 60–120.
    ini, fin = ventana_solicitada(100, 110, 600, 20)
    assert 60 <= fin - ini <= 120 and fin - ini != 20


def test_contexto_tipico_con_contexto_de_fabrica():
    # Momento de 1 segmento (10 s) a mitad de un en vivo largo: 65 s.
    ini, fin = contexto(100, 110, 600)
    assert (ini, fin) == (70.0, 135.0)
    # Momento de 3 segmentos (30 s): 85 s. Ambos en el típico 60–90.
    ini, fin = contexto(100, 130, 600)
    assert (ini, fin) == (70.0, 155.0)
    assert 60 <= fin - ini <= 90


# ---------------------------------------------------------------------------
# 2. API: validación del largo manual y payload del job
# ---------------------------------------------------------------------------
def _sesion_con_evento(h, wh):
    ses = client.post("/sessions", json={"title": "Sesión v136"}, headers=h).json()
    r = client.post(f"/workers/sessions/{ses['id']}/live-events", headers=wh, json={
        "events": [{"start_s": 100, "end_s": 110, "type": "general",
                    "mode": "general", "score": 91, "breakdown": {}}],
        "clips": []})
    assert r.status_code == 200, r.text
    evs = client.get(f"/events?session_id={ses['id']}", headers=h).json()
    assert len(evs) == 1
    return ses, evs[0]["id"]


def _payload_job(job_id):
    s = apimain.SessionLocal()
    try:
        job = s.get(apimain.Job, job_id)
        return json.loads(job.payload or "{}")
    finally:
        s.close()


def test_api_largo_manual_solo_60_a_120():
    h = _usuario(); wh = _worker(h)
    ses, eid = _sesion_con_evento(h, wh)
    # Las opciones viejas del desplegable ahora son 400, con el motivo.
    for viejo in (3, 5, 10, 15, 20):
        r = client.post(f"/events/{eid}/clip?largo_s={viejo}", headers=h)
        assert r.status_code == 400, f"largo_s={viejo} debió ser 400: {r.text}"
        assert "60, 75, 90 o 120" in r.json()["detail"]
    r = client.post(f"/sessions/{ses['id']}/generar-clips?largo_s=20", headers=h)
    assert r.status_code == 400
    # Un largo nuevo válido llega TAL CUAL al payload del job del worker.
    r = client.post(f"/events/{eid}/clip?largo_s=75", headers=h)
    assert r.status_code == 200, r.text
    assert _payload_job(r.json()["job_id"])["largo_s"] == 75
    # Y sin largo (Automática) el payload lleva None: manda contexto().
    ses2, eid2 = _sesion_con_evento(h, wh)
    r = client.post(f"/events/{eid2}/clip", headers=h)
    assert r.status_code == 200, r.text
    assert _payload_job(r.json()["job_id"])["largo_s"] is None


# ---------------------------------------------------------------------------
# 3. Panel: contrato del desplegable «Duración del clip»
# ---------------------------------------------------------------------------
def test_desplegable_duracion_solo_opciones_de_un_minuto_o_mas():
    index = (ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    bloque = index.split('id="clip-largo"')[1].split("</select>")[0]
    opciones = re.findall(r'<option value="([^"]*)">([^<]+)</option>', bloque)
    assert [v for v, _ in opciones] == ["", "60", "75", "90", "120"]
    assert [t for _, t in opciones] == ["Automática", "60 s", "75 s", "90 s", "120 s"]
    for vieja in ('value="3"', 'value="5"', 'value="10"', 'value="15"', 'value="20"'):
        assert vieja not in bloque


# ---------------------------------------------------------------------------
# 4. Trabajador: el clip manual sale con el largo pedido (ffprobe)
# ---------------------------------------------------------------------------
def _video_largo(path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=blue:size=640x360:duration=200",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=200",
                    "-af", "volume=0.5", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True, capture_output=True, timeout=300)
    return path


def test_generar_clip_manual_largo_exacto_y_automatico(tmp_path):
    video = _video_largo(tmp_path / "fuente.mp4")
    cfg = {"carpeta_clips": str(tmp_path / "clips"),
           "carpeta_grabaciones": str(tmp_path / "grab")}
    base = {"event_id": 9, "project_id": 4, "session_id": None,
            "start_s": 100, "end_s": 110, "mode": "general",
            "type": "general", "score": 90, "file_path": str(video)}
    # Largo elegido en el desplegable: 90 s exactos de ventana.
    res = worker_mod.procesar(
        {"id": 1, "type": "generar_clip", "payload": {**base, "largo_s": 90}}, cfg)
    clip = res["clips"][0]
    assert clip["duration_s"] == 90.0
    dur = _ffprobe_duracion(clip["file_path"])
    assert 88 <= dur <= 92, f"el MP4 debió durar ~90 s y dura {dur}"
    # Automática (sin largo): la ventana de contexto cae en la banda.
    res = worker_mod.procesar(
        {"id": 2, "type": "generar_clip", "payload": {**base, "event_id": 10}}, cfg)
    clip = res["clips"][0]
    assert 60 <= clip["duration_s"] <= 120
    dur = _ffprobe_duracion(clip["file_path"])
    assert 60 <= dur <= 120, f"clip automático fuera de la banda 60–120: {dur}"
