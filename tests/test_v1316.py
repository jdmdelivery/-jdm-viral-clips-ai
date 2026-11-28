"""v1.3.16 — Modo Épicos: solo discusión, baile y pelea.

Pedido de Jose (2026-11-27): que el programa detecte SOLO los momentos
épicos — discusión, baile, pelea — los que se van virales.

Piezas:
1. vision.movimiento_detalle: serie de movimiento por segmento (media,
   pico y fracción de pares activos) con la decodificación gris de
   FFmpeg que ya existía para conteo_movimiento — sin dependencias.
2. viral.clasificar: «pelea» (pico de movimiento violento + audio muy
   alto) y «baile» (movimiento sostenido + audio medio alto) SOLO si la
   señal de movimiento llegó medida; sin ella, todo como antes.
3. viral.puntuar: componente «movimiento» y modo «epicos».
4. Radar en modo «epicos»: solo las ventanas discusión/pelea/baile
   generan clip; cualquier otro tipo se trata como segmento flojo.
5. Panel: el modo se elige en el selector de modo de la sesión.
"""
import json
import subprocess
from pathlib import Path

import viral
import vision
import worker as worker_mod
from radar import RadarSesion

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. Medición de movimiento con videos sintéticos reales
# ---------------------------------------------------------------------------

def _video(tmp_path, nombre, fuente_lavfi):
    p = tmp_path / nombre
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", fuente_lavfi,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)],
                   check=True, capture_output=True, timeout=120)
    return p


def test_movimiento_detalle_estatico_casi_cero(tmp_path):
    p = _video(tmp_path, "estatico.mp4", "color=black:size=320x240:rate=10:duration=3")
    mov = vision.movimiento_detalle(p)
    assert mov["disponible"] is True
    assert mov["mov_medio"] < 1.0
    assert mov["fraccion_activa"] == 0.0


def test_movimiento_detalle_en_movimiento_dispara(tmp_path):
    # testsrc2 medido en esta VM: media ~8.4, pico ~9.3, fracción 0.6.
    # Los márgenes dejan aire entre versiones de FFmpeg sin perder el
    # contraste con el video estático (todo ~0).
    p = _video(tmp_path, "movimiento.mp4", "testsrc2=size=320x240:rate=10:duration=3")
    mov = vision.movimiento_detalle(p)
    assert mov["disponible"] is True
    assert mov["fraccion_activa"] > 0.4
    assert mov["mov_pico"] > 5


# ---------------------------------------------------------------------------
# 2. Clasificación pelea / baile (y que sin señal nada cambia)
# ---------------------------------------------------------------------------

PELEA = {"rms_medio_db": -18.0, "rms_pico_db": -5.0, "cambios_escena": 1,
         "duracion_s": 10, "mov_medio": 16.0, "mov_pico": 55.0,
         "mov_fraccion_activa": 0.45}
BAILE = {"rms_medio_db": -22.0, "rms_pico_db": -8.0, "cambios_escena": 1,
         "duracion_s": 10, "mov_medio": 13.0, "mov_pico": 26.0,
         "mov_fraccion_activa": 0.8}


def test_clasificar_pelea_y_baile():
    assert viral.clasificar(dict(PELEA)) == "pelea"
    assert viral.clasificar(dict(BAILE)) == "baile"


def test_pelea_exige_audio_muy_alto():
    # El mismo movimiento violento SIN gritos no es pelea: la puerta del
    # audio evita marcar como pelea cualquier escena agitada en silencio.
    senales = dict(PELEA, rms_medio_db=-25.0, rms_pico_db=-20.0)
    assert viral.clasificar(senales) == "general"


def test_baile_exige_movimiento_sostenido():
    # Movimiento fuerte pero breve (una reacción) no es baile.
    senales = dict(BAILE, mov_fraccion_activa=0.3)
    assert viral.clasificar(senales) != "baile"


def test_sin_senal_de_movimiento_todo_como_antes():
    sin_mov = {k: v for k, v in PELEA.items() if not k.startswith("mov_")}
    sin_mov.update({"rms_medio_db": -25.0, "rms_pico_db": -12.0})
    # Dinámica 13 dB: ni discusión (>16) ni gritos (pico > -9) -> general.
    assert viral.clasificar(sin_mov) == "general"
    discusion = {"rms_medio_db": -35.0, "rms_pico_db": -10.0,
                 "cambios_escena": 0, "duracion_s": 10}
    assert viral.clasificar(discusion) == "discusion"


# ---------------------------------------------------------------------------
# 3. Puntuación del modo épicos
# ---------------------------------------------------------------------------

def test_puntuar_epicos_premia_el_movimiento():
    score_epicos, desglose = viral.puntuar(dict(BAILE), "epicos")
    score_general, _ = viral.puntuar(dict(BAILE), "general")
    assert desglose["movimiento"] > 20
    assert score_epicos > score_general
    assert "epicos" in desglose["modo"]
    assert desglose["senales_medidas"]["mov_fraccion_activa"] == 0.8


def test_puntuar_sin_movimiento_no_inventa_componente():
    senales = {"rms_medio_db": -22.0, "rms_pico_db": -8.0,
               "cambios_escena": 1, "duracion_s": 10}
    _, desglose = viral.puntuar(senales, "epicos")
    assert "movimiento" not in desglose
    assert "mov_fraccion_activa" not in desglose["senales_medidas"]


# ---------------------------------------------------------------------------
# 4. El radar en modo épicos filtra por tipo
# ---------------------------------------------------------------------------

class _BufferFalso:
    def __init__(self):
        self.entradas = []

    def segmentos(self):
        return list(self.entradas)

    def marcar_en_uso(self, indices):
        pass

    def liberar_en_uso(self, indices):
        pass

    def proteger(self, indices):
        pass


class _RecorderFalso:
    def __init__(self):
        self.buffer = _BufferFalso()
        self.inicio = None
        self._t_inicio = None


class _ApiFalsa:
    def __init__(self):
        self.reportes = []

    def reportar_en_vivo(self, session_id, eventos, clips):
        self.reportes.append((eventos, clips))
        return {"ok": True, "clip_ids": [1000 + len(self.reportes)] * len(clips)}


# Guión de segmentos: (score, tipo). Un «gritos» y una «sorpresa» por
# encima del umbral NO deben generar clip en modo épicos.
GUION = {0: (90, "gritos"), 1: (90, "discusion"), 2: (95, "pelea"),
         3: (90, "sorpresa"), 4: (88, "baile"), 5: (10, "general")}


def _radar_con_guion(modo):
    rec = _RecorderFalso()
    api = _ApiFalsa()
    radar = RadarSesion(rec, 7, api, Path("clips"), modo=modo, umbral=40)
    radar._puntuar_segmento = lambda e: (GUION[e["indice"]][0], {}, GUION[e["indice"]][1])
    radar._generar_clip = lambda v, fin: {"file_path": "clip.mp4", "thumb_path": None,
                                          "format": "9:16", "duration_s": 65.0,
                                          "score": v["score"]}
    for i in range(6):
        rec.buffer.entradas.append({"indice": i, "inicio_s": i * 10.0,
                                    "duracion_s": 10.0, "path": f"seg{i}.mp4"})
    radar.pasada(incluir_ultimo=True)
    return api


def test_radar_epicos_solo_discusion_pelea_baile():
    api = _radar_con_guion("epicos")
    tipos = [ev[0]["type"] for ev, _ in api.reportes]
    # Ventana 1: discusión+pelea (gana el tipo del mejor segmento);
    # ventana 2: baile. Los «gritos» y la «sorpresa» no generaron nada.
    assert tipos == ["pelea", "baile"]


def test_radar_general_no_filtra_por_tipo():
    api = _radar_con_guion("general")
    # Sin filtro: todo lo caliente forma UNA sola ventana (10–50).
    assert len(api.reportes) == 1


# ---------------------------------------------------------------------------
# 5. Panel y títulos: el modo y los tipos nuevos existen
# ---------------------------------------------------------------------------

def test_panel_ofrece_modo_epicos_y_titulos_nuevos():
    html = (ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    assert 'value="epicos"' in html
    titulos = (ROOT / "apps" / "api" / "titles.py").read_text(encoding="utf-8")
    assert '"pelea"' in titulos and '"baile"' in titulos


def test_version_1_3_16_y_manifiesto():
    assert worker_mod.VERSION == "1.3.19"
    manifiesto = json.loads((ROOT / "worker-windows" / "manifest.json")
                            .read_text(encoding="utf-8"))
    assert manifiesto["version"] == "1.3.19"
    for nombre in ("viral.py", "vision.py", "radar.py", "analyzer.py"):
        assert nombre in manifiesto["files"]
