"""Grabación con FFmpeg en segmentos de 10 s. La grabación corre en un proceso
separado: NUNCA se detiene porque la IA esté procesando un clip.
Incluye reconexión con reintentos y unión de segmentos al finalizar."""
import json, os, subprocess, threading, time
from pathlib import Path
from buffer import BufferCircular

SEG = 10

def duracion_real(path):
    """Duración real de un segmento cerrado (el muxer corta en keyframes: no asumir 10 s exactos)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return float(SEG)

def _cmd_grabar(fuente, outdir, start_number=0, en_vivo=False):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    if en_vivo: cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    cmd += ["-i", str(fuente), "-map", "0:v:0?", "-map", "0:a:0?",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-sc_threshold", "0",
            "-force_key_frames", f"expr:gte(t,n_forced*{SEG})",
            "-c:a", "aac", "-ar", "48000",
            "-f", "segment", "-segment_time", str(SEG), "-reset_timestamps", "1",
            "-segment_start_number", str(start_number),
            str(Path(outdir) / "seg_%05d.mp4")]
    return cmd

class Recorder:
    def __init__(self, fuente, outdir, buffer_min=5, en_vivo=True, max_reintentos=5, resolver=None):
        # resolver (opcional): callable sin argumentos que devuelve una URL de
        # señal FRESCA. Las señales resueltas de plataformas rotan/caducan;
        # en cada reconexión se pide una nueva en vez de reutilizar la muerta.
        # Sin resolver, el comportamiento es idéntico al histórico.
        self.fuente, self.outdir = fuente, Path(outdir)
        self.resolver = resolver
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.buffer = BufferCircular(self.outdir, minutos=buffer_min)
        self.en_vivo, self.max_reintentos = en_vivo, max_reintentos
        self.proc = None; self.detener_flag = threading.Event()
        self.hilo = None; self.reintentos = 0; self.inicio = None
        self._cursor = 0.0; self._finalizados = set(); self.error_union = ""
    def _segmentos(self):
        return sorted(self.outdir.glob("seg_*.mp4"))
    def _archivo_estable(self, p, intentos=6, pausa=0.25):
        """El muxer de segmentos crea el archivo antes de cerrarlo. No se mide
        ni se puntúa hasta que el tamaño deja de cambiar."""
        anterior = -1
        for _ in range(intentos):
            try:
                tam = p.stat().st_size
            except OSError:
                return False
            if tam > 0 and tam == anterior:
                return True
            anterior = tam
            time.sleep(pausa)
        return False
    def _finalizar(self, p):
        """Registra un segmento YA cerrado con su línea de tiempo real (inicio acumulado + duración medida)."""
        if not self._archivo_estable(p):
            return
        idx = int(p.stem.split("_")[1])
        d = duracion_real(p)
        self.buffer.registrar(str(p), idx, round(self._cursor, 3), round(d, 3))
        self._cursor += d; self._finalizados.add(p.name)
    def _registrar_nuevos(self, vistos):
        # Se finalizan todos menos el último (que aún puede estar escribiéndose).
        segs = self._segmentos()
        for p in segs[:-1]:
            if p.name not in self._finalizados:
                self._finalizar(p); vistos.add(p.name)
    def _finalizar_todos(self):
        for p in self._segmentos():
            if p.name not in self._finalizados:
                self._finalizar(p)
    def _refrescar_fuente(self):
        """Pide una URL fresca al resolver (si hay). Si el resolver falla, se
        conserva la última URL conocida y el reintento sigue su curso."""
        if not self.resolver: return
        try:
            nueva = self.resolver()
            if nueva: self.fuente = nueva
        except Exception:
            pass
    def _bucle(self):
        self.inicio = time.time()
        vistos = set()
        start_number = 0
        while not self.detener_flag.is_set():
            log = open(self.outdir / "ffmpeg.log", "ab")
            self.proc = subprocess.Popen(_cmd_grabar(self.fuente, self.outdir, start_number, self.en_vivo),
                                         stdout=subprocess.DEVNULL, stderr=log)
            self._log_fh = log
            while self.proc.poll() is None and not self.detener_flag.is_set():
                time.sleep(0.5)
                self._registrar_nuevos(vistos)
            self._registrar_nuevos(vistos)
            if self.detener_flag.is_set(): break
            # Salida inesperada: reconexión (solo fuentes en vivo; un archivo que terminó no reintenta)
            segs = self._segmentos()
            if not self.en_vivo or not segs: break
            self.reintentos += 1
            if self.reintentos > self.max_reintentos: break
            start_number = len(segs)
            self._refrescar_fuente()  # señal de plataforma: URL fresca, no la caducada
            time.sleep(2)
        # Un archivo finito ya terminó: une ahora, mientras los segmentos siguen
        # en disco. Esperar al botón Detener dejaba la película sin archivo si
        # esos segmentos desaparecían antes.
        if not self.en_vivo and not self.detener_flag.is_set():
            try: self.unir()
            except Exception: pass
    def _pista(self, path, selector):
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", selector,
                                "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                               capture_output=True, text=True, timeout=15)
            return r.returncode == 0 and selector[0] in (r.stdout or "")
        except Exception:
            return False
    def _captura_lista(self, min_bytes):
        for p in self._segmentos():
            try:
                tam = p.stat().st_size
            except OSError:
                continue
            if tam < min_bytes:
                continue
            if self._pista(p, "v:0"):
                return {"bytes": tam, "tiene_video": True, "tiene_audio": self._pista(p, "a:0"),
                        "archivo": p.name}
        return None
    def _motivo_sin_captura(self):
        cola = ""
        log = self.outdir / "ffmpeg.log"
        try:
            if log.exists():
                texto = log.read_text(encoding="utf-8", errors="replace").strip()
                if texto:
                    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
                    util = next((ln for ln in reversed(lineas) if "not divisible" in ln.lower()
                                 or "error while" in ln.lower() or "invalid" in ln.lower()), lineas[-1])
                    cola = " " + util[:240]
        except OSError:
            pass
        if self.proc is not None and self.proc.poll() not in (None, 0):
            return "FFmpeg terminó sin escribir video utilizable." + cola
        return "No llegó video con datos. No se marca la sesión como grabando." + cola
    def esperar_datos(self, timeout=30, min_bytes=8192):
        """Espera un segmento con pista de video y tamaño real.
        Devuelve (ok, info_o_motivo). No declara captura si el archivo está vacío."""
        limite = time.time() + timeout
        while True:
            info = self._captura_lista(min_bytes)
            if info:
                return True, info
            muerto = self.proc is not None and self.proc.poll() is not None
            if muerto or time.time() >= limite:
                time.sleep(0.3)
                info = self._captura_lista(min_bytes)
                if info:
                    return True, info
                return False, self._motivo_sin_captura()
            time.sleep(0.4)
    def start(self):
        self.hilo = threading.Thread(target=self._bucle, daemon=True); self.hilo.start()
    def stop(self):
        self.detener_flag.set()
        fh = getattr(self, "_log_fh", None)
        if fh:
            try: fh.close()
            except Exception: pass
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired: self.proc.kill()
        if self.hilo: self.hilo.join(timeout=15)
        self._finalizar_todos()
        return self.unir()
    def bytes_en_disco(self):
        """Bytes ya escritos: la película unida si existe, si no la suma de segmentos."""
        film = self.outdir / "grabacion_completa.mp4"
        try:
            if film.is_file() and film.stat().st_size >= 8192:
                return film.stat().st_size
        except OSError:
            pass
        total = 0
        for p in self._segmentos():
            try:
                if p.is_file(): total += p.stat().st_size
            except OSError:
                continue
        return total
    def _film_valida(self, path):
        path = Path(path)
        try:
            if not path.is_file() or path.stat().st_size < 8192: return False
        except OSError:
            return False
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                               capture_output=True, text=True, timeout=20)
            return r.returncode == 0 and "video" in (r.stdout or "")
        except Exception:
            return False
    def unir(self, salida="grabacion_completa.mp4"):
        segs = [p for p in self._segmentos() if p.is_file() and p.stat().st_size > 0]
        out = self.outdir / salida
        if not segs:
            if self._film_valida(out):
                self.error_union = ""
                return str(out)
            self.error_union = "No había segmentos en la carpeta de la sesión al unir la grabación."
            return None
        lista = self.outdir / "concat.txt"
        lineas = []
        for p in segs:
            ruta = p.resolve().as_posix().replace("'", "'\\''")
            lineas.append(f"file '{ruta}'\n")
        lista.write_text("".join(lineas), encoding="utf-8")
        parcial = self.outdir / (Path(salida).stem + ".parcial.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lista),
                            "-c", "copy", "-f", "mp4", str(parcial)],
                           capture_output=True, text=True, timeout=3600, cwd=str(self.outdir))
        if r.returncode != 0 or not self._film_valida(parcial):
            try: parcial.unlink(missing_ok=True)
            except OSError: pass
            cola = [ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()]
            self.error_union = (cola[-1] if cola else "FFmpeg no pudo unir los segmentos.")[:500]
            # Los segmentos se quedan. No se reemplaza una película que ya era válida.
            if self._film_valida(out):
                return str(out)
            return None
        if self._film_valida(out) and out.stat().st_size > parcial.stat().st_size + 4096:
            try: parcial.unlink(missing_ok=True)
            except OSError: pass
            self.error_union = ""
            return str(out)
        os.replace(parcial, out)
        self.error_union = ""
        return str(out)

def segmentar_archivo(fuente, outdir, segment_time=SEG):
    """Segmenta un ARCHIVO ya grabado (sin tiempo real) con el mismo formato de segmentos.
    Usado para importar videos/OBS y para las pruebas del pipeline."""
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(fuente),
                        "-map", "0:v:0?", "-map", "0:a:0?",
                        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
                        "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-sc_threshold", "0", "-force_key_frames", f"expr:gte(t,n_forced*{segment_time})",
                        "-c:a", "aac", "-ar", "48000",
                        "-f", "segment", "-segment_time", str(segment_time), "-reset_timestamps", "1",
                        str(outdir / "seg_%05d.mp4")], capture_output=True, text=True, timeout=7200)
    if r.returncode != 0: raise RuntimeError(f"FFmpeg segmentando falló: {r.stderr[-500:]}")
    return sorted(outdir.glob("seg_*.mp4"))
