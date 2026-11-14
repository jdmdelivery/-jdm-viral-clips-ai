"""Pipeline REAL de prueba: genera video -> graba con Recorder -> analiza -> clip vertical.
Guarda resultados en tests/output/resultado-pipeline.json"""
import json, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker-windows"))
OUT = ROOT / "tests" / "output"; OUT.mkdir(parents=True, exist_ok=True)

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError(r.stderr[-800:])
    return r

def generar_video(dest):
    """90 s: 0-30 tranquilo, 30-60 FUERTE (audio alto + cambio de color cada 5 s), 60-90 tranquilo."""
    tmp = OUT / "partes"; tmp.mkdir(exist_ok=True)
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-af", "volume=0.02",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(tmp / "a.mp4")])
    colores = ["red", "yellow", "white", "magenta", "cyan", "orange"]
    for i, c in enumerate(colores):
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={c}:s=640x360:r=30:d=5",
             "-f", "lavfi", "-i", f"sine=frequency={300 + i * 200}:duration=5", "-af", "volume=0.85",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(tmp / f"b{i}.mp4")])
    (tmp / "lista.txt").write_text("".join(f"file '{n}'\n" for n in ["a.mp4"] + [f"b{i}.mp4" for i in range(6)] + ["c.mp4"]))
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=640x360:r=30:d=30",
         "-f", "lavfi", "-i", "sine=frequency=330:duration=30", "-af", "volume=0.02",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(tmp / "c.mp4")])
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "lista.txt"),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(dest)])

def main():
    video = OUT / "video-prueba-90s.mp4"
    generar_video(video)
    from recorder import Recorder
    from analyzer import analizar_video
    # 1) GRABACIÓN con Recorder (proceso separado, segmentos de 10 s, unión final)
    t0 = time.time()
    rec = Recorder(str(video), OUT / "grabacion", buffer_min=5, en_vivo=False)
    rec.start()
    while rec.hilo.is_alive(): time.sleep(0.5)
    completa = rec.stop()
    segs = sorted((OUT / "grabacion").glob("seg_*.mp4"))
    grab = {"segmentos": len(segs), "grabacion_completa": completa, "segundos": round(time.time() - t0, 2)}
    assert len(segs) >= 8, f"segmentos insuficientes: {len(segs)}"
    assert completa and Path(completa).exists(), "no se unió la grabación"
    # 2) ANÁLISIS + CLIP durante el proceso (sobre el video importado)
    res = analizar_video(str(video), OUT / "analisis", modo="alofoke", umbral_evento=45, max_clips=1)
    assert res["clips"], f"no se generó clip: eventos={res['eventos']}"
    clip = res["clips"][0]
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,codec_name", "-of", "json", clip["file_path"]],
                           capture_output=True, text=True)
    aprobe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                             "stream=codec_name", "-of", "json", clip["file_path"]],
                            capture_output=True, text=True)
    info = {"grabacion": grab, "duracion_total_s": res["duracion_total_s"],
            "segmentos_analizados": res["segmentos"], "mediciones": res["mediciones"],
            "eventos_detectados": [{k: e[k] for k in ("type", "mode", "start_s", "end_s", "score")} for e in res["eventos"]],
            "clip": {k: clip.get(k) for k in ("file_path", "format", "duration_s", "score", "title", "clip_inicio_s", "clip_fin_s")},
            "ffprobe_video": json.loads(probe.stdout)["streams"], "ffprobe_audio": json.loads(aprobe.stdout)["streams"],
            "tiempo_evento_a_clip_s": clip["_generado_en_s"], "tiempo_total_pipeline_s": res["tiempo_total_pipeline_s"],
            "opencv_disponible": res["opencv_disponible"]}
    v = info["ffprobe_video"][0]
    assert (v["width"], v["height"], v["codec_name"]) == (1080, 1920, "h264"), v
    assert info["ffprobe_audio"][0]["codec_name"] == "aac"
    # el evento principal debe caer en el tramo fuerte (30-60 s)
    mejor = max(res["eventos"], key=lambda e: e["score"])
    info["mejor_evento_en_tramo_fuerte"] = 30 <= mejor["start_s"] < 60
    (OUT / "resultado-pipeline.json").write_text(json.dumps(info, indent=1, ensure_ascii=False))
    print(json.dumps(info, indent=1, ensure_ascii=False))

if __name__ == "__main__":
    main()
