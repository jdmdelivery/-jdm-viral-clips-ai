"""Biblioteca local de memes, sonidos, efectos, transiciones y animaciones.

Cada recurso guarda fuente, licencia, emociones y categorías. No se tocan
grabaciones ni clips. Los archivos viven en JDMViralClips/biblioteca.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path

TIPOS = ("memes", "sonidos", "efectos", "transiciones", "animaciones", "propios")
EMOCIONES = (
    "risa", "sarcasmo", "sorpresa", "enojo", "tristeza", "orgullo",
    "tension", "alivio", "confusion", "celebracion",
)
SITUACIONES = (
    "discusion", "chiste", "doble_sentido", "insulto_suave", "reaccion",
    "confesion", "exito", "fracaso", "silencio", "general",
)
LICENCIAS_OK = {
    "CC0", "CC0-1.0", "CC-BY", "CC-BY-4.0", "CC-BY-3.0",
    "Pixabay Content License", "Mixkit License", "propia",
}


def raiz_biblioteca(cfg=None):
    if cfg and cfg.get("carpeta_biblioteca"):
        return Path(cfg["carpeta_biblioteca"])
    env = os.environ.get("JDM_BIBLIOTECA_DIR", "").strip()
    if env:
        return Path(env)
    # Por defecto: biblioteca dentro de esta carpeta del motor (worker-windows).
    return Path(__file__).resolve().parent / "biblioteca"


def _catalogo_path(raiz):
    return Path(raiz) / "catalogo.json"


def sha256_archivo(ruta):
    digest = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(bloque)
    return digest.hexdigest()


def asegurar_estructura(cfg=None):
    raiz = raiz_biblioteca(cfg)
    for tipo in TIPOS:
        (raiz / tipo).mkdir(parents=True, exist_ok=True)
    (raiz / "_tmp").mkdir(parents=True, exist_ok=True)
    cat = _catalogo_path(raiz)
    if not cat.exists():
        cat.write_text(json.dumps({"version": 1, "recursos": []}, indent=2), encoding="utf-8")
    return raiz


def cargar_catalogo(cfg=None):
    raiz = asegurar_estructura(cfg)
    cat = _catalogo_path(raiz)
    try:
        data = json.loads(cat.read_text(encoding="utf-8-sig"))
    except Exception:
        data = {"version": 1, "recursos": []}
    if not isinstance(data.get("recursos"), list):
        data["recursos"] = []
    return raiz, data


def guardar_catalogo(raiz, data):
    tmp = Path(raiz) / "catalogo.json.tmp"
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, _catalogo_path(raiz))


def _normalizar_lista(valores, permitidos):
    out = []
    for v in valores or []:
        clave = str(v or "").strip().lower().replace(" ", "_")
        if clave in permitidos and clave not in out:
            out.append(clave)
    return out


def registrar(cfg, *, tipo, ruta, nombre, fuente, licencia, emociones=None,
              situaciones=None, categorias=None, url=None, notas="", id_fijo=None):
    """Registra un archivo ya presente. Rechaza licencia desconocida y duplicados."""
    if tipo not in TIPOS:
        raise ValueError(f"Tipo no válido: {tipo}")
    licencia = str(licencia or "").strip()
    if licencia not in LICENCIAS_OK:
        raise ValueError(f"Licencia no compatible con monetización o desconocida: {licencia}")
    origen = Path(ruta)
    if not origen.is_file() or origen.stat().st_size < 8:
        raise FileNotFoundError("El recurso no existe o está vacío")
    raiz, data = cargar_catalogo(cfg)
    digest = sha256_archivo(origen)
    fijo = (str(id_fijo).strip() if id_fijo else "") or None
    for r in data["recursos"]:
        if fijo and r.get("id") == fijo:
            return {"ok": True, "duplicado": True, "recurso": r,
                    "nota": "Ya estaba en la biblioteca (mismo id); no se descargó de nuevo."}
        if r.get("sha256") == digest:
            return {"ok": True, "duplicado": True, "recurso": r,
                    "nota": "Ya estaba en la biblioteca; no se descargó de nuevo."}
    destino_dir = raiz / tipo
    destino_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in (nombre or origen.stem))[:80]
    destino = destino_dir / f"{safe}{origen.suffix.lower()}"
    if destino.exists() and sha256_archivo(destino) != digest:
        destino = destino_dir / f"{safe}-{uuid.uuid4().hex[:6]}{origen.suffix.lower()}"
    if origen.resolve() != destino.resolve():
        shutil.copy2(origen, destino)
        digest = sha256_archivo(destino)
    recurso = {
        "id": fijo or digest[:12],
        "tipo": tipo,
        "nombre": nombre or destino.stem,
        "archivo": str(destino.relative_to(raiz)).replace("\\", "/"),
        "sha256": digest,
        "bytes": destino.stat().st_size,
        "fuente": fuente or "desconocida",
        "licencia": licencia,
        "url": url or "",
        "emociones": _normalizar_lista(emociones, EMOCIONES),
        "situaciones": _normalizar_lista(situaciones, SITUACIONES),
        "categorias": [str(c).strip().lower() for c in (categorias or []) if str(c).strip()][:12],
        "notas": (notas or "")[:500],
        "creado": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    data["recursos"].append(recurso)
    guardar_catalogo(raiz, data)
    return {"ok": True, "duplicado": False, "recurso": recurso}


def listar(cfg=None, tipo=None, emocion=None, situacion=None, q=None):
    raiz, data = cargar_catalogo(cfg)
    out = []
    for r in data["recursos"]:
        if tipo and r.get("tipo") != tipo:
            continue
        if emocion and emocion not in (r.get("emociones") or []):
            continue
        if situacion and situacion not in (r.get("situaciones") or []):
            continue
        if q:
            blob = " ".join([
                r.get("nombre", ""), r.get("fuente", ""), r.get("licencia", ""),
                " ".join(r.get("categorias") or []), " ".join(r.get("emociones") or []),
            ]).lower()
            if q.lower() not in blob:
                continue
        item = dict(r)
        item["ruta"] = str((raiz / r["archivo"]).resolve())
        item["existe"] = (raiz / r["archivo"]).is_file()
        out.append(item)
    return {"raiz": str(raiz), "total": len(out), "recursos": out}


def buscar_para_contexto(cfg, emociones=None, situaciones=None, tipo=None, limite=5):
    """Elige recursos que coincidan con emociones/situaciones del análisis."""
    emos = set(_normalizar_lista(emociones, EMOCIONES))
    sits = set(_normalizar_lista(situaciones, SITUACIONES))
    cand = listar(cfg, tipo=tipo)["recursos"]
    puntuados = []
    for r in cand:
        if not r.get("existe"):
            continue
        re = set(r.get("emociones") or [])
        rs = set(r.get("situaciones") or [])
        score = len(emos & re) * 3 + len(sits & rs) * 2
        if not emos and not sits:
            score = 1
        if score <= 0:
            continue
        puntuados.append((score, r))
    puntuados.sort(key=lambda x: (-x[0], x[1].get("nombre", "")))
    return [r for _, r in puntuados[: max(1, int(limite or 5))]]


def importar_propio(cfg, ruta, nombre=None, emociones=None, situaciones=None, categorias=None):
    """El usuario agrega su propio meme o sonido. Licencia: propia."""
    p = Path(ruta)
    ext = p.suffix.lower()
    if ext in {".mp3", ".wav", ".ogg", ".m4a", ".aac"}:
        tipo = "propios"
        categorias = list(categorias or []) + ["sonido"]
    elif ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".webm", ".mp4"}:
        tipo = "propios"
        categorias = list(categorias or []) + ["visual"]
    else:
        raise ValueError("Formato no soportado. Usa audio o imagen/video habitual.")
    return registrar(
        cfg, tipo=tipo, ruta=p, nombre=nombre or p.stem,
        fuente="usuario", licencia="propia",
        emociones=emociones, situaciones=situaciones, categorias=categorias,
        notas="Agregado por el usuario en esta PC.",
    )


def sembrar_locales(cfg=None):
    """Registra los archivos seed CC0 ya generados en disco, sin re-descargar."""
    raiz = asegurar_estructura(cfg)
    # id_fijo estable: el panel en Render y el worker local deben coincidir.
    semillas = [
        ("sonidos", "risas-punch-cc0.wav", "Risas punch", "seed-risas-punch", ["risa", "celebracion"], ["chiste", "reaccion"], ["sfx"]),
        ("sonidos", "boom-suave-cc0.wav", "Boom suave", "seed-boom-suave", ["sorpresa", "tension"], ["reaccion", "exito"], ["sfx"]),
        ("sonidos", "ping-cc0.wav", "Ping", "seed-ping", ["sorpresa"], ["reaccion"], ["sfx"]),
        ("efectos", "flash-blanco-cc0.png", "Flash blanco", "seed-flash-blanco", ["sorpresa"], ["reaccion"], ["overlay"]),
        ("memes", "alerta-roja-cc0.png", "Alerta roja", "seed-alerta-roja", ["enojo", "tension"], ["discusion", "reaccion"], ["overlay"]),
        ("memes", "impacto-azul-cc0.png", "Impacto azul", "seed-impacto-azul", ["sorpresa"], ["reaccion", "chiste"], ["overlay"]),
    ]
    resultados = []
    for tipo, archivo, nombre, id_fijo, emos, sits, cats in semillas:
        ruta = raiz / tipo / archivo
        if not ruta.is_file():
            continue
        resultados.append(registrar(
            cfg, tipo=tipo, ruta=ruta, nombre=nombre, id_fijo=id_fijo,
            fuente="JDM Viral Clips AI (generado localmente)",
            licencia="CC0", emociones=emos, situaciones=sits, categorias=cats,
            notas="Recurso seed CC0 con id estable para panel+worker.",
        ))
    return {"ok": True, "registrados": len(resultados), "detalle": resultados}
