# INFORME DE AUDITORÍA Y REPARACIÓN — JDM VIRAL CLIPS AI

Fecha: 2026-11-14 · Base: commit `f6db5834` · Motor del trabajador: **v1.2.0**

Regla aplicada en todo el lote (pedido del dueño): **ningún botón decorativo,
ningún éxito falso**. Cada botón ejecuta una acción real contra la API, los
errores se muestran tal cual y los estados que ve el panel son solo los que
el trabajador confirma.

---

## 1. Errores encontrados

### Centro LIVE
1. **Estados de grabación falsos.** La API marcaba la sesión «grabando» al
   pulsar INICIAR GRABACIÓN y «detenida» al pulsar DETENER, sin que el
   trabajador hubiera empezado (o terminado) la captura. Si el trabajo
   fallaba en la PC, el panel seguía diciendo «grabando».
2. **ABRIR LIVE trataba todo como video directo.** Con un enlace de YouTube
   (o de otra plataforma) el reproductor recibía la URL de la página, fallaba
   y mostraba un aviso de «CORS» en rojo que parecía un error del sistema.
   No había reproductor oficial de YouTube ni alternativa para plataformas
   sin embed.
3. **`cargarSesion()` era un stub vacío.** El selector de sesión tenía su
   `onchange` conectado a una función que no hacía nada: cambiar de sesión
   no restauraba estado de grabación, radar ni clips.
4. **El estado del trabajador no se veía en el Centro LIVE.** Se podía
   «iniciar» una grabación sin motor conectado y el trabajo quedaba en cola
   sin que el panel lo dijera.
5. **PROYECTO CAPCUT no mostraba resultado.** El botón encolaba el trabajo y
   el mensaje quedaba en «encolado»: nunca se supo si el borrador se creó,
   dónde quedó, ni si falló.
6. **Sin paquete de importación para CapCut.** El trabajador creaba el
   borrador (o la copia de respaldo), pero no existía un paquete con los MP4
   y un manifiesto de tiempos para importar a mano de forma ordenada.
7. **VER MOMENTOS sin acciones reales por momento.** La tabla de eventos no
   tenía GENERAR CLIP ni DESCARTAR por evento, no mostraba motivo ni
   duración, no se refrescaba sola y tenía un botón «PUBLICAR» que navegaba
   a otra sección sin relación con el evento.
8. **El botón de radar no reflejaba un estado verificable** ni cuántos
   momentos llevaba detectados la sesión, y GENERAR CLIPS desde el LIVE
   obligaba a salir del módulo (solo navegaba a «Analizar con IA»).

### Resto de secciones (auditoría de cableado)
9. El nuevo `tests/test_cableado.py` recorre **todos** los `onclick`/
   `onchange` de `index.html` y de las plantillas de `app.js`, todos los
   ids referenciados desde `app.js` y **todas** las rutas `api()`/`fetch()`
   del panel contra la tabla de rutas de la API: **ningún otro botón muerto
   ni endpoint inexistente** en Proyectos/Importar, Analizar, Eventos,
   Clips, Editor, Calendario, Mis redes sociales, Publicar, Piloto
   automático, Administración, Configuración, Trabajos y Estadísticas.
10. Los trabajos de un trabajador viejo incompatible (p. ej. `radar_sesion`
    con «Tipo de trabajo no soportado») ya quedaban marcados `fallido` con
    su motivo en la columna Error de la tabla de Trabajos tras 3 intentos.
    Confirmado en la auditoría: **no se cambió**.

## 2. Errores corregidos

1. **Flujo de estados con confirmación real (API).** Nuevo flujo:
   `creada → iniciando → grabando → deteniendo → detenida`, con `error` si
   el trabajo llega a `fallido`. `POST /start` deja la sesión en
   `iniciando`; solo cuando el trabajador **completa** el trabajo
   `iniciar_grabacion` la API la pasa a `grabando` y guarda `started_at`.
   `POST /stop` deja `deteniendo` hasta que el trabajador confirma.
   La guarda de grabación única bloquea también contra `iniciando`.
   Columna `live_sessions.started_at` añadida al modelo y a la
   auto-migración de arranque (migración 005 documentada).
2. **ABRIR LIVE por tipo de fuente (panel).** YouTube (`watch?v=`,
   `youtu.be`, `/live/`, `/shorts/`, `/embed/`) → reproductor **oficial
   embebido** (iframe `youtube-nocookie`) dentro del panel. HLS/MP4 → el
   `<video>` de siempre (Hls.js). Otra plataforma → mensaje informativo
   claro + botón «Abrir en la plataforma». El aviso de CORS de señales
   directas pasa a estilo neutro `.info` (ya no parece un error) y solo
   aparece cuando aplica.
3. **`cargarSesion()` implementada de verdad:** al cambiar de sesión se
   restaura el estado de grabación (distintivo y contador si está
   grabando; seguimiento si está iniciando/deteniendo), la etiqueta del
   radar, el contador de momentos y los clips de la sesión.
4. **Chip de motor en el Centro LIVE:** «🟢 Motor conectado vX» o
   «🔴 TRABAJADOR DESCONECTADO» según el último latido (≤30 s) de
   `GET /workers`. Sin motor, INICIAR GRABACIÓN queda **deshabilitado**
   con el motivo visible. Refresco cada 15 s con la sección visible.
5. **INICIAR/DETENER con seguimiento (panel):** tras pulsar, el panel
   sigue el trabajo (poll 2 s → 5 s) y solo muestra
   «🔴 GRABANDO EN VIVO — HH:MM:SS» (contador desde `started_at`) cuando
   la API confirma `grabando`. Si el trabajo falla, muestra el error real
   del trabajador. Timeout de 90 s con mensaje honesto. Al recargar la
   página, el estado se reconstruye desde la API.
6. **RADAR con estado real y contador:** el botón muestra
   «📡 RADAR ACTIVADO — DESACTIVAR — N momentos» según `radar_activo` de
   la API y el conteo real de `GET /events?session_id=`.
7. **VER MOMENTOS completo (API + panel):** auto-refresco cada 15 s sin
   recargar; por momento: inicio–fin, duración (`duracion_s`), motivo
   (desglose de señales), puntuación; **GENERAR CLIP** por evento
   (`POST /events/{id}/clip`, con anti-duplicados y 409 si ya tiene clip);
   **DESCARTAR** (`POST /events/{id}/descartar` + columna
   `events.descartado` en la auto-migración: se oculta, no se borra);
   **REPRODUCIR** solo cuando el clip está en el servidor
   (`clip_descargable`); si vive en la PC se muestra su ruta.
   Botón «PUBLICAR» eliminado de la tabla de eventos (corrección anotada:
   la publicación se hace desde Clips/Publicar).
8. **GENERAR CLIPS dentro del LIVE:** bloque «Clips de esta sesión» en el
   Centro LIVE (sin salir del módulo) con **GENERAR TODOS LOS CLIPS**
   (`POST /sessions/{id}/generar-clips`: encola solo eventos no
   descartados, sin clip y sin trabajo en curso; devuelve encolados y
   omitidos) y refresco de la lista tras generar.
9. **PROYECTO CAPCUT con resultado visible (API + trabajador + panel):**
   el panel sigue el trabajo hasta `completado`/`fallido` y muestra la
   ruta real del borrador o de la carpeta de respaldo. Además el
   trabajador ahora crea **siempre** el paquete de importación en
   `<datos>/capcut/<nombre>-paquete/` con los MP4 copiados,
   `manifiesto.json` (archivo, título, `inicio_s`/`fin_s` en la sesión,
   duración — solo de clips que existen de verdad) y `LEEME.txt`; la ruta
   del paquete viaja en el resultado del trabajo y el panel la muestra.
   La API incluye `clip_id`, `start_s` y `end_s` en el payload del
   trabajo. Éxito solo con trabajo completado.
10. **Soportes de API:** `GET /jobs` incluye `result` parseado;
    `GET /clips` acepta `?session_id=`; `GET /events` añade `descartado`,
    `duracion_s`, `tiene_clip`, `clip_id`, `clip_file_path`,
    `clip_descargable`.

## 3. Botones reparados

| Botón / control | Estado |
|---|---|
| ABRIR LIVE | Reparado: embed oficial de YouTube / video directo / alternativa externa según la fuente |
| INICIAR GRABACIÓN | Reparado: deshabilitado sin motor; distintivo 🔴 solo tras confirmación del trabajador |
| DETENER GRABACIÓN | Reparado: «Detención solicitada…» hasta la confirmación |
| ACTIVAR RADAR VIRAL | Reparado: etiqueta con el estado real de la API + contador de momentos |
| VER MOMENTOS DETECTADOS | Reparado: auto-refresco, motivo, duración y acciones por momento |
| GENERAR CLIP (por momento) | **Nuevo y real**: encola `generar_clip` de ese evento, sin duplicados |
| GENERAR TODOS LOS CLIPS | **Nuevo y real**: encola todos los momentos sin clip de la sesión |
| DESCARTAR (por momento) | **Nuevo y real**: oculta el momento sin borrarlo |
| REPRODUCIR (momento con clip) | **Nuevo y real**: solo si el archivo está en el servidor; si no, muestra la ruta en la PC |
| PROYECTO CAPCUT (sesión) | Reparado: sigue el trabajo y muestra borrador/respaldo + paquete, o el error |
| Selector de sesión | Reparado: `cargarSesion()` ya restaura el estado completo |
| GENERAR CLIPS (Centro LIVE) | Reparado: bloque de clips dentro del módulo, sin salir a otra sección |
| PUBLICAR (tabla de eventos) | **Eliminado** de esa tabla: era navegación sin relación con el evento |

El resto de botones de la aplicación quedaron **verificados por el test de
cableado** (handler definido + endpoint existente) sin cambios.

## 4. Funciones comprobadas

- Cableado completo panel ↔ API (`tests/test_cableado.py`): handlers de
  HTML y de plantillas, ids referenciados, rutas del panel contra la tabla
  de rutas de la API, y presencia en `app.js` de la lógica nueva
  (confirmación con `started_at`, embed `youtube-nocookie`,
  «TRABAJADOR DESCONECTADO», auto-refresco, funciones nuevas).
- Flujo LIVE extremo a extremo con trabajador falso
  (`tests/test_flujo_live.py`), sobre SQLite: crear sesión → fuente HLS →
  iniciar (queda `iniciando`) → el trabajador reclama y comple
...[truncated 2700 chars]