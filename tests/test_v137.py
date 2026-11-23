"""Tests de la v1.3.8 — Jose rechazó el rediseño de la v1.3.7
(«Ahora está peor, déjalo como estaba») y el panel volvió a la vista
de la v1.3.5, con UNA sola excepción y todo lo demás intacto:

1. Vista restaurada: el Centro LIVE es el de la v1.3.5 — botones
   separados #btn-iniciar («⏺ GRABAR») y #btn-detener, botón-toggle
   del radar #btn-radar, botones ABRIR LIVE / VER MOMENTOS / GENERAR
   CLIPS / PUBLICAR CLIP en la vista principal y la paleta de la
   v1.3.5 (#ff0033). Fuera el botón único #btn-grabar, el desplegable
   «Más opciones», la línea #radar-estado y el acento #e63946.
2. EXCEPCIÓN: el desplegable «Duración del clip» conserva las opciones
   nuevas de la v1.3.6 (Automática, 60, 75, 90, 120 s) — la API solo
   acepta esos largos y la banda 60–120 fue orden expresa de Jose.
   Las opciones viejas de 3–20 s NO vuelven.
3. Se quedan los arreglos de fondo: banda 60–120, instancia única,
   reaper de jobs, anti-huérfanos, subtítulos OFF al VER y
   aprobar/rechazar por clip (botones en la tarjeta, como en v1.3.5).
4. Versión visible 1.3.8: VERSION del trabajador y manifest.json.
"""
import json
import re
from pathlib import Path

import worker as worker_mod

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
APPJS = (ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "apps" / "web" / "style.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Vista v1.3.5 restaurada: botones separados, sin rediseño v1.3.7
# ---------------------------------------------------------------------------
def test_vista_restaurada_botones_v135():
    assert 'id="btn-iniciar" class="rojo btn-grabar" onclick="iniciarGrab()"' in INDEX
    assert "⏺ GRABAR" in INDEX
    assert 'id="btn-detener" class="rojo" onclick="detenerGrab()"' in INDEX
    assert 'id="btn-radar" onclick="activarRadar()"' in INDEX
    assert 'onclick="abrirLive()"' in INDEX
    assert "🔥 VER MOMENTOS DETECTADOS" in INDEX
    assert 'id="clip-largo"' in INDEX
    # Fuera todo rastro del rediseño rechazado.
    assert 'id="btn-grabar"' not in INDEX
    assert "botonGrabar" not in APPJS and "pintarBtnGrabar" not in APPJS
    assert "radar-estado" not in INDEX and "radar-estado" not in APPJS
    assert "mas-opciones" not in INDEX and "Más opciones" not in INDEX
    assert "#e63946" not in CSS


def test_appjs_vuelve_a_btn_iniciar():
    assert '$("btn-iniciar")' in APPJS
    cuerpo = APPJS.split("function tarjetaClipLive")[1].split("async function aprobarClipLive")[0]
    # Aprobar/Rechazar como BOTONES en la tarjeta (como en la v1.3.5),
    # no como enlaces pequeños del rediseño.
    assert "✅ APROBAR</button>" in APPJS and "✖ RECHAZAR" in APPJS
    assert 'class="bib-mini"' not in cuerpo


# ---------------------------------------------------------------------------
# 2. Excepción: duraciones nuevas 60–120 en el desplegable restaurado
# ---------------------------------------------------------------------------
def test_desplegable_duraciones_nuevas_en_vista_restaurada():
    bloque = INDEX.split('id="clip-largo"')[1].split("</select>")[0]
    opciones = re.findall(r'<option value="([^"]*)">([^<]+)</option>', bloque)
    assert opciones == [("", "Automática"), ("60", "60 s"), ("75", "75 s"),
                        ("90", "90 s"), ("120", "120 s")]


# ---------------------------------------------------------------------------
# 3/4. Versión visible 1.3.8
# ---------------------------------------------------------------------------
def test_version_visible_1_3_8():
    assert worker_mod.VERSION == "1.3.9"
    manifiesto = json.loads((ROOT / "worker-windows" / "manifest.json").read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.9"
