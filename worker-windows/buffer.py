"""Buffer circular de segmentos: conserva los últimos N minutos (2-10, defecto 5).
Los segmentos protegidos por un evento NUNCA se borran automáticamente."""
import json, time
from pathlib import Path

class BufferCircular:
    def __init__(self, directorio, minutos=5):
        self.dir = Path(directorio); self.dir.mkdir(parents=True, exist_ok=True)
        self.minutos = max(2, min(10, minutos))
        self.protegidos = set()
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
        self.manifest_path.write_text("[]", encoding="utf-8")
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
