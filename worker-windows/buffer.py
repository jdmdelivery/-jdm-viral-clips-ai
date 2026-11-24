"""Buffer circular de segmentos: conserva los últimos N minutos (2-10, defecto 5).
En la poda clásica (con película), los segmentos protegidos por un evento
NUNCA se borran automáticamente; en la poda rodante en vivo manda la
ventana y el «en uso» (ver podar_rodante).

Poda rodante EN VIVO (v1.3.11, orden de Jose 2026-11-23: «solo guarda los
clips, no el vídeo completo, así no se me llena el disco duro»): durante
una grabación en vivo los segmentos se podan con podar_rodante() contra
una ventana de línea de tiempo (ver recorder.VENTANA_PODA_S). La podar()
clásica de abajo es otro flujo (exige la película ya unida) y no se usa
en vivo: hasta la v1.3.10 nadie la llamaba durante la grabación y los
segmentos crecían sin límite (~0,9-1,6 GB por hora)."""
import json, time
from pathlib import Path

class BufferCircular:
    def __init__(self, directorio, minutos=5):
        self.dir = Path(directorio); self.dir.mkdir(parents=True, exist_ok=True)
        self.minutos = max(2, min(10, minutos))
        self.protegidos = set()
        self.en_uso = set()
        self.manifest_path = self.dir / "manifest.json"
        if not self.manifest_path.exists():
            self.manifest_path.write_text("[]", encoding="utf-8")
    def _manifest(self):
        return json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))
    def segmentos(self):
        """Manifest actual: los segmentos YA finalizados con su línea de
        tiempo real (inicio_s/duracion_s medidos). Lo usa el radar en vivo."""
        return self._manifest()
    def reiniciar(self):
        """Empieza una corrida NUEVA: el manifest vuelve a [] y se olvidan
        los protegidos. La carpeta de la sesión se REUTILIZA entre
        grabaciones (sesion-<id> es siempre la misma) y el manifest de la
        corrida anterior describe OTRA fuente con OTRA línea de tiempo:
        heredarlo hacía que el radar puntuara y cortara los segmentos
        viejos como si fueran de la captura actual (sesión #20, 2026-11-23:
        clips del GTA anterior dentro del en vivo de Alofoke). Solo se
        reinicia el ÍNDICE: los archivos de segmentos viejos NO se borran,
        quedan en disco como grabación anterior del usuario."""
        self.protegidos = set()
        self.en_uso = set()
        self.manifest_path.write_text("[]", encoding="utf-8")
    def marcar_en_uso(self, indices):
        """Marca segmentos que un clip EN CURSO está leyendo ahora mismo
        (el radar, al concatenar/exportar). La poda rodante nunca borra
        un segmento en uso: borrarlo a mitad de lectura rompería el clip."""
        self.en_uso.update(indices)
    def liberar_en_uso(self, indices):
        self.en_uso.difference_update(indices)
    def registrar(self, path, indice, inicio_s, duracion_s=10):
        m = self._manifest()
        m.append({"path": str(path), "indice": indice, "inicio_s": inicio_s,
                  "duracion_s": duracion_s, "creado": time.time()})
        self.manifest_path.write_text(json.dumps(m, indent=1), encoding="utf-8")
        return m[-1]
    def proteger(self, indices):
        self.protegidos.update(indices)
    def podar(self, ahora_s=None):
        """Borra segmentos fuera de la ventana salvo protegidos.
        No borra nada hasta que la película completa exista y tenga tamaño real."""
        film = self.dir / "grabacion_completa.mp4"
        try:
            if not film.is_file() or film.stat().st_size < 8192:
                return 0
        except OSError:
            return 0
        m = self._manifest()
        if not m: return 0
        fin = ahora_s if ahora_s is not None else max(x["inicio_s"] + x["duracion_s"] for x in m)
        limite = fin - self.minutos * 60
        quedan, borrados = [], 0
        for seg in m:
            viejo = (seg["inicio_s"] + seg["duracion_s"]) < limite
            if viejo and seg["indice"] not in self.protegidos:
                try: Path(seg["path"]).unlink(missing_ok=True)
                except Exception: pass
                borrados += 1
            else:
                quedan.append(seg)
        self.manifest_path.write_text(json.dumps(quedan, indent=1), encoding="utf-8")
        return borrados
    def podar_rodante(self, ventana_s, fin_s=None):
        """Poda DURANTE la grabación en vivo (v1.3.11): borra del disco y
        del manifest los segmentos cuya línea de tiempo ya quedó fuera de
        la ventana reciente (fin_s - ventana_s), sin esperar a que exista
        película alguna. NUNCA borra un segmento EN USO (un clip
        concatenándolo/leyéndolo en este instante, o parte de la ventana
        caliente abierta del radar). Los «protegidos» de clips YA
        generados no bloquean esta poda — a diferencia de la podar()
        clásica — porque ese clip ya quedó guardado como archivo propio
        en la carpeta de clips: conservar también sus segmentos de
        origen haría crecer el disco sin límite en un en vivo 24/7,
        justo lo que Jose mandó evitar. Devuelve cuántos borró."""
        m = self._manifest()
        if not m: return 0
        fin = fin_s if fin_s is not None else max(x["inicio_s"] + x["duracion_s"] for x in m)
        limite = fin - float(ventana_s)
        quedan, borrados = [], 0
        for seg in m:
            fuera = (seg["inicio_s"] + seg["duracion_s"]) <= limite
            if fuera and seg["indice"] not in self.en_uso:
                try: Path(seg["path"]).unlink(missing_ok=True)
                except Exception: pass
                borrados += 1
            else:
                quedan.append(seg)
        if borrados:
            self.manifest_path.write_text(json.dumps(quedan, indent=1), encoding="utf-8")
        return borrados
