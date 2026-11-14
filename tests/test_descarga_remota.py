"""Fix análisis remoto (arquitectura híbrida): el trabajador Windows no tiene
la ruta del disco del servidor, así que descarga el video del proyecto desde
la API (GET /projects/{id}/file) y analiza la copia local."""
from pathlib import Path

from fastapi.testclient import TestClient

import main as apimain
import worker as worker_mod

client = TestClient(apimain.app)
EMAIL, PW = "remoto@test.do", "clave-segura-123"
CONTENIDO = b"\x00\x01\x02video-de-prueba-remoto" * 64


def _usuario():
    client.post("/auth/register", json={"email": EMAIL, "password": PW})
    r = client.post("/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _proyecto(h):
    r = client.post("/projects/import", headers=h,
                    files={"file": ("remoto.mp4", CONTENIDO, "video/mp4")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_descarga_archivo_proyecto_autorizaciones():
    h = _usuario()
    pid = _proyecto(h)
    w = client.post("/workers/register", json={"name": "PC remoto", "info": {}}, headers=h)
    assert w.status_code == 200, w.text
    token = w.json()["worker_token"]
    # Trabajador (X-Worker-Token): 200 y bytes idénticos
    r = client.get(f"/projects/{pid}/file", headers={"X-Worker-Token": token})
    assert r.status_code == 200 and r.content == CONTENIDO
    # Usuario del panel (Bearer): 200 y bytes idénticos
    r = client.get(f"/projects/{pid}/file", headers=h)
    assert r.status_code == 200 and r.content == CONTENIDO
    # Sin autenticación: 401 · token de trabajador inválido y sin Bearer: 401
    assert client.get(f"/projects/{pid}/file").status_code == 401
    assert client.get(f"/projects/{pid}/file",
                      headers={"X-Worker-Token": "token-falso"}).status_code == 401
    # Proyecto inexistente (con auth válida): 404
    assert client.get("/projects/999999/file", headers=h).status_code == 404


class ApiFalsa:
    """Sustituto del ApiClient: 'descarga' escribiendo un archivo de mentira."""
    def __init__(self):
        self.descargas = []

    def descargar_proyecto(self, project_id, destino):
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"copia-descargada")
        self.descargas.append((project_id, destino))
        return destino


def _cfg(tmp_path):
    return {"carpeta_clips": str(tmp_path / "clips"),
            "carpeta_grabaciones": str(tmp_path / "grab")}


def test_resolver_ruta_inexistente_descarga(tmp_path):
    api = ApiFalsa()
    p = {"file_path": "/opt/render/project/src/data/uploads/1720_remoto.mp4",
         "project_id": 7}
    r = worker_mod.resolver_archivo(p, _cfg(tmp_path), api)
    assert len(api.descargas) == 1 and api.descargas[0][0] == 7
    assert Path(r).read_bytes() == b"copia-descargada"
    assert Path(r).name == "proyecto-7-1720_remoto.mp4"
    # Segunda vez: reutiliza la copia (no descarga de nuevo)
    r2 = worker_mod.resolver_archivo(p, _cfg(tmp_path), api)
    assert r2 == r and len(api.descargas) == 1


def test_resolver_ruta_existente_y_url_no_descargan(tmp_path):
    api = ApiFalsa()
    real = tmp_path / "local.mp4"
    real.write_bytes(b"local")
    p = {"file_path": str(real), "project_id": 7}
    assert worker_mod.resolver_archivo(p, _cfg(tmp_path), api) == str(real)
    url = "https://example.com/live/stream.m3u8"
    assert worker_mod.resolver_archivo({"file_path": url}, _cfg(tmp_path), api) == url
    assert api.descargas == []
