-- JDM VIRAL CLIPS AI — Migración 004 (versión del motor del trabajador)
-- Compatible con PostgreSQL (Render) y SQLite local.
--
-- Qué crea AUTOMÁTICAMENTE SQLAlchemy create_all() en una BD NUEVA (no hace
-- falta aplicar esta migración): la columna workers.version (VARCHAR,
-- defecto '') junto al resto del esquema.
--
-- Qué necesita esta migración en una BD EXISTENTE:
--   1) La columna workers.version (create_all NO altera tablas).
-- El ALTER fallará si la columna ya existe: aplicarlo una sola vez.
--
-- La versión la reporta el trabajador en cada latido
-- (POST /workers/heartbeat con body opcional {"version": "1.1.0"}) y el
-- panel la muestra en «Administración — Trabajadores» como «Motor v…».
-- Los trabajadores viejos no envían versión: su valor queda en ''.

-- 1) Versión del motor por trabajador.
ALTER TABLE workers ADD COLUMN version VARCHAR DEFAULT '';
UPDATE workers SET version = '' WHERE version IS NULL;
