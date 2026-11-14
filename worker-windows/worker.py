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

def resolver_archivo(p, cfg, api=None):
    """Resuelve la fuente del análisis en la arquitectura híbrida:
    URL http(s) tal cual (la abre FFmpeg) · ruta local existente tal cual ·
    ruta del servidor inexistente aquí -> descarga desde la API a la carpeta
    local de descargas (reutiliza una copia previa con tamaño > 0)."""
    ruta = p["file_path"]
    if ruta.startswith(("http://", "https://")):
        return ruta
    if Path(ruta).exists():
        return ruta
    pid = p.get("project_id")
    if pid and api is not None:
        base = ruta.replace("\\", "/").rstrip("/").split("/")[-1] or f"proyecto-{pid}.mp4"
        destino = Path(cfg["carpeta_clips"]) / "descargas" / f"proyecto-{pid}-{base}"
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists() and destino.stat().st_size > 0:
            return str(destino)
        return str(api.descargar_proyecto(pid, destino))
    return ruta

def procesar(job, cfg, api=None):
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
        r = analizar_video(resolver_archivo(p, cfg, api), salida, modo=p.get("mode", "general"),
                           pre_s=cfg.get("pre_s", 20), post_s=cfg.get("post_s", 15),
                           max_clips=p.get("max_clips", 3))
        return {"events": [{"type": e["type"], "mode": e["mode"], "start_s": e["start_s"],
                            "end_s": e["end_s"], "score": e["score"], "breakdown": e["breakdown"]} for e in r["eventos"]],
                "clips": [{k: c[k] for k in ("file_path", "format", "duration_s", "score", "title", "titles", "subtitles")} for c in r["clips"]],
                "tiempo_pipeline_s": r["tiempo_total_pipeline_s"]}
    if tipo == "generar_clip":
        # Etapa 5 (lado trabajador): genera el clip de UN evento (piloto
        # automático o futuro botón manual). El payload de la API trae
        # event_id/project_id/session_id, start_s/end_s, mode y file_path
        # (ruta del proyecto en ESTE trabajador). Sin file_path pero con
        # sesión, la fuente es la grabación completa de esa sesión.
        from clipper import exportar_clip, miniatura, contexto
        from analyzer import duracion
        from titles import generar as generar_titulos
        if not p.get("file_path") and p.get("session_id"):
            candidata = Path(cfg["carpeta_grabaciones"]) / f"sesion-{p['session_id']}" / "grabacion_completa.mp4"
            if candidata.exists(): p = {**p, "file_path": str(candidata)}
        if not p.get("file_path"):
            raise ValueError("El evento no tiene archivo asociado (sin proyecto ni "
                             "grabación de sesión): no se puede generar el clip.")
        fuente = resolver_archivo(p, cfg, api)
        total = duracion(fuente)
        ini, fin = contexto(float(p["start_s"]), float(p["end_s"]), total,
                            cfg.get("pre_s", 20), cfg.get("post_s", 15))
        carpeta = (f"proyecto-{p['project_id']}" if p.get("project_id")
                   else f"sesion-{p['session_id']}" if p.get("session_id") else "eventos")
        salida = Path(cfg["carpeta_clips"]) / carpeta / f"clip-evento-{p.get('event_id', 'x')}.mp4"
        salida.parent.mkdir(parents=True, exist_ok=True)
        exportar_clip(fuente, ini, fin, salida, "9:16")
        thumb = miniatura(salida, 1, salida.with_suffix(".jpg"))
        score = int(p.get("score", 0))
        tit = generar_titulos(p.get("type", "general"), p.get("mode", "general"), score)
        return {"clips": [{"file_path": str(salida), "thumb_path": thumb, "format": "9:16",
                            "duration_s": round(fin - ini, 2), "score": score,
                            "title": tit["titulos"][0], "titles": tit, "subtitles": False,
                            "event_id": p.get("event_id"), "project_id": p.get("project_id"),
                            "session_id": p.get("session_id")}]}
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
                try: api.completar(job["id"], procesar(job, cfg, api))
                except Exception as e: api.fallar(job["id"], e)
            else:
                time.sleep(5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error de red/API (se reintenta): {e}"); time.sleep(10)

if __name__ == "__main__":
    main()
