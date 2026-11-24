"""Diagnóstico de Jose (2026-11-23, noche del estreno de Alofoke):
«cuando le doy a descargar vídeo no hace nada o está lento; descargué uno,
pero cuando vuelvo a intentarlo no se descarga».

Reproducción de las tres piezas del fallo:

1. SEGUNDA DESCARGA: la copia del clip vive en el disco EFÍMERO del
   servidor (Render free, sin R2 ni JDM_MEDIOS_DIR): un reinicio o un
   despliegue la borra, pero la tarjeta ya pintada en el panel sigue
   creyendo que hay copia. El clic va a /download (404) y cae al respaldo
   /archivo (404), y el frontend tragaba ese segundo 404 EN SILENCIO
   (descargarArchivoServidor: `if (!r.ok) return;` + `.catch(() => {})`):
   literalmente no pasaba nada en pantalla. La API sí se recupera sola al
   refrescar (tiene_copia_servidor vuelve a False y solicitar-subida
   encola otro job): lo que faltaba era que el propio clic lo hiciera.

2. LENTITUD SIN SEÑAL: descargarCopia bajaba el archivo ENTERO con
   fetch + blob() antes de guardar: sin progreso visible, un clip de
   60-85 s (decenas de MB) servido desde el free tier parece «no hace
   nada» durante minutos.

3. COLA FIFO: /jobs/claim entrega el trabajo pendiente más viejo sin
   prioridades: un «subir_clip» (lo que el usuario está esperando para
   descargar) quedaba detrás de generar_clip/analizar_video largos en
   el bucle síncrono del trabajador.
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

import main as apimain

client = TestClient(apimain.app)
EMAIL, PW = "descarga2@test.do", "clave-segura-123"
BYTES = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8
RUTA_PC = "C:\\Users\\thene\\JDMViralClips\\clips\\sesion-9\\clip-radar-100s.mp4"
RAIZ = Path(__file__).resolve().parents[1]


def _usuario():
    client.post("/auth/register", json={"email": EMAIL, "password": PW})
    r = client.post("/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h):
    r = client.post("/workers/register", json={"name": "PC descarga", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _crear_clip():
    s = apimain.SessionLocal()
    try:
        c = apimain.Clip(file_path=RUTA_PC, title="Clip descarga")
        s.add(c); s.commit(); s.refresh(c)
        return c.id
    finally:
        s.close()


def _ruta_copia(cid):
    s = apimain.SessionLocal()
    try:
        c = s.get(apimain.Clip, cid)
        return c.archivo_servidor if c else None
    finally:
        s.close()


# ---------- 1: segunda descarga tras perderse la copia del servidor ----------
def test_segunda_descarga_tras_perderse_la_copia():
    h, wh = _usuario(), _worker(_usuario())
    cid = _crear_clip()
    # El trabajador sube la copia (como hace el radar al generar el clip).
    r = client.post(f"/clips/{cid}/archivo", content=BYTES,
                    headers={**wh, "Content-Type": "video/mp4"})
    assert r.status_code == 200, r.text
    # Primera descarga: funciona.
    r = client.get(f"/clips/{cid}/download", headers=h)
    assert r.status_code == 200 and r.content == BYTES
    # El servidor se reinicia / se despliega (Render free): el disco
    # efímero pierde la copia, la BD sigue apuntando a la ruta muerta.
    copia = Path(_ruta_copia(cid))
    copia.unlink()
    # La tarjeta ya pintada llama a /download y a su respaldo /archivo:
    # hoy ambos dan 404 (el frontend lo tragaba sin mostrar nada).
    assert client.get(f"/clips/{cid}/download", headers=h).status_code == 404
    assert client.get(f"/clips/{cid}/archivo", headers=h).status_code == 404
    # Pero la API se auto-recupera: el listado ya no promete copia y
    # solicitar-subida encola un job nuevo para que la PC la re-suba.
    d = [c for c in client.get("/clips", headers=h).json() if c["id"] == cid][0]
    assert d["tiene_copia_servidor"] is False
    r = client.post(f"/clips/{cid}/solicitar-subida", headers=h)
    assert r.status_code == 200 and r.json()["estado"] == "subiendo", r.text


# ---------- 3: el claim prioriza «subir_clip» sobre trabajos pesados ----------
def test_claim_prioriza_subir_clip_sobre_trabajos_pesados():
    h, wh = _usuario(), _worker(_usuario())
    cid = _crear_clip()
    # Primero entra un trabajo pesado; después, la subida que el usuario espera.
    r = client.post("/jobs", json={"type": "generar_clip",
                                   "payload": {"clip_id": cid}}, headers=h)
    assert r.status_code == 200, r.text
    pesado = r.json()["id"]
    r = client.post(f"/clips/{cid}/solicitar-subida", headers=h)
    assert r.status_code == 200, r.text
    job = client.post("/jobs/claim", headers=wh).json()["job"]
    assert job is not None and job["type"] == "subir_clip", (
        f"el claim entregó {job and job['type']}: la subida que el usuario "
        "espera quedó detrás del trabajo pesado")
    assert job["id"] != pesado


# ---------- 1+2: el frontend no traga errores y muestra el progreso ----------
def test_panel_descarga_no_traga_errores_y_muestra_progreso():
    js = (RAIZ / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    cuerpo = js.split("function descargarArchivoServidor", 1)[1].split(
        "async function pedirCopia", 1)[0]
    # Nada de silencios: ni el retorno mudo ante !ok ni el catch vacío.
    assert "if (!r.ok) return;" not in cuerpo
    assert ".catch(() => {})" not in js.split("function descargarCopia", 1)[1].split(
        "function descargarArchivoServidor", 1)[0] + cuerpo
    # Si la copia ya no está (404 en las dos rutas), el clic re-pide la
    # subida a la PC en vez de morir en silencio.
    assert "pedirCopia" in js.split("function descargarCopia", 1)[1].split(
        "async function pedirCopia", 1)[0]
    # La descarga muestra avance (lector de stream con porcentaje/MB),
    # no un blob invisible que parece que «no hace nada».
    assert "getReader()" in js and "Descargando" in js
