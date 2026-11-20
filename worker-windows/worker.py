"""Trabajador JDM Viral Clips AI (Windows y Linux).
Grabación en proceso separado (Recorder) + análisis en el bucle de trabajos:
procesar un clip NUNCA detiene una grabación en curso.
Uso: python worker.py --config worker-config.json   (ver README.md)"""
import argparse, json, os, subprocess, sys, threading, time
from pathlib import Path

VERSION = "1.3.0"
sys.path.insert(0, str(Path(__file__).parent))
from sync import ApiClient
from recorder import Recorder
from analyzer import analizar_video
from resolver import resolver_fuente, es_pagina_plataforma, es_archivo_local
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

def _carpeta_datos(cfg):
    return Path(cfg.get("carpeta_datos") or Path(cfg["carpeta_clips"]).parent)

def procesar(job, cfg, api=None):
    tipo, p = job["type"], job.get("payload", {})
    if tipo == "iniciar_grabacion":
        sid = p["session_id"]
        otras = [k for k in grabaciones if k != sid]
        if otras:
            raise RuntimeError(
                f"Este trabajador ya graba la sesión {otras[0]}. "
                "No se inicia otra captura hasta detener esa.")
        url = p["url"]
        # Regla de Jose (2026-11-14): los enlaces de plataformas se resuelven
        # AQUÍ, en la PC, ANTES de grabar (resolver.py/yt-dlp). Si la señal no
        # se puede obtener, resolver_fuente lanza ErrorResolucion con el
        # motivo honesto y el trabajo falla con ese mensaje — nunca se finge
        # una grabación. Al Recorder se le pasa un callable de re-resolución
        # para las reconexiones (las URLs de señal de plataformas caducan).
        senal = resolver_fuente(url)
        local = es_archivo_local(senal)
        refrescar = None if local or not es_pagina_plataforma(url) else (lambda: resolver_fuente(url))
        rec = Recorder(senal, Path(cfg["carpeta_grabaciones"]) / f"sesion-{sid}",
                       buffer_min=cfg.get("buffer_min", 5), en_vivo=not local, resolver=refrescar)
        rec.start()
        ok, info = rec.esperar_datos(timeout=float(cfg.get("espera_captura_s", 30)))
        if not ok:
            try: rec.stop()
            except Exception: rec.detener_flag.set()
            raise RuntimeError(info if isinstance(info, str) else "No hubo captura de video.")
        grabaciones[sid] = rec
        nota = "Captura confirmada: hay video en disco."
        if info.get("tiene_audio"): nota += " También hay audio."
        else: nota += " Esta fuente no entregó pista de audio."
        if refrescar: nota += " Señal resuelta desde el enlace de la plataforma en esta PC."
        if local: nota += " Fuente: archivo local autorizado."
        if p.get("radar_activo") or p.get("radar"):
            _activar_radar(sid, rec, cfg, api, p.get("mode", "general"))
            nota += " Radar analizando la captura real."
        return {"estado": "grabando", "bytes": info["bytes"], "tiene_video": True,
                "tiene_audio": bool(info.get("tiene_audio")), "archivo": info.get("archivo"),
                "nota": nota}
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
        nota = ""
        if not completa:
            nota = getattr(rec, "error_union", "") or "La captura se detuvo, pero no quedó el archivo de la grabación completa."
        bytes_final = 0
        if completa:
            try: bytes_final = Path(completa).stat().st_size
            except OSError: bytes_final = 0
        return {"estado": "detenida", "grabacion_completa": completa, "nota": nota, "bytes": bytes_final}
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
        from clipper import exportar_clip, miniatura, ventana_solicitada, clip_tiene_video
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
        ini, fin = ventana_solicitada(float(p["start_s"]), float(p["end_s"]), total,
                                      p.get("largo_s"), cfg.get("pre_s", 20), cfg.get("post_s", 15))
        carpeta = (f"proyecto-{p['project_id']}" if p.get("project_id")
                   else f"sesion-{p['session_id']}" if p.get("session_id") else "eventos")
        salida = Path(cfg["carpeta_clips"]) / carpeta / f"clip-evento-{p.get('event_id', 'x')}.mp4"
        salida.parent.mkdir(parents=True, exist_ok=True)
        exportar_clip(fuente, ini, fin, salida, "9:16")
        if not clip_tiene_video(salida):
            raise RuntimeError("El clip no contiene video. No se marca el trabajo como completado.")
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
    if tipo == "subir_clip":
        # Copia del clip al servidor para VER/DESCARGAR desde el panel: el
        # original queda en esta PC; se sube una copia en streaming. Si el
        # archivo no está en este disco, el trabajo falla con el motivo
        # honesto (nada de fingir una subida).
        ruta = p.get("file_path")
        if not ruta or not Path(ruta).is_file():
            raise FileNotFoundError(
                f"El archivo del clip no existe en esta PC: {ruta or '(sin ruta en el trabajo)'}")
        if api is None:
            raise RuntimeError("Sin cliente de API: no se puede subir el clip")
        r = api.subir_clip(int(p["clip_id"]), ruta)
        # La miniatura (el JPG hermano del MP4) sube con la copia: así los
        # clips que ya existían también ganan su miniatura en el panel al
        # pedir VER/DESCARGAR. Si falla, la copia del video ya quedó bien.
        miniatura = False
        jpg = Path(ruta).with_suffix(".jpg")
        if jpg.is_file() and hasattr(api, "subir_miniatura"):
            try:
                api.subir_miniatura(int(p["clip_id"]), str(jpg)); miniatura = True
            except Exception as e:
                print(f"Miniatura del clip {p['clip_id']} NO subida ({e}); la copia del video sí.")
        return {"subido": True, "clip_id": p["clip_id"],
                "bytes": (r or {}).get("bytes"), "archivo_local": ruta,
                "miniatura_subida": miniatura}
    if tipo == "corregir_subtitulos":
        from subtitulos import aplicar_texto
        ruta = p.get("file_path")
        if not ruta or not Path(ruta).is_file():
            raise FileNotFoundError("El clip original no está en esta PC: no se modificó nada.")
        hecho = aplicar_texto(ruta, p.get("texto") or "")
        if not hecho.get("ok"):
            raise RuntimeError(hecho.get("error") or "No se pudo aplicar el texto.")
        if api is None:
            raise RuntimeError("Sin cliente de API: el video local quedó listo y el original sigue igual.")
        api.reportar_subtitulos(ruta, "sincronizando", "", hecho.get("texto") or "", "")
        subido = api.subir_subtitulado(int(p["clip_id"]), hecho["subtitulado"])
        return {"corregido": True, "clip_id": p["clip_id"], "bytes": (subido or {}).get("bytes")}
    raise ValueError(f"Tipo de trabajo no soportado: {tipo}")

def _subir_copias_automaticas(resultado, detalle, api):
    """Subida automática de copias: tras completar un trabajo que creó clips
    (generar_clip, analizar_video, editar_clip), sube la copia de cada clip
    recién creado para que se pueda VER/DESCARGAR desde el panel. Los ids
    vienen en detalle["clip_ids"], en el mismo orden que resultado["clips"].
    Es best-effort y nunca tumba el bucle: si una subida falla, el clip
    queda creado igual y la copia se puede pedir desde el panel."""
    if not detalle or api is None: return
    ids = detalle.get("clip_ids") or []
    for info, cid in zip((resultado or {}).get("clips") or [], ids):
        ruta = (info or {}).get("file_path")
        if not ruta or not Path(ruta).is_file(): continue
        try:
            api.subir_clip(cid, ruta)
            print(f"Copia del clip {cid} subida al servidor (ya se puede ver en el panel).")
        except Exception as e:
            print(f"Copia del clip {cid} NO subida ({e}); se puede pedir desde el panel.")
        # La miniatura viaja con la copia (el panel no puede servir la
        # ruta del JPG en esta PC): sin ella el clip sale «Sin miniatura».
        thumb = (info or {}).get("thumb_path")
        if thumb and Path(thumb).is_file() and hasattr(api, "subir_miniatura"):
            try:
                api.subir_miniatura(cid, thumb)
                print(f"Miniatura del clip {cid} subida al servidor.")
            except Exception as e:
                print(f"Miniatura del clip {cid} NO subida ({e}).")

def _configurar_log(cfg):
    """Motor invisible: sin consola (pythonw, sys.stdout es None o sin
    tty) los print van a <carpeta_datos>/motor.log, con rotación a
    motor.log.old cuando supera 1 MB. Con consola no se toca nada.
    Un fallo aquí nunca impide arrancar el trabajador."""
    try:
        sin_consola = sys.stdout is None
        if not sin_consola:
            try:
                sin_consola = not sys.stdout.isatty()
            except Exception:
                sin_consola = True
        if not sin_consola:
            return
        carpeta_datos = _carpeta_datos(cfg)
        carpeta_datos.mkdir(parents=True, exist_ok=True)
        ruta_log = carpeta_datos / "motor.log"
        try:
            if ruta_log.exists() and ruta_log.stat().st_size > 1024 * 1024:
                os.replace(ruta_log, carpeta_datos / "motor.log.old")
        except OSError:
            pass
        archivo = open(ruta_log, "a", encoding="utf-8", buffering=1)
        sys.stdout = archivo
        sys.stderr = archivo
    except Exception:
        pass

def _hilo_actualizador(config_path):
    """Actualizador en segundo plano: revisa a los ~20 s del arranque y
    luego cada 6 horas. Si una actualización toca worker.py o
    actualizador.py, relanza el motor y sale. Nunca tumba el bucle
    principal: todo va envuelto en try/except."""
    try:
        time.sleep(20)
        while True:
            try:
                import actualizador
                resultado = actualizador.revisar(Path(__file__).resolve().parent)
                if resultado.get("actualizados"):
                    print(f"Actualizador: archivos actualizados: "
                          f"{', '.join(resultado['actualizados'])}")
                for error in resultado.get("errores", []):
                    print(f"Actualizador: {error}")
                if resultado.get("reiniciar"):
                    print("Actualizador: el motor se reinicia para aplicar "
                          "la actualización.")
                    subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                      "--config", config_path])
                    os._exit(0)
            except Exception as e:
                print(f"Actualizador: error sin detener el motor: {e}")
            time.sleep(6 * 3600)
    except Exception as e:
        print(f"Actualizador: hilo detenido por un error: {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="worker-config.json")
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    _configurar_log(cfg)
    # actualizar=false solo en esta PC: evita que el manifiesto publicado
    # reemplace el motor local que todavía no está en el repositorio remoto.
    if cfg.get("actualizar", True):
        try:
            threading.Thread(target=_hilo_actualizador, args=(args.config,),
                             daemon=True).start()
        except Exception as e:
            print(f"Actualizador: no se pudo iniciar el hilo: {e}")
    else:
        print("Actualizador: desactivado en esta configuración local.")
    api = ApiClient(cfg["api_url"], cfg["worker_token"], cfg.get("cola_offline", "cola-offline.json"))
    print(f"Trabajador conectado a {cfg['api_url']} — Ctrl+C para salir")
    try:
        from subtitulos import iniciar_cola
        iniciar_cola(cfg, api, lambda: bool(grabaciones))
    except Exception as e:
        print(f"Subtítulos: la cola no arrancó ({e}). La grabación y el radar siguen igual.")
    while True:
        try:
            api.heartbeat(VERSION); api.sincronizar_pendientes()
            for sid_b, rec_b in list(grabaciones.items()):
                try: api.reportar_progreso(sid_b, rec_b.bytes_en_disco())
                except Exception: pass
            job = api.reclamar()
            if job:
                print(f"Trabajo {job['id']}: {job['type']}")
                try:
                    resultado = procesar(job, cfg, api)
                    detalle = api.completar_detalle(job["id"], resultado)
                    # Sin detener nada: la copia de cada clip nuevo sube aquí,
                    # en el mismo flujo del bucle de trabajos.
                    _subir_copias_automaticas(resultado, detalle, api)
                except Exception as e: api.fallar(job["id"], e)
            else:
                time.sleep(5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error de red/API (se reintenta): {e}"); time.sleep(10)

if __name__ == "__main__":
    main()
