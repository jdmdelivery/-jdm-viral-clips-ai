"""Cliente de sincronización con la API (HTTPS). Cola local offline en JSON:
si no hay internet, los resultados se guardan y se envían al reconectar."""
import json
from pathlib import Path
import requests

class ApiClient:
    def __init__(self, base_url, worker_token, cola_path=None):
        self.base = base_url.rstrip("/")
        self.headers = {"X-Worker-Token": worker_token}
        self.cola_path = Path(cola_path or "cola-offline.json")
    def _req(self, metodo, ruta, **kw):
        return requests.request(metodo, self.base + ruta, headers=self.headers, timeout=30, **kw)
    def heartbeat(self):
        try:
            r = self._req("POST", "/workers/heartbeat")
            return r.status_code == 200
        except requests.RequestException:
            return False
    def descargar_proyecto(self, project_id, destino):
        """Descarga el video de un proyecto desde la API (GET /projects/{id}/file)
        en streaming: escribe a un .part y renombra atómicamente al completar."""
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = Path(str(destino) + ".part")
        with requests.get(self.base + f"/projects/{project_id}/file", headers=self.headers,
                          timeout=600, stream=True) as r:
            r.raise_for_status()
            with open(parcial, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk: f.write(chunk)
        parcial.replace(destino)
        return destino
    def reclamar(self):
        r = self._req("POST", "/jobs/claim")
        r.raise_for_status()
        return r.json().get("job")
    def completar(self, job_id, result):
        try:
            r = self._req("POST", f"/jobs/{job_id}/complete", json={"result": result})
            r.raise_for_status()
            return True
        except requests.RequestException:
            self.guardar_pendiente({"accion": "complete", "job_id": job_id, "result": result})
            return False
    def fallar(self, job_id, error):
        try:
            self._req("POST", f"/jobs/{job_id}/fail", json={"error": str(error)})
        except requests.RequestException:
            self.guardar_pendiente({"accion": "fail", "job_id": job_id, "error": str(error)})
    def guardar_pendiente(self, item):
        cola = json.loads(self.cola_path.read_text(encoding="utf-8-sig")) if self.cola_path.exists() else []
        cola.append(item)
        self.cola_path.write_text(json.dumps(cola, indent=1), encoding="utf-8")
    def sincronizar_pendientes(self):
        if not self.cola_path.exists(): return 0
        cola = json.loads(self.cola_path.read_text(encoding="utf-8-sig"))
        enviados = 0
        for item in cola:
            try:
                if item["accion"] == "complete":
                    self._req("POST", f"/jobs/{item['job_id']}/complete", json={"result": item["result"]}).raise_for_status()
                else:
                    self._req("POST", f"/jobs/{item['job_id']}/fail", json={"error": item["error"]}).raise_for_status()
                enviados += 1
            except requests.RequestException:
                break
        self.cola_path.write_text(json.dumps(cola[enviados:], indent=1), encoding="utf-8")
        return enviados
