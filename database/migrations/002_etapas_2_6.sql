-- JDM VIRAL CLIPS AI — Migración 002 (Etapas 2–6)
-- Compatible con PostgreSQL (Render) y SQLite local.
--
-- Qué crea AUTOMÁTICAMENTE SQLAlchemy create_all() en una BD NUEVA (no hace falta
-- aplicar esta migración): todas las tablas de la 001, la columna
-- calendar_entries.status (defecto 'programada') y los índices marcados con
-- index=True en los modelos (jobs.status, events.score, events.session_id,
-- clips.event_id, publications.clip_id, publications.status,
-- calendar_entries.when_at, audit_log.created_at).
--
-- Qué necesita esta migración en una BD EXISTENTE (creada en la Etapa 1):
--   1) La columna calendar_entries.status (create_all NO altera tablas ya creadas).
--   2) Los índices de la Etapa 6 sobre tablas ya existentes.
-- Nota: CREATE INDEX usa IF NOT EXISTS (válido en PostgreSQL y SQLite modernos).
-- El ALTER de abajo fallará si la columna ya existe: aplicarlo una sola vez.

-- 1) Etapa 5 — Calendario: estado de la entrada (programada|cancelada).
--    Cancelar una entrada la marca 'cancelada'; la fila NO se borra.
ALTER TABLE calendar_entries ADD COLUMN status VARCHAR DEFAULT 'programada';
UPDATE calendar_entries SET status = 'programada' WHERE status IS NULL;

-- 2) Etapa 6 — Índices para las consultas frecuentes (listados y filtros).
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_events_score ON events(score);
CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_clips_event_id ON clips(event_id);
CREATE INDEX IF NOT EXISTS idx_publications_clip_id ON publications(clip_id);
CREATE INDEX IF NOT EXISTS idx_publications_status ON publications(status);
CREATE INDEX IF NOT EXISTS idx_calendar_when_at ON calendar_entries(when_at);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at);

-- 3) Etapa 4/5 — Sin cambios de esquema:
--    * Los tokens OAuth cifrados (Fernet) reutilizan social_accounts.token_encrypted (001).
--    * El state de OAuth y la configuración del piloto (autopilot_*) reutilizan
--      la tabla config (clave/valor) de la 001.
--    * social_accounts.status pasa a admitir el valor 'conectado' (es VARCHAR libre).
