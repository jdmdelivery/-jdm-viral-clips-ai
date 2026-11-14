# Trabajador Windows — JDM Viral Clips AI

Ejecuta en TU computadora: grabación FFmpeg por segmentos de 10 s, buffer circular,
análisis local (audio/escena), puntuación viral determinista y exportación de clips.
La grabación corre en un proceso separado: analizar un clip nunca la detiene.

## Requisitos
- Windows 10/11, Python 3.10+, FFmpeg en el PATH.
- Recomendado por Jose: 16 GB RAM + RTX 3050 (los modelos locales son pequeños; sin GPU usa CPU).

## Instalación
1. Doble clic a `install-worker.bat` (verifica Python/FFmpeg, crea el entorno, pide URL de la API y token).
2. El token se obtiene en el panel web → Administración → Registrar trabajador.
3. Arranque: `.venv\Scripts\python worker.py --config worker-config.json`
4. El panel muestra el trabajador como «conectado» cuando hace heartbeat.

## Módulos
- `recorder.py` grabación/segmentos/reconexión/unión · `buffer.py` buffer circular 2–10 min con protección de eventos
- `viral.py` puntuación 0–100 explicable · `vision.py` cambios de escena · `transcribe.py` faster-whisper opcional
- `clipper.py` exportación 9:16/16:9/1:1 (subtítulos OFF por defecto) · `analyzer.py` pipeline completo
- `sync.py` cliente API + cola offline · `titles.py` títulos locales · `worker.py` bucle principal

## Etapa 2/3 del trabajador (2026-11-14, verificado en esta VM)
- **Transcripción real**: faster-whisper 1.2.1 instalado; `transcribe.py` devuelve texto +
  segmentos con tiempos y genera `.srt`. La ruta directa rompe con PyAV 19, así que usa un
  fallback FFmpeg→array (probado: frase TTS en español transcrita palabra por palabra).
- **viral.py v2**: `puntuar()`/`clasificar()` aceptan transcripción opcional. Sin ella, resultados
  idénticos a la v1. Con ella, componente «conversacion» 0–20 (densidad de habla 0–8 + signos
  ¡!¿? máx 4 + palabras clave de discusión/confesión/reacción máx 8) y
  `total = min(100, round(base*0.85 + conversacion))`, todo explicado en el desglose.
- **dedup.py**: fusión de eventos con solape >70% del intervalo menor (se conserva el mayor score;
  el otro queda `status="duplicado"` y anotado en `fusionado_con`), integrada en `analyzer.py`
  (campo `duplicados_fusionados`), + aHash 8×8 sin opencv (Hamming ≤6 y cercanía temporal =
  duplicado visual).
- **Rostros**: opencv-headless 5.0.0.93 instalado, pero opencv 5 eliminó Haar; `detectar_rostros`
  usa YuNet con `yunet.onnx` (opencv_zoo, en esta carpeta) y Haar si cv2 es 4.x. Verificado:
  foto real = 1 rostro, clip sintético = 0. Alternativa sin opencv: `conteo_movimiento()` por
  FFmpeg, funcional. En el analyzer la señal de rostros es opcional (`con_rostros=False` por defecto).
- **editor.py (Etapa 3)**: trabajo `editar_clip` en `worker.py`. Recorte, reencuadre a
  9:16/16:9/1:1, zoom + offsets, volumen (factor o dB), texto drawtext, logo overlay y subtítulos
  SOLO con `subtitulos=True` (desde `transcript` o transcripción real; sin flag, nunca). Smoke
  verificado: clip de 45 s → editado 10,00 s 1080×1920, texto visible en frame y volumen −6,04 dB
  con factor 0,5. Artefactos en `tests/output/etapa2/`.

## Si apagas la PC
El panel web sigue en línea y muestra el trabajador «desconectado»; los trabajos quedan en cola
y los resultados locales sin enviar se sincronizan al volver (cola-offline.json).
