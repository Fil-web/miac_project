import secrets
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.models import (
    AUDIT_ACTION_LABELS,
    AUDIT_ENTITY_LABELS,
    MOVEMENT_TYPE_LABELS,
    PART_STATUS_LABELS,
    ROLE_LABELS,
    User,
)


templates = Jinja2Templates(directory="app/templates")
templates.env.globals["status_labels"] = PART_STATUS_LABELS
templates.env.globals["movement_labels"] = MOVEMENT_TYPE_LABELS
templates.env.globals["audit_action_labels"] = AUDIT_ACTION_LABELS
templates.env.globals["audit_entity_labels"] = AUDIT_ENTITY_LABELS
templates.env.globals["role_labels"] = ROLE_LABELS


def csrf_token(request: Request) -> str:
    """Создаёт непредсказуемый CSRF-токен в подписанной сессии."""

    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def flash(request: Request, message: str, category: str = "success") -> None:
    """Сохраняет одноразовое уведомление в сессии."""

    request.session.setdefault("flashes", []).append({"message": message, "category": category})


def render(
    request: Request,
    name: str,
    *,
    user: User | None = None,
    status_code: int = 200,
    **context: Any,
) -> Response:
    """Рендерит шаблон с единым безопасным контекстом интерфейса."""

    base_context = {
        "user": user,
        "csrf_token": csrf_token(request),
        "flashes": request.session.pop("flashes", []),
        **context,
    }
    return templates.TemplateResponse(request=request, name=name, context=base_context, status_code=status_code)
