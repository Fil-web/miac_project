from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import INET, JSONB

from app.db import Base


class Reserve(Base):
    """Источник финансирования или поступления комплектующего."""

    __tablename__ = "reserves"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Category(Base):
    """Категория комплектующего."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Employee(Base):
    """Сотрудник или материально ответственное лицо."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    department: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Supplier(Base):
    """Поставщик комплектующих."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    inn: Mapped[str | None] = mapped_column(String(12), unique=True)
    contact_info: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Role(Base):
    """Роль в ролевой модели доступа."""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(String(255))
    users: Mapped[list[User]] = relationship(back_populates="role")


class User(Base):
    """Локальная учётная запись пользователя."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), index=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deletion_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    role: Mapped[Role] = relationship(back_populates="users")
    employee: Mapped[Employee | None] = relationship()
    deleted_by_user: Mapped[User | None] = relationship(remote_side=[id], foreign_keys=[deleted_by])


class Workstation(Base):
    """Автоматизированное рабочее место."""

    __tablename__ = "workstations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Поле оставлено nullable для чтения прежних данных после отказа от
    # инвентарного номера на уровне АРМ. Новые записи его не заполняют.
    inventory_number: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    cabinet: Mapped[str] = mapped_column(String(100))
    responsible_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deletion_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    responsible: Mapped[Employee | None] = relationship()
    deleted_by_user: Mapped[User | None] = relationship(foreign_keys=[deleted_by])
    installed_parts: Mapped[list[Part]] = relationship(back_populates="current_workstation")


class Part(Base):
    """Текущее состояние карточки комплектующего."""

    __tablename__ = "parts"
    __table_args__ = (
        CheckConstraint("status IN ('in_stock', 'installed', 'written_off')", name="ck_parts_status"),
        CheckConstraint(
            "(status = 'installed' AND current_workstation_id IS NOT NULL) "
            "OR (status <> 'installed' AND current_workstation_id IS NULL)",
            name="ck_parts_workstation_status",
        ),
        Index("ix_parts_reserve_status", "reserve_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    part_type: Mapped[str] = mapped_column(String(150))
    serial_number: Mapped[str | None] = mapped_column(String(150), index=True)
    inventory_number: Mapped[str | None] = mapped_column(String(150), unique=True, index=True)
    received_at: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    reserve_id: Mapped[int] = mapped_column(ForeignKey("reserves.id", ondelete="RESTRICT"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="in_stock")
    current_workstation_id: Mapped[int | None] = mapped_column(
        ForeignKey("workstations.id", ondelete="RESTRICT"), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deletion_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    reserve: Mapped[Reserve] = relationship()
    category: Mapped[Category] = relationship()
    supplier: Mapped[Supplier | None] = relationship()
    current_workstation: Mapped[Workstation | None] = relationship(back_populates="installed_parts")
    creator: Mapped[User | None] = relationship(foreign_keys=[created_by])
    deleted_by_user: Mapped[User | None] = relationship(foreign_keys=[deleted_by])
    movements: Mapped[list[PartMovement]] = relationship(
        back_populates="part", order_by="PartMovement.performed_at.desc()"
    )


class PartMovement(Base):
    """Неизменяемая операция над комплектующим."""

    __tablename__ = "part_movements"
    __table_args__ = (
        CheckConstraint(
            "movement_type IN ('receipt', 'installation', 'removal', "
            "'reserve_transfer', 'write_off', 'correction')",
            name="ck_part_movements_type",
        ),
        Index("ix_part_movements_part_time", "part_id", "performed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    part_id: Mapped[int] = mapped_column(ForeignKey("parts.id", ondelete="RESTRICT"))
    movement_type: Mapped[str] = mapped_column(String(30))
    from_reserve_id: Mapped[int | None] = mapped_column(ForeignKey("reserves.id", ondelete="RESTRICT"))
    to_reserve_id: Mapped[int | None] = mapped_column(ForeignKey("reserves.id", ondelete="RESTRICT"))
    from_workstation_id: Mapped[int | None] = mapped_column(
        ForeignKey("workstations.id", ondelete="RESTRICT")
    )
    to_workstation_id: Mapped[int | None] = mapped_column(ForeignKey("workstations.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(Text)
    document_number: Mapped[str | None] = mapped_column(String(100))
    performed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    part: Mapped[Part] = relationship(back_populates="movements")
    from_reserve: Mapped[Reserve | None] = relationship(foreign_keys=[from_reserve_id])
    to_reserve: Mapped[Reserve | None] = relationship(foreign_keys=[to_reserve_id])
    from_workstation: Mapped[Workstation | None] = relationship(foreign_keys=[from_workstation_id])
    to_workstation: Mapped[Workstation | None] = relationship(foreign_keys=[to_workstation_id])
    performer: Mapped[User | None] = relationship(foreign_keys=[performed_by])


class AuditLog(Base):
    """Событие обязательного журнала действий."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    ip: Mapped[str | None] = mapped_column(INET)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deletion_reason: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User | None] = relationship(foreign_keys=[user_id])
    deleted_by_user: Mapped[User | None] = relationship(foreign_keys=[deleted_by])


PART_STATUS_LABELS = {
    "in_stock": "На складе",
    "installed": "Установлено",
    "written_off": "Списано",
}

# Отображаемые названия ролей отделены от неизменяемых системных кодов.
ROLE_LABELS = {
    "admin": "Администратор",
    "storekeeper": "Сотрудник",
    "viewer": "Пользователь",
}

MOVEMENT_TYPE_LABELS = {
    "receipt": "Поступление",
    "installation": "Установка в АРМ",
    "removal": "Возврат на склад",
    "reserve_transfer": "Перемещение между резервами",
    "write_off": "Списание",
    "correction": "Корректировка",
}

AUDIT_ACTION_LABELS = {
    "LOGIN_FAILED": "Неудачный вход",
    "LOGIN_SUCCESS": "Успешный вход",
    "LOGOUT": "Выход из системы",
    "PASSWORD_CHANGE_FAILED": "Ошибка смены пароля",
    "PASSWORD_CHANGED": "Пароль изменён",
    "PART_CREATED": "Комплектующее принято на учёт",
    "PART_UPDATED": "Карточка комплектующего изменена",
    "PART_RESERVE_TRANSFERRED": "Комплектующее перемещено между резервами",
    "PART_WRITTEN_OFF": "Комплектующее списано",
    "PART_DELETED": "Комплектующее удалено",
    "PART_RESTORED": "Комплектующее восстановлено",
    "WORKSTATION_CREATED": "АРМ создано",
    "WORKSTATION_UPDATED": "Карточка АРМ изменена",
    "WORKSTATION_DELETED": "АРМ удалено",
    "WORKSTATION_RESTORED": "АРМ восстановлено",
    "PART_INSTALLED": "Комплектующее установлено",
    "PART_RETURNED_TO_STOCK": "Комплектующее возвращено на склад",
    "PART_MOVED_BETWEEN_WORKSTATIONS": "Комплектующее перемещено между АРМ",
    "RESERVE_CREATED": "Резерв добавлен",
    "CATEGORY_CREATED": "Категория добавлена",
    "EMPLOYEE_CREATED": "Сотрудник добавлен",
    "SUPPLIER_CREATED": "Поставщик добавлен",
    "CATALOG_ENTRY_TOGGLED": "Состояние справочника изменено",
    "CATALOG_ENTRY_UPDATED": "Элемент справочника изменён",
    "CATALOG_ENTRY_DELETED": "Элемент справочника удалён",
    "USER_CREATED": "Учётная запись создана",
    "USER_UPDATED": "Учётная запись изменена",
    "USER_PASSWORD_RESET": "Пароль учётной записи сброшен",
    "USER_STATUS_CHANGED": "Состояние учётной записи изменено",
    "USER_DELETED": "Учётная запись удалена",
    "USER_RESTORED": "Учётная запись восстановлена",
    "AUDIT_EVENT_HIDDEN": "Событие журнала удалено из основного списка",
}

AUDIT_ENTITY_LABELS = {
    "user": "Пользователь",
    "part": "Комплектующее",
    "workstation": "АРМ",
    "reserve": "Резерв",
    "category": "Категория",
    "employee": "Сотрудник",
    "supplier": "Поставщик",
    "audit_log": "Журнал действий",
}
