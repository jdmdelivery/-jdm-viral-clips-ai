"""Descarga automática de recursos gratuitos con licencia compatible.

Solo fuentes con licencia explícita y conocida. Si el archivo ya está
(por sha256 o por id de catálogo), no se vuelve a bajar. Sin licencia
clara: se omite.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

import biblioteca_recursos as bib

# Catálogo curado: cada entrada trae licencia verificada y URL directa.
# Mixkit License y Wikimedia CC0 permiten uso comercial / monetización.
# No se incluye ningún archivo de origen desconocido.
CATALOGO_CURADO = [
    {
        "id": "mixkit-crowd-laugh-1",
        "tipo": "sonidos",
        "nombre": "Risas de público",
        "url": "https://assets.mixkit.co/active_storage/sfx/2005/2005-preview.mp3",
        "fuente": "Mixkit",
        "licencia": "Mixkit License",
        "emociones": ["risa", "celebracion"],
        "situaciones": ["chiste", "reaccion"],
        "categorias": ["sfx", "publico"],
        "notas": "Licencia Mixkit: uso gratuito incluyendo proyectos monetizados.",
    },
    {
        "id": "mixkit-cartoon-fail-1",
        "tipo": "sonidos",
        "nombre": "Fallo cartoon",
        "url": "https://assets.mixkit.co/active_storage/sfx/2955/2955-preview.mp3",
        "fuente": "Mixkit",
        "licencia": "Mixkit License",
        "emociones": ["risa", "sorpresa"],
        "situaciones": ["fracaso", "chiste"],
        "categorias": ["sfx", "comedia"],
        "notas": "Licencia Mixkit.",
    },
    {
        "id": "mixkit-winning-chime-1",
        "tipo": "sonidos",
        "nombre": "Campana de victoria",
        "url": "https://assets.mixkit.co/active_storage/sfx/2019/2019-preview.mp3",
        "fuente": "Mixkit",
        "licencia": "Mixkit License",
        "emociones": ["orgullo", "celebracion"],
        "situaciones": ["exito"],
        "categorias": ["sfx"],
        "notas": "Licencia Mixkit.",
    },
]


def _ya_tiene_url(cfg, url):
    _, data = bib.cargar_catalogo(cfg)
    for r in data["recursos"]:
        if r.get("url") == url:
            return r
    return None


def _descargar(url, destino, timeout=60):
    req = Request(url, headers={"User-Agent": "JDMViralClipsAI/1.0 (biblioteca local; +local)"})
    with urlopen(req, timeout=timeout) as resp:
        datos = resp.read()
    if len(datos) < 32:
        raise RuntimeError("La descarga devolvió un archivo demasiado pequeño")
    Path(destino).write_bytes(datos)
    return destino


def sincronizar(cfg=None, solo_ids=None, timeout=60):
    """Descarga el catálogo curado. Omite duplicados y licencias dudosas."""
    bib.asegurar_estructura(cfg)
    semilla = bib.sembrar_locales(cfg)
    resultados = {"semilla": semilla, "descargados": [], "omitidos": [], "errores": []}
    for item in CATALOGO_CURADO:
        if solo_ids and item["id"] not in solo_ids:
            continue
        if item["licencia"] not in bib.LICENCIAS_OK:
            resultados["omitidos"].append({"id": item["id"], "motivo": "licencia no permitida"})
            continue
        previo = _ya_tiene_url(cfg, item["url"])
        if previo:
            resultados["omitidos"].append({
                "id": item["id"], "motivo": "ya_almacenado", "recurso_id": previo.get("id"),
            })
            continue
        tmp = Path(tempfile.gettempdir()) / f"jdm-rec-{item['id']}"
        try:
            ext = Path(item["url"]).suffix.split("?")[0] or ".bin"
            if len(ext) > 5:
                ext = ".bin"
            destino = tmp.with_suffix(ext)
            _descargar(item["url"], destino, timeout=timeout)
            reg = bib.registrar(
                cfg,
                tipo=item["tipo"],
                ruta=destino,
                nombre=item["nombre"],
                fuente=item["fuente"],
                licencia=item["licencia"],
                emociones=item.get("emociones"),
                situaciones=item.get("situaciones"),
                categorias=item.get("categorias"),
                url=item["url"],
                notas=item.get("notas", ""),
            )
            if reg.get("duplicado"):
                resultados["omitidos"].append({"id": item["id"], "motivo": "duplicado_hash"})
            else:
                resultados["descargados"].append(reg["recurso"])
        except Exception as e:
            resultados["errores"].append({"id": item["id"], "error": str(e)[:300]})
        finally:
            for cand in tmp.parent.glob(f"jdm-rec-{item['id']}*"):
                try:
                    cand.unlink()
                except OSError:
                    pass
    resultados["catalogo"] = bib.listar(cfg)
    return resultados


def manifesto_publico():
    """Lista el catálogo curado sin descargar (para el panel)."""
    return [
        {
            "id": x["id"], "tipo": x["tipo"], "nombre": x["nombre"],
            "fuente": x["fuente"], "licencia": x["licencia"],
            "emociones": x.get("emociones", []), "situaciones": x.get("situaciones", []),
        }
        for x in CATALOGO_CURADO
    ]


if __name__ == "__main__":
    r = sincronizar({})
    print(json.dumps({
        "descargados": len(r["descargados"]),
        "omitidos": len(r["omitidos"]),
        "errores": r["errores"],
        "total_biblioteca": r["catalogo"]["total"],
    }, indent=2, ensure_ascii=False))
