# 🚀 JDM VIRAL CLIPS AI

Plataforma en español para grabar transmisiones en vivo autorizadas, detectar momentos
con señales reales, generar clips verticales y preparar su publicación con aprobación del dueño.

**Función principal: LIVE VIRAL RADAR AI** — graba por segmentos de 10 s con buffer circular,
mide audio (FFmpeg astats) y escena por segmento, puntúa 0–100 de forma determinista y
explicable, y genera clips con contexto (20 s antes / 15 s después por defecto) sin detener la grabación.

## Arquitectura (híbrida, Etapa 1)

| Pieza | Dónde vive | Qué hace |
|---|---|---|
| Panel web + API FastAPI | Render (o local) | Usuarios, sesiones LIVE, cola de trabajos, eventos, clips, borradores, calendario, estadísticas |
| PostgreSQL | Render | Datos (sin MP4 dentro) — local usa SQLite |
| Trabajador | **PC Windows de Jose** (también corre en Linux) | FFmpeg: grabar, buffer, analizar, puntuar, exportar clips, sincronizar |

Render tiene disco efímero: **las grabaciones y los clips viven en el trabajador**, nunca en Render.

## Correr en local (probado)

```bash
bash scripts/run-local.sh        # panel + API en http://127.0.0.1:8077
# pruebas:
.venv/bin/python -m pytest tests/ -q
.venv/bin/python tests/run_pipeline.py   # pipeline real con video generado (tarda ~1 min)
```

## Desplegar en Render

1. Sube esta carpeta a un repositorio (privado) de GitHub.
2. Render → New → Blueprint → el repo (usa `render.yaml`: web `jdm-viral-clips-ai` + PostgreSQL `jdm-viral-clips-ai-db`, planes free).
3. Abre la URL, crea tu cuenta (el primer usuario queda como administrador).
4. En el panel → Administración, registra el trabajador y guarda su token.

## Instalar el trabajador Windows

Ver `worker-windows/README.md` (doble clic en `install-worker.bat`).
Requiere Python 3.10+ y FFmpeg. faster-whisper y OpenCV son opcionales: sin ellos el
trabajador lo dice claramente y sigue analizando audio/escena.

## Etapas 2–6 (construidas y probadas — ver `INFORME-ETAPAS-2-6.md`)

Transcripción real con faster-whisper (tiny), puntuación viral v2 con señales de
conversación, deduplicación de eventos, rostros con YuNet, editor profesional
(API + trabajador + panel), adaptadores OAuth de TikTok/Meta/YouTube (publicar solo
con confirmación real), Piloto Automático Viral con bandeja de aprobación, calendario
completo, paginación/índices/rate-limit/límite de subida y guarda de una sola
grabación. **40 tests en verde.** Siguen pendientes (no dependen del código): el
despliegue en Render y las conexiones OAuth reales, que requieren a Jose.

## Límites honestos (Etapa 1)

- No hay publicación real en TikTok/Facebook/Instagram/YouTube: faltan las apps OAuth de cada
  plataforma y sus aprobaciones. El sistema crea **borradores** con estado «requiere conexión»
  y jamás marca nada «publicado» sin confirmación real.
- Las plataformas no permiten grabar desde enlaces públicos: la validación de fuentes lo
  explica y ofrece alternativas autorizadas (tu propio RTMP/HLS, OBS, importar video con permiso).
- La puntuación viral es una **estimación de señales medidas**, no una garantía de vistas.
- Subtítulos automáticos: desactivados por defecto.
- Ver `INFORME-ETAPA1.md` para qué se probó de verdad y qué falta.
