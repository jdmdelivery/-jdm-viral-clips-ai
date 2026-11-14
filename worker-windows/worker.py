"""Trabajador JDM Viral Clips AI (Windows y Linux).
Grabación en proceso separado (Recorder) + análisis en el bucle de trabajos:
procesar un clip NUNCA detiene una grabación en curso.
Uso: python worker.py --config worker-config.json   (ver README.md)"""
import argparse, json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from sync import ApiClient
from recorder import Recorder
from analyzer import analizar_video

grabaciones = {}  # session_id -> Recorder

def procesar(job, cfg):
    tipo, p = job["type"], job.get("payload", {})
    if tipo == "iniciar_grabacion":
        sid = p["session_id"]
        rec = Recorder(p["url"], Path(cfg["carpeta_grabaciones"]) / f"sesion-{sid}",
                       buffer_min=cfg.get("buffer_min", 5), en_vivo=True)
        rec.start(); grabaciones[sid] = rec
        return {"estado": "grabando", "nota": "Grabación iniciada en proceso separado; sigue aunque se procesen clips."}
    if tipo == "detener_grabacion":
        rec = grabaciones.pop(p["session_id"], None)
        if not rec: return {"estado": "sin_grabacion_activa"}
        return {"estado": "detenida", "grabacion_completa": rec.stop()}
    if tipo == "analizar_video":
        salida = Path(cfg["carpeta_clips"]) / f"proyecto-{p.get('project_id', 'x')}"
        r = analizar_video(p["file_path"], salida, modo=p.get("mode", "general"),
                           pre_s=cfg.get("pre_s", 20), post_s=cfg.get("post_s", 15),
                           max_clips=p.get("max_clips", 3))
        return {"events": [{"type": e["type"], "mode": e["mode"], "start_s": e["start_s"],
                            "end_s": e["end_s"], "score": e["score"], "breakdown": e["breakdown"]} for e in r["eventos"]],
                "clips": [{k: c[k] for k in ("file_path", "format", "duration_s", "score", "title", "titles", "subtitles")} for c in r["clips"]],
                "tiempo_pipeline_s": r["tiempo_total_pipeline_s"]}
    if tipo == "editar_clip":
        # Etapa 3 (lado trabajador): edición real con FFmpeg (ver editor.py).
        from editor import aplicar_edicion
        params = p.get("params", {})
        base = Path(p["salida_dir"]) if p.get("salida_dir") else Path(cfg.get("carpeta_clips", "clips")) / "ediciones"
        salida = base / f"clip-{p.get('clip_id', 'x')}-editado.mp4"
        r = aplicar_edicion(p["file_path"], salida, params)
        return {"clips": [{"file_path": r["file_path"], "format": r["format"],
                            "duration_s": r["duration_s"],
                            "score": p.get("score", params.get("score", 0)),
                            "title": p.get("title") or "Clip editado",
                            "subtitles": r["subtitles"]}],
                "nota": r["nota"]}
    if tipo == "miniatura":
        # Etapa 3: miniatura suelta (la API la encola como job propio «miniatura»).
        from clipper import miniatura
        params = p.get("params", {})
        base = Path(cfg.get("carpeta_clips", "clips")) / "miniaturas"
        base.mkdir(parents=True, exist_ok=True)
        salida = base / f"clip-{p.get('clip_id', 'x')}.jpg"
        thumb = miniatura(p["file_path"], params.get("segundo", 1), salida)
        if not thumb:
            raise RuntimeError("FFmpeg no pudo generar la miniatura")
        return {"thumb_path": thumb,
                "nota": "Miniatura generada por el trabajador; la ruta queda en el resultado del trabajo."}
    raise ValueError(f"Tipo de trabajo no soportado: {tipo}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="worker-config.json")
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    api = ApiClient(cfg["api_url"], cfg["worker_token"], cfg.get("cola_offline", "cola-offline.json"))
    print(f"Trabajador conectado a {cfg['api_url']} — Ctrl+C para salir")
    while True:
        try:
            api.heartbeat(); api.sincronizar_pendientes()
            job = api.reclamar()
            if job:
                print(f"Trabajo {job['id']}: {job['type']}")
                try: api.completar(job["id"], procesar(job, cfg))
                except Exception as e: api.fallar(job["id"], e)
            else:
                time.sleep(5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error de red/API (se reintenta): {e}"); time.sleep(10)

if __name__ == "__main__":
    main()
