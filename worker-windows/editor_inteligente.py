"""Editor inteligente: elige memes/sonidos/efectos y los coloca en segundos exactos.

Subtítulos NUNCA se queman. La transcripción solo alimenta el análisis/títulos.
"""
from __future__ import annotations

import json
from pathlib import Path

import biblioteca_recursos as bib
import capas_recursos
import ollama_local
from titles import generar as generar_titulos


def analizar_y_planear(cfg, *, texto="", tipo_momento="general", modo="general",
                       puntuacion=70, transcript=None,
                       meme_en_s=1.0, sonido_en_s=0.8, efecto_en_s=1.0):
    analisis = ollama_local.analizar_conversacion(texto or transcript or "")
    emos = analisis.get("emociones") or []
    sits = analisis.get("situaciones") or []
    memes = bib.buscar_para_contexto(cfg, emociones=emos, situaciones=sits, tipo="memes", limite=3)
    if not memes:
        memes = bib.buscar_para_contexto(cfg, emociones=emos, situaciones=sits, tipo="propios", limite=3)
    sonidos = bib.buscar_para_contexto(cfg, emociones=emos, situaciones=sits, tipo="sonidos", limite=3)
    efectos = bib.buscar_para_contexto(cfg, emociones=emos, situaciones=sits, tipo="efectos", limite=2)

    zoom = 1.0
    if "sorpresa" in emos or "tension" in emos or puntuacion >= 85:
        zoom = 1.25
    elif "risa" in emos or "sarcasmo" in emos:
        zoom = 1.15
    elif "discusion" in sits:
        zoom = 1.2

    texts = generar_titulos(
        tipo=tipo_momento if tipo_momento in (
            "discusion", "gritos", "risas", "sorpresa", "confesion", "accion", "general"
        ) else ("risas" if "risa" in emos else "sorpresa" if "sorpresa" in emos else "general"),
        modo=modo,
        puntuacion=puntuacion,
        transcripcion=texto or transcript,
    )
    if analisis.get("humor_dominicano"):
        texts["hashtags"] = list(dict.fromkeys(
            (texts.get("hashtags") or []) + ["#RepublicaDominicana", "#HumorDominicano", "#RD"]
        ))

    meme = memes[0] if memes else None
    sonido = sonidos[0] if sonidos else None
    efecto = efectos[0] if efectos else None
    plan = {
        "analisis": analisis,
        "zoom": zoom,
        "offset_x": 0.0,
        "offset_y": -0.05 if zoom > 1.0 else 0.0,
        "subtitulos": False,
        "meme": meme,
        "sonido": sonido,
        "efecto": efecto,
        "meme_en_s": float(meme_en_s),
        "sonido_en_s": float(sonido_en_s),
        "efecto_en_s": float(efecto_en_s),
        "meme_duracion_s": 2.5,
        "efecto_duracion_s": 0.3,
        "textos": texts,
        "timeline": _timeline(meme, sonido, efecto, zoom, meme_en_s, sonido_en_s, efecto_en_s),
    }
    return plan


def _timeline(meme, sonido, efecto, zoom, meme_en_s, sonido_en_s, efecto_en_s):
    pistas = [
        {"pista": "video", "inicio_s": 0, "accion": "clip_base", "detalle": "Video principal 9:16"},
        {"pista": "camara", "inicio_s": 0.0, "accion": "zoom", "detalle": f"Zoom x{zoom}"},
    ]
    if sonido:
        pistas.append({
            "pista": "audio", "inicio_s": float(sonido_en_s), "accion": "sfx",
            "detalle": sonido.get("nombre"), "recurso_id": sonido.get("id"),
        })
    if meme:
        pistas.append({
            "pista": "overlay", "inicio_s": float(meme_en_s), "fin_s": float(meme_en_s) + 2.5,
            "accion": "meme", "detalle": meme.get("nombre"), "recurso_id": meme.get("id"),
        })
    if efecto:
        pistas.append({
            "pista": "efecto", "inicio_s": float(efecto_en_s), "fin_s": float(efecto_en_s) + 0.3,
            "accion": "efecto", "detalle": efecto.get("nombre"), "recurso_id": efecto.get("id"),
        })
    return pistas


def aplicar_plan(fuente, salida, plan, cfg=None):
    """Aplica meme/sonido/efecto en los segundos del plan. Subtítulos OFF."""
    return capas_recursos.colocar(
        cfg, fuente, salida,
        meme=plan.get("meme"),
        sonido=plan.get("sonido"),
        efecto=plan.get("efecto"),
        meme_en_s=float(plan.get("meme_en_s", 1.0)),
        sonido_en_s=float(plan.get("sonido_en_s", 0.8)),
        efecto_en_s=float(plan.get("efecto_en_s", 1.0)),
        meme_duracion_s=float(plan.get("meme_duracion_s", 2.5)),
        efecto_duracion_s=float(plan.get("efecto_duracion_s", 0.3)),
        formato="9:16",
        zoom=float(plan.get("zoom") or 1.0),
    )


def modo_viral(cfg, fuente, salida, texto="", tipo_momento="general", modo="general",
               puntuacion=80, meme_en_s=1.0, sonido_en_s=0.8, efecto_en_s=1.0):
    plan = analizar_y_planear(
        cfg, texto=texto, tipo_momento=tipo_momento, modo=modo, puntuacion=puntuacion,
        meme_en_s=meme_en_s, sonido_en_s=sonido_en_s, efecto_en_s=efecto_en_s,
    )
    resultado = aplicar_plan(fuente, salida, plan, cfg=cfg)
    resultado["textos"] = plan.get("textos")
    resultado["timeline"] = plan.get("timeline")
    resultado["plan"] = plan
    resultado["subtitles"] = False
    return resultado


def guardar_plan_preview(plan, carpeta):
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "plan-editor.json"
    ruta.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(ruta)
