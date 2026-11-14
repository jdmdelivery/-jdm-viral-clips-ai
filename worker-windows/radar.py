"""Radar EN VIVO — la idea central de Jose: grabar TODO el en vivo y
detectar los shorts DURANTE la transmisión, no al final.

Un hilo por sesión vigila los segmentos que el Recorder va FINALIZANDO
(línea de tiempo real del manifest del buffer, como hace analyzer.py),
puntúa cada ventana con viral.py (audio/escena; la conversación solo «si
aplica» — transcribir cada 10 s en vivo no es viable y no se finge) y,
cuando un momento supera el umbral (config radar_umbral, defecto 80):

  1. genera su clip con clipper.contexto (regla 30–120 s, 9:16, SIN
     subtítulos) desde los segmentos ya cerrados, SIN detener la grabación;
  2. reporta evento+clip a la API (POST /workers/sessions/{id}/live-events),
     que además deduplica por solape >70% contra lo ya guardado.

Sin relleno: una ventana por debajo del umbral no se reporta aunque sea el
máximo local. Segmentos consecutivos sobre el umbral forman UNA ventana
(el momento completo); la ventana se cierra con el primer segmento flojo
o al detener la grabación, y nunca se repite un momento ya reportado
(solape >70% contra lo reportado en esta sesión de radar)."""
import subprocess, threading, time
from pathlib import Path
import viral, vision
from clipper import contexto, exportar_clip, miniatura
from titles import generar as generar_titulos
from dedup import _solape_ratio

class RadarSesion:
    def __init__(self, recorder, session_id, api, carpeta_clips, modo="general",
                 umbral=80, intervalo_s=30, pre_s=20, post_s=15):
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
        self.reportados = []              # intervalos ya reportados (dedup local)
        self._pendientes = []             # reportes que la red no aceptó aún
        self.latencias_s = []             # latencia momento->clip observada (s)
        self.errores = []                 # errores no fatales (diagnóstico)

    # ---------- línea de tiempo / segmentos ----------
    def _segmentos(self, incluir_ultimo=False):
        """Segmentos finalizados según el manifest del buffer del Recorder,
        ordenados por índice. Mientras se graba se excluye el último (puede
        estar cerrándose); en la pasada final se incluyen todos."""
        entradas = sorted(self.recorder.buffer.segmentos(), key=lambda e: e["indice"])
        if not incluir_ultimo and len(entradas) >= 1:
            entradas = entradas[:-1]
        return entradas

    def _fin_total(self):
        entradas = self.recorder.buffer.segmentos()
        if not entradas: return 0.0
        return max(e["inicio_s"] + e["duracion_s"] for e in entradas)

    # ---------- puntuación ----------
    def _puntuar_segmento(self, entrada):
        path = entrada["path"]
        medio, pico = viral.medir_audio(path)
        escenas = vision.cambios_de_escena(path)
        senales = {"rms_medio_db": medio, "rms_pico_db": pico,
                   "cambios_escena": escenas, "duracion_s": entrada["duracion_s"]}
        score, desglose = viral.puntuar(senales, self.modo)
        return score, desglose, viral.clasificar(senales, self.modo)

    # ---------- clip desde segmentos (sin tocar la grabación) ----------
    def _generar_clip(self, ventana, fin_total):
        ini, fin = contexto(ventana["start_s"], ventana["end_s"], fin_total,
                            self.pre_s, self.post_s)
        entradas = [e for e in self.recorder.buffer.segmentos()
                    if e["inicio_s"] + e["duracion_s"] > ini and e["inicio_s"] < fin]
        if not entradas: return None
        entradas.sort(key=lambda e: e["indice"])
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
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not unido.exists():
            raise RuntimeError(f"Concat de segmentos del radar falló: {r.stderr[-300:]}")
        salida = (self.carpeta_clips / f"sesion-{self.session_id}"
                  / f"clip-radar-{int(ventana['start_s'])}s.mp4")
        salida.parent.mkdir(parents=True, exist_ok=True)
        if salida.exists():
            salida = salida.with_name(f"clip-radar-{int(ventana['start_s'])}s-{int(time.time())}.mp4")
        exportar_clip(str(unido), max(0.0, ini - base), fin - base, salida, "9:16")
        thumb = miniatura(salida, 1, salida.with_suffix(".jpg"))
        try: unido.unlink(missing_ok=True); lista.unlink(missing_ok=True)
        except Exception: pass
        # Los segmentos del momento quedan protegidos en el buffer circular.
        try: self.recorder.buffer.proteger({e["indice"] for e in entradas})
        except Exception: pass
        return {"file_path": str(salida), "thumb_path": thumb, "format": "9:16",
                "duration_s": round(fin - ini, 2), "score": ventana["score"]}

    # ---------- reporte a la API ----------
    def _reportar(self, eventos, clips):
        try:
            self.api.reportar_en_vivo(self.session_id, eventos, clips)
            return True
        except Exception as e:
            self.errores.append(f"reporte en vivo: {e}")
            self._pendientes.append((eventos, clips))
            return False

    def _reintentar_pendientes(self):
        pendientes, self._pendientes = self._pendientes, []
        for eventos, clips in pendientes:
            self._reportar(eventos, clips)

    def _cerrar_ventana(self):
        """La ventana caliente terminó: genera el clip y reporta el momento.
        Si falla el clip, el evento igual se reporta (sin clip) — el momento
        detectado no se pierde; si falla la red, queda en pendientes."""
        v, self._ventana = self._ventana, None
        if v is None: return
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
            if score >= self.umbral:
                if self._ventana is None:
                    self._ventana = {"start_s": entrada["inicio_s"],
                                     "end_s": entrada["inicio_s"] + entrada["duracion_s"],
                                     "score": score, "breakdown": desglose, "type": tipo}
                else:
                    self._ventana["end_s"] = entrada["inicio_s"] + entrada["duracion_s"]
                    if score > self._ventana["score"]:
                        self._ventana.update({"score": score, "breakdown": desglose, "type": tipo})
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
