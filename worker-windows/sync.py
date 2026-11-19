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
    def heartbeat(self, version=None):
        try:
            if version is not None:
                r = self._req("POST", "/workers/heartbeat", json={"version": version})
            else:
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
    def reportar_en_vivo(self, session_id, events, clips):
        """Ingesta del radar EN VIVO: eventos y clips detectados DURANTE la
        grabación (POST /workers/sessions/{id}/live-events, token de
        trabajador). Devuelve el resumen de la API (guardados/duplicados)."""
        r = self._req("POST", f"/workers/sessions/{session_id}/live-events",
                      json={"events": events, "clips": clips})
        r.raise_for_status()
        return r.json()
    def reportar_progreso(self, session_id, bytes_captura):
        """Actualiza el contador de bytes de la sesión. Si la red falla, no tumba el bucle."""
        try:
            r = self._req("POST", f"/workers/sessions/{session_id}/progreso",
                          json={"bytes": int(bytes_captura or 0)})
            return r.status_code == 200
        except requests.RequestException:
            return False
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
    def completar_detalle(self, job_id, result):
        """Igual que completar(), pero devuelve el JSON de la API (p. ej.
        {"ok": True, "clip_ids": [...]}) cuando la compleción llega; si la
        red falla, encola el resultado offline y devuelve None."""
        try:
            r = self._req("POST", f"/jobs/{job_id}/complete", json={"result": result})
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            self.guardar_pendiente({"accion": "complete", "job_id": job_id, "result": result})
            return None
    def subir_clip(self, clip_id, ruta, reintentos=3):
        """Sube la COPIA de un clip al servidor (POST /clips/{id}/archivo,
        token de trabajador) en streaming: el archivo se envía como cuerpo
        crudo desde disco, sin cargarlo entero en memoria. Reintenta ante
        fallos de red o 5xx; un 4xx (404/413…) no se reintenta. Devuelve el
        JSON de la API; si todo falla, lanza la última excepción."""
        import time as _time
        ultimo = None
        for intento in range(1, max(1, reintentos) + 1):
            try:
                with open(ruta, "rb") as f:
                    r = requests.post(self.base + f"/clips/{clip_id}/archivo",
                                      headers={**self.headers, "Content-Type": "video/mp4"},
                                      data=f, timeout=600)
                if r.status_code < 500:
                    r.raise_for_status()  # 4xx: error definitivo, sin reintento
                    return r.json()
                ultimo = requests.HTTPError(f"HTTP {r.status_code} al subir el clip {clip_id}")
            except requests.RequestException as e:
                ultimo = e
            if intento < max(1, reintentos):
                _time.sleep(2 * intento)
        raise ultimo
    def subir_subtitulado(self, clip_id, ruta, reintentos=3):
        """Sube el MP4 con subtítulos ya quemados. No envía la ruta local:
        el servidor lo guarda con la clave del clip."""
        import time as _time
        ultimo = None
        for intento in range(1, max(1, reintentos) + 1):
            try:
                with open(ruta, "rb") as f:
                    r = requests.post(self.base + f"/clips/{clip_id}/subtitulado",
                                      headers={**self.headers, "Content-Type": "video/mp4"},
                                      data=f, timeout=600)
                if r.status_code < 500:
                    r.raise_for_status()
                    return r.json()
                ultimo = requests.HTTPError(f"HTTP {r.status_code} al subir subtítulos del clip {clip_id}")
            except requests.RequestException as e:
                ultimo = e
            if intento < max(1, reintentos):
                _time.sleep(2 * intento)
        raise ultimo
    def reportar_subtitulos(self, file_path, estado, error="", texto="", subtitulo_path=""):
        """Avisa al panel el resultado de los subtítulos. Nunca lanza: un fallo
        de red no afecta a la grabación ni borra el clip original."""
        try:
            r = self._req("POST", "/workers/clips/subtitulos", json={
                "file_path": file_path, "estado": estado, "error": error or "",
                "texto": texto or "", "subtitulo_path": ""})
            if r.status_code >= 400:
                return {"ok": False, "motivo": f"HTTP {r.status_code}"}
            return r.json()
        except requests.RequestException as e:
            return {"ok": False, "motivo": str(e)}
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
