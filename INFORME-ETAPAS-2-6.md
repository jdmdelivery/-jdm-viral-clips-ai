# INFORME ETAPAS 2–6 — JDM VIRAL CLIPS AI (resultados reales, 2026-11-14)

Construido sobre la Etapa 1 sin romperla: **la suite completa pasó de 13 a 40 tests, todos en
verde** (re-ejecutado al final, tras integrar el panel: `40 passed in 14.37s`).
Nada de lo siguiente se declara sin haberlo ejecutado; lo no ejecutable está en la sección
de pendientes, con su motivo.

## ETAPA 2 — IA avanzada (trabajador)
- **Transcripción REAL activada**: `faster-whisper 1.2.1` instalado en el `.venv` (con
  ctranslate2 4.9.0) y modelo **tiny** descargado de HuggingFace. Prueba: frase sintetizada
  con `/opt/hatch/bin/tts` (voz española, 6,648 s: «¡Esto es una discusión fuerte! Yo confieso
  que nunca quise que esto pasara en la casa, ¿me estás oyendo?») → transcripción obtenida
  **palabra por palabra idéntica** (el modelo tiny normaliza la puntuación). Artefactos en
  `tests/output/etapa2/` (`frase-prueba.mp3`, `transcripcion-prueba.json`, `frase-prueba.srt`).
  Hallazgo real: la ruta directa de faster-whisper 1.2.1 rompe con PyAV 19
  (`TypeError: open() got an unexpected keyword argument 'metadata_errors'`); `transcribe.py`
  ahora decodifica con FFmpeg a float32/16 kHz y pasa el array a Whisper. Devuelve también
  `segmentos` con tiempos y `generar_srt()`.
- **Puntuación viral v2** (`viral.puntuar(senales, modo, transcripcion=None)`): sin
  transcripción, salida **idéntica a v1** (tests viejos intactos y verdes). Con transcripción:
  componente «conversación» 0–20 = densidad de habla (0–8, satura a 3 palabras/s) + signos
  ¡!¿? (1 pt c/u, máx 4) + palabras clave por categoría disjunta (discusión/confesión/reacción,
  2 pts c/u, máx 8); `total = min(100, round(base × 0.85 + conversación))`. El desglose guarda
  el detalle (`conversacion_detalle`, `formula_conversacion`). `clasificar` con transcripción
  detecta «confesion» y afina «discusion».
- **Eliminación de duplicados** (`dedup.py` nuevo): intervalos con solape >70% del menor se
  fusionan conservando el mayor score (el otro queda `status="duplicado"` y anotado en
  `breakdown["fusionado_con"]`); duplicado visual por aHash de 64 bits (frame FFmpeg 8×8 sin
  OpenCV): distancia de Hamming ≤6 **y** cercanía ≤60 s. Integrado en `analyzer.py` (campo
  `duplicados_fusionados`). Smoke del pipeline de 90 s: 4 eventos, 0 duplicados (correcto).
- **Rostros**: `opencv-python-headless 5.0.0.93` instalado, pero **OpenCV 5 eliminó
  CascadeClassifier y los XML Haar**. Solución real: `detectar_rostros()` usa **YuNet**
  (`FaceDetectorYN`) con el modelo oficial opencv_zoo guardado junto a `vision.py`. Verificado:
  foto real de Jose = **1 rostro**; clip sintético = 0. Alternativa `conteo_movimiento()`
  (FFmpeg) funcional: 4,008 en clip con movimiento, 0,0 en video estático. En el analyzer la
  señal de rostros va tras el flag `con_rostros=False` por defecto.

## ETAPA 3 — Editor profesional (API + trabajador + panel)
- **API**: `POST /clips/{id}/edit` valida (404 clip; 400 formato, trims, offsets −1..1,
  zoom <1, volumen ≤0) y encola el trabajo real `editar_clip`; el clip editado hereda
  evento/sesión/proyecto del origen al completarse. `POST /clips/{id}/logo` (PNG/JPG, máx
  5 MB → 413) y `POST /clips/{id}/thumbnail` (job propio `miniatura`).
- **Trabajador**: `editor.py` nuevo — `aplicar_edicion()` con FFmpeg: recorte, reencuadre
  9:16/16:9/1:1, zoom con crop y offsets, volumen por factor o dB, **drawtext** (DejaVuSans-Bold
  vía textfile), **logo** overlay por esquina, miniatura, y subtítulos **solo** con
  `subtitulos=True` (desde transcript → .srt por frases, o desde Whisper); sin flag jamás quema
  subtítulos. `worker.py` procesa `editar_clip` y `miniatura`.
- **PRUEBA obligatoria, verificada dos veces (agente y coordinador)**: clip de la Etapa 1
  (`clip-evento-5.mp4`, 45 s) + recorte 2→12 s + texto «PRUEBA EDITOR» + volumen 0,5 →
  `tests/output/etapa2/clip-evento-5-editado.mp4`: **ffprobe = 10,000 s, H.264 1080×1920 + AAC**;
  volumen medido por astats −22,54 dB → −28,58 dB (**Δ −6,04 dB** vs −6,02 teórico del factor
  0,5); el frame `frame-texto-editor.jpg` muestra el texto quemado abajo, legible. Extra:
  edición completa (1:1 + zoom 1,5 + offsets + logo + subtítulos desde transcript) → 1080×1080
  con logo y subtítulo visibles en `frame-full-editor.jpg`. Miniatura por job: JPEG real
  1080×1920 en `tests/output/etapa2/worker-smoke/miniaturas/clip-5.jpg`.
- **Panel**: sección Editor funcional (clip real, todos los parámetros, subir logo, encolar
  exportación, miniatura, descargar, lista de clips resultantes).

## ETAPA 4 — Redes sociales (código real, conexión pendiente de Jose)
- `apps/api/social.py`: adaptadores TikTok / Meta (Facebook+Instagram) / YouTube con las
  **URLs OAuth oficiales** (TikTok v2, Graph v19.0, Google OAuth2) y métodos de publicación
  reales (YouTube resumable, Facebook `/me/videos`, TikTok init+upload+confirmación; Instagram
  declara honestamente que exige `video_url` pública + ig_user_id). Credenciales **solo** por
  entorno (`TIKTOK_CLIENT_KEY/SECRET`, `META_APP_ID/SECRET`, `YOUTUBE_CLIENT_ID/SECRET`).
- `GET /social` añade `oauth_configurado` y la nota «Requiere configuración…»;
  `GET /auth/{plataforma}/conectar` sin credenciales → **400**; con credenciales → redirect
  307 oficial con `state` de un solo uso. Callback: intercambia el código contra la API oficial
  y guarda el token **cifrado con Fernet SOLO si existe `JDM_ENCRYPTION_KEY`** (`cryptography`
  instalado en el .venv); sin clave NO se almacena nada. `POST /publications/{id}/publish`
  audita cada intento y **nunca** marca «publicado» sin confirmación real — probado: sin
  credenciales, todo queda en `requiere_conexion`/`sin_conectar` y ningún endpoint produce un
  falso publicado (tests + humo en vivo: publish → `ok:false` con el motivo).
- **Panel** «Mis redes sociales»: estado real por plataforma, si la app OAuth está configurada
  y botón CONECTAR que muestra la respuesta honesta de la API.

## ETAPA 5 — Automatización
- **Piloto Automático Viral**: `GET/PUT /autopilot` (PUT solo admin) persistido en Config:
  `{activo, min_score=80 por defecto, plataformas, hora_inicio, hora_fin, timezone
  America/Santo_Domingo}` (ventana horaria con `zoneinfo`, soporta ventana nocturna).
  `evaluar_piloto` se invoca al crear eventos y al persistir los del trabajador: evento ≥
  umbral → job `generar_clip` (anti-duplicados por event_id); al completarse, crea una
  Publication por plataforma con títulos generados en `requiere_conexion`/`borrador` —
  **nunca publica solo**. Probado de principio a fin con trabajador registrado
  (claim+complete): 4 borradores, 0 publicados; evento de score 10 no crea job; doble
  evaluación no duplica. **Panel**: interruptor + reglas en Configuración.
- **Calendario completo**: `POST /calendar`, `PUT /calendar/{id}` (reprogramar; reactiva si estaba cancelada), `DELETE /calendar/{id}` (cancelar = status «cancelada», la fila NO se borra; columna `status` nueva, también en la migración 002). Todo auditado. **Panel**: crear / reprogramar / cancelar desde la sección Calendario.
- **Estadísticas**: `/stats` añade agregados reales de la BD: `eventos_por_modo`, `clips_por_formato`, `publicaciones_por_estado`, `mejor_score`, `eventos_sobre_umbral`.
## ETAPA 6 — Optimización y seguridad
- **Paginación** `?limit&offset` (defecto 200, máx 500) en eventos, clips, trabajos, publicaciones, sesiones, proyectos, calendario e historial.
- **Índices** (`index=True` + `CREATE INDEX` en la 002): `jobs.status`, `events.score`, `events.session_id`, `clips.event_id`, `publications.clip_id/status`, `calendar_entries.when_at`, `audit_log.created_at`.
- **Límite de subida real** en `/projects/import`: `JDM_MAX_UPLOAD_MB` leído en cada petición (defecto 2048), escritura en chunks de 1 MB, **413** al superar y borrado del parcial (probado con límite 0).
- **Rate-limit de login** en memoria por (email, IP): 5 fallos en 60 s → **429** al siguiente intento; un login correcto fuera del límite limpia el contador (probado con correo propio).
- **Auditoría** ampliada: editar_clip, subir_logo, miniatura, piloto, calendario, intentos de publicación y conexión de redes.
- **Guarda de concurrencia**: `POST /sessions/{id}/start` → **409** si otra sesión ya está «grabando» (una sola grabación activa por trabajador hasta demostrar hardware); detener libera la guarda (probado).
- **Migración** `database/migrations/002_etapas_2_6.sql`: columna `status` en calendario + índices, con comentarios de qué crea `create_all` en BD nuevas.
- **Prueba de humo en vivo** (uvicorn, puerto 8099, BD temporal): health OK; registro→admin; login y /auth/me OK; GET/PUT /autopilot OK; /social con `oauth_configurado:false` y nota de requiere-configuración; conectar TikTok sin credenciales → 400; edit job creado; publicación en `requiere_conexion`; publish → `ok:false` con motivo; calendario crear→cancelada; /stats con las claves nuevas; el panel sirve «Editor profesional» y «PILOTO AUTOMÁTICO VIRAL».
- **Re-ejecución total al integrar**: `pytest tests/ -q` → **40 passed in 14.37s**; `py_compile` de API y trabajador OK; `node --check` del panel OK.

## Tests por etapa (todos ejecutados)
| Suite | Tests | Resultado |
|---|---|---|
| Etapa 1 (`test_api.py` + `test_viral.py`, sin modificar) | 13 | ✅ verdes |
| Trabajador v2 (`test_worker_v2.py`) | 13 | ✅ verdes |
| API etapas (`test_etapas_api.py`) | 14 | ✅ verdes |
| **Total** | **40** | ✅ **40 passed** |

## Pendientes que NO dependen del código (honestidad)
1. **Despliegue en Render**: no hay acceso autorizado a la cuenta de Render de Jose en esta sesión. Jose debe conectar GitHub/Render y autorizar el blueprint (`render.yaml` sigue listo). El despliegue NO se ejecutó.
2. **Publicación real en redes**: requiere que Jose cree las apps OAuth de TikTok / Meta / YouTube, pase sus revisiones/aprobaciones y defina las credenciales + `JDM_ENCRYPTION_KEY` en el entorno. Los caminos de éxito contra las APIs oficiales están implementados pero NO ejecutados contra plataformas reales.
3. **Instalación en su PC Windows**: el instalador y el trabajador están probados en Linux; falta instalar en su Windows (16 GB / RTX 3050) y validar allí.
4. **Prueba con un LIVE real autorizado** (GTA o La Casa de Alofoke): sigue sin probarse contra un stream real; la grabación segmentada está probada desde archivo con el mismo código FFmpeg.
5. Menores: la ventana horaria del piloto está implementada pero los tests usan la configuración sin ventana para no depender de la hora real; Instagram exige URL pública de video (no sirve un archivo local del trabajador) — necesitará alojamiento público del clip cuando se conecte.
