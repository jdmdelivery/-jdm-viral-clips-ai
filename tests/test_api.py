import pytest
from fastapi.testclient import TestClient
import main as apimain

client = TestClient(apimain.app)
EMAIL, PW = "jose@test.do", "clave-segura-123"

def registro_login():
    client.post("/auth/register", json={"email": EMAIL, "password": PW})
    r = client.post("/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}

def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True

def test_auth_y_password_incorrecta():
    h = registro_login()
    assert client.get("/auth/me", headers=h).json()["email"] == EMAIL
    assert client.post("/auth/login", json={"email": EMAIL, "password": "malisima-xx"}).status_code == 401
    assert client.get("/auth/me").status_code == 401
    r = client.post("/auth/register", json={"email": "corto@test.do", "password": "123"})
    assert r.status_code == 400  # contraseña mínima

def test_panel_servido():
    r = client.get("/")
    assert r.status_code == 200 and "JDM VIRAL CLIPS AI" in r.text

def test_validacion_fuentes():
    h = registro_login()
    sid = client.post("/sessions", json={"title": "Prueba", "mode": "gta"}, headers=h).json()["id"]
    # IP privada: bloqueada anti-SSRF
    r = client.post(f"/sessions/{sid}/sources", json={"url": "http://192.168.1.50/live.m3u8"}, headers=h).json()
    assert r["allowed"] is False and "SSRF" in r["explanation"]
    # loopback
    r = client.post(f"/sessions/{sid}/sources", json={"url": "http://127.0.0.1:8080/x.m3u8"}, headers=h).json()
    assert r["allowed"] is False
    # YouTube (regla de Jose, 2026-11-14): el enlace de plataforma SE ACEPTA
    # como fuente «plataforma»; la señal la resuelve el trabajador en su PC.
    r = client.post(f"/sessions/{sid}/sources", json={"url": "https://www.youtube.com/watch?v=abc"}, headers=h).json()
    assert r["allowed"] is True and r["kind"] == "plataforma" and "resuelve la señal" in r["explanation"]
    # file:// rechazado
    r = client.post(f"/sessions/{sid}/sources", json={"url": "file:///etc/passwd"}, headers=h).json()
    assert r["allowed"] is False
    # HLS público (IP pública literal): permitido
    r = client.post(f"/sessions/{sid}/sources", json={"url": "https://8.8.8.8/live/stream.m3u8"}, headers=h).json()
    assert r["allowed"] is True and r["kind"] == "hls"
    # iniciar con fuente autorizada funciona; detener también
    assert client.post(f"/sessions/{sid}/start", headers=h).status_code == 200
    assert client.post(f"/sessions/{sid}/stop", headers=h).status_code == 200

def test_iniciar_sin_fuente_falla():
    h = registro_login()
    sid = client.post("/sessions", json={"title": "Sin fuente"}, headers=h).json()["id"]
    assert client.post(f"/sessions/{sid}/start", headers=h).status_code == 400

def test_publicacion_nunca_publicada_sin_oauth():
    h = registro_login()
    clip = client.post("/clips", json={"file_path": "/tmp/x.mp4", "title": "Clip prueba", "score": 90}, headers=h).json()
    pub = client.post("/publications", json={"clip_id": clip["id"], "platform": "tiktok", "title": "T"}, headers=h).json()
    assert pub["status"] == "requiere_conexion"
    r = client.post(f"/publications/{pub['id']}/approve", headers=h).json()
    assert r["ok"] is False and "publicad" in r["nota"]  # jamás se marca publicado
    pubs = client.get("/publications", headers=h).json()
    assert all(p["status"] != "publicado" for p in pubs)
    soc = client.get("/social", headers=h).json()
    assert all(s["status"] == "sin_conectar" for s in soc)

def test_titulos_no_inventan():
    h = registro_login()
    d = client.post("/titles", json={"type": "discusion", "mode": "alofoke", "score": 88}, headers=h).json()
    assert len(d["titulos"]) == 5 and d["basado_en_transcripcion"] is False
    assert not any("“" in t for t in d["titulos"])  # sin cita falsa
    d2 = client.post("/titles", json={"type": "confesion", "mode": "alofoke", "score": 90,
                                      "transcript": "Yo nunca quise que esto pasara en la casa"}, headers=h).json()
    assert d2["basado_en_transcripcion"] is True

def test_trabajos_cola():
    h = registro_login()
    j = client.post("/jobs", json={"type": "analizar_video", "payload": {"project_id": 1}}, headers=h).json()
    assert j["status"] == "pendiente"
    lst = client.get("/jobs", headers=h).json()
    assert any(x["id"] == j["id"] for x in lst)
    stats = client.get("/stats", headers=h).json()
    assert stats["trabajos_pendientes"] >= 1
