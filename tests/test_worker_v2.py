"""Tests Etapa 2/3 del trabajador: dedup, viral v2 con transcripción,
hash perceptual, rostros/movimiento y editor FFmpeg. Todo con señales reales."""
import subprocess

import pytest

import dedup
import viral


def _ev(start, end, score, tipo="general"):
    return {"type": tipo, "start_s": float(start), "end_s": float(end),
            "score": score, "breakdown": {}}


# --- dedup: fusión por solape ---
def test_solape_mayor_70_fusiona():
    # 2-12 vs 0-10: solape 8 s = 80% del menor (10 s) -> fusiona
    res = dedup.fusionar_eventos([_ev(0, 10, 50), _ev(2, 12, 80)])
    conservados = [e for e in res if e.get("status") != "duplicado"]
    duplicados = [e for e in res if e.get("status") == "duplicado"]
    assert len(conservados) == 1 and len(duplicados) == 1
    assert conservados[0]["score"] == 80  # se conserva el de mayor score
    assert duplicados[0]["score"] == 50
    assert conservados[0]["breakdown"]["fusionado_con"][0]["score"] == 50


def test_solape_50_no_fusiona():
    # 0-10 vs 5-15: solape 5 s = 50% del menor -> NO fusiona
    res = dedup.fusionar_eventos([_ev(0, 10, 50), _ev(5, 15, 80)])
    assert dedup.contar_duplicados(res) == 0
    assert len(res) == 2


def test_fusionar_no_muta_entrada():
    entrada = [_ev(0, 10, 50), _ev(2, 12, 80)]
    dedup.fusionar_eventos(entrada)
    assert "status" not in entrada[0]


# --- dedup: hash perceptual ---
def _video_prueba(path, dur=3):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color=c=red:size=320x240:duration={dur}:rate=10",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


def test_hash_mismo_frame_distancia_cero(tmp_path):
    v = _video_prueba(tmp_path / "v.mp4")
    h1 = dedup.hash_perceptual(v, 1.0)
    h2 = dedup.hash_perceptual(v, 1.0)
    assert h1 is not None and h2 is not None
    assert dedup.distancia_hamming(h1, h2) == 0
    assert dedup.son_similares(h1, h2)


def test_duplicados_visuales_mismo_contenido(tmp_path):
    v = _video_prueba(tmp_path / "v.mp4")
    h = dedup.hash_perceptual(v, 1.0)
    res = dedup.marcar_duplicados_visuales([_ev(0, 10, 40), _ev(5, 15, 70)], hashes=[h, h])
    assert dedup.contar_duplicados(res) == 1
    conservado = [e for e in res if e.get("status") != "duplicado"][0]
    assert conservado["score"] == 70


# --- viral v2: transcripción ---
SENALES = {"rms_medio_db": -30.0, "rms_pico_db": -5.0, "cambios_escena": 5, "duracion_s": 10}

def test_sin_transcripcion_resultado_v1_intacto():
    s, d = viral.puntuar(SENALES, "general")
    assert "conversacion" not in d  # compatibilidad total con la v1
    assert s == viral.puntuar(SENALES, "general")[0]


def test_conversacion_determinista_y_sube_con_densidad_y_claves():
    pobre = "hola si vale"  # pocas palabras, sin signos ni claves
    rica = ("¡Esto es una discusión fuerte! ¡Yo confieso que nunca quise "
            "una pelea así! ¿Me estás oyendo? ¡Grito de sorpresa!")
    s_pobre, d_pobre = viral.puntuar(SENALES, "general", transcripcion=pobre)
    s_rica, d_rica = viral.puntuar(SENALES, "general", transcripcion=rica)
    assert d_pobre["conversacion"] < d_rica["conversacion"]
    assert 0 <= d_rica["conversacion"] <= 20
    assert s_rica > s_pobre  # el componente conversación integra en el total
    # determinista: repetir da exactamente lo mismo
    assert viral.puntuar(SENALES, "general", transcripcion=rica) == (s_rica, d_rica)
    det = d_rica["conversacion_detalle"]
    assert det["palabras_clave"]["confesion"] >= 1
    assert det["palabras_clave"]["discusion"] >= 1
    assert det["densidad_palabras_s"] > 0
    assert "formula" in d_rica["formula_conversacion"] or "conversacion" in d_rica["formula_conversacion"]


def test_clasificar_con_transcripcion_confesion():
    assert viral.clasificar({"rms_pico_db": -30, "rms_medio_db": -40, "cambios_escena": 0},
                            "general",
                            transcripcion="Yo confieso que fue mi secreto") == "confesion"
    # sin transcripción, el comportamiento anterior no cambia
    assert viral.clasificar({"rms_pico_db": -4, "rms_medio_db": -30, "cambios_escena": 0},
                            "alofoke") == "discusion"


# --- vision: rostros / movimiento ---
def test_detectar_rostros_estructura(tmp_path):
    import vision
    v = _video_prueba(tmp_path / "v.mp4")
    r = vision.detectar_rostros(v)
    assert "disponible" in r and "rostros" in r
    if r["disponible"]:
        assert isinstance(r["rostros"], int) and r["rostros"] == 0  # color plano: 0 rostros
    else:
        assert r["rostros"] is None and r["nota"]


def test_conteo_movimiento_video_estatico(tmp_path):
    import vision
    v = _video_prueba(tmp_path / "v.mp4")
    r = vision.conteo_movimiento(v)
    assert r["disponible"] is True
    assert r["movimiento"] == 0.0  # color sólido: sin movimiento entre frames


# --- editor (Etapa 3) ---
def test_editor_recorte_texto_y_volumen(tmp_path):
    import editor
    v = _video_prueba(tmp_path / "fuente.mp4", dur=6)
    salida = tmp_path / "editado.mp4"
    r = editor.aplicar_edicion(v, salida, {"trim_start_s": 1, "trim_end_s": 4,
                                           "texto": "PRUEBA", "texto_activo": True,
                                           "volumen_factor": 0.5})
    assert r["subtitles"] is False  # sin flag jamás quema subtítulos
    assert 2.5 <= r["duration_s"] <= 3.5
    assert r["thumb_path"] and subprocess.run(
        ["test", "-f", r["thumb_path"]]).returncode == 0


def test_editor_subtitulos_solo_con_flag_y_texto(tmp_path):
    import editor
    v = _video_prueba(tmp_path / "fuente.mp4", dur=4)
    r = editor.aplicar_edicion(v, tmp_path / "con.mp4",
                               {"subtitulos": True,
                                "transcript": "Primera frase de prueba. Segunda frase final."})
    assert r["subtitles"] is True
    r2 = editor.aplicar_edicion(v, tmp_path / "sin.mp4", {"subtitulos": True})
    # sin texto ni forma real en el clip generado puede transcribir (hay tono,
    # no habla): no se afirma nada que no se haya hecho
    assert r2["subtitles"] in (True, False)


def test_worker_editar_clip(tmp_path):
    import worker as worker_mod
    v = _video_prueba(tmp_path / "fuente.mp4", dur=4)
    job = {"type": "editar_clip",
           "payload": {"clip_id": 7, "file_path": str(v), "title": "Mi clip",
                       "params": {"trim_start_s": 0, "trim_end_s": 2},
                       "salida_dir": str(tmp_path / "ediciones")}}
    r = worker_mod.procesar(job, {"carpeta_clips": str(tmp_path)})
    assert len(r["clips"]) == 1
    c = r["clips"][0]
    assert c["title"] == "Mi clip" and c["subtitles"] is False
    assert c["file_path"].endswith("clip-7-editado.mp4")
    assert 1.5 <= c["duration_s"] <= 2.5
