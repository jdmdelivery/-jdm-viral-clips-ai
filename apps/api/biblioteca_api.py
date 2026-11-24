"""Puente de la API a la biblioteca de memes/sonidos/efectos.

En Render usa la biblioteca del repo (worker-windows/biblioteca).
En la PC del motor puede preferir Desktop\\worker-windows\\biblioteca.
Los ids seed son estables (seed-…) para que el worker resuelva lo mismo.
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WORKER = _ROOT / "worker-windows"
if str(_WORKER) not in sys.path:
    sys.path.insert(0, str(_WORKER))

import biblioteca_recursos  # noqa: E402
import capas_recursos  # noqa: E402
import descargar_recursos  # noqa: E402
import ollama_local  # noqa: E402
import editor_inteligente  # noqa: E402


def cfg_desde_entorno():
    env = os.environ.get("JDM_BIBLIOTECA_DIR", "").strip()
    desk = Path(r"C:\Users\thene\OneDrive\Attachments\Desktop\worker-windows\biblioteca")
    repo = _WORKER / "biblioteca"
    # Render / Linux: siempre la del repo. En Windows local: Desktop si existe.
    if env:
        raiz = Path(env)
    elif os.environ.get("RENDER") or os.name != "nt" or not desk.is_dir():
        raiz = repo
    else:
        raiz = desk
    raiz.mkdir(parents=True, exist_ok=True)
    clips = os.environ.get("JDM_CLIPS_DIR", "").strip()
    return {
        "carpeta_biblioteca": str(raiz),
        "carpeta_clips": clips or str(Path(r"C:\Users\thene\JDMViralClips\clips")),
    }


def asegurar_biblioteca():
    cfg = cfg_desde_entorno()
    capas_recursos.asegurar_ejemplos(cfg)
    return cfg


def capas_clip_key(cid: int) -> str:
    return f"clip_capas_{int(cid)}"
