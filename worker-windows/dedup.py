"""Deduplicación de eventos (Etapa 2).

(i) fusionar_eventos: dos intervalos [start_s, end_s] que se solapan más del
    70% del intervalo MENOR se consideran el mismo momento. Se conserva el de
    mayor score; el descartado queda en la lista con status="duplicado" y el
    conservado anota en su breakdown la clave "fusionado_con" con los datos
    del descartado. La función devuelve la lista COMPLETA (conservados +
    duplicados marcados), ordenada por start_s, para que el llamador decida:
    el analyzer filtra los de status="duplicado" antes de elegir clips y
    cuenta cuántos se fusionaron. Los eventos sin duplicar llevan
    status="conservado" (o el que ya trajeran). No muta la entrada.

(ii) Hash perceptual SIN opencv: se extrae un frame con FFmpeg reducido a
    8x8 en gris crudo y se calcula un aHash de 64 bits (bit=1 si el píxel
    supera la media). Dos eventos con distancia de Hamming <= 6 entre sus
    hashes Y cercanos en el tiempo (sus intervalos se solapan o distan
    <= ventana_s) se marcan como duplicados visuales.
"""
import copy, subprocess
from pathlib import Path
from procesos import SIN_VENTANA

UMBRAL_SOLAPE = 0.70
UMBRAL_HAMMING = 6
VENTANA_VISUAL_S = 60.0


def _solape_ratio(a, b):
    inter = min(a["end_s"], b["end_s"]) - max(a["start_s"], b["start_s"])
    if inter <= 0:
        return 0.0
    menor = min(a["end_s"] - a["start_s"], b["end_s"] - b["start_s"])
    return inter / menor if menor > 0 else 0.0


def fusionar_eventos(eventos, umbral=UMBRAL_SOLAPE):
    """Fusiona eventos solapados >umbral (del intervalo menor). Ver docstring."""
    evs = copy.deepcopy(list(eventos))
    for e in evs:
        e.setdefault("status", "conservado")
    evs.sort(key=lambda e: e["start_s"])
    conservados = []
    for ev in evs:
        destino = None
        for c in conservados:
            if _solape_ratio(ev, c) > umbral:
                destino = c
                break
        if destino is None:
            conservados.append(ev)
            continue
        # Hay solape fuerte: se queda el de mayor score (empate: el primero).
        ganador, perdedor = (ev, destino) if ev.get("score", 0) > destino.get("score", 0) else (destino, ev)
        perdedor["status"] = "duplicado"
        bd = ganador.setdefault("breakdown", {})
        bd.setdefault("fusionado_con", []).append(
            {"start_s": perdedor["start_s"], "end_s": perdedor["end_s"],
             "score": perdedor.get("score"), "type": perdedor.get("type"),
             "motivo": f"solape >{int(umbral * 100)}% del intervalo menor"})
        if ganador is ev:
            conservados[conservados.index(destino)] = ev
    resultado = sorted(evs, key=lambda e: e["start_s"])
    return resultado


def contar_duplicados(eventos):
    return sum(1 for e in eventos if e.get("status") == "duplicado")


# --- Hash perceptual (aHash 8x8, sin opencv) ---------------------------------

def hash_perceptual(path, segundo=0.0):
    """aHash 64 bits del frame en `segundo`. Devuelve int o None si FFmpeg falla."""
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(segundo), "-i", str(path),
                            "-frames:v", "1", "-vf", "scale=8:8,format=gray",
                            "-f", "rawvideo", "-"],
                           capture_output=True, timeout=120, creationflags=SIN_VENTANA)
        datos = r.stdout
        if r.returncode != 0 or len(datos) < 64:
            return None
        pix = datos[:64]
        media = sum(pix) / 64.0
        h = 0
        for i, p in enumerate(pix):
            if p > media:
                h |= (1 << (63 - i))
        return h
    except Exception:
        return None


def distancia_hamming(a, b):
    if a is None or b is None:
        return None
    return bin(int(a) ^ int(b)).count("1")


def son_similares(hash_a, hash_b, umbral=UMBRAL_HAMMING):
    d = distancia_hamming(hash_a, hash_b)
    return d is not None and d <= umbral


def _cercanos_en_tiempo(a, b, ventana_s):
    gap = max(a["start_s"], b["start_s"]) - min(a["end_s"], b["end_s"])
    return gap <= ventana_s


def marcar_duplicados_visuales(eventos, fuente=None, hashes=None,
                               umbral=UMBRAL_HAMMING, ventana_s=VENTANA_VISUAL_S):
    """Marca como duplicados visuales los eventos con hash similar (<=umbral)
    y cercanos en el tiempo. `hashes`: lista paralela a `eventos` (si es None,
    se calcula con FFmpeg el frame central de cada evento desde `fuente`).
    Entre dos similares se conserva el de mayor score. No muta la entrada."""
    evs = copy.deepcopy(list(eventos))
    if hashes is None:
        hashes = []
        for e in evs:
            medio = (e["start_s"] + e["end_s"]) / 2.0
            hashes.append(hash_perceptual(fuente, medio) if fuente else None)
    for i in range(len(evs)):
        if evs[i].get("status") == "duplicado":
            continue
        for j in range(i + 1, len(evs)):
            if evs[j].get("status") == "duplicado":
                continue
            if not _cercanos_en_tiempo(evs[i], evs[j], ventana_s):
                continue
            d = distancia_hamming(hashes[i], hashes[j])
            if d is None or d > umbral:
                continue
            perdedor, ganador = (j, i) if evs[j].get("score", 0) <= evs[i].get("score", 0) else (i, j)
            evs[perdedor]["status"] = "duplicado"
            bd = evs[ganador].setdefault("breakdown", {})
            bd.setdefault("fusionado_con", []).append(
                {"start_s": evs[perdedor]["start_s"], "end_s": evs[perdedor]["end_s"],
                 "score": evs[perdedor].get("score"), "type": evs[perdedor].get("type"),
                 "motivo": f"duplicado visual: distancia de Hamming {d} <= {umbral}"})
            if perdedor == i:
                break
    return evs
