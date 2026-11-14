"""Generador local de títulos (misma lógica que la API; sin servicios de pago).
Nunca inventa declaraciones: sin transcripción describe el tipo de momento."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))
try:
    from titles import generar  # noqa: F401  (fuente única en apps/api/titles.py)
except Exception:  # copia mínima por si el trabajador se instala solo
    def generar(tipo="general", modo="general", puntuacion=0, transcripcion=None):
        ctx = {"gta": "GTA", "alofoke": "La Casa de Alofoke"}.get(modo, "el LIVE")
        titulos = [f"😱 Momento destacado en {ctx}", f"Esto pasó en {ctx} y nadie lo esperaba",
                   f"El clip del momento en {ctx}", f"🔥 Momento fuerte en pleno LIVE de {ctx}",
                   f"Lo que la cámara captó en {ctx}"]
        return {"titulos": titulos,
                "descripciones": {p: f"Momento detectado por JDM Viral Clips AI en {ctx}. Puntuación estimada: {puntuacion}/100." for p in ("tiktok", "facebook", "instagram", "youtube")},
                "hashtags": ["#Viral", "#Clip"], "miniatura_texto": "MOMENTO DESTACADO",
                "basado_en_transcripcion": bool(transcripcion)}
