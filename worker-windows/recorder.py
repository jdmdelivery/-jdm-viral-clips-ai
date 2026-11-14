"""Grabación con FFmpeg en segmentos de 10 s. La grabación corre en un proceso
separado: NUNCA se detiene porque la IA esté procesando un clip.
Incluye reconexión con reintentos y unión de segmentos al finalizar."""
import json, subprocess, threading, time
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
        self._cursor = 0.0; self._finalizados = set()
    def _segmentos(self):
        return sorted(self.outdir.glob("seg_*.mp4"))
    def _finalizar(self, p):
        """Registra un segmento YA cerrado con su línea de tiempo real (inicio acumulado + duración medida)."""
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
            self.proc = subprocess.Popen(_cmd_grabar(self.fuente, self.outdir, start_number, self.en_vivo),
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    def start(self):
        self.hilo = threading.Thread(target=self._bucle, daemon=True); self.hilo.start()
    def stop(self):
        self.detener_flag.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try: self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired: self.proc.kill()
        if self.hilo: self.hilo.join(timeout=15)
        self._finalizar_todos()
        return self.unir()
    def unir(self, salida="grabacion_completa.mp4"):
        segs = self._segmentos()
        if not segs: return None
        lista = self.outdir / "concat.txt"
        lista.write_text("".join(f"file '{p.name}'\n" for p in segs))
        out = self.outdir / salida
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lista),
                            "-c", "copy", str(out)], capture_output=True, text=True, timeout=3600)
        return str(out) if r.returncode == 0 and out.exists() else None

def segmentar_archivo(fuente, outdir, segment_time=SEG):
    """Segmenta un ARCHIVO ya grabado (sin tiempo real) con el mismo formato de segmentos.
    Usado para importar videos/OBS y para las pruebas del pipeline."""
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(fuente),
                        "-map", "0:v:0?", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-sc_threshold", "0", "-force_key_frames", f"expr:gte(t,n_forced*{segment_time})",
                        "-c:a", "aac", "-ar", "48000",
                        "-f", "segment", "-segment_time", str(segment_time), "-reset_timestamps", "1",
                        str(outdir / "seg_%05d.mp4")], capture_output=True, text=True, timeout=7200)
    if r.returncode != 0: raise RuntimeError(f"FFmpeg segmentando falló: {r.stderr[-500:]}")
    return sorted(outdir.glob("seg_*.mp4"))
