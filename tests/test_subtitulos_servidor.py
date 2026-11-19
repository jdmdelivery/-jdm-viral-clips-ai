"""Subtítulos en el almacén de la API.

La ruta de Windows del trabajador no se guarda como archivo reproducible.
El MP4 subtitulado entra por el token del trabajador y sale solo para el
dueño de la sesión. Sin JDM_MEDIOS_DIR, Render no escribe en el disco efímero.
"""
import os, subprocess, sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main as apimain

client = TestClient(apimain.app)
BYTES = b"\x00\x00\x00\x18ftypmp42" + b"sub" * 400
ORIGINAL = b"\x00\x00\x00\x18ftypmp42" + b"ori" * 400


@pytest.fixture(autouse=True)
def _medios(monkeypatch, tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("medios")
    monkeypatch.setenv("JDM_MEDIOS_DIR", str(carpeta))
    monkeypatch.delenv("RENDER", raising=False)
    for clave in ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_API_TOKEN"):
        monkeypatch.delenv(clave, raising=False)
    return carpeta


def _entrar(email):
    client.post("/auth/register", json={"email": email, "password": "clave-segura-123"})
    r = client.post("/auth/login", json={"email": email, "password": "clave-segura-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _usuario_id(email):
    s = apimain.SessionLocal()
    try:
        return s.query(apimain.User).filter_by(email=email).one().id
    finally:
        s.close()


def _worker(h):
    r = client.post("/workers/register", json={"name": "PC subtitulos", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _clip(email, ruta, titulo="Clip con voz"):
    s = apimain.SessionLocal()
    try:
        ses = apimain.LiveSession(title="Sesion prueba", user_id=_usuario_id(email), status="detenida")
        s.add(ses)
        s.commit()
        s.refresh(ses)
        c = apimain.Clip(session_id=ses.id, file_path=ruta, title=titulo, format="9:16", score=80)
        s.add(c)
        s.commit()
        s.refresh(c)
        return c.id, ses.id
    finally:
        s.close()


def _fila(cid):
    s = apimain.SessionLocal()
    try:
        c = s.get(apimain.Clip, cid)
        return {"path": c.subtitulo_path, "estado": c.subtitulo_estado, "texto": c.subtitulo_texto,
                "error": c.subtitulo_error, "session_id": c.session_id}
    finally:
        s.close()


def test_subida_registra_clave_y_no_ruta_de_windows(tmp_path):
    dueno = _entrar("dueno-subs@test.do")
    otro = _entrar("otro-subs@test.do")
    wh = _worker(dueno)
    ruta_pc = r"C:\Users\thene\JDMViralClips\clips\sesion-9\clip-radar-10s.mp4"
    cid, sid = _clip("dueno-subs@test.do", ruta_pc)
    original = apimain.CLIPS_DIR / f"clip_{cid}.mp4"
    original.write_bytes(ORIGINAL)
    assert client.post(f"/clips/{cid}/subtitulado", content=BYTES).status_code == 401
    assert client.post(f"/clips/{cid}/subtitulado", content=BYTES,
                       headers={"X-Worker-Token": "falso"}).status_code == 401
    aviso = client.post("/workers/clips/subtitulos", headers=wh, json={
        "file_path": ruta_pc, "estado": "listo", "texto": "Mira esto",
        "subtitulo_path": r"C:\Users\thene\JDMViralClips\clips\sesion-9\clip-radar-10s.subtitulado.mp4"})
    assert aviso.status_code == 200, aviso.text
    assert aviso.json()["clip_id"] == cid
    assert aviso.json()["sesion_id"] == sid
    assert aviso.json()["tiene_subtitulado"] is False
    assert _fila(cid)["path"] in (None, "")
    assert "Users" not in str(_fila(cid)["path"] or "")
    subida = client.post(f"/clips/{cid}/subtitulado", content=BYTES, headers=wh)
    assert subida.status_code == 200, subida.text
    assert subida.json()["clave"] == f"clip_{cid}.subtitulado.mp4"
    assert _fila(cid)["path"] == f"clip_{cid}.subtitulado.mp4"
    assert "\\" not in _fila(cid)["path"]
    assert original.read_bytes() == ORIGINAL
    assert client.get(f"/clips/{cid}/subtitulado").status_code == 401
    assert client.get(f"/clips/{cid}/subtitulado", headers=otro).status_code == 403
    assert client.get(f"/clips/{cid}/subtitulos", headers=otro).status_code == 403
    visto = client.get(f"/clips/{cid}/subtitulado", headers=dueno)
    assert visto.status_code == 200 and visto.content == BYTES
    baja = client.get(f"/clips/{cid}/subtitulado?descargar=1", headers=dueno)
    assert baja.status_code == 200 and baja.content == BYTES
    assert "subtitulado.mp4" in baja.headers.get("content-disposition", "")
    lista = client.get("/clips", headers=dueno).json()
    fila = [c for c in lista if c["id"] == cid][0]
    assert fila["tiene_subtitulado"] is True
    assert fila["subtitulo_texto"] == "Mira esto"
    assert all(c["id"] != cid for c in client.get("/clips", headers=otro).json())


def test_render_sin_almacen_persistente_no_guarda(monkeypatch):
    dueno = _entrar("render-subs@test.do")
    wh = _worker(dueno)
    cid, _sid = _clip("render-subs@test.do", r"C:\clips\a.mp4")
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("JDM_MEDIOS_DIR", raising=False)
    r = client.post(f"/clips/{cid}/subtitulado", content=BYTES, headers=wh)
    assert r.status_code == 503
    assert "persistente" in r.json()["detail"]
    assert not list(apimain.CLIPS_DIR.glob(f"clip_{cid}.subtitulado.mp4"))


def test_borrado_quita_el_derivado_y_deja_el_otro_clip(tmp_path):
    dueno = _entrar("borra-subs@test.do")
    wh = _worker(dueno)
    origen = tmp_path / "clip-a.mp4"
    origen.write_bytes(ORIGINAL)
    derivado = tmp_path / "clip-a.subtitulado.mp4"
    derivado.write_bytes(b"local")
    grabacion = tmp_path / "grabaciones"
    grabacion.mkdir()
    completa = grabacion / "grabacion_completa.mp4"
    completa.write_bytes(b"completa")
    cid, _sid = _clip("borra-subs@test.do", str(origen))
    otro, _s2 = _clip("borra-subs@test.do", str(tmp_path / "clip-b.mp4"), "Otro")
    assert client.post(f"/clips/{cid}/subtitulado", content=BYTES, headers=wh).status_code == 200
    assert client.post(f"/clips/{otro}/subtitulado", content=b"otro-sub", headers=wh).status_code == 200
    ajeno = _entrar("ajeno-borra@test.do")
    assert client.delete(f"/clips/{cid}", headers=ajeno).status_code == 403
    assert (apimain.directorio_medios() / f"clip_{cid}.subtitulado.mp4").is_file()
    r = client.delete(f"/clips/{cid}", headers=dueno)
    assert r.status_code == 200, r.text
    assert not (apimain.directorio_medios() / f"clip_{cid}.subtitulado.mp4").exists()
    assert (apimain.directorio_medios() / f"clip_{otro}.subtitulado.mp4").read_bytes() == b"otro-sub"
    assert not derivado.exists()
    assert completa.read_bytes() == b"completa"
    assert not origen.exists()


def test_correccion_encola_trabajo_sin_escribir_ruta_windows(tmp_path):
    dueno = _entrar("corrige-subs@test.do")
    cid, _sid = _clip("corrige-subs@test.do", r"C:\Users\thene\JDMViralClips\clips\sesion-1\clip.mp4")
    r = client.put(f"/clips/{cid}/subtitulos", headers=dueno, json={"texto": "Texto corregido de verdad"})
    assert r.status_code == 200, r.text
    assert r.json()["job_id"]
    assert _fila(cid)["estado"] == "corregir"
    assert _fila(cid)["texto"] == "Texto corregido de verdad"
    assert _fila(cid)["path"] in (None, "")
    s = apimain.SessionLocal()
    try:
        jobs = []
        for j in s.query(apimain.Job).filter_by(type="corregir_subtitulos").all():
            import json
            if json.loads(j.payload).get("clip_id") == cid:
                jobs.append(j)
    finally:
        s.close()
    assert len(jobs) == 1
    r2 = client.put(f"/clips/{cid}/subtitulos", headers=dueno, json={"texto": "Texto corregido otra vez"})
    assert r2.status_code == 200
    assert r2.json()["job_id"] == r.json()["job_id"]


def test_el_archivo_sigue_despues_de_otro_proceso():
    dueno = _entrar("reinicio-subs@test.do")
    wh = _worker(dueno)
    cid, _sid = _clip("reinicio-subs@test.do", r"C:\clips\reinicio.mp4")
    assert client.post(f"/clips/{cid}/subtitulado", content=BYTES, headers=wh).status_code == 200
    s = apimain.SessionLocal()
    try:
        uid = _usuario_id("reinicio-subs@test.do")
        token = s.query(apimain.SessionToken).filter_by(user_id=uid).one().token
    finally:
        s.close()
    script = (
        "import os\n"
        "from fastapi.testclient import TestClient\n"
        "import main\n"
        f"c = TestClient(main.app)\n"
        f"r = c.get('/clips/{cid}/subtitulado', headers={{'Authorization': 'Bearer {token}'}})\n"
        "open(os.environ['JDM_RESULTADO'], 'w', encoding='utf-8').write(str(r.status_code)+':'+str(len(r.content)))\n"
    )
    salida = apimain.directorio_medios() / "resultado-reinicio.txt"
    env = os.environ.copy()
    env["JDM_RESULTADO"] = str(salida)
    env["JDM_MEDIOS_DIR"] = str(apimain.directorio_medios())
    proc = subprocess.run([sys.executable, "-c", script], cwd=str(apimain.BASE_DIR / "apps" / "api"),
                          env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert salida.read_text(encoding="utf-8") == f"200:{len(BYTES)}"


def test_panel_conserva_controles():
    js = Path(apimain.BASE_DIR, "apps", "web", "app.js").read_text(encoding="utf-8")
    for texto in ("SUBTÍTULOS", "VER CLIP", "DESCARGAR CLIP", "DESCARGAR CON SUBTÍTULOS",
                  "ELIMINAR CLIP", "descargarSubtitulado", "El original siempre queda en tu PC."):
        assert texto in js
    req = Path(apimain.BASE_DIR, "apps", "api", "requirements.txt").read_text(encoding="utf-8")
    assert "faster-whisper" not in req
    assert "faster_whisper" not in req


def test_job_corregir_no_borra_el_original(tmp_path, monkeypatch):
    import subtitulos
    import worker as worker_mod
    origen = tmp_path / "voz.mp4"
    origen.write_bytes(b"video-original")
    sub = tmp_path / "voz.subtitulado.mp4"
    sub.write_bytes(b"video-sub")

    def falso(ruta, texto):
        assert Path(ruta).read_bytes() == b"video-original"
        return {"ok": True, "texto": texto, "subtitulado": str(sub)}

    class Api:
        def __init__(self):
            self.subido = None
        def reportar_subtitulos(self, file_path, estado, error="", texto="", subtitulo_path=""):
            assert "subtitulado" not in (subtitulo_path or "")
            return {"ok": True, "clip_id": 7}
        def subir_subtitulado(self, clip_id, ruta):
            self.subido = (clip_id, Path(ruta).read_bytes())
            return {"bytes": 9}

    monkeypatch.setattr(subtitulos, "aplicar_texto", falso)
    api = Api()
    r = worker_mod.procesar({"id": 1, "type": "corregir_subtitulos", "payload": {
        "clip_id": 7, "file_path": str(origen), "texto": "Hola mundo"}}, {}, api)
    assert r["corregido"] is True
    assert origen.read_bytes() == b"video-original"
    assert api.subido == (7, b"video-sub")


class _Resp:
    def __init__(self, codigo, datos=b""):
        self.status_code = codigo
        self._datos = datos
        self.headers = {"Content-Length": str(len(datos))}
    def close(self):
        return None
    def iter_content(self, n):
        yield self._datos


class _R2Falso:
    bucket = "jdm-viral-clips"
    def __init__(self):
        self.objs = {}
    def existe(self, clave):
        return clave in self.objs
    def put(self, clave, data, content_type="application/octet-stream"):
        self.objs[clave] = bytes(data)
        return _Resp(200)
    def abrir(self, clave, rango=None):
        return _Resp(200, self.objs[clave])
    def borrar(self, clave):
        self.objs.pop(clave, None)
        return _Resp(204)
    def uri(self, clave):
        return f"r2://jdm-viral-clips/{clave}"


def test_r2_guarda_subtitulado_sin_disco_y_no_pisa(monkeypatch, tmp_path):
    falso = _R2Falso()
    monkeypatch.setattr(apimain.almacen_r2, "desde_entorno", lambda: falso)
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("JDM_MEDIOS_DIR", raising=False)
    dueno = _entrar("r2-subs@test.do")
    otro = _entrar("r2-otro@test.do")
    wh = _worker(dueno)
    cid, sid = _clip("r2-subs@test.do", str(tmp_path / "voz.mp4"))
    uid = _usuario_id("r2-subs@test.do")
    r = client.post(f"/clips/{cid}/subtitulado", content=BYTES, headers=wh)
    assert r.status_code == 200, r.text
    clave = f"usuarios/{uid}/sesiones/{sid}/clips/{cid}/subtitulado.mp4"
    assert r.json()["clave"] == clave
    assert r.json()["destino"] == "r2"
    assert falso.objs[clave] == BYTES
    assert not list(tmp_path.glob("*.mp4"))
    otra = client.post(f"/clips/{cid}/subtitulado", content=b"nuevo", headers=wh)
    assert otra.status_code == 409
    assert falso.objs[clave] == BYTES
    assert client.get(f"/clips/{cid}/subtitulado", headers=otro).status_code == 403
    visto = client.get(f"/clips/{cid}/subtitulado", headers=dueno)
    assert visto.status_code == 200 and visto.content == BYTES
    assert client.delete(f"/clips/{cid}", headers=dueno).status_code == 200
    assert clave not in falso.objs
