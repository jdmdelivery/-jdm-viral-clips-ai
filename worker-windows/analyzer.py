"""Pipeline de análisis: segmenta -> mide señales reales por segmento -> puntúa ->
protege segmentos del evento en el buffer -> genera clip con contexto SIN detener nada más."""
import subprocess, time
from pathlib import Path
import viral, vision, dedup
from buffer import BufferCircular
from recorder import segmentar_archivo
from clipper import exportar_clip, miniatura, contexto
from titles import generar as generar_titulos
from transcribe import transcribir

def duracion(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
    return float(r.stdout.strip())

def analizar_video(fuente, outdir, modo="general", pre_s=20, post_s=15,
                   umbral_evento=45, max_clips=1, con_transcripcion=False,
                   con_rostros=False):
    t0 = time.time()
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    total = duracion(fuente)
    # Carpeta nueva: una análisis no debe leer ni mezclar segmentos de una
    # captura anterior que hayan quedado en el mismo proyecto.
    segdir = outdir / f"segmentos-{time.strftime('%Y%m%d-%H%M%S')}"
    segs = segmentar_archivo(fuente, segdir)
    # Línea de tiempo REAL: el muxer corta en keyframes, así que cada segmento se mide
    # y los inicios se acumulan. Nunca se asume que el segmento i empieza en i*10.
    from recorder import duracion_real
    inicios, cursor = [], 0.0
    for p in segs:
        inicios.append(cursor); cursor += duracion_real(p)
    buf = BufferCircular(segdir, minutos=10)
    for i, p in enumerate(segs):
        buf.registrar(str(p), i, round(inicios[i], 3), round(inicios[min(i + 1, len(segs) - 1)] - inicios[i] if i + 1 < len(segs) else total - inicios[i], 3))
    eventos, mediciones = [], []
    for i, p in enumerate(segs):
        medio, pico = viral.medir_audio(p)
        escenas = vision.cambios_de_escena(p)
        fin_i = inicios[i + 1] if i + 1 < len(segs) else total
        senales = {"rms_medio_db": medio, "rms_pico_db": pico,
                   "cambios_escena": escenas, "duracion_s": round(fin_i - inicios[i], 2)}
        score, desglose = viral.puntuar(senales, modo)
        med = {"segmento": i, "inicio_s": round(inicios[i], 2), **senales, "score": score}
        if con_rostros:
            # Señal informativa opcional (desactivada por defecto): un solo
            # frame por segmento, el central, para no encarecer el pipeline.
            med["rostros"] = vision.detectar_rostros(p)
        mediciones.append(med)
        if score >= umbral_evento:
            eventos.append({"type": viral.clasificar(senales, modo), "mode": modo,
                            "start_s": round(inicios[i], 2), "end_s": round(fin_i, 2),
                            "score": score, "breakdown": desglose, "_segmento": i})
    # Etapa 2: deduplicación por solape (>70% del intervalo menor) antes de
    # elegir clips; los duplicados quedan anotados en el breakdown del que
    # se conserva (fusionado_con) y aquí solo se informa cuántos hubo.
    eventos_fusionados = dedup.fusionar_eventos(eventos)
    duplicados_n = dedup.contar_duplicados(eventos_fusionados)
    eventos = [e for e in eventos_fusionados if e.get("status") != "duplicado"]
    eventos.sort(key=lambda e: -e["score"])
    clips = []
    for ev in eventos[:max_clips]:
        buf.proteger({ev["_segmento"]})
        t_ev = time.time()
        ini, fin = contexto(ev["start_s"], ev["end_s"], total, pre_s, post_s)
        salida = outdir / "clips" / f"clip-evento-{ev['_segmento']}.mp4"
        salida.parent.mkdir(exist_ok=True)
        if salida.exists():
            salida = salida.with_name(f"clip-evento-{ev['_segmento']}-{int(time.time())}.mp4")
        exportar_clip(fuente, ini, fin, salida, "9:16")
        thumb = miniatura(salida, 1, salida.with_suffix(".jpg"))
        texto_tr = None
        if con_transcripcion:
            tr = transcribir(salida)
            texto_tr = tr["texto"] if tr["disponible"] else None
            ev["transcripcion_nota"] = tr["nota"]
        tit = generar_titulos(ev["type"], modo, ev["score"], texto_tr)
        clips.append({"file_path": str(salida), "thumb_path": thumb, "format": "9:16",
                      "duration_s": round(fin - ini, 2), "score": ev["score"],
                      "title": tit["titulos"][0], "titles": tit, "subtitles": False,
                      "_desde_evento_s": ev["start_s"], "_generado_en_s": round(time.time() - t_ev, 2),
                      "clip_inicio_s": ini, "clip_fin_s": fin})
    for ev in eventos: ev.pop("_segmento", None)
    return {"duracion_total_s": total, "segmentos": len(segs), "mediciones": mediciones,
            "eventos": eventos, "duplicados_fusionados": duplicados_n, "clips": clips,
            "tiempo_total_pipeline_s": round(time.time() - t0, 2),
            "opencv_disponible": vision.opencv_disponible()}
