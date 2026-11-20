"""Tests del helper de procesos sin ventana (worker-windows/procesos.py),
de la auditoría estática de lanzamientos y de la guarda anti-bucle de
reinicios del actualizador.

El bug (2026-11-19): el motor corre con pythonw (sin consola) y NINGÚN
subprocess del trabajador pasaba CREATE_NO_WINDOW, así que cada
ffmpeg/ffprobe abría una consola negra visible; durante una grabación
eso era un parpadeo continuo de ventanas sobre el escritorio de Jose.
"""
import hashlib, json, threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import actualizador
import procesos
import worker as worker_mod

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_TRABAJADOR = RAIZ / "worker-windows"


# ---------------------------------------------------------------------------
# Helper procesos.py
# ---------------------------------------------------------------------------
def test_sin_ventana_segun_plataforma():
    if procesos.os.name == "nt":
        assert procesos.SIN_VENTANA == 0x08000000  # CREATE_NO_WINDOW
    else:
        # En POSIX creationflags debe ser 0 (otro valor es inválido).
        assert procesos.SIN_VENTANA == 0


def test_flags_combina_extra_solo_en_windows():
    if procesos.os.name == "nt":
        assert procesos.flags() == 0x08000000
        assert procesos.flags(0x00004000) == 0x08004000
    else:
        assert procesos.flags() == 0
        assert procesos.flags(0x00004000) == 0


# ---------------------------------------------------------------------------
# Auditoría estática: ningún lanzamiento sin creationflags
# ---------------------------------------------------------------------------
def _llamadas_sin_flags(texto):
    """Devuelve las llamadas subprocess.run/Popen cuyo texto (paréntesis
    balanceados) NO menciona creationflags."""
    malas = []
    for marca in ("subprocess.run(", "subprocess.Popen("):
        i = 0
        while True:
            j = texto.find(marca, i)
            if j < 0:
                break
            apertura = j + len(marca) - 1
            profundidad, k, en_cadena, escape = 0, apertura, None, False
            while k < len(texto):
                c = texto[k]
                if en_cadena:
                    if escape:
                        escape = False
                    elif c == "\\":
                        escape = True
                    elif c == en_cadena:
                        en_cadena = None
                else:
                    if c in "\"'":
                        en_cadena = c
                    elif c == "(":
                        profundidad += 1
                    elif c == ")":
                        profundidad -= 1
                        if profundidad == 0:
                            break
                k += 1
            if "creationflags" not in texto[apertura:k + 1]:
                malas.append(texto[j:k + 1][:80])
            i = j + len(marca)
    return malas


def test_ningun_lanzamiento_sin_creationflags():
    # procesos.py es el helper (no lanza nada); el resto del trabajador
    # debe pasar creationflags en CADA subprocess.run/Popen.
    for ruta in sorted(CARPETA_TRABAJADOR.glob("*.py")):
        if ruta.name == "procesos.py":
            continue
        malas = _llamadas_sin_flags(ruta.read_text(encoding="utf-8"))
        assert not malas, f"{ruta.name} lanza procesos sin suprimir la ventana: {malas}"


def test_subtitulos_baja_prioridad_incluye_sin_ventana():
    import subtitulos
    if procesos.os.name == "nt":
        assert subtitulos._ffmpeg_baja_prioridad() == 0x08004000
    else:
        assert subtitulos._ffmpeg_baja_prioridad() == 0


# ---------------------------------------------------------------------------
# Guarda anti-bucle de reinicios (worker.py) + huella (actualizador.py)
# ---------------------------------------------------------------------------
def test_guardia_reinicio_recuerda_huella(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_mod, "__file__", str(tmp_path / "worker.py"))
    assert worker_mod._reinicio_ya_aplicado("huella-x") is False
    assert worker_mod._reinicio_ya_aplicado("") is False
    worker_mod._registrar_reinicio("huella-x")
    assert worker_mod._reinicio_ya_aplicado("huella-x") is True
    assert worker_mod._reinicio_ya_aplicado("huella-y") is False


class _ServidorSilencioso(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _servir(directorio):
    manejador = partial(_ServidorSilencioso, directory=str(directorio))
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), manejador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor, f"http://127.0.0.1:{servidor.server_address[1]}"


def test_revisar_devuelve_huella_estable(tmp_path):
    remoto, local = tmp_path / "remoto", tmp_path / "local"
    remoto.mkdir(); local.mkdir()
    contenido = "VERSION_NUEVA = 1\n"
    (remoto / "demo.py").write_text(contenido, encoding="utf-8", newline="\n")
    manifiesto = {"version": "9.9.9",
                  "files": {"demo.py": hashlib.sha256(contenido.encode()).hexdigest()}}
    (remoto / "manifest.json").write_text(json.dumps(manifiesto), encoding="utf-8")
    (local / "demo.py").write_text(contenido, encoding="utf-8", newline="\n")
    servidor, base = _servir(remoto)
    try:
        r1 = actualizador.revisar(local, base_url=base)
        r2 = actualizador.revisar(local, base_url=base)
    finally:
        servidor.shutdown()
    assert r1["sin_cambios"] and r2["sin_cambios"]
    assert r1["huella"] and r1["huella"] == r2["huella"]
    assert len(r1["huella"]) == 64
