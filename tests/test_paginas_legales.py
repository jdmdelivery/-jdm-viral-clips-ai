"""Lote rediseño + páginas legales (2026-11-14):
- GET /terminos y GET /privacidad son públicos (sin login), devuelven 200
  y contienen su título y el contacto del propietario.
- El panel (/) sigue sirviéndose igual, con los enlaces legales y el
  nuevo CSS referenciado."""
from fastapi.testclient import TestClient
import main as apimain

client = TestClient(apimain.app)


def test_terminos_publico():
    r = client.get("/terminos")  # sin headers de autenticación
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Términos de uso" in r.text
    assert "JDM Viral Clips AI" in r.text
    assert "joseluisdiazmartinez90@gmail.com" in r.text
    # Hechos del programa: nada se publica sin aprobación del propietario.
    assert "aprobación del propietario" in " ".join(r.text.split())


def test_privacidad_publica():
    r = client.get("/privacidad")  # sin headers de autenticación
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Política de privacidad" in r.text
    assert "JDM Viral Clips AI" in r.text
    assert "joseluisdiazmartinez90@gmail.com" in r.text
    # Hechos del programa: no vende datos; tokens cifrados.
    assert "No vendemos datos" in r.text
    assert "cifrados" in r.text


def test_panel_sigue_igual_con_enlaces_legales():
    r = client.get("/")
    assert r.status_code == 200 and "JDM VIRAL CLIPS AI" in r.text
    # Pie de la pantalla de acceso: enlaces a las páginas legales.
    assert 'href="/terminos"' in r.text and 'href="/privacidad"' in r.text
    # El CSS del rediseño sigue referenciado desde el panel.
    assert "/static/style.css" in r.text


def test_verificacion_tiktok_en_raiz():
    # La verificación de TikTok for Developers exige la raíz del dominio y
    # el contenido exacto del archivo (68 bytes, sin salto de línea final).
    r = client.get("/tiktokwgWIbOYQU4ZZROQ99XH8furOtNXmJ1b9.txt")
    assert r.status_code == 200
    assert r.content == b"tiktok-developers-site-verification=wgWIbOYQU4ZZROQ99XH8furOtNXmJ1b9"
    assert len(r.content) == 68


def test_css_redisenado_servido():
    r = client.get("/static/style.css")
    assert r.status_code == 200
    # Acento único del rediseño sobrio 2026-11-23 (sustituye al rojo
    # neón #ff0033 del rediseño 2026-11-14, por orden de Jose).
    assert "#e63946" in r.text
    assert "#ff0033" not in r.text
    assert "#7b2fff" not in r.text
