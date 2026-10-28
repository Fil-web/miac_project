import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import RedirectResponse

from app.audit import add_audit_event
from app.auth import get_current_user, hash_password, router as auth_router, validate_csrf
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import AuditLog, Part, PartMovement, Role, User, Workstation
from app.templating import flash, render


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()


def parse_optional_int(value: str | None, field_label: str) -> int | None:
    """Преобразует необязательный параметр фильтра, принимая пустую строку."""

    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Поле «{field_label}» должно быть числом") from exc
    if parsed <= 0:
        raise HTTPException(status_code=400, detail=f"Поле «{field_label}» должно быть положительным числом")
    return parsed


def parse_optional_date(value: str | None, field_label: str) -> date | None:
    """Преобразует дату фильтра, не считая пустое поле ошибкой."""

    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Поле «{field_label}» содержит неверную дату") from exc


def seed_default_users() -> None:
    """Создаёт первичные учётные записи только при их отсутствии."""

    definitions = [
        (settings.admin_username, settings.admin_password.get_secret_value(), "Администратор", "admin"),
        (
            settings.storekeeper_username,
            settings.storekeeper_password.get_secret_value(),
            "Сотрудник",
            "storekeeper",
        ),
        (settings.viewer_username, settings.viewer_password.get_secret_value(), "Пользователь", "viewer"),
    ]
    with SessionLocal() as db:
        roles = {role.name: role for role in db.scalars(select(Role)).all()}
        if set(roles) != {"admin", "storekeeper", "viewer"}:
            raise RuntimeError("Схема не инициализирована: отсутствуют обязательные роли")

        for username, password, full_name, role_name in definitions:
            normalized = username.strip().lower()
            if not db.scalar(select(User).where(User.username == normalized)):
                db.add(
                    User(
                        username=normalized,
                        password_hash=hash_password(password),
                        full_name=full_name,
                        role_id=roles[role_name].id,
                        must_change_password=role_name != "admin",
                    )
                )
                if role_name == "admin":
                    logger.warning("Создана первичная учётная запись администратора %s", normalized)
                else:
                    logger.warning("Создана первичная учётная запись %s; пароль требуется сменить", normalized)
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Проверяет схему и подготавливает первичные учётные записи."""

    seed_default_users()
    yield


app = FastAPI(
    title="Учёт комплектующих АРМ МИАЦ",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts_list)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret.get_secret_value(),
    session_cookie="miac_session",
    max_age=3600,
    same_site="strict",
    https_only=settings.session_https_only,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Добавляет защитные заголовки без внешних источников контента."""

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "font-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_router)

# Импорт после создания приложения исключает циклические зависимости модулей.
from app.routes import catalogs, parts, reports, users, workstations  # noqa: E402

app.include_router(parts.router)
app.include_router(workstations.router)
app.include_router(reports.router)
app.include_router(catalogs.router)
app.include_router(users.router)


@app.get("/", name="dashboard")
def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Главная панель с ключевыми показателями."""

    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    counters = {
        "workstations": db.scalar(
            select(func.count()).select_from(Workstation).where(
                Workstation.is_active.is_(True),
                Workstation.is_deleted.is_(False),
            )
        ),
        "in_stock": db.scalar(
            select(func.count()).select_from(Part).where(Part.status == "in_stock", Part.is_deleted.is_(False))
        ),
        "written_off_month": db.scalar(
            select(func.count()).select_from(PartMovement).join(Part).where(
                PartMovement.movement_type == "write_off",
                PartMovement.performed_at >= month_start,
                Part.is_deleted.is_(False),
            )
        ),
    }
    recent_movements = db.scalars(
        select(PartMovement)
        .join(Part)
        .options(joinedload(PartMovement.part), joinedload(PartMovement.performer))
        .where(Part.is_deleted.is_(False))
        .order_by(PartMovement.performed_at.desc())
        .limit(8)
    ).all()
    return render(
        request,
        "dashboard.html",
        user=user,
        page_title="Главная",
        counters=counters,
        recent_movements=recent_movements,
    )


@app.get("/audit", name="audit_log")
def audit_log(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    page: int = 1,
    deleted: bool = False,
    user_id: str | None = Query(default=None, max_length=30),
    action: str | None = Query(default=None, max_length=100),
    date_from: str | None = Query(default=None, max_length=10),
    date_to: str | None = Query(default=None, max_length=10),
):
    """Показывает журнал аудита только администратору."""

    if user.role.name != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    page = max(1, page)
    selected_user_id = parse_optional_int(user_id, "Пользователь")
    selected_date_from = parse_optional_date(date_from, "Дата с")
    selected_date_to = parse_optional_date(date_to, "Дата по")
    selected_action = action.strip() if action and action.strip() else None
    filters = [AuditLog.is_deleted.is_(deleted)]
    if selected_user_id is not None:
        filters.append(AuditLog.user_id == selected_user_id)
    if selected_action:
        filters.append(AuditLog.action == selected_action)
    if selected_date_from:
        filters.append(AuditLog.timestamp >= datetime.combine(selected_date_from, time.min, tzinfo=timezone.utc))
    if selected_date_to:
        filters.append(
            AuditLog.timestamp
            < datetime.combine(selected_date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        )
    events = db.scalars(
        select(AuditLog)
        .options(joinedload(AuditLog.user), joinedload(AuditLog.deleted_by_user))
        .where(*filters)
        .order_by(AuditLog.timestamp.desc())
        .offset((page - 1) * 100)
        .limit(100)
    ).all()
    return render(
        request,
        "audit/list.html",
        user=user,
        page_title="Журнал действий",
        events=events,
        page=page,
        deleted=deleted,
        audit_users=db.scalars(select(User).order_by(User.full_name, User.username)).all(),
        audit_actions=db.scalars(
            select(AuditLog.action)
            .where(AuditLog.is_deleted.is_(deleted))
            .distinct()
            .order_by(AuditLog.action)
        ).all(),
        selected_user_id=selected_user_id,
        selected_action=selected_action,
        date_from=selected_date_from,
        date_to=selected_date_to,
    )


@app.post("/audit/{event_id}/delete", name="audit_event_delete")
def audit_event_delete(
    event_id: int,
    request: Request,
    reason: Annotated[str, Form(min_length=3, max_length=1000)],
    _: None = Depends(validate_csrf),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Скрывает событие из основного журнала, сохраняя контрольный след."""

    if user.role.name != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    event = db.scalar(select(AuditLog).where(AuditLog.id == event_id).with_for_update())
    if not event:
        raise HTTPException(status_code=404, detail="Событие журнала не найдено")
    if event.is_deleted:
        flash(request, "Событие уже удалено из основного списка.", "warning")
        return RedirectResponse("/audit?deleted=true", status_code=status.HTTP_303_SEE_OTHER)
    if event.action == "AUDIT_EVENT_HIDDEN":
        flash(request, "Контрольную запись удаления скрывать нельзя.", "danger")
        return RedirectResponse("/audit", status_code=status.HTTP_303_SEE_OTHER)

    event.is_deleted = True
    event.deleted_at = datetime.now(timezone.utc)
    event.deleted_by = user.id
    event.deletion_reason = reason.strip()
    add_audit_event(
        db,
        request,
        "AUDIT_EVENT_HIDDEN",
        "audit_log",
        event.id,
        user=user,
        details={
            "deleted_action": event.action,
            "deleted_entity": event.entity,
            "reason": event.deletion_reason,
        },
    )
    db.commit()
    flash(request, "Событие удалено из основного списка. Контрольная запись сохранена.", "warning")
    return RedirectResponse("/audit", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/health", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    """Проверяет доступность приложения и базы данных без раскрытия деталей."""

    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except SQLAlchemyError:
        return JSONResponse({"status": "error"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc: HTTPException):
    """Возвращает русскоязычную страницу запрета доступа."""

    user = None
    user_id = request.session.get("user_id")
    if user_id:
        with SessionLocal() as db:
            user = db.scalar(
                select(User)
                .options(joinedload(User.role))
                .where(User.id == user_id, User.is_deleted.is_(False))
            )
    return render(
        request,
        "errors/403.html",
        user=user,
        page_title="Доступ запрещён",
        message=str(exc.detail),
        status_code=403,
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, _: HTTPException):
    """Возвращает русскоязычную страницу отсутствующего ресурса."""

    return render(request, "errors/404.html", page_title="Страница не найдена", status_code=404)
