"""Análisis local de conversaciones con Ollama.

Sin Ollama o sin modelo: degrada a reglas honestas (no inventa que «entendió»
con IA). No usa APIs de pago.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

EMOCIONES = (
    "risa", "sarcasmo", "sorpresa", "enojo", "tristeza", "orgullo",
    "tension", "alivio", "confusion", "celebracion",
)
SITUACIONES = (
    "discusion", "chiste", "doble_sentido", "insulto_suave", "reaccion",
    "confesion", "exito", "fracaso", "silencio", "general",
)

# Señales locales de humor dominicano / sarcasmo / doble sentido (heurística).
SENALES_DO = {
    "risa": ["jajaja", "jeje", "jsjs", "me muero", "te mataste", "que vaina", "diablo"],
    "sarcasmo": ["claro que sí", "seguro", "ajá", "ajá", "como no", "imagínate", "imaginate", "qué bien"],
    "doble_sentido": ["tú sabe", "tu sabe", "esa vaina", "el palo", "la yuca", "está duro", "esta duro"],
    "enojo": ["coño", "carajo", "basta", "cállate", "callate", "respeta"],
    "sorpresa": ["qué", "en serio", "no puede ser", "wow", "dios mío", "dios mio"],
    "celebracion": ["wepa", "eso es", "tú eres un", "tu eres un", "brutal", "duro"],
}


def _post_ollama(payload, timeout=90, base="http://127.0.0.1:11434"):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def modelos_disponibles(base="http://127.0.0.1:11434"):
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/tags", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name") for m in (data.get("models") or []) if m.get("name")]
    except Exception:
        return []


def _heuristica(texto):
    t = (texto or "").lower()
    emos, sits = set(), set()
    for emo, claves in SENALES_DO.items():
        if any(c in t for c in claves):
            emos.add(emo)
            if emo in {"risa", "sarcasmo", "doble_sentido"}:
                sits.add("chiste" if emo == "risa" else emo if emo != "doble_sentido" else "doble_sentido")
            if emo == "enojo":
                sits.add("discusion")
            if emo == "celebracion":
                sits.add("exito")
            if emo == "sorpresa":
                sits.add("reaccion")
    if "!" in t or "¡" in t:
        emos.add("tension")
        sits.add("reaccion")
    if not emos:
        emos.add("confusion" if "?" in t else "risa" if len(t) > 40 else "sorpresa")
    if not sits:
        sits.add("general")
    humor_do = any(k in t for k in ("vaina", "tú sabe", "tu sabe", "wepa", "tiguere", "tigre", "diablo"))
    return {
        "via": "heuristica_local",
        "emociones": sorted(emos),
        "situaciones": sorted(sits),
        "humor_dominicano": humor_do,
        "sarcasmo": "sarcasmo" in emos,
        "doble_sentido": "doble_sentido" in sits,
        "resumen": (texto or "").strip()[:180] or "Sin texto claro.",
        "confianza": 0.45 if emos else 0.2,
        "nota": "Ollama no respondió o no hay modelo; se usó análisis local por señales del texto.",
    }


def _parsear_json(texto):
    if not texto:
        return None
    m = re.search(r"\{[\s\S]*\}", texto)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def analizar_conversacion(texto, modelo=None, base="http://127.0.0.1:11434"):
    """Devuelve emociones, situaciones y marcas de humor dominicano."""
    texto = (texto or "").strip()
    if len(texto) < 3:
        return {
            "via": "sin_texto",
            "emociones": [],
            "situaciones": ["silencio"],
            "humor_dominicano": False,
            "sarcasmo": False,
            "doble_sentido": False,
            "resumen": "",
            "confianza": 0.0,
            "nota": "No hay conversación para analizar.",
        }
    modelos = modelos_disponibles(base)
    if not modelos:
        return _heuristica(texto)
    elegido = modelo if modelo in modelos else (
        next((m for m in modelos if "llama" in m.lower() or "qwen" in m.lower() or "phi" in m.lower()), modelos[0])
    )
    prompt = (
        "Eres un analista de clips virales en español dominicano. "
        "Lee el texto y responde SOLO JSON con claves: "
        "emociones (lista de: " + ",".join(EMOCIONES) + "), "
        "situaciones (lista de: " + ",".join(SITUACIONES) + "), "
        "humor_dominicano (bool), sarcasmo (bool), doble_sentido (bool), "
        "resumen (string corto), confianza (0 a 1). "
        "No inventes hechos que no estén en el texto.\n\nTexto:\n" + texto[:2500]
    )
    try:
        data = _post_ollama({
            "model": elegido,
            "stream": False,
            "messages": [
                {"role": "system", "content": "Responde únicamente JSON válido."},
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0.2},
        }, base=base)
        contenido = ((data.get("message") or {}).get("content")) or ""
        parsed = _parsear_json(contenido)
        if not parsed:
            h = _heuristica(texto)
            h["nota"] = "Ollama respondió sin JSON usable; se usó la heurística local."
            h["modelo"] = elegido
            return h
        emos = [e for e in (parsed.get("emociones") or []) if e in EMOCIONES][:6]
        sits = [s for s in (parsed.get("situaciones") or []) if s in SITUACIONES][:6]
        if not emos:
            emos = _heuristica(texto)["emociones"]
        if not sits:
            sits = _heuristica(texto)["situaciones"]
        return {
            "via": "ollama",
            "modelo": elegido,
            "emociones": emos,
            "situaciones": sits,
            "humor_dominicano": bool(parsed.get("humor_dominicano")),
            "sarcasmo": bool(parsed.get("sarcasmo")),
            "doble_sentido": bool(parsed.get("doble_sentido")),
            "resumen": str(parsed.get("resumen") or "")[:300],
            "confianza": float(parsed.get("confianza") or 0.6),
            "nota": "Análisis local con Ollama. Sin API de pago.",
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        h = _heuristica(texto)
        h["nota"] = f"Ollama no disponible ({type(e).__name__}); heurística local."
        return h


def estado_ollama():
    modelos = modelos_disponibles()
    return {
        "disponible": bool(modelos),
        "modelos": modelos,
        "nota": ("Ollama listo en esta PC." if modelos
                 else "Ollama no tiene modelos o no está en marcha. Se usará heurística local."),
    }
