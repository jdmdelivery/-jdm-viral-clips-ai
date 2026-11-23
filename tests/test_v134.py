"""Tests de la v1.3.4: los tres pendientes de la auditoría del trabajo #71
que quedaron fuera de la v1.3.3.

(a) Instancia única del motor (worker-windows/instancia.py): dos
    pythonw.exe en la misma PC se pisaban los trabajos y las carpetas.
(b) Caducidad de jobs «procesando» (API): un trabajo reclamado por un
    motor que murió quedaba «procesando» para siempre, con la sesión
    colgada en «iniciando»/«deteniendo».
(c) FFmpeg huérfano (worker-windows/recorder.py + worker.py): la
    relanzada del actualizador salía con os._exit(0) sin detener el
    Recorder y el FFmpeg quedaba escribiendo sin motor; ahora el
    reinicio se difiere mientras haya grabación y cada Recorder barre
    el huérfano de su carpeta al arrancar.
"""
import json, os, shutil, subprocess, time, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main as apimain
import instancia
import recorder as recorder_mod
import worker as worker_mod

client = TestClient(apimain.app)
PW = "clave-segura-123"


def _usuario():
    email = f"v134-{uuid.uuid4().hex[:10]}@test.do"
    client.post("/auth/register", json={"email": email, "password": PW})
    r = client.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h):
    r = client.post("/workers/register",
                    json={"name": "PC v134", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


# ---------------------------------------------------------------------------
# (a) Instancia única
# ---------------------------------------------------------------------------
def test_instancia_segundo_motor_no_entra(tmp_path):
    primero = instancia.adquirir(tmp_path)
    assert primero is not None
    try:
        # Sin espera: el segundo arranque se rinde al instante.
        assert instancia.adquirir(tmp_path) is None
    finally:
        primero.close()
    # Liberado el bloqueo (el SO lo suelta al cerrar/morir), otro sí entra.
    tercero = instancia.adquirir(tmp_path)
    assert tercero is not None
    tercero.close()


def test_instancia_espera_al_que_sale(tmp_path):
    primero = instancia.adquirir(tmp_path)
    assert primero is not None
    primero.close()  # el motor viejo ya salió: el nuevo no debe esperar de más
    t0 = time.time()
    nuevo = instancia.adquirir(tmp_path, espera_s=5)
    assert nuevo is not None and time.time() - t0 < 2
    nuevo.close()


def test_instancia_anota_pid(tmp_path):
    f = instancia.adquirir(tmp_path)
    assert f is not None
    f.close()
    contenido = (tmp_path / instancia.NOMBRE_BLOQUEO).read_text(encoding="utf-8")
    assert contenido.strip() == f"pid={os.getpid()}"


# ---------------------------------------------------------------------------
# (b) Caducidad de jobs «procesando»
# ---------------------------------------------------------------------------
def _envejecer_job(job_id, horas=7):
    vieja = datetime.now(timezone.utc) - timedelta(hours=horas)
    from sqlalchemy import text as _texto_sql
    with apimain.engine.begin() as conn:
        conn.execute(_texto_sql("UPDATE jobs SET updated_at = :t WHERE id = :i"),
                     {"t": vieja.strftime("%Y-%m-%d %H:%M:%S"), "i": job_id})


def test_job_procesando_abandonado_caduca_al_reclamar():
    h, wh = _usuario(), None
    wh = _worker(h)
    j1 = client.post("/jobs", json={"type": "analizar_video", "payload": {}},
                     headers=h).json()
    reclamado = client.post("/jobs/claim", headers=wh).json()["job"]
    assert reclamado["id"] == j1["id"]  # quedó «procesando»
    _envejecer_job(j1["id"])  # el motor "murió" hace 7 horas
    j2 = client.post("/jobs", json={"type": "analizar_video", "payload": {}},
                     headers=h).json()
    # El siguiente reclamo caduca el abandonado y entrega el nuevo.
    reclamado2 = client.post("/jobs/claim", headers=wh).json()["job"]
    assert reclamado2["id"] == j2["id"]
    jobs = {j["id"]: j for j in client.get("/jobs?limit=200", headers=h).json()}
    assert jobs[j1["id"]]["status"] == "error"
    assert "caducado" in jobs[j1["id"]]["error"]
    assert jobs[j1["id"]]["updated_at"] is not None


def test_job_procesando_reciente_no_caduca():
    s = apimain.SessionLocal()
    try:
        j = apimain.Job(type="analizar_video", payload="{}", status="procesando")
        s.add(j); s.commit()
        assert apimain._expirar_jobs_procesando(s) == 0
        s.refresh(j)
        assert j.status == "procesando"
    finally:
        s.close()


def test_caducidad_saca_la_sesion_de_iniciando():
    s = apimain.SessionLocal()
    try:
        ses = apimain.LiveSession(title="Sesión v134", status="iniciando")
        s.add(ses); s.commit()
        vieja = datetime.now(timezone.utc) - timedelta(hours=7)
        j = apimain.Job(type="iniciar_grabacion", status="procesando",
                        payload=json.dumps({"session_id": ses.id}),
                        updated_at=vieja, created_at=vieja)
        s.add(j); s.commit()
        assert apimain._expirar_jobs_procesando(s) == 1
        s.refresh(j); s.refresh(ses)
        assert j.status == "error"
        assert ses.status == "error" and "caducó" in ses.error_fuente
    finally:
        s.close()


# ---------------------------------------------------------------------------
# (c) FFmpeg huérfano y reinicio diferido
# ---------------------------------------------------------------------------
def test_huerfano_pid_muerto_solo_borra_el_archivo(tmp_path):
    (tmp_path / recorder_mod.ARCHIVO_PID).write_text("99999999", encoding="utf-8")
    assert recorder_mod.limpiar_ffmpeg_huerfano(tmp_path) is False
    assert not (tmp_path / recorder_mod.ARCHIVO_PID).exists()


def test_huerfano_pid_reutilizado_no_se_toca(tmp_path):
    # El PID anotado es hoy ESTE proceso (Python, no ffmpeg): no se mata
    # ni se borra nada — un PID reutilizado no es un huérfano nuestro.
    (tmp_path / recorder_mod.ARCHIVO_PID).write_text(str(os.getpid()), encoding="utf-8")
    assert recorder_mod.limpiar_ffmpeg_huerfano(tmp_path) is False
    assert (tmp_path / recorder_mod.ARCHIVO_PID).exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"),
                    reason="sin ffmpeg en esta máquina")
def test_huerfano_ffmpeg_real_se_termina(tmp_path):
    proc = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc", "-t", "300", "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=recorder_mod.SIN_VENTANA)
    try:
        (tmp_path / recorder_mod.ARCHIVO_PID).write_text(str(proc.pid), encoding="utf-8")
        assert recorder_mod.limpiar_ffmpeg_huerfano(tmp_path) is True
        proc.wait(timeout=15)
        assert proc.poll() is not None
        assert not (tmp_path / recorder_mod.ARCHIVO_PID).exists()
    finally:
        if proc.poll() is None:
            proc.kill()


def test_recorder_anota_y_borra_pid(tmp_path):
    rec = recorder_mod.Recorder("fuente", tmp_path / "sesion-x", en_vivo=False)

    class _Proc:
        pid = 424242

    rec.proc = _Proc()
    rec._anotar_pid()
    assert (rec.outdir / recorder_mod.ARCHIVO_PID).read_text(encoding="utf-8") == "424242"
    rec._borrar_pid()
    assert not (rec.outdir / recorder_mod.ARCHIVO_PID).exists()


class _FinHilo(Exception):
    pass


def _correr_hilo_una_pasada(monkeypatch, hay_grabacion):
    """Corre _hilo_actualizador con el actualizador simulado y el reloj
    cortado: la primera «siesta» pasa y la segunda corta el hilo."""
    llamadas = {"popen": [], "registros": []}
    monkeypatch.setattr(worker_mod, "_registrar_reinicio",
                        lambda huella: llamadas["registros"].append(huella))
    monkeypatch.setattr(worker_mod, "_reinicio_ya_aplicado", lambda huella: False)
    monkeypatch.setattr(worker_mod.subprocess, "Popen",
                        lambda *a, **k: llamadas["popen"].append((a, k)))
    monkeypatch.setattr(worker_mod.os, "_exit",
                        lambda codigo: (_ for _ in ()).throw(_FinHilo()))
    import actualizador as actualizador_mod
    monkeypatch.setattr(actualizador_mod, "revisar",
                        lambda carpeta: {"actualizados": ["worker.py"],
                                         "reiniciar": True, "errores": [],
                                         "huella": "huella-x",
                                         "sin_cambios": False})
    siestas = {"n": 0}

    def _dormir(segundos):
        siestas["n"] += 1
        if siestas["n"] > 1:
            raise _FinHilo()

    monkeypatch.setattr(worker_mod.time, "sleep", _dormir)
    worker_mod._hilo_actualizador("worker-config.json",
                                  hay_grabacion=hay_grabacion)
    return llamadas


def test_reinicio_diferido_mientras_graba(monkeypatch):
    llamadas = _correr_hilo_una_pasada(monkeypatch, hay_grabacion=lambda: True)
    # Con grabación activa: ni relanzada ni huella marcada (se reintenta
    # en la siguiente revisión, cuando la grabación ya haya terminado).
    assert llamadas["popen"] == [] and llamadas["registros"] == []


def test_reinicio_sin_grabacion_si_relanza(monkeypatch):
    llamadas = _correr_hilo_una_pasada(monkeypatch, hay_grabacion=lambda: False)
    assert len(llamadas["popen"]) == 1 and llamadas["registros"] == ["huella-x"]
