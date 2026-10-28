-- Мягкое удаление комплектующих и учётных записей с возможностью восстановления.
-- Скрипт идемпотентен и подходит для существующего Docker-тома PostgreSQL.

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_by BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS deletion_reason TEXT;

COMMENT ON COLUMN users.is_deleted IS 'Признак мягкого удаления учётной записи';
COMMENT ON COLUMN users.deleted_at IS 'Дата и время мягкого удаления учётной записи';
COMMENT ON COLUMN users.deleted_by IS 'Администратор, удаливший учётную запись';
COMMENT ON COLUMN users.deletion_reason IS 'Обязательное основание удаления учётной записи';

CREATE INDEX IF NOT EXISTS ix_users_is_deleted ON users (is_deleted, created_at DESC);

ALTER TABLE parts ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE parts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE parts ADD COLUMN IF NOT EXISTS deleted_by BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE parts ADD COLUMN IF NOT EXISTS deletion_reason TEXT;

COMMENT ON COLUMN parts.is_deleted IS 'Признак мягкого удаления карточки комплектующего';
COMMENT ON COLUMN parts.deleted_at IS 'Дата и время мягкого удаления карточки';
COMMENT ON COLUMN parts.deleted_by IS 'Администратор, удаливший карточку';
COMMENT ON COLUMN parts.deletion_reason IS 'Обязательное основание удаления карточки';

CREATE INDEX IF NOT EXISTS ix_parts_is_deleted ON parts (is_deleted, created_at DESC);
