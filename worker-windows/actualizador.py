"""Auto-actualización del trabajador desde el manifiesto del repositorio.

Solo biblioteca estándar. Descarga manifest.json, compara el sha256 de cada
archivo local con el del manifiesto y solo sustituye un archivo cuando la
copia descargada coincide EXACTAMENTE con el hash publicado (si no, se
borra la descarga y el original queda intacto). Antes de sustituir guarda
una copia .bak. revisar() NUNCA lanza: cualquier error queda registrado en
el resultado para que un fallo del actualizador jamás tumbe el motor.
"""
import hashlib, json, os, shutil, urllib.request
from pathlib import Path

URL_BASE = "https://raw.githubusercontent.com/jdmdelivery/-jdm-viral-clips-ai/main/worker-windows"

ARCHIVOS_QUE_REINICIAN = ("worker.py", "actualizador.py")


def sha256_de(path):
    """Hash sha256 (hex) de un archivo, leído por bloques."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def _descargar(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as respuesta:
        return respuesta.read()


def revisar(carpeta, base_url=URL_BASE, timeout=30):
    """Revisa y aplica actualizaciones en `carpeta` desde `base_url`.

    Devuelve {"actualizados": [nombres], "reiniciar": bool,
              "errores": [str], "sin_cambios": bool}. Nunca lanza."""
    carpeta = Path(carpeta)
    resultado = {"actualizados": [], "reiniciar": False,
                 "errores": [], "sin_cambios": False}
    base = str(base_url).rstrip("/")
    try:
        crudo = _descargar(base + "/manifest.json", timeout)
        manifiesto = json.loads(crudo.decode("utf-8"))
        archivos = manifiesto.get("files") or {}
        if not isinstance(archivos, dict):
            raise ValueError("El manifiesto no trae una lista de archivos válida")
    except Exception as e:
        resultado["errores"].append(f"No se pudo leer el manifiesto: {e}")
        resultado["sin_cambios"] = not resultado["actualizados"]
        return resultado

    for nombre, hash_esperado in archivos.items():
        try:
            # SEGURIDAD: solo nombres de archivo planos, nunca rutas.
            if (not nombre or "/" in nombre or "\\" in nombre
                    or ".." in nombre):
                resultado["errores"].append(
                    f"Nombre rechazado por seguridad en el manifiesto: {nombre!r}")
                continue
            esperado = str(hash_esperado).strip().lower()
            destino = carpeta / nombre
            if destino.is_file() and sha256_de(destino) == esperado:
                continue  # ya está al día
            temporal = carpeta / (nombre + ".nuevo")
            datos = _descargar(base + "/" + nombre, timeout)
            temporal.write_bytes(datos)
            if sha256_de(temporal) != esperado:
                try:
                    temporal.unlink()
                except OSError:
                    pass
                resultado["errores"].append(
                    f"El hash de {nombre} no coincide con el manifiesto; "
                    "no se tocó el archivo original")
                continue
            if destino.exists():
                shutil.copy2(destino, carpeta / (nombre + ".bak"))
            os.replace(temporal, destino)
            resultado["actualizados"].append(nombre)
            if nombre in ARCHIVOS_QUE_REINICIAN:
                resultado["reiniciar"] = True
        except Exception as e:
            resultado["errores"].append(f"Error actualizando {nombre}: {e}")
    resultado["sin_cambios"] = not resultado["actualizados"]
    return resultado
