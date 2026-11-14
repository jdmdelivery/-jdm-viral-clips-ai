"""Generador propio de borradores de CapCut (JianYing) para Windows.

Solo biblioteca estándar (más ffprobe opcional si está disponible, como en
analyzer.py): crea la carpeta del borrador con draft_content.json y
draft_meta_info.json en la estructura estándar mínima, para que CapCut lo
abra como un proyecto vertical 9:16 con los clips en orden y un texto con
el título de cada clip. Si CapCut no está instalado o no se detecta su
carpeta de borradores, el trabajador usa copiar_respaldo() y Jose importa
los clips a mano.

Generador PROPIO escrito desde la especificación del formato; no copia
código de pyCapCut ni de ninguna otra librería.
"""
import json, os, re, shutil, subprocess, time, uuid
from pathlib import Path

CARACTERES_INVALIDOS = '<>:"/\\|?*'


def _nuevo_id():
    return str(uuid.uuid4())


def sanear_nombre(nombre):
    """Nombre seguro para una carpeta de Windows: sin caracteres inválidos
    ni de control, espacios colapsados a uno solo y 'borrador' si queda
    vacío."""
    texto = "" if nombre is None else str(nombre)
    limpio = "".join(" " if (c in CARACTERES_INVALIDOS or ord(c) < 32) else c
                     for c in texto)
    limpio = " ".join(limpio.split()).strip(" .")
    return limpio or "borrador"


def _carpeta_unica(carpeta, nombre):
    """Subcarpeta carpeta/nombre; si ya existe, sufijo ' (2)', ' (3)'..."""
    base = Path(carpeta) / nombre
    if not base.exists():
        return base
    n = 2
    while True:
        candidato = Path(carpeta) / f"{nombre} ({n})"
        if not candidato.exists():
            return candidato
        n += 1


def detectar_carpeta_borradores(config):
    """Carpeta de borradores de CapCut, o None si no se detecta:
    1) config['capcut_carpeta'] si existe como carpeta;
    2) %LOCALAPPDATA%\\CapCut\\User Data\\Projects\\com.lveditor.draft
       si existe; 3) None."""
    config = config or {}
    configurada = config.get("capcut_carpeta")
    if configurada and Path(configurada).is_dir():
        return Path(configurada)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidata = (Path(local_app_data) / "CapCut" / "User Data"
                     / "Projects" / "com.lveditor.draft")
        if candidata.is_dir():
            return candidata
    return None


def _sondear_video(ruta):
    """(ancho, alto) reales vía ffprobe si está disponible; si algo falla,
    el formato vertical por defecto del canal (1080x1920). Nunca bloquea:
    la duración válida es siempre la del item, no la de ffprobe."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(ruta)],
            capture_output=True, text=True, timeout=30)
        datos = json.loads(r.stdout or "{}")
        flujos = datos.get("streams") or []
        if flujos:
            ancho = int(flujos[0].get("width") or 0)
            alto = int(flujos[0].get("height") or 0)
            if ancho > 0 and alto > 0:
                return ancho, alto
    except Exception:
        pass
    return 1080, 1920


def _material_video(item):
    ruta = str(item.get("ruta_video", ""))
    duracion_us = int(round(float(item.get("duracion_s") or 0) * 1_000_000))
    ancho, alto = _sondear_video(ruta)
    return {
        "id": _nuevo_id(),
        "type": "video",
        "material_name": Path(ruta.replace("\\", "/")).name,
        "path": ruta,
        "duration": duracion_us,
        "width": ancho,
        "height": alto,
        "crop": {"lower_left_x": 0.0, "lower_left_y": 0.0,
                 "lower_right_x": 1.0, "lower_right_y": 0.0,
                 "upper_left_x": 0.0, "upper_left_y": 1.0,
                 "upper_right_x": 1.0, "upper_right_y": 1.0},
        "category_name": "local",
        "check_flag": 1,
    }, duracion_us


def _material_texto(titulo):
    contenido = {
        "text": titulo,
        "styles": [{
            "range": [0, len(titulo)],
            "size": 15.0,
            "bold": False,
            "italic": False,
            "underline": False,
            "fill": {"type": 0,
                     "solid": {"red": 1.0, "green": 1.0, "blue": 1.0,
                               "alpha": 1.0}},
            "font": {"id": "", "path": ""},
            "strokes": [],
            "effectStyle": {"id": "", "path": ""},
            "shadows": [],
            "background": None,
        }],
    }
    return {
        "id": _nuevo_id(),
        "type": "text",
        "content": json.dumps(contenido, ensure_ascii=False),
        "text_color": "#ffffff",
        "font_size": 15.0,
        "alignment": 1,
        "font_name": "",
        "font_path": "",
        "border_width": 0.0,
        "background_color": "",
        "background_alpha": 0.0,
        "line_feed": 1,
        "line_max_width": 0.82,
        "line_spacing": 0.02,
        "force_apply_line_max_width": False,
        "text_alpha": 1.0,
        "is_rich_text": False,
        "check_flag": 1,
    }


def crear_borrador(nombre, items, carpeta):
    """Crea el borrador en carpeta/<nombre saneado> y devuelve su Path."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre_ok = sanear_nombre(nombre)
    subcarpeta = _carpeta_unica(carpeta, nombre_ok)
    subcarpeta.mkdir(parents=True)

    draft_id = _nuevo_id()
    videos, textos = [], []
    segmentos_video, segmentos_texto = [], []
    desplazamiento_us = 0
    for item in items or []:
        material_video, duracion_us = _material_video(item)
        videos.append(material_video)
        segmentos_video.append({
            "id": _nuevo_id(),
            "material_id": material_video["id"],
            "source_timerange": {"start": 0, "duration": duracion_us},
            "target_timerange": {"start": desplazamiento_us,
                                 "duration": duracion_us},
            "speed": 1.0,
            "volume": 1.0,
            "visible": True,
            "reverse": False,
            "transform": {"x": 0.0, "y": 0.0},
            "clip": {"alpha": 1.0, "scale": {"x": 1.0, "y": 1.0},
                     "rotation": 0.0, "transform": {"x": 0.0, "y": 0.0}},
            "render_index": 0,
            "track_render_index": 0,
        })
        titulo = str(item.get("titulo") or "").strip()
        if titulo:
            material_texto = _material_texto(titulo)
            textos.append(material_texto)
            segmentos_texto.append({
                "id": _nuevo_id(),
                "material_id": material_texto["id"],
                "source_timerange": None,
                "target_timerange": {"start": desplazamiento_us,
                                     "duration": duracion_us},
                "render_index": 14000,
                "visible": True,
            })
        desplazamiento_us += duracion_us
    total_us = desplazamiento_us

    track_video = {"id": _nuevo_id(), "type": "video", "name": "",
                   "segments": segmentos_video, "attribute": 0, "flag": 0}
    track_texto = {"id": _nuevo_id(), "type": "text", "name": "",
                   "segments": segmentos_texto, "attribute": 0, "flag": 0}
    plataforma = {"os": "windows", "os_version": "", "app_id": 359289,
                  "app_version": "", "app_source": "cc"}
    contenido = {
        "id": draft_id,
        "name": nombre_ok,
        "version": 360000,
        "new_version": "110.0.0",
        "duration": total_us,
        "fps": 30.0,
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "9:16",
                          "background": None},
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "materials": {
            "videos": videos,
            "texts": textos,
            "audios": [],
            "images": [],
            "effects": [],
            "transitions": [],
            "stickers": [],
            "canvases": [],
            "speeds": [],
            "sound_channel_mappings": [],
            "material_animations": [],
            "loudnesses": [],
        },
        "tracks": [track_video, track_texto],
        "keyframes": {"adjusts": [], "audios": [], "effects": [],
                      "filters": [], "handwrites": [], "stickers": [],
                      "texts": [], "videos": []},
        "platform": plataforma,
        "last_modified_platform": dict(plataforma),
        "relationships": [],
    }
    (subcarpeta / "draft_content.json").write_text(
        json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")

    ahora_us = int(time.time() * 1_000_000)
    tamano_total = 0
    for item in items or []:
        try:
            ruta = Path(str(item.get("ruta_video", "")))
            if ruta.is_file():
                tamano_total += ruta.stat().st_size
        except OSError:
            pass
    meta = {
        "draft_id": draft_id,
        "draft_name": nombre_ok,
        "draft_root_path": str(carpeta),
        "draft_fold_path": str(subcarpeta),
        "draft_json_file": str(subcarpeta / "draft_content.json"),
        "tm_duration": total_us,
        "tm_draft_create": ahora_us,
        "tm_draft_modified": ahora_us,
        "draft_timeline_materials_size": tamano_total,
        "draft_cloud_last_action_download": False,
        "draft_cover": "draft_cover.jpg",
        "draft_is_ai_shorts_in_gui": False,
        "draft_is_invisible": False,
        "draft_segment_extra_info": [],
    }
    (subcarpeta / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return subcarpeta


LEEME = """Clips de JDM Viral Clips AI para CapCut
=======================================

En esta PC no se detecto la carpeta de borradores de CapCut, asi que el
trabajador no pudo crear el borrador automaticamente. Estos son los clips
ya generados, listos para usar:

1. Abre CapCut y crea un proyecto nuevo en formato vertical 9:16.
2. Importa los videos de esta carpeta (boton Importar).
3. Arrastralos a la linea de tiempo en el mismo orden en que aparecen
   aqui (el nombre de cada archivo es el titulo del clip).
4. Ajusta textos, musica y efectos a tu gusto, y exporta el resultado.

Carpeta del borrador: {nombre}
Clips incluidos: {cantidad}
"""


LEEME_PAQUETE = """Paquete de clips de JDM Viral Clips AI para CapCut
====================================================

¿Qué es este paquete?
---------------------
Es un paquete de importación manual para CapCut generado por
JDM Viral Clips AI. Contiene los clips en MP4 de este proyecto, el
archivo manifiesto.json con el orden y los datos de cada clip, y este
LEEME.txt con las instrucciones.

Proyecto: {nombre}
Carpeta del paquete: {carpeta}
Clips incluidos: {cantidad}

Cómo importarlo en CapCut
--------------------------
1. Abre CapCut y crea un proyecto nuevo en formato vertical 9:16.
2. Importa los MP4 de esta carpeta (botón Importar) en el orden en
   que aparecen en manifiesto.json: ese es el orden en el que deben
   ir en la línea de tiempo.
3. Arrastra cada clip a la línea de tiempo respetando ese orden.
4. Usa los campos inicio_s y fin_s de cada clip en manifiesto.json
   como referencia de dónde iba ese clip en la grabación original
   (inicio_s es el segundo de inicio y fin_s el segundo de fin en la
   grabación); duracion_s es su duración en segundos.
5. Ajusta textos, música y efectos a tu gusto, y exporta el resultado.

Nota sobre el borrador nativo
-----------------------------
Si el trabajador ya creó un borrador nativo de CapCut para este
proyecto, ese borrador es la vía principal y este paquete es el
respaldo de importación manual: úsalo solo si el borrador no se abre
o si prefieres montar el proyecto a mano desde los MP4.
"""


def crear_paquete(nombre, items, carpeta_datos):
    """Paquete de importación manual para CapCut: crea la carpeta
    <carpeta_datos>/capcut/<nombre saneado>-paquete (única, sin
    sobrescribir paquetes anteriores), copia dentro cada MP4 cuyo
    archivo exista, escribe manifiesto.json solo con los clips
    copiados de verdad y un LEEME.txt en español. Devuelve el Path
    de la carpeta del paquete."""
    destino_base = Path(carpeta_datos) / "capcut"
    destino_base.mkdir(parents=True, exist_ok=True)
    nombre_paquete = f"{sanear_nombre(nombre)}-paquete"
    destino = _carpeta_unica(destino_base, nombre_paquete)
    destino.mkdir(parents=True)
    clips = []
    usados = {}
    for item in items or []:
        origen = Path(str(item.get("ruta_video", "")))
        if not origen.is_file():
            continue
        nombre_archivo = origen.name
        if nombre_archivo in usados:
            usados[nombre_archivo] += 1
            nombre_final = f"{origen.stem} ({usados[nombre_archivo]}){origen.suffix}"
            while nombre_final in usados:
                usados[nombre_archivo] += 1
                nombre_final = f"{origen.stem} ({usados[nombre_archivo]}){origen.suffix}"
            usados[nombre_final] = 1
        else:
            usados[nombre_archivo] = 1
            nombre_final = nombre_archivo
        shutil.copy2(origen, destino / nombre_final)
        clips.append({
            "archivo": nombre_final,
            "titulo": item.get("titulo"),
            "inicio_s": item.get("inicio_s"),
            "fin_s": item.get("fin_s"),
            "duracion_s": item.get("duracion_s"),
        })
    manifiesto = {
        "proyecto": nombre,
        "generador": "JDM Viral Clips AI",
        "clips": clips,
    }
    (destino / "manifiesto.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (destino / "LEEME.txt").write_text(
        LEEME_PAQUETE.format(nombre=nombre, carpeta=destino.name,
                             cantidad=len(clips)),
        encoding="utf-8")
    return destino


def copiar_respaldo(nombre, items, carpeta_datos):
    """Respaldo manual: copia los videos que existan a
    carpeta_datos/capcut/<nombre saneado> con un LEEME.txt en español."""
    destino_base = Path(carpeta_datos) / "capcut"
    destino_base.mkdir(parents=True, exist_ok=True)
    destino = _carpeta_unica(destino_base, sanear_nombre(nombre))
    destino.mkdir(parents=True)
    copiados = 0
    for item in items or []:
        origen = Path(str(item.get("ruta_video", "")))
        if origen.is_file():
            shutil.copy2(origen, destino / origen.name)
            copiados += 1
    (destino / "LEEME.txt").write_text(
        LEEME.format(nombre=destino.name, cantidad=copiados),
        encoding="utf-8")
    return destino
