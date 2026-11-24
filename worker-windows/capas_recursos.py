"""Importar, previsualizar y colocar memes/sonidos/efectos en un segundo exacto.

La exportación NUNCA quema subtítulos. La transcripción interna no se usa aquí.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import biblioteca_recursos as bib
from clipper import FORMATOS
from procesos import SIN_VENTANA


def _duracion(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60, creationflags=SIN_VENTANA,
        )
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _esc(p):
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def asegurar_ejemplos(cfg=None):
    """Crea la biblioteca local con ejemplos CC0 si faltan."""
    raiz = bib.asegurar_estructura(cfg)
    seeds = [
        ("sonidos", "risas-punch-cc0.wav", "seed-risas-punch",
         'ffmpeg -y -v error -f lavfi -i "sine=frequency=880:duration=0.35" '
         '-af "afade=t=out:st=0.2:d=0.15" "{out}"'),
        ("sonidos", "boom-suave-cc0.wav", "seed-boom-suave",
         'ffmpeg -y -v error -f lavfi -i "sine=frequency=220:duration=0.5" '
         '-af "afade=t=in:st=0:d=0.05,afade=t=out:st=0.35:d=0.15" "{out}"'),
        ("sonidos", "ping-cc0.wav", "seed-ping",
         'ffmpeg -y -v error -f lavfi -i "sine=frequency=440:duration=0.25" '
         '-af "volume=0.4" "{out}"'),
        ("efectos", "flash-blanco-cc0.png", "seed-flash-blanco",
         'ffmpeg -y -v error -f lavfi -i "color=c=white:s=512x512:d=1" -frames:v 1 "{out}"'),
        ("memes", "alerta-roja-cc0.png", "seed-alerta-roja",
         'ffmpeg -y -v error -f lavfi -i "color=c=0xFF2020:s=512x512:d=1" -frames:v 1 "{out}"'),
        ("memes", "impacto-azul-cc0.png", "seed-impacto-azul",
         'ffmpeg -y -v error -f lavfi -i "color=c=0x00BFFF:s=512x512:d=1" -frames:v 1 "{out}"'),
    ]
    meta = {
        "risas-punch-cc0.wav": (["risa"], ["chiste", "reaccion"], "Risas punch"),
        "boom-suave-cc0.wav": (["sorpresa"], ["reaccion"], "Boom suave"),
        "ping-cc0.wav": (["sorpresa"], ["reaccion"], "Ping"),
        "flash-blanco-cc0.png": (["sorpresa"], ["reaccion"], "Flash blanco"),
        "alerta-roja-cc0.png": (["enojo", "tension"], ["discusion"], "Alerta roja"),
        "impacto-azul-cc0.png": (["sorpresa"], ["reaccion", "chiste"], "Impacto azul"),
    }
    creados = []
    for tipo, nombre, id_fijo, plantilla in seeds:
        out = raiz / tipo / nombre
        if not out.is_file() or out.stat().st_size < 32:
            out.parent.mkdir(parents=True, exist_ok=True)
            cmd = plantilla.format(out=str(out))
            subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=60, creationflags=SIN_VENTANA)
        if out.is_file():
            emos, sits, etiqueta = meta[nombre]
            r = bib.registrar(
                cfg, tipo=tipo, ruta=out, nombre=etiqueta, id_fijo=id_fijo,
                fuente="JDM Viral Clips AI (generado localmente)", licencia="CC0",
                emociones=emos, situaciones=sits, categorias=[tipo],
                notas="Ejemplo CC0 con id estable panel+worker.",
            )
            creados.append(r)
    return {"raiz": str(raiz), "total": bib.listar(cfg)["total"], "detalle": creados}


def importar(cfg, ruta, *, nombre=None, emociones=None, situaciones=None):
    return bib.importar_propio(
        cfg, ruta, nombre=nombre, emociones=emociones, situaciones=situaciones,
    )


def listar(cfg=None, tipo=None):
    return bib.listar(cfg, tipo=tipo)


def previsualizar(cfg, recurso_id, carpeta_preview=None):
    """Copia o genera una vista previa del recurso (ruta usable por el sistema)."""
    items = bib.listar(cfg)["recursos"]
    rec = next((r for r in items if r.get("id") == recurso_id), None)
    if not rec:
        raise FileNotFoundError(f"Recurso no encontrado: {recurso_id}")
    origen = Path(rec["ruta"])
    if not origen.is_file():
        raise FileNotFoundError(f"Archivo ausente: {origen}")
    dest_dir = Path(carpeta_preview or (bib.raiz_biblioteca(cfg) / "_preview"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"preview-{rec['id']}{origen.suffix.lower()}"
    if origen.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".wav", ".mp3", ".ogg"}:
        dest.write_bytes(origen.read_bytes())
    else:
        # Video/efecto: un frame o copia corta
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(origen), "-frames:v", "1",
             str(dest.with_suffix(".jpg"))],
            capture_output=True, text=True, timeout=60, creationflags=SIN_VENTANA,
        )
        dest = dest.with_suffix(".jpg") if dest.with_suffix(".jpg").is_file() else origen
    return {
        "recurso": rec,
        "preview": str(dest),
        "tipo": rec.get("tipo"),
        "nota": "Vista previa lista. No se modificó el clip.",
    }


def _capa_normalizada(capa, idx):
    """Normaliza una capa: {tipo, ruta|recurso_id, en_s, duracion_s?}."""
    en_s = float(capa.get("en_s", capa.get("inicio_s", 0)) or 0)
    dur = capa.get("duracion_s")
    dur_s = float(dur) if dur is not None else None
    tipo = (capa.get("tipo") or "meme").lower()
    return {
        "id": capa.get("id") or f"capa-{idx}",
        "tipo": tipo,
        "ruta": capa.get("ruta"),
        "recurso_id": capa.get("recurso_id"),
        "en_s": max(0.0, en_s),
        "duracion_s": dur_s if dur_s is None else max(0.1, dur_s),
        "ancho": int(capa.get("ancho") or (280 if tipo == "meme" else 512)),
        "x": capa.get("x"),  # None = esquina superior derecha
        "y": capa.get("y"),
    }


def resolver_ruta(cfg, capa):
    if capa.get("ruta") and Path(capa["ruta"]).is_file():
        return Path(capa["ruta"])
    rid = capa.get("recurso_id")
    if rid:
        for r in bib.listar(cfg)["recursos"]:
            if r.get("id") == rid and Path(r.get("ruta") or "").is_file():
                return Path(r["ruta"])
    return None


def exportar_con_capas(fuente, salida, capas, *, cfg=None, formato="9:16", zoom=1.0):
    """Exporta el clip con capas en segundos exactos. Sin subtítulos quemados."""
    fuente, salida = Path(fuente), Path(salida)
    if not fuente.is_file():
        raise FileNotFoundError(f"Clip fuente no existe: {fuente}")
    salida.parent.mkdir(parents=True, exist_ok=True)
    w, h = FORMATOS.get(formato, FORMATOS["9:16"])
    zoom = max(1.0, float(zoom or 1.0))
    capas_n = [_capa_normalizada(c, i) for i, c in enumerate(capas or [])]

    entradas = ["-i", str(fuente)]
    filtros = []
    if zoom > 1.0:
        filtros.append(
            f"[0:v]crop=w='iw/{zoom}':h='ih/{zoom}':"
            f"x='(iw-iw/{zoom})/2':y='(ih-ih/{zoom})/2',"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[v0]"
        )
    else:
        filtros.append(
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}[v0]"
        )

    mapa_v = "[v0]"
    audios_extra = []
    idx = 1
    aplicadas = []

    for capa in capas_n:
        ruta = resolver_ruta(cfg, capa)
        if not ruta:
            continue
        tipo = capa["tipo"]
        en = capa["en_s"]
        dur = capa["duracion_s"]
        if tipo in {"meme", "efecto"} and ruta.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            entradas += ["-i", str(ruta)]
            fin = en + (dur if dur is not None else 2.0)
            ancho = capa["ancho"]
            x = capa["x"] if capa["x"] is not None else "W-w-40"
            y = capa["y"] if capa["y"] is not None else "40"
            if tipo == "efecto":
                # Flash a pantalla casi completa, breve
                ancho = min(w, int(capa.get("ancho") or w))
                x, y = "(W-w)/2", "(H-h)/2"
                fin = en + (dur if dur is not None else 0.25)
            ov = f"ov{idx}"
            out = f"v{idx}"
            filtros.append(f"[{idx}:v]scale={ancho}:-1[{ov}]")
            filtros.append(
                f"{mapa_v}[{ov}]overlay={x}:{y}:enable='between(t\\,{en}\\,{fin})'[{out}]"
            )
            mapa_v = f"[{out}]"
            aplicadas.append({**capa, "ruta": str(ruta), "fin_s": fin})
            idx += 1
        elif tipo in {"sonido", "efecto"} and ruta.suffix.lower() in {".wav", ".mp3", ".ogg", ".m4a", ".aac"}:
            entradas += ["-i", str(ruta)]
            # Retrasar el audio al segundo exacto con adelay (ms)
            ms = int(round(en * 1000))
            tag = f"a{idx}"
            filtros.append(f"[{idx}:a]adelay={ms}|{ms},volume=1.0[{tag}]")
            audios_extra.append(f"[{tag}]")
            aplicadas.append({**capa, "ruta": str(ruta), "fin_s": None})
            idx += 1

    # Mezcla de audio
    if audios_extra:
        n = 1 + len(audios_extra)
        mezcla = "[0:a]" + "".join(audios_extra) + f"amix=inputs={n}:duration=first:dropout_transition=0:normalize=0[aout]"
        filtros.append(mezcla)
        mapa_a = "[aout]"
    else:
        mapa_a = "0:a:0?"

    fc = ";".join(filtros)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *entradas,
           "-filter_complex", fc, "-map", mapa_v]
    if mapa_a.startswith("["):
        cmd += ["-map", mapa_a]
    else:
        cmd += ["-map", "0:a:0?"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-shortest", str(salida)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, creationflags=SIN_VENTANA)
    if r.returncode != 0 or not salida.is_file():
        raise RuntimeError(f"FFmpeg con capas falló: {(r.stderr or '')[-800:]}")
    return {
        "file_path": str(salida),
        "format": formato,
        "duration_s": round(_duracion(salida), 2),
        "subtitles": False,
        "capas": aplicadas,
        "nota": "Exportado con capas en segundos exactos. Sin subtítulos quemados.",
    }


def colocar(cfg, fuente, salida, *, meme=None, sonido=None, efecto=None,
            meme_en_s=1.0, sonido_en_s=1.0, efecto_en_s=1.0,
            meme_duracion_s=2.5, efecto_duracion_s=0.3, formato="9:16", zoom=1.0):
    """Atajo: coloca meme + sonido + efecto en segundos dados."""
    capas = []
    if meme:
        capas.append({"tipo": "meme", "recurso_id": meme.get("id") if isinstance(meme, dict) else None,
                      "ruta": meme.get("ruta") if isinstance(meme, dict) else meme,
                      "en_s": meme_en_s, "duracion_s": meme_duracion_s})
    if sonido:
        capas.append({"tipo": "sonido", "recurso_id": sonido.get("id") if isinstance(sonido, dict) else None,
                      "ruta": sonido.get("ruta") if isinstance(sonido, dict) else sonido,
                      "en_s": sonido_en_s})
    if efecto:
        capas.append({"tipo": "efecto", "recurso_id": efecto.get("id") if isinstance(efecto, dict) else None,
                      "ruta": efecto.get("ruta") if isinstance(efecto, dict) else efecto,
                      "en_s": efecto_en_s, "duracion_s": efecto_duracion_s})
    return exportar_con_capas(fuente, salida, capas, cfg=cfg, formato=formato, zoom=zoom)
