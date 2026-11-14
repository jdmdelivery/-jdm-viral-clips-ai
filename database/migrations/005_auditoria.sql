-- JDM VIRAL CLIPS AI — Migración 005 (auditoría 2026-11-14: confirmación
-- real de grabación y descarte de momentos)
-- Compatible con PostgreSQL (Render) y SQLite local.
--
-- Qué crea AUTOMÁTICAMENTE SQLAlchemy create_all() en una BD NUEVA (no hace
-- falta aplicar esta migración): las columnas live_sessions.started_at y
-- events.descartado junto al resto del esquema.
--
-- Qué necesita esta migración en una BD EXISTENTE:
--   1) La columna live_sessions.started_at (create_all NO altera tablas).
--   2) La columna events.descartado.
-- En la práctica NINGUNA BD necesita aplicarla a mano: ambas columnas están
-- en la lista de auto-migración de apps/api/main.py (_COLUMNAS_POST_001) y
-- la API las añade al arrancar si faltan (idempotente). Este archivo queda
-- como documentación del esquema, igual que 002/003/004.
-- Los ALTER fallarán si la columna ya existe: aplicarlos una sola vez.
--
-- started_at: instante en que el TRABAJADOR confirma que la captura empezó
-- (al completar el trabajo «iniciar_grabacion»). Antes la API marcaba la
-- sesión «grabando» al pulsar el botón, sin confirmación real; ahora el
-- flujo es creada -> iniciando -> grabando (con started_at) -> deteniendo
-- -> detenida, y «error» si el trabajo llega a fallido.
--
-- descartado: los momentos descartados desde el panel se ocultan de
-- GET /events (salvo incluir_descartados=true) pero NO se borran.

-- 1) Confirmación real de inicio de grabación.
ALTER TABLE live_sessions ADD COLUMN started_at TIMESTAMP;

-- 2) Descarte de momentos detectados.
ALTER TABLE events ADD COLUMN descartado BOOLEAN DEFAULT FALSE;
UPDATE events SET descartado = FALSE WHERE descartado IS NULL;
