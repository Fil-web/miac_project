-- Схема БД «Учёт комплектующих АРМ МИАЦ» для PostgreSQL 15+.
-- Повторный запуск безопасен для пустой или уже инициализированной схемы.

BEGIN;

CREATE TABLE IF NOT EXISTS reserves (
    id              BIGSERIAL PRIMARY KEY,
    code            VARCHAR(50) NOT NULL UNIQUE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE reserves IS 'Справочник резервов (источников поступления комплектующих)';
COMMENT ON COLUMN reserves.id IS 'Уникальный идентификатор резерва';
COMMENT ON COLUMN reserves.code IS 'Краткий уникальный код резерва';
COMMENT ON COLUMN reserves.name IS 'Наименование резерва';
COMMENT ON COLUMN reserves.description IS 'Описание и основание использования резерва';
COMMENT ON COLUMN reserves.is_active IS 'Признак доступности резерва для новых операций';
COMMENT ON COLUMN reserves.created_at IS 'Дата и время создания записи';

CREATE TABLE IF NOT EXISTS categories (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE categories IS 'Справочник категорий комплектующих';
COMMENT ON COLUMN categories.id IS 'Уникальный идентификатор категории';
COMMENT ON COLUMN categories.name IS 'Наименование категории';
COMMENT ON COLUMN categories.description IS 'Описание категории';
COMMENT ON COLUMN categories.is_active IS 'Признак доступности категории для новых записей';
COMMENT ON COLUMN categories.created_at IS 'Дата и время создания записи';

CREATE TABLE IF NOT EXISTS employees (
    id              BIGSERIAL PRIMARY KEY,
    full_name       VARCHAR(255) NOT NULL,
    department      VARCHAR(255),
    position        VARCHAR(255),
    email           VARCHAR(255),
    phone           VARCHAR(50),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE employees IS 'Справочник сотрудников и материально ответственных лиц';
COMMENT ON COLUMN employees.id IS 'Уникальный идентификатор сотрудника';
COMMENT ON COLUMN employees.full_name IS 'Фамилия, имя и отчество сотрудника';
COMMENT ON COLUMN employees.department IS 'Подразделение сотрудника';
COMMENT ON COLUMN employees.position IS 'Должность сотрудника';
COMMENT ON COLUMN employees.email IS 'Служебная электронная почта';
COMMENT ON COLUMN employees.phone IS 'Служебный телефон';
COMMENT ON COLUMN employees.is_active IS 'Признак действующего сотрудника';
COMMENT ON COLUMN employees.created_at IS 'Дата и время создания записи';

CREATE INDEX IF NOT EXISTS ix_employees_full_name ON employees (full_name);

CREATE TABLE IF NOT EXISTS suppliers (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL,
    inn             VARCHAR(12),
    contact_info    TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_suppliers_inn UNIQUE (inn)
);

COMMENT ON TABLE suppliers IS 'Справочник поставщиков комплектующих';
COMMENT ON COLUMN suppliers.id IS 'Уникальный идентификатор поставщика';
COMMENT ON COLUMN suppliers.name IS 'Полное или краткое наименование поставщика';
COMMENT ON COLUMN suppliers.inn IS 'ИНН поставщика';
COMMENT ON COLUMN suppliers.contact_info IS 'Контактные данные поставщика';
COMMENT ON COLUMN suppliers.is_active IS 'Признак действующего поставщика';
COMMENT ON COLUMN suppliers.created_at IS 'Дата и время создания записи';

CREATE TABLE IF NOT EXISTS roles (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(50) NOT NULL UNIQUE,
    description     VARCHAR(255) NOT NULL
);

COMMENT ON TABLE roles IS 'Роли пользователей для разграничения доступа';
COMMENT ON COLUMN roles.id IS 'Уникальный идентификатор роли';
COMMENT ON COLUMN roles.name IS 'Системное имя роли';
COMMENT ON COLUMN roles.description IS 'Описание полномочий роли';

CREATE TABLE IF NOT EXISTS users (
    id                      BIGSERIAL PRIMARY KEY,
    username                VARCHAR(100) NOT NULL UNIQUE,
    password_hash           VARCHAR(255) NOT NULL,
    full_name               VARCHAR(255) NOT NULL,
    role_id                 BIGINT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    employee_id             BIGINT REFERENCES employees(id) ON DELETE SET NULL,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    must_change_password    BOOLEAN NOT NULL DEFAULT TRUE,
    failed_login_attempts   INTEGER NOT NULL DEFAULT 0 CHECK (failed_login_attempts >= 0),
    locked_until            TIMESTAMPTZ,
    last_login_at           TIMESTAMPTZ,
    is_deleted              BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at              TIMESTAMPTZ,
    deleted_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
    deletion_reason         TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE users IS 'Локальные учётные записи пользователей системы';
COMMENT ON COLUMN users.id IS 'Уникальный идентификатор пользователя';
COMMENT ON COLUMN users.username IS 'Уникальное имя для входа';
COMMENT ON COLUMN users.password_hash IS 'Хеш пароля bcrypt; исходный пароль не хранится';
COMMENT ON COLUMN users.full_name IS 'Отображаемое ФИО пользователя';
COMMENT ON COLUMN users.role_id IS 'Роль пользователя';
COMMENT ON COLUMN users.employee_id IS 'Связанный сотрудник, если применимо';
COMMENT ON COLUMN users.is_active IS 'Признак разрешённого входа';
COMMENT ON COLUMN users.must_change_password IS 'Требование сменить первичный пароль';
COMMENT ON COLUMN users.failed_login_attempts IS 'Число последовательных неудачных входов';
COMMENT ON COLUMN users.locked_until IS 'Время окончания временной блокировки';
COMMENT ON COLUMN users.last_login_at IS 'Дата и время последнего успешного входа';
COMMENT ON COLUMN users.is_deleted IS 'Признак мягкого удаления учётной записи';
COMMENT ON COLUMN users.deleted_at IS 'Дата и время мягкого удаления учётной записи';
COMMENT ON COLUMN users.deleted_by IS 'Администратор, удаливший учётную запись';
COMMENT ON COLUMN users.deletion_reason IS 'Обязательное основание удаления учётной записи';
COMMENT ON COLUMN users.created_at IS 'Дата и время создания записи';
COMMENT ON COLUMN users.updated_at IS 'Дата и время последнего изменения записи';

CREATE INDEX IF NOT EXISTS ix_users_role_id ON users (role_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_employee_not_null
    ON users (employee_id) WHERE employee_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_users_is_deleted ON users (is_deleted, created_at DESC);

CREATE TABLE IF NOT EXISTS workstations (
    id                  BIGSERIAL PRIMARY KEY,
    inventory_number    VARCHAR(100) UNIQUE,
    name                VARCHAR(255) NOT NULL,
    cabinet             VARCHAR(100) NOT NULL,
    responsible_id      BIGINT REFERENCES employees(id) ON DELETE SET NULL,
    description         TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    is_deleted          BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at          TIMESTAMPTZ,
    deleted_by          BIGINT REFERENCES users(id) ON DELETE SET NULL,
    deletion_reason     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE workstations IS 'Автоматизированные рабочие места, в которых устанавливаются комплектующие';
COMMENT ON COLUMN workstations.id IS 'Уникальный идентификатор АРМ';
COMMENT ON COLUMN workstations.inventory_number IS 'Устаревший инвентарный номер АРМ; для новых записей не используется';
COMMENT ON COLUMN workstations.name IS 'Краткое наименование АРМ';
COMMENT ON COLUMN workstations.cabinet IS 'Кабинет или помещение размещения';
COMMENT ON COLUMN workstations.responsible_id IS 'Материально ответственное лицо';
COMMENT ON COLUMN workstations.description IS 'Дополнительное описание АРМ';
COMMENT ON COLUMN workstations.is_active IS 'Признак действующего АРМ';
COMMENT ON COLUMN workstations.is_deleted IS 'Признак мягкого удаления АРМ';
COMMENT ON COLUMN workstations.deleted_at IS 'Дата и время мягкого удаления АРМ';
COMMENT ON COLUMN workstations.deleted_by IS 'Администратор, удаливший АРМ';
COMMENT ON COLUMN workstations.deletion_reason IS 'Обязательное основание удаления АРМ';
COMMENT ON COLUMN workstations.created_at IS 'Дата и время создания записи';
COMMENT ON COLUMN workstations.updated_at IS 'Дата и время последнего изменения записи';

CREATE INDEX IF NOT EXISTS ix_workstations_inventory_number ON workstations (inventory_number);
CREATE INDEX IF NOT EXISTS ix_workstations_responsible_id ON workstations (responsible_id);
CREATE INDEX IF NOT EXISTS ix_workstations_is_deleted ON workstations (is_deleted, created_at DESC);

CREATE TABLE IF NOT EXISTS parts (
    id                      BIGSERIAL PRIMARY KEY,
    name                    VARCHAR(255) NOT NULL,
    part_type               VARCHAR(150) NOT NULL,
    serial_number           VARCHAR(150),
    inventory_number        VARCHAR(150),
    received_at             DATE NOT NULL DEFAULT CURRENT_DATE,
    reserve_id              BIGINT NOT NULL REFERENCES reserves(id) ON DELETE RESTRICT,
    category_id             BIGINT NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    supplier_id             BIGINT REFERENCES suppliers(id) ON DELETE SET NULL,
    status                  VARCHAR(20) NOT NULL DEFAULT 'in_stock',
    current_workstation_id  BIGINT REFERENCES workstations(id) ON DELETE RESTRICT,
    notes                   TEXT,
    created_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
    is_deleted              BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at              TIMESTAMPTZ,
    deleted_by              BIGINT REFERENCES users(id) ON DELETE SET NULL,
    deletion_reason         TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_parts_status CHECK (status IN ('in_stock', 'installed', 'written_off')),
    CONSTRAINT ck_parts_workstation_status CHECK (
        (status = 'installed' AND current_workstation_id IS NOT NULL)
        OR (status <> 'installed' AND current_workstation_id IS NULL)
    )
);

COMMENT ON TABLE parts IS 'Карточки компьютерных комплектующих и запасных частей';
COMMENT ON COLUMN parts.id IS 'Уникальный идентификатор комплектующего';
COMMENT ON COLUMN parts.name IS 'Наименование комплектующего';
COMMENT ON COLUMN parts.part_type IS 'Тип, модель или исполнение комплектующего';
COMMENT ON COLUMN parts.serial_number IS 'Заводской серийный номер';
COMMENT ON COLUMN parts.inventory_number IS 'Уникальный инвентарный номер комплектующего; может отсутствовать';
COMMENT ON COLUMN parts.received_at IS 'Дата поступления на учёт';
COMMENT ON COLUMN parts.reserve_id IS 'Обязательный источник поступления (резерв)';
COMMENT ON COLUMN parts.category_id IS 'Категория комплектующего';
COMMENT ON COLUMN parts.supplier_id IS 'Поставщик, если известен';
COMMENT ON COLUMN parts.status IS 'Текущий статус: на складе, установлено или списано';
COMMENT ON COLUMN parts.current_workstation_id IS 'Текущее АРМ при статусе «установлено»';
COMMENT ON COLUMN parts.notes IS 'Примечание к карточке';
COMMENT ON COLUMN parts.created_by IS 'Пользователь, создавший карточку';
COMMENT ON COLUMN parts.is_deleted IS 'Признак мягкого удаления карточки комплектующего';
COMMENT ON COLUMN parts.deleted_at IS 'Дата и время мягкого удаления карточки';
COMMENT ON COLUMN parts.deleted_by IS 'Администратор, удаливший карточку';
COMMENT ON COLUMN parts.deletion_reason IS 'Обязательное основание удаления карточки';
COMMENT ON COLUMN parts.created_at IS 'Дата и время создания записи';
COMMENT ON COLUMN parts.updated_at IS 'Дата и время последнего изменения записи';

CREATE INDEX IF NOT EXISTS ix_parts_serial_number ON parts (serial_number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_parts_serial_number_not_null
    ON parts (serial_number) WHERE serial_number IS NOT NULL AND serial_number <> '';
CREATE INDEX IF NOT EXISTS ix_parts_inventory_number ON parts (inventory_number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_parts_inventory_number_not_null
    ON parts (inventory_number) WHERE inventory_number IS NOT NULL AND inventory_number <> '';
CREATE INDEX IF NOT EXISTS ix_parts_reserve_status ON parts (reserve_id, status);
CREATE INDEX IF NOT EXISTS ix_parts_category_id ON parts (category_id);
CREATE INDEX IF NOT EXISTS ix_parts_workstation_id ON parts (current_workstation_id);
CREATE INDEX IF NOT EXISTS ix_parts_is_deleted ON parts (is_deleted, created_at DESC);

CREATE TABLE IF NOT EXISTS part_movements (
    id                      BIGSERIAL PRIMARY KEY,
    part_id                 BIGINT NOT NULL REFERENCES parts(id) ON DELETE RESTRICT,
    movement_type           VARCHAR(30) NOT NULL,
    from_reserve_id         BIGINT REFERENCES reserves(id) ON DELETE RESTRICT,
    to_reserve_id           BIGINT REFERENCES reserves(id) ON DELETE RESTRICT,
    from_workstation_id     BIGINT REFERENCES workstations(id) ON DELETE RESTRICT,
    to_workstation_id       BIGINT REFERENCES workstations(id) ON DELETE RESTRICT,
    reason                  TEXT NOT NULL,
    document_number         VARCHAR(100),
    performed_by            BIGINT REFERENCES users(id) ON DELETE SET NULL,
    performed_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_part_movements_type CHECK (
        movement_type IN ('receipt', 'installation', 'removal', 'reserve_transfer', 'write_off', 'correction')
    )
);

COMMENT ON TABLE part_movements IS 'Неизменяемая история поступлений, установок, перемещений и списаний';
COMMENT ON COLUMN part_movements.id IS 'Уникальный идентификатор операции движения';
COMMENT ON COLUMN part_movements.part_id IS 'Комплектующее, с которым выполнена операция';
COMMENT ON COLUMN part_movements.movement_type IS 'Тип операции движения';
COMMENT ON COLUMN part_movements.from_reserve_id IS 'Исходный резерв';
COMMENT ON COLUMN part_movements.to_reserve_id IS 'Целевой резерв';
COMMENT ON COLUMN part_movements.from_workstation_id IS 'Исходное АРМ';
COMMENT ON COLUMN part_movements.to_workstation_id IS 'Целевое АРМ';
COMMENT ON COLUMN part_movements.reason IS 'Описание операции; для типовых перемещений формируется автоматически';
COMMENT ON COLUMN part_movements.document_number IS 'Номер прежнего первичного документа или акта, если был указан';
COMMENT ON COLUMN part_movements.performed_by IS 'Пользователь, выполнивший операцию';
COMMENT ON COLUMN part_movements.performed_at IS 'Дата и время выполнения операции';

CREATE INDEX IF NOT EXISTS ix_part_movements_part_time
    ON part_movements (part_id, performed_at DESC);
CREATE INDEX IF NOT EXISTS ix_part_movements_workstations
    ON part_movements (from_workstation_id, to_workstation_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT REFERENCES users(id) ON DELETE SET NULL,
    action          VARCHAR(100) NOT NULL,
    entity          VARCHAR(100) NOT NULL,
    entity_id       BIGINT,
    "timestamp"     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ip              INET,
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at      TIMESTAMPTZ,
    deleted_by      BIGINT REFERENCES users(id) ON DELETE SET NULL,
    deletion_reason TEXT
);

COMMENT ON TABLE audit_log IS 'Журнал действий пользователей и событий безопасности';
COMMENT ON COLUMN audit_log.id IS 'Уникальный идентификатор события аудита';
COMMENT ON COLUMN audit_log.user_id IS 'Пользователь, инициировавший событие; может отсутствовать при неуспешном входе';
COMMENT ON COLUMN audit_log.action IS 'Код выполненного действия';
COMMENT ON COLUMN audit_log.entity IS 'Тип затронутой сущности';
COMMENT ON COLUMN audit_log.entity_id IS 'Идентификатор затронутой сущности';
COMMENT ON COLUMN audit_log."timestamp" IS 'Дата и время события в часовом поясе БД';
COMMENT ON COLUMN audit_log.ip IS 'IP-адрес клиента';
COMMENT ON COLUMN audit_log.details IS 'Дополнительные структурированные сведения без секретов';
COMMENT ON COLUMN audit_log.is_deleted IS 'Признак скрытия события администратором без физического удаления';
COMMENT ON COLUMN audit_log.deleted_at IS 'Дата и время скрытия события';
COMMENT ON COLUMN audit_log.deleted_by IS 'Администратор, скрывший событие';
COMMENT ON COLUMN audit_log.deletion_reason IS 'Обязательное основание скрытия события';

CREATE INDEX IF NOT EXISTS ix_audit_log_timestamp ON audit_log ("timestamp" DESC);
CREATE INDEX IF NOT EXISTS ix_audit_log_user_id ON audit_log (user_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_entity ON audit_log (entity, entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_log_is_deleted ON audit_log (is_deleted, "timestamp" DESC);

-- Роли и базовые справочные записи не содержат секретов и создаются идемпотентно.
INSERT INTO roles (name, description) VALUES
    ('admin', 'Полный доступ, управление справочниками и пользователями'),
    ('storekeeper', 'Сотрудник: приход, установка, перемещение и списание комплектующих'),
    ('viewer', 'Пользователь: просмотр данных и отчётов')
ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO reserves (code, name, description) VALUES
    ('MIAC', 'Резерв МИАЦ', 'Собственный резерв МИАЦ'),
    ('MINZDRAV', 'Резерв Минздрава', 'Резерв регионального Министерства здравоохранения'),
    ('FEDREG', 'Федеральный/региональный резерв', 'Федеральные и региональные поставки')
ON CONFLICT (code) DO NOTHING;

INSERT INTO categories (name, description) VALUES
    ('Накопители', 'Жёсткие диски, SSD и другие накопители'),
    ('Оперативная память', 'Модули оперативной памяти'),
    ('Блоки питания', 'Блоки питания системных блоков и периферии'),
    ('Видеокарты', 'Дискретные видеокарты и графические ускорители'),
    ('Сетевые карты', 'Проводные и беспроводные сетевые адаптеры'),
    ('Процессоры', 'Центральные процессоры'),
    ('Материнские платы', 'Системные и материнские платы'),
    ('Системы охлаждения', 'Кулеры, вентиляторы и радиаторы'),
    ('Платы расширения', 'Контроллеры и прочие платы расширения'),
    ('Кабели и адаптеры', 'Интерфейсные кабели, переходники и адаптеры'),
    ('Периферия', 'Мониторы, клавиатуры, мыши и иная периферия'),
    ('Прочее', 'Прочие комплектующие и запасные части')
ON CONFLICT (name) DO NOTHING;

COMMIT;
