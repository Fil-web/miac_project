import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from starlette.responses import RedirectResponse

from app.audit import add_audit_event
from app.auth import hash_password, require_role, validate_csrf, validate_password_policy
from app.db import get_db
from app.models import Employee, Role, User
from app.templating import flash, render


router = APIRouter(prefix="/users", tags=["Учётные записи"])
ALLOWED_ROLES = {"admin", "storekeeper", "viewer"}


def validate_issued_password(password: str, role_name: str) -> str | None:
    """Проверяет выдаваемый пароль с учётом роли получателя."""

    if len(password.encode("utf-8")) > 72:
        return "Пароль не должен превышать 72 байта в кодировке UTF-8."
    if role_name == "admin":
        if len(password) < 5 or not password.strip():
            return "Пароль администратора должен содержать не менее 5 символов."
        return None
    return validate_password_policy(password)


def available_employees(db: Session) -> list[Employee]:
    """Возвращает действующих сотрудников без учётной записи."""

    linked_employee_ids = select(User.employee_id).where(User.employee_id.is_not(None))
    return db.scalars(
        select(Employee)
        .where(Employee.is_active.is_(True), Employee.id.not_in(linked_employee_ids))
        .order_by(Employee.full_name)
    ).all()


@router.get("", name="users_list")
def users_list(
    request: Request,
    deleted: bool = Query(default=False),
    edit_id: int | None = Query(default=None),
    user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Показывает административный список учётных записей."""

    users = db.scalars(
        select(User)
        .options(joinedload(User.role), joinedload(User.employee), joinedload(User.deleted_by_user))
        .where(User.is_deleted.is_(deleted))
        .order_by(User.username)
    ).all()
    edit_account = None
    if edit_id is not None and not deleted:
        edit_account = db.scalar(
            select(User)
            .options(joinedload(User.role), joinedload(User.employee))
            .where(User.id == edit_id, User.is_deleted.is_(False))
        )
        if not edit_account:
            raise HTTPException(status_code=404, detail="Учётная запись не найдена")

    return render(
        request,
        "users/list.html",
        user=user,
        page_title="Учётные записи",
        users=users,
        employees=available_employees(db) if not deleted else [],
        deleted=deleted,
        edit_account=edit_account,
    )


@router.post("", name="user_create")
def user_create(
    request: Request,
    employee_id: Annotated[int, Form()],
    username: Annotated[str, Form(min_length=3, max_length=100)],
    role_name: Annotated[str, Form()],
    temporary_password: Annotated[str, Form(min_length=1, max_length=200)],
    _: None = Depends(validate_csrf),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Создаёт для сотрудника учётную запись с первичным паролем."""

    normalized_username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]+", normalized_username):
        flash(request, "Логин может содержать латинские буквы, цифры, точку, дефис и подчёркивание.", "danger")
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)
    if role_name not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    password_error = validate_issued_password(temporary_password, role_name)
    if password_error:
        flash(request, password_error, "danger")
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)

    employee = db.scalar(
        select(Employee).where(Employee.id == employee_id, Employee.is_active.is_(True)).with_for_update()
    )
    role = db.scalar(select(Role).where(Role.name == role_name))
    if not employee or not role:
        raise HTTPException(status_code=400, detail="Сотрудник или роль недоступны")
    if db.scalar(select(User).where(User.employee_id == employee.id)):
        flash(request, "Для выбранного сотрудника учётная запись уже существует.", "danger")
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)

    account = User(
        username=normalized_username,
        password_hash=hash_password(temporary_password),
        full_name=employee.full_name,
        role_id=role.id,
        employee_id=employee.id,
        must_change_password=role.name != "admin",
    )
    try:
        db.add(account)
        db.flush()
        add_audit_event(
            db,
            request,
            "USER_CREATED",
            "user",
            account.id,
            user=current_user,
            details={"username": account.username, "employee_id": employee.id, "role": role.name},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        flash(request, "Логин уже используется либо сотрудник уже связан с другой записью.", "danger")
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)

    if role.name == "admin":
        flash(request, "Учётная запись администратора создана без обязательной смены пароля.")
    else:
        flash(request, "Учётная запись создана. При первом входе потребуется сменить первичный пароль.")
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{user_id}/edit", name="user_edit")
def user_edit(
    user_id: int,
    request: Request,
    full_name: Annotated[str, Form(min_length=3, max_length=255)],
    role_name: Annotated[str, Form()],
    _: None = Depends(validate_csrf),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Изменяет ФИО и роль без возможности изменить уникальный логин."""

    if role_name not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    account = db.scalar(
        select(User)
        .options(joinedload(User.role), joinedload(User.employee))
        .where(User.id == user_id)
        .with_for_update(of=User)
    )
    role = db.scalar(select(Role).where(Role.name == role_name))
    if not account or account.is_deleted or not role:
        raise HTTPException(status_code=404, detail="Учётная запись не найдена")
    if account.id == current_user.id and role_name != "admin":
        flash(request, "Нельзя изменить собственную роль администратора.", "danger")
        return RedirectResponse(f"/users?edit_id={account.id}", status_code=status.HTTP_303_SEE_OTHER)
    if account.role.name == "admin" and role_name != "admin" and account.is_active:
        active_admins = db.scalar(
            select(func.count())
            .select_from(User)
            .join(Role)
            .where(User.is_active.is_(True), User.is_deleted.is_(False), Role.name == "admin")
        )
        if (active_admins or 0) <= 1:
            flash(request, "Нельзя изменить роль последнего действующего администратора.", "danger")
            return RedirectResponse(f"/users?edit_id={account.id}", status_code=status.HTTP_303_SEE_OTHER)

    cleaned_name = full_name.strip()
    before = {"full_name": account.full_name, "role": account.role.name}
    account.full_name = cleaned_name
    account.role_id = role.id
    if role.name == "admin":
        account.must_change_password = False
    elif account.last_login_at is None:
        account.must_change_password = True
    account.updated_at = datetime.now(timezone.utc)
    if account.employee:
        account.employee.full_name = cleaned_name
    add_audit_event(
        db,
        request,
        "USER_UPDATED",
        "user",
        account.id,
        user=current_user,
        details={"before": before, "after": {"full_name": cleaned_name, "role": role.name}},
    )
    db.commit()
    flash(request, "Учётная запись обновлена. Логин оставлен без изменений.")
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{user_id}/reset-password", name="user_reset_password")
def user_reset_password(
    user_id: int,
    request: Request,
    new_password: Annotated[str, Form(min_length=1, max_length=200)],
    _: None = Depends(validate_csrf),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Выдаёт новый пароль без знания забытого и фиксирует действие в аудите."""

    account = db.scalar(
        select(User)
        .options(joinedload(User.role))
        .where(User.id == user_id)
        .with_for_update(of=User)
    )
    if not account or account.is_deleted:
        raise HTTPException(status_code=404, detail="Учётная запись не найдена")
    password_error = validate_issued_password(new_password, account.role.name)
    if password_error:
        flash(request, password_error, "danger")
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)

    account.password_hash = hash_password(new_password)
    account.must_change_password = account.role.name != "admin"
    account.failed_login_attempts = 0
    account.locked_until = None
    account.updated_at = datetime.now(timezone.utc)
    add_audit_event(
        db,
        request,
        "USER_PASSWORD_RESET",
        "user",
        account.id,
        user=current_user,
        details={"username": account.username, "must_change_password": account.must_change_password},
    )
    db.commit()
    if account.role.name == "admin":
        flash(request, "Пароль администратора изменён.")
    else:
        flash(request, "Новый пароль выдан. При следующем входе пользователь должен его сменить.")
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{user_id}/toggle", name="user_toggle")
def user_toggle(
    user_id: int,
    request: Request,
    _: None = Depends(validate_csrf),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Блокирует или восстанавливает учётную запись без её удаления."""

    account = db.scalar(
        select(User).options(joinedload(User.role)).where(User.id == user_id).with_for_update(of=User)
    )
    if not account:
        raise HTTPException(status_code=404, detail="Учётная запись не найдена")
    if account.id == current_user.id:
        flash(request, "Нельзя заблокировать собственную учётную запись.", "danger")
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)
    if account.is_deleted:
        flash(request, "Удалённую учётную запись сначала необходимо восстановить.", "danger")
        return RedirectResponse("/users?deleted=true", status_code=status.HTTP_303_SEE_OTHER)
    if account.is_active and account.role.name == "admin":
        active_admins = db.scalar(
            select(func.count())
            .select_from(User)
            .join(Role)
            .where(User.is_active.is_(True), User.is_deleted.is_(False), Role.name == "admin")
        )
        if (active_admins or 0) <= 1:
            flash(request, "Нельзя заблокировать последнего действующего администратора.", "danger")
            return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)

    account.is_active = not account.is_active
    account.updated_at = datetime.now(timezone.utc)
    add_audit_event(
        db,
        request,
        "USER_STATUS_CHANGED",
        "user",
        account.id,
        user=current_user,
        details={"username": account.username, "is_active": account.is_active},
    )
    db.commit()
    flash(request, "Состояние учётной записи изменено.")
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{user_id}/delete", name="user_delete")
def user_delete(
    user_id: int,
    request: Request,
    reason: Annotated[str, Form(min_length=3, max_length=1000)],
    _: None = Depends(validate_csrf),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Мягко удаляет учётную запись, сохраняя связи и журнал действий."""

    account = db.scalar(
        select(User).options(joinedload(User.role)).where(User.id == user_id).with_for_update(of=User)
    )
    if not account:
        raise HTTPException(status_code=404, detail="Учётная запись не найдена")
    if account.id == current_user.id:
        flash(request, "Нельзя удалить собственную учётную запись.", "danger")
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)
    if account.is_deleted:
        flash(request, "Учётная запись уже удалена.", "warning")
        return RedirectResponse("/users?deleted=true", status_code=status.HTTP_303_SEE_OTHER)
    if account.is_active and account.role.name == "admin":
        active_admins = db.scalar(
            select(func.count())
            .select_from(User)
            .join(Role)
            .where(User.is_active.is_(True), User.is_deleted.is_(False), Role.name == "admin")
        )
        if (active_admins or 0) <= 1:
            flash(request, "Нельзя удалить последнего действующего администратора.", "danger")
            return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)

    account.is_deleted = True
    account.is_active = False
    account.deleted_at = datetime.now(timezone.utc)
    account.deleted_by = current_user.id
    account.deletion_reason = reason.strip()
    account.updated_at = account.deleted_at
    add_audit_event(
        db,
        request,
        "USER_DELETED",
        "user",
        account.id,
        user=current_user,
        details={"username": account.username, "reason": account.deletion_reason},
    )
    db.commit()
    flash(request, "Учётная запись удалена. Её можно восстановить в разделе «Удалённые».", "warning")
    return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{user_id}/restore", name="user_restore")
def user_restore(
    user_id: int,
    request: Request,
    _: None = Depends(validate_csrf),
    current_user: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    """Восстанавливает мягко удалённую учётную запись."""

    account = db.scalar(select(User).where(User.id == user_id).with_for_update(of=User))
    if not account:
        raise HTTPException(status_code=404, detail="Учётная запись не найдена")
    if not account.is_deleted:
        flash(request, "Учётная запись не удалена.", "warning")
        return RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)

    account.is_deleted = False
    account.is_active = True
    account.deleted_at = None
    account.deleted_by = None
    account.deletion_reason = None
    account.failed_login_attempts = 0
    account.locked_until = None
    account.updated_at = datetime.now(timezone.utc)
    add_audit_event(
        db,
        request,
        "USER_RESTORED",
        "user",
        account.id,
        user=current_user,
        details={"username": account.username},
    )
    db.commit()
    flash(request, "Учётная запись восстановлена и активирована.")
    return RedirectResponse("/users?deleted=true", status_code=status.HTTP_303_SEE_OTHER)
