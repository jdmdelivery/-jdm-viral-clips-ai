"""Radar EN VIVO — la idea central de Jose: grabar TODO el en vivo y
detectar los shorts DURANTE la transmisión, no al final.

Un hilo por sesión vigila los segmentos que el Recorder va FINALIZANDO
(línea de tiempo real del manifest del buffer, como hace analyzer.py),
puntúa cada ventana con viral.py (audio/escena; la conversación solo «si
aplica» — transcribir cada 10 s en vivo no es viable y no se finge) y,
cuando un momento supera el umbral (config radar_umbral, defecto 80):

  1. genera su clip con clipper.contexto (regla 60–120 s, 9:16, SIN
     subtítulos) desde los segmentos ya cerrados, SIN detener la grabación;
  2. reporta evento+clip a la API (POST /workers/sessions/{id}/live-events),
     que además deduplica por solape >70% contra lo ya guardado.

Sin relleno: una ventana por debajo del umbral no se reporta aunque sea el
máximo local. Segmentos consecutivos sobre el umbral forman UNA ventana
(el momento completo); la ventana se cierra con el primer segmento flojo
o al detener la grabación, y nunca se repite un momento ya reportado
(solape >70% contra lo reportado en esta sesión de radar).

Modo «epicos» (v1.3.16): cada segmento también se mide en movimiento
(vision.movimiento_detalle) para clasificar «baile» y «pelea», y SOLO
las ventanas de tipo discusión/pelea/baile generan clip; el resto se
descarta en silencio, como un segmento flojo más."""
import subprocess, threading, time
from pathlib import Path
from procesos import SIN_VENTANA
import viral, vision
from clipper import contexto, exportar_clip, miniatura, clip_tiene_video
from titles import generar as generar_titulos
from dedup import _solape_ratio

class RadarSesion:
    def __init__(self, recorder, session_id, api, carpeta_clips, modo="general",
                 umbral=80, intervalo_s=30, pre_s=30, post_s=25):
        self.recorder = recorder          # Recorder en curso (NO se detiene)
        self.session_id = session_id
        self.api = api                    # ApiClient o compatible (reportar_en_vivo)
        self.carpeta_clips = Path(carpeta_clips)
        self.modo = modo
        self.umbral = int(umbral)
        self.intervalo_s = float(intervalo_s)
        self.pre_s, self.post_s = pre_s, post_s
        self._detener = threading.Event()
        self.hilo = None
        self._puntuados = set()           # índices de segmento ya puntuados
        self._ventana = None              # momento caliente en curso (dict) o None
        self._ventana_indices = set()     # segmentos de la ventana abierta (en uso)
        self.reportados = []              # intervalos ya reportados (dedup local)
        self._pendientes = []             # reportes que la red no aceptó aún
        self.latencias_s = []             # latencia momento->clip observada (s)
        self.errores = []                 # errores no fatales (diagnóstico)

    # ---------- línea de tiempo / segmentos ----------
    def _entradas_corrida(self):
        """Entradas del manifest que pertenecen a la corrida ACTUAL del
        Recorder. La carpeta de la sesión se reutiliza entre grabaciones:
        si una entrada vieja sobreviviera en el manifest (otra fuente,
        otra línea de tiempo), puntuarla y cortarla metería la grabación
        anterior dentro de los clips de esta (sesión #20, 2026-11-23).
        El criterio es el mtime del archivo contra el inicio de la
        corrida (el Recorder lo expone en _t_inicio; en su defecto se
        usa recorder.inicio), con 1 s de tolerancia — el mismo de la
        guarda de _captura_lista de la v1.3.3. Sin referencia temporal
        no se filtra nada."""
        entradas = self.recorder.buffer.segmentos()
        referencia = getattr(self.recorder, "_t_inicio", None)
        if referencia is None:
            referencia = getattr(self.recorder, "inicio", None)
        if referencia is None:
            return list(entradas)
        corte = float(referencia) - 1.0
        propias = []
        for e in entradas:
            try:
                if Path(e["path"]).stat().st_mtime >= corte:
                    propias.append(e)
            except OSError:
                continue
        return propias

    def _segmentos(self, incluir_ultimo=False):
        """Segmentos finalizados según el manifest del buffer del Recorder,
        ordenados por índice. Mientras se graba se excluye el último (puede
        estar cerrándose); en la pasada final se incluyen todos."""
        entradas = sorted(self._entradas_corrida(), key=lambda e: e["indice"])
        if not incluir_ultimo and len(entradas) >= 1:
            entradas = entradas[:-1]
        return entradas

    def _fin_total(self):
        entradas = self._entradas_corrida()
        if not entradas: return 0.0
        return max(e["inicio_s"] + e["duracion_s"] for e in entradas)

    # ---------- puntuación ----------
    def _puntuar_segmento(self, entrada):
        path = entrada["path"]
        medio, pico = viral.medir_audio(path)
        escenas = vision.cambios_de_escena(path)
        senales = {"rms_medio_db": medio, "rms_pico_db": pico,
                   "cambios_escena": escenas, "duracion_s": entrada["duracion_s"]}
        # Movimiento (v1.3.16): serie de diferencias entre frames. Sin
        # esta señal no hay «baile» ni «pelea»; si la medición falla, el
        # segmento se puntúa como siempre (nunca se finge la señal).
        mov = vision.movimiento_detalle(path)
        if mov.get("disponible"):
            senales.update({"mov_medio": mov["mov_medio"], "mov_pico": mov["mov_pico"],
                            "mov_fraccion_activa": mov["fraccion_activa"]})
        score, desglose = viral.puntuar(senales, self.modo)
        return score, desglose, viral.clasificar(senales, self.modo)

    # ---------- uso compartido con la poda rodante (v1.3.11) ----------
    def _marcar_en_uso(self, indices):
        """La poda rodante del Recorder nunca borra un segmento en uso:
        ni los de la ventana caliente abierta ni los que un clip está
        concatenando en este instante (borrarlos a mitad de lectura
        rompería el clip)."""
        try: self.recorder.buffer.marcar_en_uso(set(indices))
        except Exception: pass
    def _liberar_en_uso(self, indices):
        try: self.recorder.buffer.liberar_en_uso(set(indices))
        except Exception: pass

    # ---------- clip desde segmentos (sin tocar la grabación) ----------
    def _generar_clip(self, ventana, fin_total):
        ini, fin = contexto(ventana["start_s"], ventana["end_s"], fin_total,
                            self.pre_s, self.post_s)
        entradas = [e for e in self._entradas_corrida()
                    if e["inicio_s"] + e["duracion_s"] > ini and e["inicio_s"] < fin]
        if not entradas: return None
        entradas.sort(key=lambda e: e["indice"])
        # Mientras el concat/export lee estos archivos, la poda rodante
        # no puede borrarlos (quedan marcados en uso hasta terminar).
        self._marcar_en_uso({e["indice"] for e in entradas})
        try:
            base = entradas[0]["inicio_s"]
            trabajo = self.carpeta_clips / f"sesion-{self.session_id}" / "_radar"
            trabajo.mkdir(parents=True, exist_ok=True)
            # Concat de los segmentos que cubren la ventana (archivos ya cerrados:
            # leerlos no interfiere con el FFmpeg que sigue grabando).
            lista = trabajo / f"concat-{int(ini)}-{int(fin)}.txt"
            lista.write_text("".join(f"file '{Path(e['path']).resolve()}'\n" for e in entradas),
                             encoding="utf-8")
            unido = trabajo / f"ventana-{int(ini)}-{int(fin)}.mp4"
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                                "-i", str(lista), "-c", "copy", str(unido)],
                               capture_output=True, text=True, timeout=600, creationflags=SIN_VENTANA)
            if r.returncode != 0 or not unido.exists():
                raise RuntimeError(f"Concat de segmentos del radar falló: {r.stderr[-300:]}")
            salida = (self.carpeta_clips / f"sesion-{self.session_id}"
                      / f"clip-radar-{int(ventana['start_s'])}s.mp4")
            salida.parent.mkdir(parents=True, exist_ok=True)
            if salida.exists():
                salida = salida.with_name(f"clip-radar-{int(ventana['start_s'])}s-{int(time.time())}.mp4")
            exportar_clip(str(unido), max(0.0, ini - base), fin - base, salida, "9:16",
                          pico_s=max(0.0, float(ventana["start_s"]) - base))
            if not clip_tiene_video(salida):
                raise RuntimeError("El clip del radar quedó sin video.")
            thumb = miniatura(salida, 1, salida.with_suffix(".jpg"))
            try: unido.unlink(missing_ok=True); lista.unlink(missing_ok=True)
            except Exception: pass
            # Los segmentos del momento quedan protegidos en el buffer circular.
            try: self.recorder.buffer.proteger({e["indice"] for e in entradas})
            except Exception: pass
            return {"file_path": str(salida), "thumb_path": thumb, "format": "9:16",
                    "duration_s": round(fin - ini, 2), "score": ventana["score"]}
        finally:
            self._liberar_en_uso({e["indice"] for e in entradas})

    # ---------- reporte a la API ----------
    def _reportar(self, eventos, clips):
        try:
            respuesta = self.api.reportar_en_vivo(self.session_id, eventos, clips)
        except Exception as e:
            self.errores.append(f"reporte en vivo: {e}")
            self._pendientes.append((eventos, clips))
            return False
        # Subida automática de la copia de cada clip reportado (para VERLO en
        # el panel sin esperar): best-effort, en este mismo hilo del radar y
        # sin tocar la grabación. La API devuelve los ids en «clip_ids», en el
        # mismo orden que los clips enviados; si la subida falla, el clip
        # queda creado igual y la copia se puede pedir desde el panel.
        subir = getattr(self.api, "subir_clip", None)
        subir_thumb = getattr(self.api, "subir_miniatura", None)
        ids = (respuesta or {}).get("clip_ids") or []
        if subir is not None and clips:
            for clip, cid in zip(clips, ids):
                ruta = (clip or {}).get("file_path")
                if not ruta: continue
                try: subir(cid, ruta)
                except Exception as e:
                    self.errores.append(f"subida automática del clip {cid}: {e}")
                # La miniatura viaja con la copia: sin su JPG en el
                # servidor, el panel enseña «Sin miniatura» aunque el
                # archivo exista en esta PC.
                thumb = (clip or {}).get("thumb_path")
                if subir_thumb is not None and thumb:
                    try: subir_thumb(cid, thumb)
                    except Exception as e:
                        self.errores.append(f"subida de la miniatura del clip {cid}: {e}")
        return True

    def _reintentar_pendientes(self):
        pendientes, self._pendientes = self._pendientes, []
        for eventos, clips in pendientes:
            self._reportar(eventos, clips)

    def _cerrar_ventana(self):
        """La ventana caliente terminó: genera el clip y reporta el momento.
        Si falla el clip, el evento igual se reporta (sin clip) — el momento
        detectado no se pierde; si falla la red, queda en pendientes."""
        v, self._ventana = self._ventana, None
        indices, self._ventana_indices = self._ventana_indices, set()
        if v is None: return
        try:
            candidato = {"start_s": v["start_s"], "end_s": v["end_s"]}
            for rep in self.reportados:
                if _solape_ratio(candidato, rep) > 0.70:
                    return  # mismo momento ya reportado: no repetir
            evento = {"type": v["type"], "mode": self.modo, "start_s": v["start_s"],
                      "end_s": v["end_s"], "score": v["score"], "breakdown": v["breakdown"]}
            clips = []
            try:
                clip = self._generar_clip(v, self._fin_total())
                if clip:
                    tit = generar_titulos(v["type"], self.modo, v["score"])
                    clip.update({"title": tit["titulos"][0], "titles": tit, "subtitles": False})
                    clips.append({**clip, "event_index": 0})
                    if self.recorder.inicio:
                        # Latencia momento->clip: del fin del momento (en el reloj
                        # de la grabación) a tener el clip listo en disco.
                        self.latencias_s.append(round(time.time() - (self.recorder.inicio + v["end_s"]), 1))
            except Exception as e:
                self.errores.append(f"clip del radar: {e}")
            if self._reportar([evento], clips):
                self.reportados.append(candidato)
            else:
                self.reportados.append(candidato)  # ya salió de aquí: no duplicar en el reintento local
        finally:
            # La ventana ya se procesó: sus segmentos dejan de estar en
            # uso y la poda rodante podrá borrarlos cuando salgan de la
            # ventana (los del clip generado quedan protegidos aparte).
            self._liberar_en_uso(indices)

    # ---------- pasada / bucle ----------
    def pasada(self, incluir_ultimo=False):
        """Puntúa los segmentos finalizados nuevos y cierra ventanas.
        Devuelve cuántos segmentos puntuó en esta pasada."""
        self._reintentar_pendientes()
        n = 0
        for entrada in self._segmentos(incluir_ultimo):
            if entrada["indice"] in self._puntuados: continue
            self._puntuados.add(entrada["indice"]); n += 1
            try:
                score, desglose, tipo = self._puntuar_segmento(entrada)
            except Exception as e:
                self.errores.append(f"puntuando segmento {entrada['indice']}: {e}")
                continue
            # Modo Épicos (v1.3.16): un segmento solo abre o extiende
            # ventana si su tipo es discusión/pelea/baile; cualquier
            # otro momento, aunque supere el umbral, se trata como
            # segmento flojo (cierra la ventana abierta y no genera
            # clip). En los demás modos la condición es la de siempre.
            apto = score >= self.umbral and (self.modo != "epicos" or tipo in viral.TIPOS_EPICOS)
            if apto:
                if self._ventana is None:
                    self._ventana = {"start_s": entrada["inicio_s"],
                                     "end_s": entrada["inicio_s"] + entrada["duracion_s"],
                                     "score": score, "breakdown": desglose, "type": tipo}
                    self._ventana_indices = {entrada["indice"]}
                else:
                    self._ventana["end_s"] = entrada["inicio_s"] + entrada["duracion_s"]
                    self._ventana_indices.add(entrada["indice"])
                    if score > self._ventana["score"]:
                        self._ventana.update({"score": score, "breakdown": desglose, "type": tipo})
                # La ventana abierta marca sus segmentos en uso: la poda
                # rodante no puede borrar el inicio de un momento que
                # aún no se ha cerrado ni cortado.
                self._marcar_en_uso({entrada["indice"]})
            elif self._ventana is not None:
                self._cerrar_ventana()
        return n

    def _bucle(self):
        while not self._detener.is_set():
            try: self.pasada(incluir_ultimo=False)
            except Exception as e: self.errores.append(f"pasada: {e}")
            self._detener.wait(self.intervalo_s)

    def start(self):
        self.hilo = threading.Thread(target=self._bucle, daemon=True)
        self.hilo.start()

    def detener(self):
        """Para el hilo y hace la pasada FINAL: todos los segmentos (incluido
        el último) y cierre de la ventana que quedara abierta. Se llama
        DESPUÉS de Recorder.stop(), cuando el manifest ya está completo."""
        self._detener.set()
        if self.hilo: self.hilo.join(timeout=max(10.0, self.intervalo_s + 5))
        try: self.pasada(incluir_ultimo=True)
        except Exception as e: self.errores.append(f"pasada final: {e}")
        if self._ventana is not None: self._cerrar_ventana()
        self._reintentar_pendientes()
