"""Tests del auto-actualizador (worker-windows/actualizador.py), del
manifiesto generado y de los estáticos del instalador. El «remoto» es un
servidor HTTP local en un hilo, con puerto efímero."""
import hashlib, json, threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import actualizador

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_TRABAJADOR = RAIZ / "worker-windows"


def _sha(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class _ServidorSilencioso(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _servir(directorio):
    manejador = partial(_ServidorSilencioso, directory=str(directorio))
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), manejador)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor, f"http://127.0.0.1:{servidor.server_address[1]}"


def _preparar_remoto(remoto, archivos, hashes_extra=None):
    manifiesto = {"version": "9.9.9", "files": {}}
    for nombre, contenido in archivos.items():
        (remoto / nombre).write_text(contenido, encoding="utf-8", newline="\n")
        manifiesto["files"][nombre] = _sha(contenido)
    if hashes_extra:
        manifiesto["files"].update(hashes_extra)
    (remoto / "manifest.json").write_text(
        json.dumps(manifiesto), encoding="utf-8")


def test_revisar_actualiza_y_marca_reinicio(tmp_path):
    remoto, local = tmp_path / "remoto", tmp_path / "local"
    remoto.mkdir(); local.mkdir()
    _preparar_remoto(remoto, {"demo.py": "VERSION_NUEVA = 2\n",
                              "worker.py": "# motor nuevo\n"})
    (local / "demo.py").write_text("VERSION_NUEVA = 1\n", encoding="utf-8")
    (local / "worker.py").write_text("# motor viejo\n", encoding="utf-8")
    servidor, base = _servir(remoto)
    try:
        r = actualizador.revisar(local, base_url=base, timeout=10)
        assert r["errores"] == []
        assert set(r["actualizados"]) == {"demo.py", "worker.py"}
        assert r["reiniciar"] is True  # worker.py está en la lista
        assert r["sin_cambios"] is False
        assert (local / "demo.py").read_text(encoding="utf-8") == "VERSION_NUEVA = 2\n"
        assert (local / "worker.py").read_text(encoding="utf-8") == "# motor nuevo\n"
        assert (local / "demo.py.bak").read_text(encoding="utf-8") == "VERSION_NUEVA = 1\n"
        assert not list(local.glob("*.nuevo"))
        # Segunda revisión: todo coincide -> sin cambios
        r2 = actualizador.revisar(local, base_url=base, timeout=10)
        assert r2["actualizados"] == [] and r2["sin_cambios"] is True
        assert r2["reiniciar"] is False and r2["errores"] == []
    finally:
        servidor.shutdown()


def test_revisar_solo_un_modulo_tambien_reinicia(tmp_path):
    """Regresión 2026-11-19: actualizar SOLO resolver.py debe marcar
    reinicio. Antes solo worker.py/actualizador.py reiniciaban, y como el
    proceso importa los módulos al arrancar, el motor seguía ejecutando
    el resolver viejo en memoria aunque el archivo nuevo ya estuviera en
    disco: el panel enseñaba el mismo error tras publicar el arreglo."""
    remoto, local = tmp_path / "remoto", tmp_path / "local"
    remoto.mkdir(); local.mkdir()
    _preparar_remoto(remoto, {"resolver.py": "# resolver nuevo\n"})
    (local / "resolver.py").write_text("# resolver viejo\n", encoding="utf-8")
    servidor, base = _servir(remoto)
    try:
        r = actualizador.revisar(local, base_url=base, timeout=10)
        assert r["errores"] == []
        assert r["actualizados"] == ["resolver.py"]
        assert r["reiniciar"] is True
        assert (local / "resolver.py").read_text(encoding="utf-8") == "# resolver nuevo\n"
    finally:
        servidor.shutdown()


def test_revisar_hash_distinto_no_toca_original(tmp_path):
    remoto, local = tmp_path / "remoto", tmp_path / "local"
    remoto.mkdir(); local.mkdir()
    # El manifiesto anuncia un hash que NO es el del archivo servido
    _preparar_remoto(remoto, {"malo.py": "contenido servido\n"},
                     hashes_extra={"malo.py": _sha("otro contenido distinto\n")})
    (local / "malo.py").write_text("original intocable\n", encoding="utf-8")
    servidor, base = _servir(remoto)
    try:
        r = actualizador.revisar(local, base_url=base, timeout=10)
        assert r["actualizados"] == []
        assert r["errores"], "debió registrar el error de hash"
        assert (local / "malo.py").read_text(encoding="utf-8") == "original intocable\n"
        assert not (local / "malo.py.nuevo").exists()
    finally:
        servidor.shutdown()


def test_revisar_rechaza_nombre_malicioso(tmp_path):
    remoto, local = tmp_path / "remoto", tmp_path / "local"
    remoto.mkdir(); local.mkdir()
    _preparar_remoto(remoto, {}, hashes_extra={"../fuera.py": _sha("x")})
    servidor, base = _servir(remoto)
    try:
        r = actualizador.revisar(local, base_url=base, timeout=10)
        assert r["actualizados"] == []
        assert any("fuera" in e or ".." in e for e in r["errores"])
        assert not (tmp_path / "fuera.py").exists()
    finally:
        servidor.shutdown()


def test_revisar_sin_servidor_nunca_lanza(tmp_path):
    r = actualizador.revisar(tmp_path, base_url="http://127.0.0.1:1", timeout=2)
    assert r["actualizados"] == [] and r["errores"]
    assert r["reiniciar"] is False


def test_sha256_de(tmp_path):
    ruta = tmp_path / "a.txt"
    ruta.write_bytes(b"hola")
    assert actualizador.sha256_de(ruta) == hashlib.sha256(b"hola").hexdigest()


# --- Manifiesto real del repo ---
def test_manifest_coincide_con_disco():
    manifiesto = json.loads(
        (CARPETA_TRABAJADOR / "manifest.json").read_text(encoding="utf-8"))
    assert manifiesto["version"]
    for nombre, esperado in manifiesto["files"].items():
        ruta = CARPETA_TRABAJADOR / nombre
        assert ruta.is_file(), nombre
        real = hashlib.sha256(ruta.read_bytes()).hexdigest()
        assert real == esperado, nombre
    for obligatorio in ("worker.py", "actualizador.py",
                        "requirements.txt", "install-worker.ps1",
                        "install-worker.bat"):
        assert obligatorio in manifiesto["files"]


# --- Estáticos del instalador ---
def test_instalador_tarea_programada_y_sin_bom():
    ruta = CARPETA_TRABAJADOR / "install-worker.ps1"
    crudo = ruta.read_bytes()
    assert not crudo.startswith(b"\xef\xbb\xbf"), "install-worker.ps1 no debe llevar BOM"
    texto = crudo.decode("utf-8")
    for esperado in ("Register-ScheduledTask", "JDM Viral Clips AI - Motor",
                     "pythonw", "start-motor.bat", "AtLogOn"):
        assert esperado in texto
    # El bloque antiguo de la carpeta Startup quedó eliminado
    assert "Iniciar el trabajador automaticamente con Windows" not in texto
    assert "Startup" not in texto
