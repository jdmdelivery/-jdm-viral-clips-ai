"""Lote SIN CAPCUT (orden de Jose, 2026-11-14): la función CapCut se retira
por completo — «eso no funciona y cuando le doy click me pone la PC loca».

Contrato probado:
- Los endpoints POST /clips/{id}/capcut y POST /sessions/{id}/capcut ya
  no existen (404).
- Al arrancar, la API cancela los trabajos PENDIENTES de tipos
  enviar_capcut_clip / enviar_capcut_sesion con estado «cancelado» y
  error «Función CapCut retirada por Jose» (los que quedaran encolados
  en la BD, como el #37, nunca llegan al trabajador). Idempotente y sin
  tocar trabajos de otros tipos ni estados.
- El trabajador ya no tiene handlers de CapCut: un trabajo viejo de esos
  tipos falla rápido como «no soportado», sin ejecutar nada.
- capcut.py no existe en worker-windows ni en el manifiesto, y el panel
  (app.js / index.html) no menciona CapCut en ninguna parte.
"""
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main as apimain
import worker as worker_mod

client = TestClient(apimain.app)
PW = "clave-segura-123"
RAIZ = Path(__file__).resolve().parent.parent


def _usuario():
    email = f"u-{uuid.uuid4().hex[:10]}@t.com"
    client.post("/auth/register", json={"email": email, "password": PW})
    tok = client.post("/auth/login", json={"email": email, "password": PW}).json()["token"]
    return {"Authorization": "Bearer " + tok}


# --- Los endpoints ya no existen -------------------------------------------
def test_endpoints_capcut_no_existen():
    h = _usuario()
    assert client.post("/clips/1/capcut", headers=h).status_code == 404
    assert client.post("/sessions/1/capcut", headers=h).status_code == 404
    rutas = {(r.path, tuple(sorted(getattr(r, "methods", []) or [])))
             for r in apimain.app.routes}
    assert not any("capcut" in p for p, _m in rutas), rutas


# --- Cancelación al arranque ------------------------------------------------
def test_cancelar_jobs_capcut_al_arranque_idempotente():
    s = apimain.SessionLocal()
    ids = {}
    try:
        for clave, tipo, estado in (
                ("clip_pend", "enviar_capcut_clip", "pendiente"),
                ("sesion_pend", "enviar_capcut_sesion", "pendiente"),
                ("clip_hecho", "enviar_capcut_clip", "completado"),
                ("otro", "generar_clip", "pendiente")):
            j = apimain.Job(type=tipo, payload="{}", status=estado)
            s.add(j); s.commit(); s.refresh(j)
            ids[clave] = j.id
        n = apimain._cancelar_jobs_capcut()
        assert n == 2, f"debía cancelar 2 pendientes de CapCut, canceló {n}"
        s.expire_all()
        for clave in ("clip_pend", "sesion_pend"):
            j = s.get(apimain.Job, ids[clave])
            assert j.status == "cancelado", (clave, j.status)
            assert j.error == "Función CapCut retirada por Jose", j.error
        # Los demás trabajos quedan intactos.
        assert s.get(apimain.Job, ids["clip_hecho"]).status == "completado"
        assert s.get(apimain.Job, ids["otro"]).status == "pendiente"
        # Idempotente: un segundo arranque no cancela nada más.
        assert apimain._cancelar_jobs_capcut() == 0
    finally:
        for jid in ids.values():
            j = s.get(apimain.Job, jid)
            if j is not None:
                s.delete(j)
        s.commit(); s.close()


# --- El trabajador rechaza los tipos viejos sin ejecutar nada ---------------
@pytest.mark.parametrize("tipo", ["enviar_capcut_clip", "enviar_capcut_sesion"])
def test_trabajador_rechaza_jobs_capcut(tipo):
    with pytest.raises(ValueError, match="no soportado"):
        worker_mod.procesar({"type": tipo, "payload": {"clip_id": 1}}, {})


# --- Ni rastro en archivos ---------------------------------------------------
def test_capcut_fuera_del_trabajador_y_del_panel():
    carpeta = RAIZ / "worker-windows"
    assert not (carpeta / "capcut.py").exists()
    manifiesto = json.loads((carpeta / "manifest.json").read_text(encoding="utf-8"))
    assert "capcut.py" not in manifiesto["files"]
    for fuente in ("worker.py", "sync.py", "radar.py"):
        texto = (carpeta / fuente).read_text(encoding="utf-8").lower()
        assert "capcut" not in texto, fuente
    for pagina in ("apps/web/app.js", "apps/web/index.html"):
        texto = (RAIZ / pagina).read_text(encoding="utf-8").lower()
        assert "capcut" not in texto, pagina
