"""Tests de la v1.3.5 — el flujo sencillo que pidió Jose (2026-11-23):

«Le doy a grabar y que automáticamente saque los clips y los guarde,
vuelva y saque más clips y los guarde; que se vean en pantalla con su
visualización, bien organizados y bonitos» + la dirección visual tipo
Clipea (tarjetas grandes con miniatura, GRABAR protagonista, aprobar /
rechazar por clip sin publicar nada) + su regla de subtítulos.

1. Radar: con la grabación en curso y SIN ningún clic más, al cerrarse
   una ventana sobre el umbral el clip se genera y se guarda solo
   (archivo + miniatura + título, duración 60–120 s, subtítulos NO), y
   al llegar otro momento sale OTRO clip: no hay que pedirlos.
2. API: POST /clips/{id}/aprobar deja el clip «listo para publicar»
   (status aprobado) SIN crear ninguna publicación; /rechazar lo marca
   «descartado». El listado de la sesión trae título, duración y
   tiene_miniatura para pintar las tarjetas.
3. Panel (contrato estático): la rejilla del Centro LIVE usa tarjetas
   visuales con miniatura e insignia de duración, se refresca sola
   durante la grabación (seguirEstadoGrabacion -> cargarClipsSesion,
   con firma anti-repintado), el botón protagonista es GRABAR, los
   pasos están explicados en pantalla y los subtítulos quedan
   APAGADOS por defecto (subsActivos ya no enciende solo porque
   exista la copia subtitulada).
"""
import inspect, json, re, subprocess, time, uuid
from pathlib import Path

from fastapi.testclient import TestClient

import main as apimain
from clipper import exportar_clip
from radar import RadarSesion

client = TestClient(apimain.app)
PW = "clave-segura-123"
ENC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
ROOT = Path(__file__).resolve().parents[1]


def _usuario():
    email = f"v135-{uuid.uuid4().hex[:10]}@test.do"
    client.post("/auth/register", json={"email": email, "password": PW})
    r = client.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _worker(h):
    r = client.post("/workers/register", json={"name": "PC v135", "info": {}}, headers=h)
    assert r.status_code == 200, r.text
    return {"X-Worker-Token": r.json()["worker_token"]}


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)


def _ffprobe_duracion(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    return float(json.loads(r.stdout)["format"]["duration"])


# ---------------------------------------------------------------------------
# 1. Radar: clips automáticos durante la grabación, sin un segundo clic
# ---------------------------------------------------------------------------
def _seg_tranquilo(path):
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
          "color=c=blue:size=640x360:duration=10", "-f", "lavfi", "-i",
          "sine=frequency=440:duration=10", "-af", "volume=0.02",
          "-shortest", *ENC, str(path)])


def _seg_fuerte(path, tmp):
    partes = []
    for i, c in enumerate(("red", "green")):
        p = tmp / f"parte-{path.stem}-{i}.mp4"
        _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
              f"color=c={c}:size=640x360:duration=5", "-an", *ENC, str(p)])
        partes.append(p)
    lista = tmp / f"lista-{path.stem}.txt"
    lista.write_text("".join(f"file '{p.name}'\n" for p in partes))
    v = tmp / f"v-{path.stem}.mp4"
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
          "-i", str(lista), "-c", "copy", str(v)])
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(v), "-f", "lavfi", "-i",
          r"aevalsrc=0.85*sin(2*PI*440*t)*lt(mod(t\,2)\,1):s=44100:d=10",
          "-map", "0:v", "-map", "1:a", "-shortest", *ENC, str(path)])


class _BufferFalso:
    def __init__(self): self.entradas = []
    def segmentos(self): return list(self.entradas)
    def proteger(self, indices): pass


class _RecorderFalso:
    """Sustituto del Recorder: el buffer crece como si siguiera grabando."""
    def __init__(self):
        self.buffer = _BufferFalso()
        self.inicio = time.time() - 10000


class _ApiFalsa:
    def __init__(self):
        self.reportes = []    # (eventos, clips) por cada reporte en vivo
        self.subidas = []     # (clip_id, ruta) de la copia automática
        self.miniaturas = []  # (clip_id, ruta) de la miniatura automática
        self._n = 0
    def reportar_en_vivo(self, session_id, events, clips):
        self.reportes.append((events, clips))
        ids = []
        for _ in clips:
            self._n += 1; ids.append(1000 + self._n)
        return {"ok": True, "clip_ids": ids}
    def subir_clip(self, clip_id, ruta):
        self.subidas.append((clip_id, ruta)); return {"ok": True}
    def subir_miniatura(self, clip_id, ruta):
        self.miniaturas.append((clip_id, ruta)); return {"ok": True}


def test_radar_saca_clips_solo_y_sigue_sacando(tmp_path):
    # 120 s de «grabación»: momento fuerte de 2 segmentos (40–60) y otro
    # de 1 segmento (80–90). Se usa el pre/post DE FÁBRICA del radar
    # (30/25): con la regla de Jose (60–120 s desde la v1.3.6) los clips
    # típicos caen en 65–70 s.
    segs = tmp_path / "segs"; segs.mkdir()
    nombres = ["q0", "q1", "q2", "q3", "f4", "f5",
               "q6", "q7", "f8", "q9", "q10", "q11"]
    for i, n in enumerate(nombres):
        p = segs / f"{n}.mp4"
        if n.startswith("f"): _seg_fuerte(p, tmp_path)
        else: _seg_tranquilo(p)
    rec = _RecorderFalso()
    api = _ApiFalsa()
    radar = RadarSesion(rec, 7, api, tmp_path / "clips",
                        umbral=40, intervalo_s=1)

    def meter(i):
        rec.buffer.entradas.append({"indice": i, "inicio_s": i * 10.0,
                                    "duracion_s": 10.0,
                                    "path": str(segs / f"{nombres[i]}.mp4")})

    # La grabación «avanza»: el bucle del radar solo llama a pasada().
    # Nadie pide clips: tienen que salir solos al cerrarse cada ventana.
    meter(0); meter(1); meter(2); meter(3); meter(4); meter(5)
    radar.pasada()                       # puntúa q0–q3 y f4: ventana abierta
    assert api.reportes == []            # aún no se cierra: nada que guardar
    meter(6)
    radar.pasada()                       # f5 alarga la ventana (40–60)
    assert api.reportes == []
    meter(7)
    radar.pasada()                       # q6 flojo la cierra -> clip 1 (70 s)
    assert len(api.reportes) == 1
    meter(8)
    radar.pasada()                       # q7 flojo, sin ventana
    assert len(api.reportes) == 1
    meter(9)
    radar.pasada()                       # f8 abre otra ventana (80–90)
    assert len(api.reportes) == 1
    meter(10); meter(11)
    radar.pasada()                       # q9 flojo la cierra -> clip 2 (65 s)
    assert len(api.reportes) == 2, "el radar no siguió sacando clips solo"

    clips = [c for _, cs in api.reportes for c in cs]
    assert len(clips) == 2
    for clip in clips:
        ruta = Path(clip["file_path"])
        assert ruta.exists(), f"el clip no quedó guardado: {ruta}"
        dur = _ffprobe_duracion(ruta)
        assert 60 <= dur <= 120, f"duración fuera de la regla 60–120: {dur}"
        assert 60 <= clip["duration_s"] <= 120
        assert clip["title"], "el clip automático debe llevar título"
        assert clip["subtitles"] is False, "el clip automático sale SIN subtítulos"
        thumb = Path(clip["thumb_path"])
        assert thumb.exists() and thumb.stat().st_size > 0, "el clip debe llevar miniatura"
    # La copia y la miniatura se suben solas tras reportar (para VER el
    # clip en el panel sin esperar a pedirlas).
    assert len(api.subidas) == 2 and len(api.miniaturas) == 2


def test_subtitulos_apagados_por_defecto_en_el_worker():
    # exportar_clip solo quema letras si se le pasa subtitulos=True Y un
    # .srt: por defecto, apagado. El radar declara subtitles=False.
    assert inspect.signature(exportar_clip).parameters["subtitulos"].default is False
    radar_py = (ROOT / "worker-windows" / "radar.py").read_text(encoding="utf-8")
    assert '"subtitles": False' in radar_py
    worker_py = (ROOT / "worker-windows" / "worker.py").read_text(encoding="utf-8")
    assert '"subtitles": False' in worker_py


# ---------------------------------------------------------------------------
# 2. API: aprobar = listo para publicar (sin publicar); rechazar = descartado
# ---------------------------------------------------------------------------
def _ingestar_clip(h, wh, titulo="Momento del en vivo"):
    ses = client.post("/sessions", json={"title": "Sesión v135"}, headers=h).json()
    r = client.post(f"/workers/sessions/{ses['id']}/live-events", headers=wh, json={
        "events": [{"start_s": 10, "end_s": 20, "type": "general", "mode": "general",
                    "score": 91, "breakdown": {}}],
        "clips": [{"event_index": 0,
                   "file_path": "C:\\Users\\thene\\JDMViralClips\\clips\\sesion-1\\clip-radar-10s.mp4",
                   "thumb_path": "C:\\Users\\thene\\JDMViralClips\\clips\\sesion-1\\clip-radar-10s.jpg",
                   "format": "9:16", "duration_s": 45.0, "score": 91,
                   "title": titulo, "titles": {}, "subtitles": False}]})
    assert r.status_code == 200, r.text
    return ses, r.json()["clip_ids"][0]


def test_aprobar_y_rechazar_clip():
    h = _usuario(); wh = _worker(h)
    ses, cid = _ingestar_clip(h, wh)
    lista = client.get(f"/clips?session_id={ses['id']}", headers=h).json()
    clip = next(c for c in lista if c["id"] == cid)
    # Lo que la tarjeta del panel necesita para verse: título, duración,
    # score y si hay miniatura servible (al inicio no: vive en la PC).
    assert clip["title"] == "Momento del en vivo"
    assert clip["duration_s"] == 45.0 and clip["score"] == 91
    assert clip["tiene_miniatura"] is False and clip["status"] == "listo"
    assert clip["subtitles"] is False

    r = client.post(f"/clips/{cid}/aprobar", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "aprobado", r.text
    # Aprobar NO publica nada: ni una sola publicación de este clip.
    s = apimain.SessionLocal()
    try:
        pubs = s.query(apimain.Publication).filter_by(clip_id=cid).count()
    finally:
        s.close()
    assert pubs == 0, "aprobar un clip no debe crear publicaciones"

    r = client.post(f"/clips/{cid}/rechazar", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "descartado", r.text
    lista = client.get(f"/clips?session_id={ses['id']}", headers=h).json()
    assert next(c for c in lista if c["id"] == cid)["status"] == "descartado"

    assert client.post("/clips/999999/aprobar", headers=h).status_code == 404
    assert client.post("/clips/999999/rechazar", headers=h).status_code == 404


# ---------------------------------------------------------------------------
# 3. Panel: contrato estático de la vista sencilla y moderna
# ---------------------------------------------------------------------------
def test_panel_centro_live_visual_y_auto():
    appjs = (ROOT / "apps" / "web" / "app.js").read_text(encoding="utf-8")
    index = (ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "apps" / "web" / "style.css").read_text(encoding="utf-8")
    mainpy = (ROOT / "apps" / "api" / "main.py").read_text(encoding="utf-8")

    # Tarjetas visuales en el Centro LIVE: miniatura protagonista con la
    # duración encima y marcador con duración cuando falta la miniatura.
    assert "function tarjetaClipLive(" in appjs
    assert "bib-thumb-wrap" in appjs and "bib-dur" in appjs
    assert "Sin miniatura" in appjs
    # Aprobar / rechazar por clip, cableados a la API (que no publica).
    assert "function aprobarClipLive(" in appjs and "function rechazarClipLive(" in appjs
    assert re.search(r'@app\.post\("/clips/\{cid\}/aprobar"\)', mainpy)
    assert re.search(r'@app\.post\("/clips/\{cid\}/rechazar"\)', mainpy)
    # La rejilla se refresca SOLA durante la grabación: el seguimiento de
    # estado llama a cargarClipsSesion y la firma evita repintados.
    cuerpo = appjs.split("function seguirEstadoGrabacion")[1].split("function restaurarEstadoGrabacion")[0]
    assert "cargarClipsSesion()" in cuerpo
    assert "firmaClipsSesion" in appjs
    # Botón protagonista GRABAR y pasos explicados en la propia pantalla
    # (vista v1.3.5 restaurada en la v1.3.8: el botón es #btn-iniciar
    # con clase btn-grabar, junto a su #btn-detener separado).
    assert 'id="btn-iniciar" class="rojo btn-grabar"' in index and "⏺ GRABAR" in index
    assert 'id="btn-detener"' in index
    assert 'class="pasos"' in index and "Los clips aparecen solos" in index
    assert ".btn-grabar" in css and ".bib-dur" in css and ".pasos" in css
    # Subtítulos APAGADOS por defecto en el panel: que exista la copia
    # subtitulada ya no enciende el interruptor solo.
    assert "return !!c.tiene_subtitulado" not in appjs
    cuerpo_subs = appjs.split("function subsActivos")[1].split("}")[0]
    assert "return false" in cuerpo_subs
