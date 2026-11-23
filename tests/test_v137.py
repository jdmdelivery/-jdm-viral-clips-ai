"""Tests de la v1.3.7 — el rediseño del Centro LIVE que pidió Jose
(2026-11-23, con captura del panel lleno de botones rojos):

«quiero que deje eso más moderno con menos botones, solo quiero
clipear, guardar y ver».

Contrato fijado aquí (estático, sobre apps/web):
1. UNA sola acción principal: el botón enorme #btn-grabar. Sin grabar
   dice «⏺ GRABAR»; grabando, el MISMO botón pasa a «⏹ DETENER»
   (pintarBtnGrabar lo repinta con el estado fresco desde
   pintarEstadoReal, que reciben todos los caminos de seguimiento).
   Fuera los ids viejos btn-iniciar / btn-detener.
2. Fuera de la vista principal: ABRIR LIVE, el toggle del radar, el
   desplegable «Duración del clip», VER MOMENTOS DETECTADOS, GENERAR
   TODOS LOS CLIPS, GENERAR CLIPS y PUBLICAR CLIP viven dentro del
   desplegable discreto «Más opciones» (<details>) — las funciones
   siguen existiendo y funcionando, solo dejan de apilarse.
3. El radar es automático: en la vista principal es una línea de
   estado pequeña NO clicable (#radar-estado), repintada por
   pintarBtnRadar con la cuenta de momentos.
4. Los clips, protagonistas: cada tarjeta del Centro LIVE lleva solo
   dos acciones grandes, ▶ VER y ⬇ GUARDAR; APROBAR/RECHAZAR quedan
   como enlaces pequeños secundarios (.bib-mini), sin borrar la
   función que Jose pidió por la mañana.
5. Look sobrio: un solo acento (#e63946), sin el rojo neón #ff0033 ni
   botones con degradado en el CSS.
6. Versión visible 1.3.7: VERSION del trabajador y manifest.json.
"""
import json
from pathlib import Path

import worker as worker_mod

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
APPJS = (ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "apps" / "web" / "style.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Un solo botón principal: GRABAR que pasa a DETENER
# ---------------------------------------------------------------------------
def test_un_solo_boton_principal_grabar_detener():
    assert INDEX.count('id="btn-grabar"') == 1
    assert 'id="btn-grabar" class="btn-grabar" onclick="botonGrabar()"' in INDEX
    assert "⏺ GRABAR" in INDEX
    # Los dos botones viejos (iniciar/detener separados) ya no existen.
    assert 'id="btn-iniciar"' not in INDEX and 'id="btn-detener"' not in INDEX
    assert "btn-iniciar" not in APPJS and "btn-detener" not in APPJS
    # El toggle decide con el estado fresco y se repinta desde
    # pintarEstadoReal (lo llaman el seguimiento y la restauración).
    assert "function botonGrabar(" in APPJS
    assert "function pintarBtnGrabar(" in APPJS
    assert "⏹ DETENER" in APPJS and "grabandoAhora" in APPJS
    cuerpo = APPJS.split("function pintarEstadoReal")[1].split("function mostrarGrabando")[0]
    assert "pintarBtnGrabar(ses)" in cuerpo
    # Sin trabajador conectado, el botón único queda deshabilitado.
    cuerpo_motor = APPJS.split("async function cargarMotor")[1].split("async function contarMomentos")[0]
    assert '$("btn-grabar")' in cuerpo_motor


# ---------------------------------------------------------------------------
# 2. Lo secundario vive en «Más opciones», no apilado en la vista
# ---------------------------------------------------------------------------
def test_secundarios_dentro_de_mas_opciones():
    i_det = INDEX.index("<details")
    assert 'class="mas-opciones"' in INDEX and "Más opciones" in INDEX
    for marca in ('onclick="abrirLive()"', 'id="btn-radar"', 'id="clip-largo"',
                  "onclick=\"ir('radar')\"", 'onclick="generarTodosClips()"',
                  "onclick=\"ir('analizar')\"", "onclick=\"ir('publicar')\""):
        assert INDEX.index(marca) > i_det, f"{marca} debe vivir dentro de «Más opciones»"
    # Las funciones siguen existiendo (nada de botones falsos ni rotos).
    for fn in ("function abrirLive(", "function activarRadar(",
               "function generarTodosClips(", "function largoClip("):
        assert fn in APPJS
    # La cabecera de la rejilla ya no trae el botón GENERAR TODOS.
    cuerpo = APPJS.split("async function cargarClipsSesion")[1].split("// Tarjeta visual")[0]
    assert "generarTodosClips" not in cuerpo


# ---------------------------------------------------------------------------
# 3. Radar: línea de estado pequeña, no clicable
# ---------------------------------------------------------------------------
def test_radar_es_linea_de_estado_no_clicable():
    assert '<p id="radar-estado" class="radar-linea">' in INDEX
    cuerpo = APPJS.split("function pintarBtnRadar")[1].split("async function refrescarRadarUI")[0]
    assert '$("radar-estado")' in cuerpo and "Radar analizando" in cuerpo
    assert ".radar-linea" in CSS


# ---------------------------------------------------------------------------
# 4. Tarjetas: VER / GUARDAR grandes; aprobar/rechazar, enlaces pequeños
# ---------------------------------------------------------------------------
def test_tarjeta_solo_ver_y_guardar_grandes():
    assert "▶ VER" in APPJS and "⬇ GUARDAR" in APPJS
    cuerpo = APPJS.split("function tarjetaClipLive")[1].split("async function aprobarClipLive")[0]
    assert 'class="bib-acciones clip-main"' in cuerpo
    assert 'class="bib-mini"' in cuerpo
    # Aprobar/Rechazar como enlaces pequeños que llaman a la misma función.
    assert "aprobarClipLive(${c.id});return false" in cuerpo
    assert "rechazarClipLive(${c.id});return false" in cuerpo
    assert "✅ APROBAR</button>" not in APPJS and "✖ RECHAZAR" not in APPJS
    assert ".clip-main" in CSS and ".bib-mini" in CSS


# ---------------------------------------------------------------------------
# 5/6. Look sobrio y versión 1.3.7
# ---------------------------------------------------------------------------
def test_look_sobrio_un_solo_acento():
    assert "#ff0033" not in CSS and "255,0,51" not in CSS
    assert "--red:#e63946" in CSS
    assert ".btn-grabar" in CSS and ".grabando-on" in CSS and ".mas-opciones" in CSS


def test_version_visible_1_3_7():
    assert worker_mod.VERSION == "1.3.7"
    manifiesto = json.loads((ROOT / "worker-windows" / "manifest.json").read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.7"
