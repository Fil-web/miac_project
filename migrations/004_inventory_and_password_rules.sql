-- Перенос инвентарного учёта с АРМ на комплектующие и уточнение
-- требования смены первичного пароля. Скрипт идемпотентен.

BEGIN;

-- Прежние номера АРМ сохраняются для совместимости, но новые записи их не требуют.
ALTER TABLE workstations ALTER COLUMN inventory_number DROP NOT NULL;
COMMENT ON COLUMN workstations.inventory_number IS
    'Устаревший инвентарный номер АРМ; для новых записей не используется';

ALTER TABLE parts ADD COLUMN IF NOT EXISTS inventory_number VARCHAR(150);
COMMENT ON COLUMN parts.inventory_number IS
    'Уникальный инвентарный номер комплектующего; может отсутствовать';

CREATE INDEX IF NOT EXISTS ix_parts_inventory_number ON parts (inventory_number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_parts_inventory_number_not_null
    ON parts (inventory_number)
    WHERE inventory_number IS NOT NULL AND inventory_number <> '';

-- Администраторы не направляются на обязательную смену первичного пароля.
UPDATE users AS account
SET must_change_password = FALSE,
    updated_at = CURRENT_TIMESTAMP
FROM roles AS role
WHERE account.role_id = role.id
  AND role.name = 'admin'
  AND account.must_change_password IS TRUE;

COMMIT;
