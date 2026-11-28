"""Grabación con FFmpeg en segmentos de 10 s. La grabación corre en un proceso
separado: NUNCA se detiene porque la IA esté procesando un clip.
Incluye reconexión con reintentos y unión de segmentos al finalizar.

Huérfanos (auditoría del trabajo #71, 2026-11-20): si el motor muere o se
relanza sin pasar por stop() (os._exit del actualizador, cierre a la fuerza),
su FFmpeg hijo quedaba vivo escribiendo segmentos en la carpeta de la sesión
sin que nadie lo gobernara. Ahora el Recorder anota el PID de su FFmpeg en
ffmpeg.pid dentro de la carpeta de la sesión, y al arrancar una grabación
nueva en esa carpeta se barre el huérfano anterior: solo se termina el
proceso si sigue vivo Y es de verdad un ffmpeg (nunca otro programa que
reutilizara el PID).

Retención (v1.3.11, orden de Jose 2026-11-23: «solo guarda los clips, no
el vídeo completo, así no se me llena el disco duro»): hasta la v1.3.10
NINGÚN segmento se borraba durante la grabación (la poda del buffer
exigía la película ya unida y nadie la llamaba en vivo) y al detener se
creaba siempre grabacion_completa.mp4 dejando TAMBIÉN los segmentos:
~0,9-1,6 GB por hora, el doble tras detener. Ahora, solo en vivo:
poda rodante de segmentos contra VENTANA_PODA_S y stop() sin película
(ver podar_rodante, stop y descartar_segmentos)."""
import json, os, subprocess, threading, time
from pathlib import Path
from procesos import SIN_VENTANA
from buffer import BufferCircular

SEG = 10
ARCHIVO_PID = "ffmpeg.pid"
# Ventana de segmentos que se conserva durante una grabación EN VIVO
# (único sitio donde se define; Recorder la acepta como parámetro y el
# trabajador la puede sobreescribir con la clave «ventana_poda_s»).
# Debe cubrir con margen todo lo que el radar puede necesitar hacia
# atrás: su ventana de análisis cierra con el primer segmento flojo y el
# clip llega a 120 s con pre 30 / post 25 de contexto (clipper), más el
# intervalo del radar (30 s) y el tiempo de generar el clip: lo
# estrictamente necesario ronda 4 minutos; 45 minutos dan ~11x de
# margen y acotan la carpeta de la sesión a ~0,7-1,2 GB.
VENTANA_PODA_S = 45 * 60
INTERVALO_PODA_S = 20
# Rotación de la captura en vivo (v1.3.15): ningún FFmpeg graba más de
# este tiempo de corrido. Al cumplirse, el bucle lo reinicia con señal
# fresca (sin gastar reintentos: no es una caída). Acota el desfase
# audio/video que las señales separadas de plataforma acumulan con las
# horas: cada reinicio vuelve a alinear ambas pistas desde cero.
MAX_CAPTURA_CONTINUA_S = 30 * 60

def _pid_vivo(pid):
    """True si el proceso `pid` existe ahora mismo. Nunca lanza."""
    if not pid or pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                codigo = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(codigo)):
                    return False
                return codigo.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False
    except Exception:
        return False

def _es_ffmpeg(pid):
    """True si el proceso `pid` es un ejecutable ffmpeg. Protege contra la
    reutilización de PID: un PID viejo anotado en ffmpeg.pid podría ser hoy
    otro programa, y ese NO se toca. Nunca lanza."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                buf = ctypes.create_unicode_buffer(32768)
                tam = wintypes.DWORD(len(buf))
                if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(tam)):
                    return False
                return "ffmpeg" in buf.value.lower()
            finally:
                kernel32.CloseHandle(handle)
        cmdline = Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        return "ffmpeg" in cmdline.lower()
    except Exception:
        return False

def _terminar_proceso(pid):
    """Termina el proceso `pid` (y en Windows también su árbol: el FFmpeg
    de grabación no tiene hijos propios, pero taskkill /T cubre el caso).
    Nunca lanza."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                           capture_output=True, timeout=15, creationflags=SIN_VENTANA)
        else:
            import signal
            os.kill(int(pid), signal.SIGTERM)
            for _ in range(20):
                if not _pid_vivo(pid):
                    break
                time.sleep(0.1)
            if _pid_vivo(pid):
                os.kill(int(pid), signal.SIGKILL)
    except Exception:
        pass

def limpiar_ffmpeg_huerfano(outdir):
    """Barre el FFmpeg huérfano de una corrida anterior en `outdir`.

    Lee ffmpeg.pid: si el proceso ya no existe, solo se borra el archivo
    viejo; si existe y es un ffmpeg, se termina (es el hijo de un motor
    que murió sin detenerlo) y se borra el archivo; si existe pero NO es
    un ffmpeg (PID reutilizado), no se toca ni el proceso ni el archivo.
    Devuelve True si se terminó un huérfano. Nunca lanza."""
    ruta = Path(outdir) / ARCHIVO_PID
    try:
        if not ruta.is_file():
            return False
        pid = int((ruta.read_text(encoding="utf-8") or "").strip() or 0)
    except Exception:
        return False
    if pid == os.getpid():
        return False
    if not _pid_vivo(pid):
        try: ruta.unlink(missing_ok=True)
        except OSError: pass
        return False
    if not _es_ffmpeg(pid):
        return False
    _terminar_proceso(pid)
    try: ruta.unlink(missing_ok=True)
    except OSError: pass
    return True

def duracion_real(path):
    """Duración real de un segmento cerrado (el muxer corta en keyframes: no asumir 10 s exactos)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=30, creationflags=SIN_VENTANA)
        return float(r.stdout.strip())
    except Exception:
        return float(SEG)

def _sondear_inicio(url, timeout=15):
    """Marca de tiempo de arranque de una señal (ffprobe, start_time).

    Las dos señales separadas de una plataforma en vivo comparten UNA
    línea de tiempo (la del directo): la diferencia entre sus marcas de
    arranque ES el corrimiento con el que nacen desalineadas. Devuelve
    None si no se pudo medir (sin red, URL muerta, salida ilegible):
    ante la duda no se compensa nada.
    """
    cmd = ["ffprobe", "-v", "error"]
    if str(url).startswith("http"):
        cmd += ["-rw_timeout", str(int(timeout * 1_000_000))]
    cmd += ["-show_entries", "stream=start_time", "-of", "csv=p=0", str(url)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout + 5, creationflags=SIN_VENTANA)
    except Exception:
        return None
    for linea in (r.stdout or "").splitlines():
        try:
            return float(linea.strip().split(",")[0])
        except (ValueError, IndexError):
            continue
    return None


UMBRAL_DESFASE_S = 0.25   # por debajo, el corrimiento es inaudible
MAX_DESFASE_S = 8.0       # por encima, la medición no es de fiar


def _desfase_entre(inicio_video, inicio_audio):
    """Compensación de nacimiento entre las dos señales (v1.3.18).

    Jose lo describió el 2026-11-27: «primero habla y después a los dos
    segundos se escucha lo que dijo» — la voz ~2 s tarde, FIJO desde el
    primer segundo: las dos señales se pegaban cada una desde su cero
    sin mirar la línea de tiempo común. Con las marcas de arranque
    medidas, la señal que va ADELANTADA en contenido (la de MAYOR
    marca: en su cero ya muestra un momento más nuevo) se retrasa con
    «-itsoffset» la diferencia, y ambas nacen alineadas. Devuelve
    (índice_de_entrada, segundos) o None si no hay nada que corregir
    (sin medición, corrimiento inaudible o medición absurda).
    """
    if inicio_video is None or inicio_audio is None:
        return None
    d = float(inicio_video) - float(inicio_audio)
    if abs(d) < UMBRAL_DESFASE_S or abs(d) > MAX_DESFASE_S:
        return None
    if d > 0:
        return (0, round(d, 3))
    return (1, round(-d, 3))


def _cmd_grabar(fuente, outdir, start_number=0, en_vivo=False, desfase=None):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    # La fuente puede traer DOS URLs unidas por salto de línea (video y
    # audio por separado, desde resolver.py): cada una es una entrada de
    # FFmpeg, el video sale de la primera y el audio de la última. Con una
    # sola URL el comando es el histórico.
    entradas = [l.strip() for l in str(fuente).splitlines() if l.strip()]
    if not entradas: entradas = [str(fuente)]
    # Sincronía audio/video con DOS señales en vivo (el par «bv*+ba» del
    # resolver): cada una trae su propio reloj y la voz se iba quedando
    # detrás del video, peor a más horas de sesión.
    #
    # v1.3.15 lo intentó con «use_wallclock_as_timestamps» (sellar cada
    # paquete con la hora de LLEGADA) y salió mal: las señales en vivo
    # llegan por pedazos, los paquetes de un pedazo comparten casi la
    # misma hora y la imagen resultante SE CONGELA a tramos mientras el
    # audio sigue (visto por Jose en una grabación real el 2026-11-27).
    # v1.3.17 lo retira y usa el mecanismo seguro: los tiempos propios
    # de cada señal NO se tocan (la imagen jamás se congela por esto) y
    # el AUDIO se ajusta con «aresample=async=1», que estira o encoge el
    # audio de forma imperceptible (muestras por segundo) para que siga
    # el reloj del video durante horas. La rotación de captura cada
    # MAX_CAPTURA_CONTINUA_S (v1.3.15) se queda: acota lo que cualquier
    # desfase residual pueda acumular. Solo en vivo y con dos entradas:
    # una señal combinada ya trae audio y video en un solo reloj.
    dos_senales_vivo = en_vivo and len(entradas) > 1
    for i, entrada in enumerate(entradas):
        if desfase and desfase[0] == i:
            cmd += ["-itsoffset", f"{desfase[1]:.3f}"]
        if en_vivo: cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        cmd += ["-i", entrada]
    cmd += ["-map", "0:v:0?", "-map", f"{len(entradas) - 1}:a:0?",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-sc_threshold", "0",
            "-force_key_frames", f"expr:gte(t,n_forced*{SEG})"]
    if dos_senales_vivo: cmd += ["-af", "aresample=async=1"]
    cmd += ["-c:a", "aac", "-ar", "48000",
            "-f", "segment", "-segment_time", str(SEG), "-reset_timestamps", "1",
            "-segment_start_number", str(start_number),
            str(Path(outdir) / "seg_%05d.mp4")]
    return cmd

class Recorder:
    def __init__(self, fuente, outdir, buffer_min=5, en_vivo=True, max_reintentos=5, resolver=None,
                 ventana_poda_s=VENTANA_PODA_S):
        # resolver (opcional): callable sin argumentos que devuelve una URL de
        # señal FRESCA. Las señales resueltas de plataformas rotan/caducan;
        # en cada reconexión se pide una nueva en vez de reutilizar la muerta.
        # Sin resolver, el comportamiento es idéntico al histórico.
        self.fuente, self.outdir = fuente, Path(outdir)
        self.resolver = resolver
        self.ventana_poda_s = float(ventana_poda_s)
        self._max_indice = -1   # mayor índice de segmento visto en la corrida
        self._ultima_poda = 0.0
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.buffer = BufferCircular(self.outdir, minutos=buffer_min)
        # Corrida nueva = manifest nuevo: la carpeta de la sesión se
        # reutiliza y el manifest de la captura anterior (otra fuente,
        # otra línea de tiempo) NO se hereda. Los archivos viejos no se
        # tocan; simplemente dejan de contar como parte de esta corrida.
        self.buffer.reiniciar()
        self.en_vivo, self.max_reintentos = en_vivo, max_reintentos
        self.proc = None; self.detener_flag = threading.Event()
        self.hilo = None; self.reintentos = 0; self.inicio = None
        self._t_inicio = time.time()  # inicio de ESTA corrida (filtro de segmentos viejos)
        self._cursor = 0.0; self._finalizados = set(); self.error_union = ""
    def _segmentos(self):
        return sorted(self.outdir.glob("seg_*.mp4"))
    def _segmentos_corrida(self):
        """Solo los segmentos de ESTA corrida (mtime >= inicio de la
        corrida, con 1 s de tolerancia: el mismo criterio que la guarda
        de _captura_lista de la v1.3.3). La carpeta de la sesión se
        reutiliza entre grabaciones y conserva seg_*.mp4 de capturas
        anteriores: sin este filtro, el registro en el manifest, la
        unión y el contador de bytes mezclaban dos fuentes distintas
        (sesión #20, 2026-11-23: segmentos del GTA anterior dentro del
        en vivo de Alofoke). Un segmento que el FFmpeg actual sobrescribe
        recibe mtime fresco y SÍ cuenta como de esta corrida."""
        corte = self._t_inicio - 1.0
        propios = []
        for p in self._segmentos():
            try:
                if p.stat().st_mtime >= corte:
                    propios.append(p)
            except OSError:
                continue
        return propios
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
        if idx > self._max_indice: self._max_indice = idx
        d = duracion_real(p)
        self.buffer.registrar(str(p), idx, round(self._cursor, 3), round(d, 3))
        self._cursor += d; self._finalizados.add(p.name)
    def _registrar_nuevos(self, vistos):
        # Se finalizan todos menos el último (que aún puede estar
        # escribiéndose) — y SOLO los de esta corrida: registrar un
        # segmento viejo le pondría la línea de tiempo de la captura
        # actual y el radar lo cortaría como si fuera de esta fuente.
        segs = self._segmentos_corrida()
        for p in segs[:-1]:
            if p.name not in self._finalizados:
                self._finalizar(p); vistos.add(p.name)
    def _finalizar_todos(self):
        for p in self._segmentos_corrida():
            if p.name not in self._finalizados:
                self._finalizar(p)
    def podar_rodante(self):
        """Poda rodante de segmentos DURANTE la grabación en vivo
        (v1.3.11): conserva solo la ventana reciente VENTANA_PODA_S y
        borra el resto, para que una grabación 24/7 no llene el disco
        (antes: ~0,9-1,6 GB por hora, sin límite). Solo aplica en vivo:
        una fuente de archivo se segmenta a toda velocidad y su película
        se une al terminar — podarla por línea de tiempo la rompería.

        Un segmento solo se borra si ya quedó fuera de la ventana Y no
        está en uso por un clip en curso (buffer.podar_rodante). Además barre
        los seg_*.mp4 sueltos que no están en el manifest (restos de
        corridas anteriores o parciales huérfanos) cuando su mtime ya
        salió de la ventana: el segmento que el FFmpeg está escribiendo
        ahora tiene mtime fresco y nunca cae aquí. La grabación no se
        detiene ni se toca: solo se borran archivos ya cerrados y viejos.
        Devuelve cuántos archivos borró."""
        if not self.en_vivo:
            return 0
        borrados = 0
        entradas = self.buffer.segmentos()
        if entradas:
            fin = max(e["inicio_s"] + e["duracion_s"] for e in entradas)
            borrados += self.buffer.podar_rodante(self.ventana_poda_s, fin)
        en_manifest = {Path(e["path"]).name for e in self.buffer.segmentos()}
        corte = time.time() - self.ventana_poda_s
        for p in self._segmentos():
            if p.name in en_manifest:
                continue
            try:
                if p.stat().st_mtime < corte:
                    p.unlink(missing_ok=True)
                    borrados += 1
                    self._finalizados.discard(p.name)
            except OSError:
                continue
        return borrados
    def descartar_segmentos(self):
        """Cierre de la política de disco en vivo (v1.3.11): al detener
        una sesión en vivo no queda NI película NI segmentos — solo los
        clips generados (que viven en la carpeta de clips, intactos).
        Borra los segmentos de ESTA corrida y reinicia el manifest. Si
        alguna ruta hubiera creado grabacion_completa.mp4 durante ESTA
        corrida (mtime posterior a su inicio), también se borra; una
        película de una corrida ANTERIOR no se toca (es del usuario).
        Se llama DESPUÉS de la pasada final del radar, que aún necesita
        los segmentos para cerrar su última ventana. Devuelve cuántos
        segmentos borró."""
        n = 0
        for p in self._segmentos_corrida():
            try:
                p.unlink(missing_ok=True); n += 1
            except OSError:
                pass
        film = self.outdir / "grabacion_completa.mp4"
        try:
            if film.is_file() and film.stat().st_mtime >= self._t_inicio - 1.0:
                film.unlink(missing_ok=True)
        except OSError:
            pass
        self._finalizados.clear()
        self.buffer.reiniciar()
        return n
    def _refrescar_fuente(self):
        """Pide una URL fresca al resolver (si hay). Si el resolver falla, se
        conserva la última URL conocida y el reintento sigue su curso."""
        if not self.resolver: return
        try:
            nueva = self.resolver()
            if nueva: self.fuente = nueva
        except Exception:
            pass
    def _anotar_pid(self):
        """Anota el PID del FFmpeg en curso en ffmpeg.pid (ver docstring del
        módulo): es la única pista fiable para barrerlo si este motor muere
        sin pasar por stop()."""
        try:
            if self.proc and self.proc.pid:
                (self.outdir / ARCHIVO_PID).write_text(str(self.proc.pid), encoding="utf-8")
        except OSError:
            pass
    def _borrar_pid(self):
        try:
            ruta = self.outdir / ARCHIVO_PID
            if self.proc and ruta.is_file():
                if (ruta.read_text(encoding="utf-8") or "").strip() == str(self.proc.pid):
                    ruta.unlink(missing_ok=True)
        except OSError:
            pass
    def _bucle(self):
        self.inicio = time.time()
        vistos = set()
        start_number = 0
        while not self.detener_flag.is_set():
            log = open(self.outdir / "ffmpeg.log", "ab")
            # Compensación de nacimiento (v1.3.18): con dos señales en
            # vivo se mide la marca de arranque de cada una y se retrasa
            # la adelantada (ver _desfase_entre). Se mide en CADA
            # lanzamiento: reconexión y rotación traen URLs frescas y el
            # corrimiento puede cambiar con ellas. Si la medición falla,
            # desfase=None y la grabación sigue como siempre.
            desfase = None
            if self.en_vivo:
                entradas = [l.strip() for l in str(self.fuente).splitlines() if l.strip()]
                if len(entradas) > 1:
                    desfase = _desfase_entre(_sondear_inicio(entradas[0]),
                                             _sondear_inicio(entradas[-1]))
                    if desfase:
                        print(f"Grabación: desfase entre señales de {desfase[1]:.2f} s; "
                              f"se retrasa la entrada {desfase[0]} para alinear audio y video.")
            self.proc = subprocess.Popen(_cmd_grabar(self.fuente, self.outdir, start_number, self.en_vivo,
                                                     desfase=desfase),
                                         stdout=subprocess.DEVNULL, stderr=log, creationflags=SIN_VENTANA)
            self._anotar_pid()
            self._log_fh = log
            t_corrida = time.time()
            rotacion = False
            while self.proc.poll() is None and not self.detener_flag.is_set():
                time.sleep(0.5)
                self._registrar_nuevos(vistos)
                # Poda rodante (v1.3.11): cada INTERVALO_PODA_S se borran
                # los segmentos que ya salieron de la ventana. Va en este
                # mismo hilo: nunca compite con la reconexión ni detiene
                # la grabación (solo borra archivos cerrados y viejos).
                if time.time() - self._ultima_poda >= INTERVALO_PODA_S:
                    self._ultima_poda = time.time()
                    try: self.podar_rodante()
                    except Exception: pass
                # Rotación (v1.3.15): cumplido el máximo de captura
                # continua, se cierra este FFmpeg y el bucle exterior lo
                # reinicia con señal fresca (ver MAX_CAPTURA_CONTINUA_S).
                if self.en_vivo and not rotacion and time.time() - t_corrida >= MAX_CAPTURA_CONTINUA_S:
                    rotacion = True
                    try: self.proc.terminate()
                    except Exception: pass
            self._borrar_pid()
            self._registrar_nuevos(vistos)
            if self.detener_flag.is_set(): break
            # Salida inesperada: reconexión (solo fuentes en vivo; un archivo que terminó no reintenta)
            segs = self._segmentos_corrida()
            if rotacion and segs:
                # Reinicio programado, no una caída: no gasta reintentos.
                start_number = max(len(segs), self._max_indice + 1)
                self._refrescar_fuente()  # señal fresca también al rotar
                time.sleep(2)
                continue
            if not self.en_vivo or not segs: break
            self.reintentos += 1
            if self.reintentos > self.max_reintentos: break
            # Nunca reutilizar el índice de un segmento ya podado: su
            # nombre sigue en _finalizados y el nuevo se saltaría el
            # registro (el radar no lo vería). Sin poda, len(segs) manda
            # como siempre.
            start_number = max(len(segs), self._max_indice + 1)
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
                               capture_output=True, text=True, timeout=15, creationflags=SIN_VENTANA)
            return r.returncode == 0 and selector[0] in (r.stdout or "")
        except Exception:
            return False
    def _captura_lista(self, min_bytes):
        for p in self._segmentos():
            try:
                st = p.stat(); tam = st.st_size
            except OSError:
                continue
            if tam < min_bytes:
                continue
            # Solo vale video de ESTA corrida: la carpeta de la sesión se
            # reutiliza entre grabaciones y un segmento viejo (de una
            # captura anterior, o escrito por un FFmpeg huérfano cuyo
            # motor ya murió) no prueba que esta captura esté escribiendo.
            # Sin este filtro, esperar_datos confirmaba «grabando» con
            # datos viejos aunque el FFmpeg nuevo hubiera muerto al
            # arrancar. Un segmento reescrito por el FFmpeg actual trae
            # mtime fresco y sí cuenta.
            if st.st_mtime < self._t_inicio - 1.0:
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
        # Antes de grabar: si una corrida anterior de este motor murió sin
        # detener su FFmpeg, ese huérfano seguiría escribiendo segmentos en
        # ESTA carpeta y mezclándose con la captura nueva. Se barre primero.
        if limpiar_ffmpeg_huerfano(self.outdir):
            print(f"Grabación: se terminó un FFmpeg huérfano de una corrida anterior en {self.outdir}.")
        self._t_inicio = time.time()
        self.hilo = threading.Thread(target=self._bucle, daemon=True); self.hilo.start()
    def stop(self, crear_pelicula=True):
        """Detiene la captura y finaliza los segmentos. Con
        crear_pelicula=False NO se une la grabación completa (política
        de disco en vivo de la v1.3.11: solo se conservan los clips) y
        devuelve None; el descarte de los segmentos es paso aparte
        (descartar_segmentos), porque la pasada final del radar aún los
        necesita después de parar el Recorder."""
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
        if not crear_pelicula:
            return None
        return self.unir()
    def bytes_en_disco(self):
        """Bytes de ESTA captura: la suma de los segmentos en disco.

        La película unida solo cuenta cuando NO hay segmentos. La carpeta
        de la sesión se reutiliza entre grabaciones: una
        grabacion_completa.mp4 de una captura ANTERIOR no es progreso de
        esta, y devolverla primero congelaba el contador del panel en el
        tamaño de la película vieja (316.8 MB fijos) mientras la captura
        real seguía escribiendo segmentos (caso trabajo #71, 2026-11-20).
        Tras unir, los segmentos siguen en disco, así que la suma sigue
        representando el total capturado."""
        total = 0
        for p in self._segmentos_corrida():
            try:
                if p.is_file(): total += p.stat().st_size
            except OSError:
                continue
        if total > 0:
            return total
        film = self.outdir / "grabacion_completa.mp4"
        try:
            if film.is_file(): return film.stat().st_size
        except OSError:
            pass
        return 0
    def _film_valida(self, path):
        path = Path(path)
        try:
            if not path.is_file() or path.stat().st_size < 8192: return False
        except OSError:
            return False
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                               capture_output=True, text=True, timeout=20, creationflags=SIN_VENTANA)
            return r.returncode == 0 and "video" in (r.stdout or "")
        except Exception:
            return False
    def unir(self, salida="grabacion_completa.mp4"):
        # Solo los segmentos de ESTA corrida: unir también los de una
        # captura anterior dejaría la película de esta sesión empezando
        # con la fuente vieja (la misma contaminación que sufrió el
        # radar en la sesión #20).
        segs = [p for p in self._segmentos_corrida() if p.is_file() and p.stat().st_size > 0]
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
                           capture_output=True, text=True, timeout=3600, cwd=str(self.outdir), creationflags=SIN_VENTANA)
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
                        str(outdir / "seg_%05d.mp4")], capture_output=True, text=True, timeout=7200, creationflags=SIN_VENTANA)
    if r.returncode != 0: raise RuntimeError(f"FFmpeg segmentando falló: {r.stderr[-500:]}")
    return sorted(outdir.glob("seg_*.mp4"))
