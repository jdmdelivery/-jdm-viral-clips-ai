# INFORME ETAPA 1 — JDM VIRAL CLIPS AI (resultados reales, 2026-11-14)

## Qué se construyó
- **API FastAPI** (`apps/api/main.py`): auth con pbkdf2 + token de sesión y roles; modelos SQLAlchemy
  para usuarios, trabajadores, sesiones LIVE, fuentes, segmentos, eventos, clips, proyectos, trabajos
  (cola persistente con intentos/error), cuentas sociales, publicaciones, calendario, auditoría y
  configuración. `DATABASE_URL` configurable (SQLite local / PostgreSQL Render). Migración formal en
  `database/migrations/001_inicial.sql`. Validación de fuentes anti-SSRF (bloquea IP privadas/loopback/
  reservadas y hosts que resuelven a ellas; `file://` rechazado; YouTube/TikTok/Facebook/Instagram/Twitch
  devuelven la explicación y las alternativas autorizadas). Publicaciones SOLO como borradores:
  sin cuenta OAuth el estado es «requiere_conexion» y ni siquiera aprobar puede marcar «publicado»
  (probado por test). La API sirve el panel en el mismo puerto.
- **Panel web** (`apps/web/`): SPA en español, negro + morado neón + azul eléctrico, menú con las 15
  secciones, Centro LIVE con reproductor (hls.js por CDN / `<video>` nativo), botones grandes que
  llaman a la API de verdad, listas reales de todo y estados vacíos honestos. La sección Editor dice
  explícitamente que el editor profesional es la Etapa 3 (solo incluye acciones que ya funcionan:
  descargar y enviar a publicar) — sin botones falsos.
- **Trabajador** (`worker-windows/`, corre también en Linux): módulos recorder (FFmpeg, segmentos
  ~10 s, reconexión, unión final, proceso separado de la IA), buffer circular 2–10 min con protección
  de eventos, viral (puntuación 0–100 determinista con desglose guardado), vision (filtro scene de
  FFmpeg), transcribe (faster-whisper si está; si no, «no disponible» sin fingir), clipper (9:16
  1080×1920 con fondo desenfocado, 16:9, 1:1; subtítulos OFF por defecto), títulos locales sin APIs
  de pago (sin transcripción no inventa citas), sync (heartbeat/claim/complete + cola offline JSON),
  worker (bucle). Instalador Windows `install-worker.ps1`/`.bat` + README.
- **`render.yaml`** con servicios independientes (`jdm-viral-clips-ai` + `jdm-viral-clips-ai-db`, plan free)
  y nota de disco efímero. No se tocó ninguna otra app ni servicio.

## Pruebas ejecutadas (resultados, no simulaciones)
1. **pytest: 13/13 en verde** (auth y contraseña incorrecta, panel servido, validación de fuentes:
   privada/loopback/YouTube/file/HLS público, iniciar sin fuente = 400, publicación nunca «publicada»
   sin OAuth, títulos sin citas falsas, cola de trabajos, puntuación determinista y por modos, buffer
   que protege eventos). Un fallo real durante el desarrollo (la explicación de YouTube quedaba tapada
   por el chequeo DNS) se corrigió reordenando la validación.
2. **API en vivo (puerto 8077) con curl**: `/health` OK; registro → primer usuario `admin`; login y
   `/auth/me` OK; crear sesión OK; el panel HTML se sirve en `/`. La BD local de prueba se eliminó al
   terminar (sin credenciales de prueba persistentes).
3. **Pipeline completo** (`tests/run_pipeline.py`, artefactos en `tests/output/`): video generado de
   90 s (0–30 tranquilo, 30–60 audio fuerte + cambio de color cada 5 s, 60–90 tranquilo).
   - Recorder: **9 segmentos** y unión a `grabacion_completa.mp4` en 6,7 s (lectura de archivo, no tiempo real).
   - Hallazgo real corregido: el muxer de segmentos corta en keyframes, así que los inicios NO son
     `i*10` exactos (el primer segmento midió 18,35 s). El analizador ahora mide la duración real de
     cada segmento y acumula la línea de tiempo; el Recorder registra igual los segmentos cerrados.
   - Mediciones (modo alofoke): segmentos tranquilos RMS ≈ −55 dB → **score 7–12**; segmentos del tramo
     fuerte RMS ≈ −22 a −30 dB con 1–2 cambios de escena → **score 45–64**. Los 4 eventos detectados
     caen sobre el tramo fuerte (inicios reales 28,35–58,35 s). Mejor evento: tipo «discusion»,
     58,35–68,35 s, score 64 (clasificación por dinámica de audio; en un video sintético es una
     etiqueta de señal, no una conversación real).
   - Clip generado DURANTE el proceso con contexto 20 s antes / 15 s después: 38,35–83,35 s (45 s).
     **ffprobe verificado: video H.264 1080×1920, audio AAC.** Tiempo evento→clip: **25,4 s** en esta
     VM compartida (exportación de un clip de 45 s); pipeline total del video de 90 s: **34,6 s**.
     Queda dentro del objetivo inicial de 30–120 s para «evento finalizado → clip», medido, no prometido.
4. **Trabajador ↔ API de principio a fin**: se importó el video por la API, se encoló el análisis, el
   trabajador hizo heartbeat, reclamó el trabajo, lo procesó (86,4 s con 3 clips) y lo completó; la API
   persistió **3 eventos con su desglose y 3 clips** (9:16, 45 s, subtítulos: No), visibles en
   `/events`, `/clips` y `/stats`. El trabajador quedó «conectado» en `/workers`.
5. Sintaxis: `node --check` del JS del panel y `py_compile` de todo el Python, OK.

## Lo que NO se hizo / no se pudo probar (honestidad)
- **Despliegue real en Render: NO realizado.** No hay conector ni acceso autorizado a la cuenta de
  Render de Jose en esta sesión. Queda `render.yaml` listo; falta que Jose conecte GitHub/Render.
- **Publicación real en redes: NO realizada.** Requiere crear la app oficial de cada plataforma
  (TikTok Content Posting API, Meta Graph, YouTube Data API), OAuth y aprobaciones/auditorías de cada
  una. Quedan la estructura, los borradores y los estados honestos.
- **Grabación de un LIVE real por internet: NO probada** (en esta VM no hay una fuente LIVE autorizada
  disponible). Se probó la grabación segmentada desde archivo con el mismo código/FFmpeg; el modo en
  vivo añade flags de reconexión de FFmpeg, sin probar contra un stream real.
- **Transcripción: no disponible en esta VM** (faster-whisper no instalado; no se descargó el modelo).
  El código degrada con el aviso correspondiente y no finge transcripciones.
- **OpenCV: no instalado** en esta VM (`opencv_disponible: false` en los resultados); se usó el filtro
  scene de FFmpeg. El seguimiento de personas/personajes de GTA queda para la Etapa 2.
- El instalador de Windows (`.ps1`) está escrito y revisado, pero **no se pudo ejecutar en Windows**
  desde esta VM Linux; el trabajador sí se ejecutó completo en Linux.
- El análisis vía `/projects/{id}/analyze` no lleva «modo» en el payload (entra como general); las
  sesiones LIVE sí llevan su modo al trabajo de grabación. Pendiente menor para la Etapa 2.

## Pendientes que requieren a Jose
1. Cuenta de **GitHub** conectada y repo privado para subir el proyecto.
2. Autorización en **Render** para aplicar el blueprint (servicios nuevos e independientes).
3. Cuando quiera publicar de verdad: crear/autorizar las apps **OAuth** de TikTok, Meta
   (Facebook/Instagram) y YouTube, y pasar sus revisiones.
4. Su **PC Windows** para instalar el trabajador y probar un LIVE autorizado real (GTA o Alofoke)
   y medir el retraso evento→clip en su hardware (RTX 3050).
