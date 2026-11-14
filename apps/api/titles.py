"""Generador local de títulos/descripciones. Sin APIs de pago.
Nunca inventa declaraciones: si no hay transcripción, describe el TIPO de momento."""
TIPOS = {
    "discusion": ("Discusión intensa", ["discusión", "debate"]),
    "gritos": ("Gritos y reacción fuerte", ["reacción"]),
    "risas": ("Momento de risas", ["risas", "gracioso"]),
    "sorpresa": ("Reacción sorprendente", ["sorpresa", "inesperado"]),
    "confesion": ("Confesión inesperada", ["confesión"]),
    "accion": ("Momento de acción", ["acción"]),
    "persecucion": ("Persecución policial", ["persecución", "gta"]),
    "explosion": ("Explosión en plena acción", ["explosión", "gta"]),
    "choque": ("Choque inesperado", ["choque", "accidente"]),
    "general": ("Momento destacado", ["viral"]),
}
CONTEXTO = {"gta": "GTA", "alofoke": "La Casa de Alofoke", "podcast": "el podcast", "general": "el LIVE"}

def generar(tipo="general", modo="general", puntuacion=0, transcripcion=None):
    etiqueta, claves = TIPOS.get(tipo, TIPOS["general"])
    ctx = CONTEXTO.get(modo, "el LIVE")
    if transcripcion and len(transcripcion.strip()) >= 10:
        frag = transcripcion.strip().replace("\n", " ")[:70]
        base = f'“{frag}…”'
        titulos = [f"{base} 😱", f"Esto se dijo en {ctx} y explotó todo",
                   f"{etiqueta} en {ctx}: {base}", f"No vas a creer lo que pasó en {ctx}",
                   f"El momento del que todos hablan en {ctx}"]
    else:
        titulos = [f"😱 {etiqueta} en {ctx}", f"Esto pasó en {ctx} y nadie lo esperaba",
                   f"{etiqueta}: el clip del momento en {ctx}",
                   f"🔥 {etiqueta} en pleno LIVE de {ctx}",
                   f"Lo que la cámara captó en {ctx}"]
    hashtags = ["#Viral", "#Clip"] + [f"#{c.replace(' ', '')}" for c in claves] + \
               (["#GTA6", "#GTAVI"] if modo == "gta" else ["#LaCasaDeAlofoke", "#Alofoke"] if modo == "alofoke" else ["#EnVivo"])
    desc_base = (f"{etiqueta} detectado automáticamente por JDM Viral Clips AI en {ctx}. "
                 f"Puntuación estimada de viralidad: {puntuacion}/100 (estimación, no garantiza vistas). "
                 "Míralo completo y dime qué opinas 👇")
    return {
        "titulos": titulos,
        "descripciones": {
            "tiktok": desc_base + " " + " ".join(hashtags),
            "facebook": desc_base,
            "instagram": desc_base + "\n" + " ".join(hashtags),
            "youtube": desc_base + "\n" + " ".join(hashtags),
        },
        "hashtags": hashtags,
        "miniatura_texto": etiqueta.upper()[:40],
        "basado_en_transcripcion": bool(transcripcion),
    }
