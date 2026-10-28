from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def client_ip(request: Request) -> str | None:
    """Возвращает адрес непосредственного клиента.

    Заголовки переадресации обрабатывает только настроенный доверенный reverse proxy.
    """

    return request.client.host if request.client else None


def add_audit_event(
    db: Session,
    request: Request,
    action: str,
    entity: str,
    entity_id: int | None = None,
    user: User | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Добавляет событие аудита в текущую транзакцию бизнес-операции."""

    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            entity=entity,
            entity_id=entity_id,
            ip=client_ip(request),
            details=details or {},
        )
    )

