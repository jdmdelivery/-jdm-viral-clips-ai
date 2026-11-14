"""Lote CABLEADO (auditoría JDM VIRAL CLIPS AI) — tests ESTÁTICOS, sin red.

Leen los archivos del repo y verifican que el panel (apps/web) esté
cableado contra la API (apps/api/main.py) y contra el contrato del panel:

(a)  Toda función invocada desde atributos onclick=/onchange= de
     index.html Y desde atributos onclick= dentro de los template
     literals de app.js está DEFINIDA en app.js.
(a2) Todo id referenciado en app.js vía $("...") o getElementById("...")
     existe como id="..." en index.html.
(b)  Toda ruta invocada desde app.js (api("METODO", "...") y fetch("..."))
     existe en la tabla de rutas de la API (decoradores @app.<metodo>).
(c)  Checks del contrato del panel: los textos/claves listados están
     presentes en app.js.

Si un test falla nombrando una ruta/función/id, es un botón muerto o un
contrato sin implementar (pendiente del lote API o del lote Panel).
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
APPJS = (WEB / "app.js").read_text(encoding="utf-8")
STYLE = (WEB / "style.css").read_text(encoding="utf-8")
MAINPY = (ROOT / "apps" / "api" / "main.py").read_text(encoding="utf-8")

PALABRAS_CLAVE = {"if", "for", "while", "switch", "catch", "return",
                  "function", "new", "typeof", "else", "do", "in", "of"}

# ---------------------------------------------------------------------------
# Utilidades de extracción
# ---------------------------------------------------------------------------
def _atributos_handler(fuente):
    """Valores de atributos onclick=/onchange= (comillas dobles o simples)."""
    valores = re.findall(r'on(?:click|change)\s*=\s*"([^"]*)"', fuente)
    valores += re.findall(r"on(?:click|change)\s*=\s*'([^']*)'", fuente)
    return valores


def _llamadas(valor):
    """Nombres de función invocados en un valor de atributo.

    Ignora palabras clave (if, for...) y métodos (el nombre va precedido
    de un punto, p. ej. window.open)."""
    llamadas = []
    for m in re.finditer(r'([A-Za-z_$][\w$]*)\s*\(', valor):
        nombre = m.group(1)
        if nombre in PALABRAS_CLAVE:
            continue
        if m.start() > 0 and valor[m.start() - 1] == ".":
            continue
        llamadas.append(nombre)
    return llamadas


def _funciones_definidas(fuente):
    """Funciones definidas en app.js: `function nombre(`, `async function
    nombre(` o `nombre = ...` (asignación, p. ej. const $ = id => ...)."""
    definidas = set(re.findall(r'\bfunction\s+([A-Za-z_$][\w$]*)\s*\(', fuente))
    definidas |= set(re.findall(r'\basync\s+function\s+([A-Za-z_$][\w$]*)\s*\(', fuente))
    definidas |= set(re.findall(
        r'(?m)^\s*(?:const|let|var)?\s*([A-Za-z_$][\w$]*)\s*=', fuente))
    return definidas


def _normalizar_ruta(ruta):
    """Quita query string y convierte parámetros (${...} y {nombre}) en
    un comodín de segmento, para comparar panel vs API."""
    ruta = ruta.split("?", 1)[0].split("#", 1)[0]
    segmentos = []
    for seg in ruta.split("/"):
        seg = re.sub(r'\$\{[^}]*\}', "{}", seg)
        seg = re.sub(r'\{[^}]*\}', "{}", seg)
        segmentos.append(seg)
    return "/".join(segmentos)


def _rutas_api():
    """{(METODO, ruta_normalizada)} desde los decoradores de main.py."""
    rutas = set()
    for metodo, ruta in re.findall(
            r'@app\.(get|post|put|delete|patch)\(\s*"([^"]+)"', MAINPY):
        rutas.add((metodo.upper(), _normalizar_ruta(ruta)))
    return rutas


def _rutas_panel():
    """[(metodo, ruta_normalizada, origen)] invocadas desde app.js.

    api("METODO", "ruta") aporta método + ruta; fetch("ruta") aporta la
    ruta y el método se infiere de las opciones que le siguen (o GET)."""
    rutas = []
    for metodo, doble, simple, backtick in re.findall(
            r'''\bapi\(\s*"(GET|POST|PUT|DELETE|PATCH)"\s*,\s*(?:"([^"]*)"|'([^']*)'|`([^`]*)`)''',
            APPJS):
        ruta = doble or simple or backtick
        rutas.append((metodo, _normalizar_ruta(ruta), f'api("{metodo}", "{ruta}")'))
    for doble, simple, backtick in re.findall(
            r'''\bfetch\(\s*(?:"([^"]*)"|'([^']*)'|`([^`]*)`)''', APPJS):
        ruta = doble or simple or backtick
        # Método en las opciones del fetch que siguen a la llamada.
        idx = APPJS.find(ruta)
        ventana = APPJS[idx:idx + 400] if idx >= 0 else ""
        m = re.search(r'method:\s*"(GET|POST|PUT|DELETE|PATCH)"', ventana)
        metodo = m.group(1) if m else "GET"
        rutas.append((metodo, _normalizar_ruta(ruta), f'fetch("{ruta}") [{metodo}]'))
    return rutas


# ---------------------------------------------------------------------------
# (a) Funciones invocadas en handlers están definidas
# ---------------------------------------------------------------------------
def test_a_funciones_onclick_onchange_de_index_html_definidas():
    definidas = _funciones_definidas(APPJS)
    llamadas = []
    for valor in _atributos_handler(INDEX):
        llamadas.extend(_llamadas(valor))
    faltantes = sorted(set(llamadas) - definidas)
    assert not faltantes, (
        "Funciones invocadas desde onclick=/onchange= de index.html sin "
        f"definir en app.js (botón muerto): {faltantes}")


def test_a_funciones_onclick_en_templates_de_app_js_definidas():
    definidas = _funciones_definidas(APPJS)
    llamadas = []
    for valor in _atributos_handler(APPJS):
        llamadas.extend(_llamadas(valor))
    faltantes = sorted(set(llamadas) - definidas)
    assert not faltantes, (
        "Funciones invocadas desde onclick= dentro de template literals "
        f"de app.js sin definir en app.js (botón muerto): {faltantes}")


# ---------------------------------------------------------------------------
# (a2) Ids referenciados en app.js existen en index.html
# ---------------------------------------------------------------------------
def test_a2_ids_referenciados_en_app_js_existen_en_index_html():
    ids_html = set(re.findall(r'id="([^"]+)"', INDEX))
    referenciados = set(re.findall(r'\$\(\s*"([^"]+)"\s*\)', APPJS))
    referenciados |= set(re.findall(r"\$\(\s*'([^']+)'\s*\)", APPJS))
    referenciados |= set(re.findall(r'getElementById\(\s*"([^"]+)"\s*\)', APPJS))
    referenciados |= set(re.findall(r"getElementById\(\s*'([^']+)'\s*\)", APPJS))
    faltantes = sorted(referenciados - ids_html)
    assert not faltantes, (
        "Ids referenciados en app.js ($(...)/getElementById) que no "
        f"existen como id=\"...\" en index.html: {faltantes}")


# ---------------------------------------------------------------------------
# (b) Rutas del panel existen en la API
# ---------------------------------------------------------------------------
def test_b_rutas_del_panel_existen_en_la_api():
    rutas_api = _rutas_api()
    rutas_api_sin_metodo = {ruta for _, ruta in rutas_api}
    faltantes = []
    for metodo, ruta, origen in _rutas_panel():
        if (metodo, ruta) not in rutas_api and ruta not in rutas_api_sin_metodo:
            faltantes.append(f"{metodo} {ruta} (desde {origen})")
    assert not faltantes, (
        "Rutas invocadas desde el panel sin endpoint en la API "
        "(botón muerto / detector de rutas): " + "; ".join(sorted(set(faltantes))))


def test_b_tabla_de_rutas_de_la_api_no_vacia():
    assert len(_rutas_api()) >= 20, (
        f"Solo se extrajeron {len(_rutas_api())} rutas de main.py; "
        "la extracción por decoradores @app.<metodo> puede estar rota.")


# ---------------------------------------------------------------------------
# (c) Contrato del panel: textos/claves presentes en app.js
# ---------------------------------------------------------------------------
TEXTOS_CONTRATO_PANEL = [
    "GRABANDO EN VIVO",
    "started_at",
    "youtube-nocookie",
    "TRABAJADOR DESCONECTADO",
    "generarClipEvento",
    "descartarEvento",
    "generarTodosClips",
    "cargarMotor",
    "setInterval",
]


@pytest.mark.parametrize("texto", TEXTOS_CONTRATO_PANEL)
def test_c_contrato_panel_texto_presente_en_app_js(texto):
    assert texto in APPJS, (
        f"Contrato del panel: «{texto}» no aparece en apps/web/app.js "
        "(pendiente del lote Panel)")


# ---------------------------------------------------------------------------
# (d) Tarjetas de estadísticas (Inicio) clicables: data-nav -> ir(sección)
# ---------------------------------------------------------------------------
def _secciones_html():
    """Secciones reales del panel: valores data-sec de index.html."""
    return set(re.findall(r'data-sec="([^"]+)"', INDEX))


def _nav_stats():
    """Mapa etiqueta -> sección de NAV_STATS en app.js ({} si no existe)."""
    m = re.search(r'NAV_STATS\s*=\s*\{(.*?)\};', APPJS, re.S)
    if not m:
        return {}
    return dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', m.group(1)))


def _etiquetas_stats():
    """Etiquetas que cargarStats() renderiza como tarjetas: las claves del
    literal `const datos = {...}` (entre comillas o identificadores) más las
    asignaciones posteriores datos["..."] = ..."""
    etiquetas = set()
    m = re.search(r'const datos = \{([^}]*)\}', APPJS)
    assert m, "No se encontró el literal `const datos = {...}` de cargarStats()"
    for doble, ident in re.findall(
            r'(?:"([^"]+)"|([A-Za-zÁÉÍÓÚÑ][\wÁÉÍÓÚÑ]*))\s*:', m.group(1)):
        etiquetas.add(doble or ident)
    etiquetas |= set(re.findall(r'datos\["([^"]+)"\]\s*=', APPJS))
    return etiquetas


def test_d_nav_stats_existe_y_destinos_son_secciones_reales():
    nav = _nav_stats()
    assert nav, "NAV_STATS no existe o quedó vacío en app.js: sin mapa no hay tarjetas clicables."
    secciones = _secciones_html()
    malos = {k: v for k, v in nav.items() if v not in secciones}
    assert not malos, (
        "Tarjetas con data-nav hacia una sección que NO existe en "
        f"index.html (clic muerto): {malos}. Secciones reales: {sorted(secciones)}")


def test_d_toda_tarjeta_de_inicio_tiene_destino_real():
    nav = _nav_stats()
    sin_destino = sorted(_etiquetas_stats() - set(nav))
    assert not sin_destino, (
        "Tarjetas de Inicio que se renderizan sin data-nav (quedan sin clic): "
        f"{sin_destino}. Si una no debe ser clicable, hay que documentarlo; "
        "hoy todas tienen sección destino real.")


def test_d_render_usa_data_nav_y_no_onclick():
    assert 'data-nav="${NAV_STATS[k]}"' in APPJS, (
        "cargarStats() no renderiza las tarjetas con data-nav desde NAV_STATS.")
    assert 'class="card nav"' in APPJS and 'role="button"' in APPJS \
        and 'tabindex="0"' in APPJS, (
            "Las tarjetas clicables deben llevar clase «nav», role=\"button\" "
            "y tabindex=\"0\" (accesibles por teclado).")


def test_d_handler_delegado_unico_click_y_teclado():
    assert APPJS.count('closest("[data-nav]")') == 1, (
        "Debe haber UN solo handler delegado que resuelva [data-nav] "
        "(sin handlers duplicados por tarjeta).")
    assert 'addEventListener("click"' in APPJS, "Falta el listener delegado de clic para data-nav."
    assert 'addEventListener("keydown"' in APPJS, (
        "Falta el listener delegado de teclado (Enter/Espacio) para data-nav.")
    assert "ir(t.dataset.nav)" in APPJS, (
        "El handler delegado debe navegar con la función existente ir().")


def test_d_ningun_onclick_vacio_en_panel():
    patron = re.compile(r"""on(?:click|change)\s*=\s*(""|'')""")
    for nombre, fuente in (("index.html", INDEX), ("app.js", APPJS)):
        encontrados = patron.findall(fuente)
        assert not encontrados, (
            f"{nombre} contiene handlers onclick/onchange vacíos: {encontrados}")


def test_d_css_tarjeta_navegable_con_hover_y_foco():
    assert ".card.nav" in STYLE and "cursor:pointer" in STYLE, (
        "Falta el estilo .card.nav con cursor:pointer en style.css.")
    assert ".card.nav:hover" in STYLE, "Falta el realce al hover de .card.nav."
    assert ".card.nav:focus-visible" in STYLE, (
        "Falta el realce de foco visible (:focus-visible) de .card.nav.")
