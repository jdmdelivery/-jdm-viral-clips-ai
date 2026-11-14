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
            self.manifest_path.write_text("[]")
    def _manifest(self):
        return json.loads(self.manifest_path.read_text())
    def registrar(self, path, indice, inicio_s, duracion_s=10):
        m = self._manifest()
        m.append({"path": str(path), "indice": indice, "inicio_s": inicio_s,
                  "duracion_s": duracion_s, "creado": time.time()})
        self.manifest_path.write_text(json.dumps(m, indent=1))
        return m[-1]
    def proteger(self, indices):
        self.protegidos.update(indices)
    def podar(self, ahora_s=None):
        """Borra segmentos fuera de la ventana salvo protegidos. Devuelve cuántos borró."""
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
        self.manifest_path.write_text(json.dumps(quedan, indent=1))
        return borrados
