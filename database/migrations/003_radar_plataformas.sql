-- JDM VIRAL CLIPS AI — Migración 003 (enlaces de plataformas + radar en vivo)
-- Compatible con PostgreSQL (Render) y SQLite local.
--
-- Qué crea AUTOMÁTICAMENTE SQLAlchemy create_all() en una BD NUEVA (no hace
-- falta aplicar esta migración): la columna live_sessions.radar_activo
-- (Boolean, defecto FALSE) junto al resto del esquema.
--
-- Qué necesita esta migración en una BD EXISTENTE:
--   1) La columna live_sessions.radar_activo (create_all NO altera tablas).
-- El ALTER fallará si la columna ya existe: aplicarlo una sola vez.
--
-- Sin cambios de esquema para las fuentes «plataforma»: sources.kind es
-- VARCHAR libre y pasa a admitir el valor 'plataforma' (regla de Jose
-- 2026-11-14: los enlaces de YouTube/Facebook/Twitch/TikTok/Instagram/X se
-- aceptan y la señal la resuelve el trabajador en su PC al grabar).

-- 1) Radar en vivo por sesión (ON/OFF desde el panel: POST /sessions/{id}/radar).
ALTER TABLE live_sessions ADD COLUMN radar_activo BOOLEAN DEFAULT FALSE;
UPDATE live_sessions SET radar_activo = FALSE WHERE radar_activo IS NULL;
