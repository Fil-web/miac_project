-- Мягкое удаление АРМ с сохранением состава и истории операций.
-- Скрипт идемпотентен и подходит для существующего Docker-тома PostgreSQL.

BEGIN;

ALTER TABLE workstations ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workstations ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE workstations ADD COLUMN IF NOT EXISTS deleted_by BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE workstations ADD COLUMN IF NOT EXISTS deletion_reason TEXT;

COMMENT ON COLUMN workstations.is_deleted IS 'Признак мягкого удаления АРМ';
COMMENT ON COLUMN workstations.deleted_at IS 'Дата и время мягкого удаления АРМ';
COMMENT ON COLUMN workstations.deleted_by IS 'Администратор, удаливший АРМ';
COMMENT ON COLUMN workstations.deletion_reason IS 'Обязательное основание удаления АРМ';

CREATE INDEX IF NOT EXISTS ix_workstations_is_deleted
    ON workstations (is_deleted, created_at DESC);

COMMIT;
