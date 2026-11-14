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
from resolver import resolver_fuente, es_pagina_plataforma
from radar import RadarSesion

grabaciones = {}  # session_id -> Recorder
radares = {}      # session_id -> RadarSesion (radar en vivo activo)

def _activar_radar(sid, rec, cfg, api, modo="general"):
    if sid in radares: return radares[sid]
    radar = RadarSesion(rec, sid, api, Path(cfg["carpeta_clips"]), modo=modo,
                        umbral=int(cfg.get("radar_umbral", 80)),
                        intervalo_s=float(cfg.get("radar_intervalo_s", 30)),
                        pre_s=cfg.get("pre_s", 20), post_s=cfg.get("post_s", 15))
    radar.start(); radares[sid] = radar
    return radar

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
        url = p["url"]
        # Regla de Jose (2026-11-14): los enlaces de plataformas se resuelven
        # AQUÍ, en la PC, ANTES de grabar (resolver.py/yt-dlp). Si la señal no
        # se puede obtener, resolver_fuente lanza ErrorResolucion con el
        # motivo honesto y el trabajo falla con ese mensaje — nunca se finge
        # una grabación. Al Recorder se le pasa un callable de re-resolución
        # para las reconexiones (las URLs de señal de plataformas caducan).
        senal = resolver_fuente(url)
        refrescar = (lambda: resolver_fuente(url)) if es_pagina_plataforma(url) else None
        rec = Recorder(senal, Path(cfg["carpeta_grabaciones"]) / f"sesion-{sid}",
                       buffer_min=cfg.get("buffer_min", 5), en_vivo=True, resolver=refrescar)
        rec.start(); grabaciones[sid] = rec
        nota = "Grabación iniciada en proceso separado; sigue aunque se procesen clips."
        if refrescar: nota += " Señal resuelta desde el enlace de la plataforma en esta PC."
        if p.get("radar_activo") or p.get("radar"):
            _activar_radar(sid, rec, cfg, api, p.get("mode", "general"))
            nota += " Radar en vivo ACTIVADO."
        return {"estado": "grabando", "nota": nota}
    if tipo == "radar_sesion":
        # Enciende/apaga el radar en vivo de la grabación en curso de la sesión.
        sid = p["session_id"]; activo = bool(p.get("activo"))
        if not activo:
            radar = radares.pop(sid, None)
            if radar: radar.detener()
            return {"estado": "radar_desactivado", "radar_activo": False}
        rec = grabaciones.get(sid)
        if not rec:
            return {"estado": "sin_grabacion_activa", "radar_activo": False,
                    "nota": "En este trabajador no hay grabación en curso de esa sesión. "
                            "El panel ya dejó el radar activado: se encenderá solo al "
                            "iniciar la grabación."}
        _activar_radar(sid, rec, cfg, api, p.get("mode", "general"))
        return {"estado": "radar_activado", "radar_activo": True}
    if tipo == "detener_grabacion":
        sid = p["session_id"]
        rec = grabaciones.pop(sid, None)
        if not rec: return {"estado": "sin_grabacion_activa"}
        completa = rec.stop()
        # El radar se detiene DESPUÉS de unir la grabación: su pasada final
        # revisa también el último segmento y reporta lo que quedara abierto.
        radar = radares.pop(sid, None)
        if radar: radar.detener()
        return {"estado": "detenida", "grabacion_completa": completa}
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
