"""Genera worker-windows/manifest.json para el auto-actualizador.

Solo biblioteca estándar. Calcula el sha256 de todos los worker-windows/*.py
más requirements.txt, install-worker.ps1 e install-worker.bat, lee la
versión desde VERSION en worker.py y escribe el manifiesto con las claves
ordenadas. Ejecutar tras cambiar cualquier archivo del trabajador:

    python scripts/generar_manifest.py
"""
import hashlib, json, re, sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_TRABAJADOR = RAIZ / "worker-windows"
EXTRAS = ("requirements.txt", "install-worker.ps1", "install-worker.bat")


def sha256_de(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def main():
    texto_worker = (CARPETA_TRABAJADOR / "worker.py").read_text(encoding="utf-8")
    coincidencia = re.search(r'^VERSION\s*=\s*"([^"]+)"', texto_worker, re.MULTILINE)
    if not coincidencia:
        print("ERROR: no se encontró VERSION en worker.py", file=sys.stderr)
        return 1
    version = coincidencia.group(1)

    archivos = {}
    rutas = sorted(CARPETA_TRABAJADOR.glob("*.py"))
    rutas += [CARPETA_TRABAJADOR / nombre for nombre in EXTRAS]
    for ruta in rutas:
        if ruta.is_file():
            archivos[ruta.name] = sha256_de(ruta)

    manifiesto = {"version": version, "files": archivos}
    destino = CARPETA_TRABAJADOR / "manifest.json"
    destino.write_text(json.dumps(manifiesto, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    print(f"Manifiesto escrito en {destino}: versión {version}, "
          f"{len(archivos)} archivos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
