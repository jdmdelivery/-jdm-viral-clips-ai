"""Fixes del uso real del panel (reporte de Jose, 2026-11-14):
- clipper.contexto: todo corte dura 60–120 s (regla de Jose, 2026-11-23).
- worker: rama «generar_clip» (antes los jobs se reclamaban y fallaban).
- API: detener_grabacion registra la grabación como Project (una sola vez).
- Redes: /auth/{plat}/conectar?formato=json -> 400 honesto sin credenciales.
- GET /clips: campo «descargable» según el archivo esté o no en el servidor.
- Humo extremo a extremo: detener -> proyecto -> analizar -> evento ->
  generar_clip -> clip visible en GET /clips (como lo usa Jose)."""
import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

import main as apimain
import worker as worker_mod
from clipper import contexto, MIN_CLIP_S, MAX_CLIP_S

client = TestClient(apimain.app)
EMAIL, PW = "fixpanel@test.do", "clave-segura-123"
CRED_VARS = ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "META_APP_ID",
             "META_APP_SECRET", "YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET")


# ---------- utilidades ----------
def _usuario():
    client.post("/auth/register", json={"email": EMAIL, "password": PW})
    r = client.post("/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h):
    r = client.post("/workers/register", json={"name": "PC fix-panel", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _reclamar(wh, job_id, intentos=300):
    """Reclama hasta llegar a MI job; los ajenos se completan vacíos (sin
    eventos ni clips: no crean proyectos, eventos ni publicaciones)."""
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


def _video_con_evento(path):
    """100 s: 30 s tranquilo + 40 s fuerte (8 piezas de 5 s) + 30 s
    tranquilo, con los cortes alineados a los segmentos de 10 s del
    analizador. La fuente dura ≥ 100 s para que todo clip pueda cumplir
    la regla de Jose de 60–120 s sin inventar duración."""
    tmp = Path(path).parent
    partes = []
    quieto = tmp / "q.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=blue:size=640x360:duration=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                    "-af", "volume=0.02", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(quieto)],
                   check=True, capture_output=True, timeout=120)
    partes += [quieto, quieto, quieto]
    for i, c in enumerate(("red", "green", "yellow", "white") * 2):
        p = tmp / f"f{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"color=c={c}:size=640x360:duration=5",
                        "-f", "lavfi", "-i", f"sine=frequency={300 + i * 200}:duration=5",
                        "-af", "volume=0.85", "-shortest", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(p)],
                       check=True, capture_output=True, timeout=120)
        partes.append(p)
    partes += [quieto, quieto, quieto]
    lista = tmp / "lista.txt"
    lista.write_text("".join(f"file '{p.name}'\n" for p in partes))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lista), "-c", "copy", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


def _cfg(tmp_path):
    return {"carpeta_clips": str(tmp_path / "clips"),
            "carpeta_grabaciones": str(tmp_path / "grab"),
            "pre_s": 30, "post_s": 25}


def _proyectos_por_ruta(ruta):
    """GET /projects no expone file_path: se verifica directo en la BD."""
    s = apimain.SessionLocal()
    try:
        return [(p.id, p.name) for p in
                s.query(apimain.Project).filter_by(file_path=ruta).all()]
    finally:
        s.close()


# ---------- C: duración de contexto() ----------
def test_contexto_evento_corto_se_extiende_a_60():
    ini, fin = contexto(2, 6, 300)          # evento de 4 s pegado al inicio
    assert ini == 0.0 and fin - ini == 60.0
    ini, fin = contexto(294, 298, 300)      # evento de 4 s pegado al final
    assert fin == 300.0 and fin - ini == 60.0


def test_contexto_evento_largo_se_recorta_a_120_desde_su_inicio():
    ini, fin = contexto(50, 250, 300)       # evento de 200 s en video de 300 s
    assert ini == 50.0                       # el inicio del evento no se mueve
    assert fin - ini == 120.0


def test_contexto_video_mas_corto_que_el_minimo_es_el_video_entero():
    ini, fin = contexto(5, 10, 20)
    assert (ini, fin) == (0.0, 20.0)
    ini, fin = contexto(5, 10, 45)          # 45 s < 60: tampoco se inventa
    assert (ini, fin) == (0.0, 45.0)


def test_contexto_ventana_en_banda_no_cambia():
    ini, fin = contexto(50, 90, 300, 20, 15)  # 40 s de evento + 35 de contexto
    assert (ini, fin) == (30.0, 105.0)
    assert MIN_CLIP_S <= fin - ini <= MAX_CLIP_S


# ---------- A: rama generar_clip del trabajador ----------
def test_generar_clip_job_real(tmp_path):
    video = _video_con_evento(tmp_path / "fuente.mp4")
    job = {"id": 1, "type": "generar_clip",
           "payload": {"event_id": 5, "project_id": 3, "session_id": None,
                       "start_s": 12, "end_s": 16, "mode": "general",
                       "type": "general", "score": 80, "file_path": str(video)}}
    res = worker_mod.procesar(job, _cfg(tmp_path))
    assert len(res["clips"]) == 1
    clip = res["clips"][0]
    for k in ("file_path", "format", "duration_s", "score", "title", "titles", "subtitles"):
        assert k in clip
    assert clip["event_id"] == 5 and clip["format"] == "9:16"
    assert clip["subtitles"] is False
    assert MIN_CLIP_S <= clip["duration_s"] <= MAX_CLIP_S
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name,width,height",
                        "-show_entries", "format=duration", "-of", "json",
                        clip["file_path"]], capture_output=True, text=True, timeout=60)
    info = json.loads(r.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["width"], v["height"], v["codec_name"]) == (1080, 1920, "h264")
    assert a["codec_name"] == "aac"
    assert MIN_CLIP_S <= float(info["format"]["duration"]) <= MAX_CLIP_S


# ---------- Formato: el editor también exporta en vertical por defecto ----------
def test_editor_por_defecto_exporta_vertical_1080x1920(tmp_path):
    import editor
    v = tmp_path / "horizontal.mp4"  # fuente horizontal a propósito
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=red:size=320x240:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(v)], check=True, capture_output=True, timeout=120)
    r = editor.aplicar_edicion(v, tmp_path / "editado.mp4",
                               {"trim_start_s": 0, "trim_end_s": 3})
    assert r["format"] == "9:16"
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height", "-of", "json", r["file_path"]],
        capture_output=True, text=True, timeout=60).stdout)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    au = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (vs["width"], vs["height"], vs["codec_name"]) == (1080, 1920, "h264")
    assert au["codec_name"] == "aac"


# ---------- B: detener_grabacion crea el Project una sola vez ----------
def test_detener_grabacion_crea_proyecto_sin_duplicar():
    h, wh = _usuario(), _worker(_usuario())
    ruta = "C:\\Users\\thene\\JDMViralClips\\grabaciones\\sesion-4242\\grabacion_completa.mp4"
    for _ in range(2):
        r = client.post("/jobs", json={"type": "detener_grabacion",
                                       "payload": {"session_id": 4242}}, headers=h)
        jid = r.json()["id"]
        job = _reclamar(wh, jid)
        _completar(wh, job["id"], {"estado": "detenida", "grabacion_completa": ruta})
    mismos = _proyectos_por_ruta(ruta)
    assert len(mismos) == 1
    assert mismos[0][1] == "Grabación sesión 4242"
    # Y el proyecto SÍ aparece en la lista que ve Jose en «Analizar con IA»
    ids_panel = [p["id"] for p in client.get("/projects", headers=h).json()]
    assert mismos[0][0] in ids_panel


# ---------- D: conectar en JSON sin credenciales -> 400 claro ----------
def test_conectar_json_sin_credenciales_400(monkeypatch):
    for v in CRED_VARS:
        monkeypatch.delenv(v, raising=False)
    h = _usuario()
    r = client.get("/auth/tiktok/conectar?formato=json", headers=h)
    assert r.status_code == 400
    assert "app OAuth" in r.json()["detail"]


# ---------- E: GET /clips marca «descargable» según el servidor ----------
def test_clips_descargable_segun_archivo_en_servidor(tmp_path):
    h = _usuario()
    real = tmp_path / "en-el-servidor.mp4"
    real.write_bytes(b"clip")
    r = client.post("/clips", json={"file_path": str(real), "title": "En servidor"}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/clips", json={"file_path": "/ruta/del/trabajador/clip.mp4",
                                    "title": "En la PC"}, headers=h)
    assert r.status_code == 200, r.text
    clips = {c["file_path"]: c for c in client.get("/clips", headers=h).json()}
    assert clips[str(real)]["descargable"] is True
    assert clips["/ruta/del/trabajador/clip.mp4"]["descargable"] is False
    # Los campos que la tabla del panel muestra siempre vienen en la respuesta
    for c in (clips[str(real)], clips["/ruta/del/trabajador/clip.mp4"]):
        assert "title" in c and "duration_s" in c and "score" in c


# ---------- Humo extremo a extremo: el flujo real de Jose ----------
def test_humo_detener_analizar_generar_clip(tmp_path):
    h = _usuario()
    wh = _worker(h)
    video = _video_con_evento(tmp_path / "grabacion.mp4")
    cfg = _cfg(tmp_path)
    # 1) Detener la grabación -> la grabación aparece como proyecto
    r = client.post("/jobs", json={"type": "detener_grabacion",
                                   "payload": {"session_id": 5150}}, headers=h)
    job = _reclamar(wh, r.json()["id"])
    _completar(wh, job["id"], {"estado": "detenida", "grabacion_completa": str(video)})
    proyectos = _proyectos_por_ruta(str(video))
    assert len(proyectos) == 1
    pid = proyectos[0][0]
    # 2) Analizar el proyecto con el trabajador real
    r = client.post(f"/projects/{pid}/analyze", headers=h)
    job = _reclamar(wh, r.json()["job_id"])
    resultado = worker_mod.procesar({"id": job["id"], "type": job["type"],
                                     "payload": job["payload"]}, cfg)
    _completar(wh, job["id"], resultado)
    eventos = [e for e in client.get("/events", headers=h).json()
               if e["project_id"] == pid]
    assert eventos, "el análisis no detectó ningún evento en el tramo fuerte"
    ev = eventos[0]
    # 3) generar_clip con el payload tal como lo arma evaluar_piloto
    payload = {"event_id": ev["id"], "project_id": pid, "session_id": None,
               "start_s": ev["start_s"], "end_s": ev["end_s"], "mode": ev["mode"],
               "type": ev["type"], "score": ev["score"], "file_path": str(video)}
    r = client.post("/jobs", json={"type": "generar_clip", "payload": payload}, headers=h)
    job = _reclamar(wh, r.json()["id"])
    resultado = worker_mod.procesar({"id": job["id"], "type": "generar_clip",
                                     "payload": payload}, cfg)
    _completar(wh, job["id"], resultado)
    # 4) El clip quedó persistido y visible en GET /clips
    clips = [c for c in client.get("/clips", headers=h).json()
             if c["event_id"] == ev["id"]]
    assert len(clips) == 1
    assert MIN_CLIP_S <= clips[0]["duration_s"] <= MAX_CLIP_S
    assert Path(clips[0]["file_path"]).exists()
