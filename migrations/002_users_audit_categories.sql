-- Обновление существующей БД: учётные записи сотрудников, мягкое удаление
-- событий аудита и расширенный набор категорий комплектующих.

BEGIN;

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_employee_not_null
    ON users (employee_id) WHERE employee_id IS NOT NULL;

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS deleted_by BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS deletion_reason TEXT;

COMMENT ON COLUMN audit_log.is_deleted IS 'Признак скрытия события администратором без физического удаления';
COMMENT ON COLUMN audit_log.deleted_at IS 'Дата и время скрытия события';
COMMENT ON COLUMN audit_log.deleted_by IS 'Администратор, скрывший событие';
COMMENT ON COLUMN audit_log.deletion_reason IS 'Обязательное основание скрытия события';

CREATE INDEX IF NOT EXISTS ix_audit_log_is_deleted
    ON audit_log (is_deleted, "timestamp" DESC);

INSERT INTO categories (name, description) VALUES
    ('Видеокарты', 'Дискретные видеокарты и графические ускорители'),
    ('Сетевые карты', 'Проводные и беспроводные сетевые адаптеры'),
    ('Процессоры', 'Центральные процессоры'),
    ('Материнские платы', 'Системные и материнские платы'),
    ('Системы охлаждения', 'Кулеры, вентиляторы и радиаторы'),
    ('Платы расширения', 'Контроллеры и прочие платы расширения'),
    ('Кабели и адаптеры', 'Интерфейсные кабели, переходники и адаптеры')
ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, is_active = TRUE;

COMMIT;
