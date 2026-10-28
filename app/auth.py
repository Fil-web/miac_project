import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from starlette.responses import RedirectResponse

from app.audit import add_audit_event
from app.db import get_db
from app.models import User
from app.templating import csrf_token, flash, render


router = APIRouter(tags=["Аутентификация"])

# bcrypt намеренно настроен без устаревших алгоритмов для новых хешей.
password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# Фиктивный хеш выравнивает стоимость проверки неизвестного логина.
DUMMY_PASSWORD_HASH = password_context.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    """Создаёт стойкий хеш пароля."""

    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Сверяет пароль с хешем в постоянное по смыслу время."""

    try:
        return password_context.verify(password, password_hash)
    except (TypeError, ValueError):
        return False


def validate_password_policy(password: str) -> str | None:
    """Проверяет базовую парольную политику прототипа."""

    if len(password) < 12:
        return "Пароль должен содержать не менее 12 символов."
    if len(password.encode("utf-8")) > 72:
        return "Пароль не должен превышать 72 байта в кодировке UTF-8."
    checks = [
        re.search(r"[A-ZА-ЯЁ]", password),
        re.search(r"[a-zа-яё]", password),
        re.search(r"\d", password),
        re.search(r"[^\w\s]", password),
    ]
    if not all(checks):
        return "Используйте прописные и строчные буквы, цифры и специальные символы."
    return None


def validate_csrf(request: Request, csrf_value: Annotated[str, Form(alias="csrf_token")]) -> None:
    """Отклоняет изменяющий запрос без корректного CSRF-токена."""

    expected = request.session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, csrf_value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недействительный CSRF-токен")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Загружает активного пользователя из серверно-подписанной сессии."""

    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})

    user = db.scalar(
        select(User)
        .options(joinedload(User.role))
        .where(User.id == user_id, User.is_active.is_(True), User.is_deleted.is_(False))
    )
    if not user:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})

    if (
        user.must_change_password
        and user.role.name != "admin"
        and request.scope["path"] not in {"/change-password", "/logout"}
    ):
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/change-password"})
    return user


def require_role(*allowed_roles: str):
    """Создаёт зависимость для проверки роли на уровне маршрута."""

    def role_dependency(user: User = Depends(get_current_user)) -> User:
        if user.role.name not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для выполнения операции",
            )
        return user

    return role_dependency


@router.get("/login", name="login_form")
def login_form(request: Request):
    """Показывает локальную форму входа."""

    if request.session.get("user_id"):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "auth/login.html", page_title="Вход")


@router.post("/login", name="login")
def login(
    request: Request,
    username: Annotated[str, Form(min_length=1, max_length=100)],
    password: Annotated[str, Form(min_length=1, max_length=200)],
    _: None = Depends(validate_csrf),
    db: Session = Depends(get_db),
):
    """Проверяет пароль, блокировку и создаёт новую сессию."""

    normalized_username = username.strip().lower()
    # Блокируем только строку users: PostgreSQL не допускает FOR UPDATE
    # на nullable-стороне внешнего соединения, создаваемого joinedload.
    user = db.scalar(select(User).where(User.username == normalized_username).with_for_update(of=User))
    now = datetime.now(timezone.utc)

    is_locked = bool(user and user.locked_until and user.locked_until > now)
    password_matches = verify_password(password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    is_valid = bool(user and user.is_active and not user.is_deleted and not is_locked and password_matches)

    if not is_valid:
        if user and not is_locked:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= 5:
                user.locked_until = now + timedelta(minutes=15)
                user.failed_login_attempts = 0
        add_audit_event(
            db,
            request,
            "LOGIN_FAILED",
            "user",
            user.id if user else None,
            user=user,
            details={"username": normalized_username, "locked": is_locked},
        )
        db.commit()
        return render(
            request,
            "auth/login.html",
            page_title="Вход",
            error="Неверное имя пользователя или пароль. Повторите попытку позже.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    add_audit_event(db, request, "LOGIN_SUCCESS", "user", user.id, user=user)
    db.commit()

    # Полная замена сессии защищает от фиксации идентификатора сессии.
    request.session.clear()
    request.session["user_id"] = user.id
    csrf_token(request)
    target = "/change-password" if user.must_change_password and user.role.name != "admin" else "/"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout", name="logout")
def logout(
    request: Request,
    _: None = Depends(validate_csrf),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Завершает сессию и регистрирует выход."""

    add_audit_event(db, request, "LOGOUT", "user", user.id, user=user)
    db.commit()
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/change-password", name="change_password_form")
def change_password_form(request: Request, user: User = Depends(get_current_user)):
    """Показывает форму обязательной смены пароля."""

    return render(request, "auth/change_password.html", user=user, page_title="Смена пароля")


@router.post("/change-password", name="change_password")
def change_password(
    request: Request,
    current_password: Annotated[str, Form(min_length=1, max_length=200)],
    new_password: Annotated[str, Form(min_length=1, max_length=200)],
    confirm_password: Annotated[str, Form(min_length=1, max_length=200)],
    _: None = Depends(validate_csrf),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Меняет пароль после повторной проверки текущего секрета."""

    error = None
    if not verify_password(current_password, user.password_hash):
        error = "Текущий пароль указан неверно."
    elif new_password != confirm_password:
        error = "Новый пароль и подтверждение не совпадают."
    elif verify_password(new_password, user.password_hash):
        error = "Новый пароль должен отличаться от текущего."
    else:
        error = validate_password_policy(new_password)

    if error:
        add_audit_event(db, request, "PASSWORD_CHANGE_FAILED", "user", user.id, user=user)
        db.commit()
        return render(
            request,
            "auth/change_password.html",
            user=user,
            page_title="Смена пароля",
            error=error,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.updated_at = datetime.now(timezone.utc)
    add_audit_event(db, request, "PASSWORD_CHANGED", "user", user.id, user=user)
    db.commit()
    flash(request, "Пароль успешно изменён.")
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
